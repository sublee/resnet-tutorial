# -*- coding: utf-8 -*-
from datetime import datetime
import os
import sys
import logging

from tensorboardX import SummaryWriter
import torch
from torch import nn
from torch.nn import functional as F
import numpy as np

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from skeleton.resnet import ResNet50
from skeleton.datasets import Cifar224


def correct_total(outputs, targets):
    _, predicted = torch.max(outputs.data, 1)
    correct = (predicted == targets).sum().item()
    total = targets.size(0)
    return (correct, total)


def main(args):
    logging.info('args: %s', args)
    device = torch.device('cuda', 0) if torch.cuda.is_available() else torch.device('cpu', 0)

    batch_size = args.batch
    train_loader, valid_loader, test_loader, data_shape = Cifar224.loader(batch_size, args.num_class)
    _ = test_loader

    model = ResNet50(args.num_class)
    model.to(device=device)

    # Print layer shapes.
    canary = torch.Tensor(*data_shape[0]).to(device)
    with torch.no_grad():
        model(canary, verbose=True)

    # Enable data parallelism.
    model = nn.DataParallel(model)

    # Integrate with TensorBoard.
    run_name = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    if args.run:
        run_name += '_' + args.run
    tb_train = SummaryWriter('runs/%s/train' % run_name)
    tb_valid = SummaryWriter('runs/%s/valid' % run_name)
    global_step = 0

    optimizer = torch.optim.SGD(model.parameters(), lr=1e-4 * batch_size, momentum=0.9, weight_decay=0.0001)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer)

    for epoch in range(args.epoch):
        # log LR
        for param_group in optimizer.param_groups:
            try:
                lr = param_group['lr']
            except KeyError:
                continue
            tb_train.add_scalar('lr', lr, global_step)

        # train
        for batch_idx, (inputs, targets) in enumerate(train_loader):
            targets = targets.to(device)

            outputs = model(inputs)
            loss = F.cross_entropy(outputs, targets)

            loss.backward()
            optimizer.step()
            optimizer.zero_grad()

            correct, total = correct_total(outputs, targets)
            accuracy = correct / total

            logging.info('[train] [epoch:%04d/%04d] [step:%04d/%04d] loss: %.5f',
                         epoch + 1, args.epoch, batch_idx + 1, len(train_loader), float(loss))

            global_step += 1
            tb_train.add_scalar('loss', float(loss), global_step)
            tb_train.add_scalar('accuracy', accuracy, global_step)

        # validate
        with torch.no_grad():
            losses = []

            correct = 0
            total = 0

            for inputs, targets in valid_loader:
                targets = targets.to(device)

                outputs = model(inputs)

                # loss
                loss = float(F.cross_entropy(outputs, targets))
                losses.append(loss)

                # accuracy
                correct_, total_ = correct_total(outputs, targets)
                correct += correct_
                total += total_

            loss = np.average(losses)
            accuracy = correct / total

            logging.info('[vaild] [epoch:%04d/%04d]                  loss: %.5f, accuracy: %.1f%%',
                         epoch + 1, args.epoch, loss, accuracy * 100)

            tb_valid.add_scalar('loss', float(loss), global_step)
            tb_valid.add_scalar('accuracy', accuracy, global_step)

        # adjust LR by validation loss
        scheduler.step(loss)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--num-class', type=int, default=10, help='10 or 100')
    parser.add_argument('-b', '--batch', type=int, default=128)
    parser.add_argument('-e', '--epoch', type=int, default=25)

    parser.add_argument('--run', type=str, default='')

    parser.add_argument('--gpus', type=int, default=torch.cuda.device_count())

    parser.add_argument('--log-filename', type=str, default='')
    parser.add_argument('--debug', action='store_true')
    parsed_args = parser.parse_args()

    log_format = '[%(asctime)s %(levelname)s] %(message)s'
    level = logging.DEBUG if parsed_args.debug else logging.INFO
    if not parsed_args.log_filename:
        logging.basicConfig(level=level, format=log_format, stream=sys.stderr)
    else:
        logging.basicConfig(level=level, format=log_format, filename=parsed_args.log_filename)

    main(parsed_args)
