import torch
import argparse
import json

from torchvision.datasets import cifar
from torchvision import transforms
import torch.nn.functional as F
from torch.utils.tensorboard import SummaryWriter

from resnet_2 import resnet110, resnet164, resnet110_1layer

parser = argparse.ArgumentParser(description='PyTorch ResNet Training')
parser.add_argument('--dataset', default='cifar10', type=str,
                    help='dataset (cifar10 [default] or cifar100)')
parser.add_argument('--epochs', default=160, type=int,
                    help='number of total epochs to run')
parser.add_argument('--start_epoch', default=0, type=int,
                    help='manual epoch number (useful on restarts)')
parser.add_argument('--batch_size', '--batch-size', default=128, type=int,
                    help='mini-batch size (default: 100)')
parser.add_argument('--lr', '--learning-rate', default=1e-1, type=float,
                    help='initial learning rate')
parser.add_argument('--momentum', default=0.9, type=float, help='momentum')
parser.add_argument('--nesterov', default=True, type=bool, help='nesterov momentum')
parser.add_argument('--weight-decay', '--wd', default=1e-4, type=float,
                    help='weight decay (default: 5e-4)')
parser.add_argument('--prefetch', type=int, default=0, help='Pre-fetching threads.')
parser.add_argument('--log_name', type=str, default='./log/default', help='log name')
parser.add_argument('--warmup',
                    action='store_true',
                    help='enables warm up')
parser.add_argument('--gpu',
                    type=int,
                    default=0)
parser.add_argument('--step1',
                    type=int,
                    default=80)
parser.add_argument('--step2',
                    type=int,
                    default=140)
parser.add_argument('--model', type=str, default="resnet110", choices=["resnet110", "resnet164", "resnet110-1skip"])
parser.add_argument('--case', type=int, default=0, help="Network case\n"
                                                        "0: original\n"
                                                        "1: BN after addition\n"
                                                        "2: ReLU before addition\n"
                                                        "3: ReLU-only pre-activation\n"
                                                        "4: full pre-activation")
parser.add_argument('--resume',
                    action='store_true',
                    help='resume train process')
parser.set_defaults(augment=True)

args = parser.parse_args()
use_cuda = True
device = torch.device("cuda" if use_cuda else "cpu")
torch.cuda.set_device(args.gpu)

print()
print(args)
logger = SummaryWriter(log_dir='./runs/' + args.log_name)
with open('./runs/' + args.log_name + '/params.json', mode="w") as f:
    json.dump(args.__dict__, f, indent=4)


def build_dataset(dataset):
    normalize = transforms.Normalize(mean=[x / 255.0 for x in [125.3, 123.0, 113.9]],
                                     std=[x / 255.0 for x in [63.0, 62.1, 66.7]])
    train_transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Lambda(lambda x: F.pad(x.unsqueeze(0),
                                          (4, 4, 4, 4), mode='reflect').squeeze()),
        transforms.ToPILImage(),
        transforms.RandomCrop(32),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        normalize,
    ])
    val_transform = transforms.Compose([
        transforms.ToTensor(),
        normalize,
    ])
    if dataset == "cifar10":
        train_data = cifar.CIFAR10('./CIFAR10', train=True, transform=train_transform, download=True)
        train_loader = torch.utils.data.DataLoader(train_data, batch_size=args.batch_size, shuffle=True,
                                                   num_workers=args.prefetch, pin_memory=True)
        val_data = cifar.CIFAR10('./CIFAR10', train=False, transform=val_transform, download=True)
        val_loader = torch.utils.data.DataLoader(val_data, batch_size=args.batch_size, shuffle=False,
                                                 num_workers=args.prefetch)
    if dataset == "cifar100":
        train_data = cifar.CIFAR100('./CIFAR100', train=True, transform=train_transform, download=True)
        train_loader = torch.utils.data.DataLoader(train_data, batch_size=args.batch_size, shuffle=True,
                                                   num_workers=args.prefetch, pin_memory=True)
        val_data = cifar.CIFAR100('./CIFAR100', train=False, transform=val_transform, download=True)
        val_loader = torch.utils.data.DataLoader(val_data, batch_size=args.batch_size, shuffle=False,
                                                 num_workers=args.prefetch)
    return train_loader, val_loader


def accuracy(output, target):
    """Computes the precision@k for the specified values of k"""
    batch_size = target.size(0)
    #  k, dim=None, largest=True, sorted=True
    _, pred = output.topk(1, 1, True, True)
    pred = pred.t()
    correct = pred.eq(target.view(1, -1).expand_as(pred))

    return correct.sum().item() / batch_size * 100


def test(model, test_loader):
    model.eval()
    correct = 0
    test_loss = 0

    with torch.no_grad():
        for batch_idx, (inputs, targets) in enumerate(test_loader):
            inputs, targets = inputs.to(device), targets.to(device)
            outputs = model(inputs)
            test_loss += F.cross_entropy(outputs, targets).item()
            _, predicted = outputs.max(1)
            correct += predicted.eq(targets).sum().item()

    test_loss /= len(test_loader)
    accuracy = 100. * correct / len(test_loader.dataset)

    print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.4f}%)\n'.format(
        test_loss, correct, len(test_loader.dataset),
        accuracy))
    return accuracy, test_loss


def train(model, train_loader, optimizer, epoch):
    model.train()
    train_loss = 0
    for batch_idx, (inputs, targets) in enumerate(train_loader):
        inputs = inputs.to(device)
        targets = targets.to(device)
        y_f = model(inputs)
        Loss = F.cross_entropy(y_f, targets.long())
        optimizer.zero_grad()
        Loss.backward()
        optimizer.step()
        prec_train = accuracy(y_f.data, targets.long().data)
        train_loss += Loss.item()

        if (batch_idx + 1) % 100 == 0:
            print('Epoch: [%d/%d]\t'
                  'Iters: [%d/%d]\t'
                  'Loss: %.4f\t'
                  'Prec@1 %.2f\t' % (
                      epoch, args.epochs, batch_idx + 1, len(train_loader.dataset) / args.batch_size,
                      (train_loss / (batch_idx + 1)),
                      prec_train))

    return prec_train, train_loss / (len(train_loader))


def adjust_learning_rate(optimizer, epochs):
    if args.warmup:
        lr = args.lr * ((10 ** int(epochs >= 10)) * (0.1 ** int(epochs >= args.step1)) * (0.1 ** int(epochs >= args.step2)))
    else:
        lr = args.lr * ((0.1 ** int(epochs >= args.step1)) * (0.1 ** int(epochs >= args.step2)))
        # lr = args.lr * ((0.1 ** int(epochs >= 140)) * (0.1 ** int(epochs >= 180)))
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


train_loader, val_loader = build_dataset(args.dataset)
if args.model == "resnet110":
    model = resnet110(args.case).to(device)
elif args.model == "resnet164":
    model = resnet164(args.case).to(device)
elif args.model == "resnet110-1skip":
    model = resnet110_1layer(args.case).to(device)
else:
    raise ValueError("Invalid model name, expected in [resnet110, resnet164, resnet-1skip], got " + args.model)

if args.dataset == "cifar100":
    in_channel = model.fc.in_features
    model.fc = torch.nn.Linear(in_channel, 100).to(device)
# print(model)
if args.warmup:
    args.lr = args.lr * 0.1

optimizer_model = torch.optim.SGD(model.parameters(), args.lr,
                                  momentum=args.momentum, weight_decay=args.weight_decay)

if __name__ == "__main__":
    best_acc = 0
    start_epoch = args.start_epoch

    if args.resume:
        checkpoint = torch.load('./runs/' + args.log_name + "/checkpoints.pth")
        assert ValueError("No checkpoint found!")
        model.load_state_dict(checkpoint['model'])
        optimizer_model.load_state_dict(checkpoint['optimizer'])
        start_epoch = checkpoint['epoch']
        best_acc = checkpoint['best_acc']

    for epoch in range(start_epoch, args.epochs):
        adjust_learning_rate(optimizer_model, epoch)
        train_acc, train_loss = train(model, train_loader, optimizer_model, epoch)
        logger.add_scalar("Loss/Train", train_loss, global_step=epoch)
        logger.add_scalar("Accuracy/Train", train_acc, global_step=epoch)
        test_acc, test_loss = test(model, val_loader)
        logger.add_scalar("Loss/Validation", test_loss, global_step=epoch)
        logger.add_scalar("Accuracy/Validation", test_acc, global_step=epoch)

        if best_acc < test_acc:
            best_acc = test_acc
            torch.save(model.state_dict(), './runs/' + args.log_name + "/best.pth")

        # ????????????
        state = {'model': model.state_dict(),
                 'optimizer': optimizer_model.state_dict(),
                 'epoch': epoch + 1,
                 'best_acc': best_acc}
        torch.save(state, './runs/' + args.log_name + "/checkpoints.pth")

    # ????????????
    torch.save(model.state_dict(), './runs/' + args.log_name + "/last.pth")
    print("Best Acc: {:.4f}%".format(best_acc))
    logger.add_text("Best Acc", str(best_acc), global_step=0)
