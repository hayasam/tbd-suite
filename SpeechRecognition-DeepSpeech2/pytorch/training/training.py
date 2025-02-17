import argparse
import time
import sys
sys.path.append('../')

import torch
from warpctc_pytorch import CTCLoss

from dataset.data_loader import AudioDataLoader, SpectrogramDataset
from model.decoder import GreedyDecoder
import model.params as params
from model.eval_model import eval_model
from model.utils import *



def main(args):
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)

    args.checks_per_epoch = max(1, args.checks_per_epoch)

    loss_results = torch.Tensor(params.epochs)
    cer_results = torch.Tensor(params.epochs)
    wer_results = torch.Tensor(params.epochs)
    best_wer = None
    make_folder(args.save_folder)

    labels = get_labels(params)
    audio_conf = get_audio_conf(params)

    val_batch_size = min(8, params.batch_size_val)
    print("Using bs={} for validation. Parameter found was {}".format(val_batch_size, params.batch_size_val))

    train_dataset = SpectrogramDataset(audio_conf=audio_conf,
                                       manifest_filepath=params.train_manifest,
                                       labels=labels,
                                       normalize=True,
                                       augment=params.augment)
    test_dataset = SpectrogramDataset(audio_conf=audio_conf,
                                      manifest_filepath=params.val_manifest,
                                      labels=labels,
                                      normalize=True,
                                      augment=False)
    train_loader = AudioDataLoader(train_dataset,
                                   batch_size=params.batch_size,
                                   num_workers=1)
    test_loader = AudioDataLoader(test_dataset,
                                  batch_size=val_batch_size,
                                  num_workers=1)

    model = get_model(params)

    print("=======================================================")
    for arg in vars(args):
        print("***{} = {} ".format(arg.ljust(25), getattr(args, arg)))
    print("=======================================================")

    criterion = CTCLoss()
    parameters = model.parameters()
    optimizer = torch.optim.SGD(parameters, lr=params.lr,
                                momentum=params.momentum, nesterov=True,
                                weight_decay=params.l2)
    decoder = GreedyDecoder(labels)

    if args.continue_from:
        print("Loading checkpoint model {}".format(args.continue_from))
        package = torch.load(args.continue_from)
        model.load_state_dict(package['state_dict'])
        model = model.cuda()
        optimizer.load_state_dict(package['optim_dict'])
        start_epoch = int(package.get('epoch', 1)) - 1  # Python index start at 0 for training
        start_iter = package.get('iteration', None)
        if start_iter is None:
            start_epoch += 1  # Assume that we saved a model after an epoch finished, so start at the next epoch.
            start_iter = 0
        else:
            start_iter += 1
        avg_loss = int(package.get('avg_loss', 0))

        if args.start_epoch != -1:
            start_epoch = args.start_epoch

        loss_results[:start_epoch] = package['loss_results'][:start_epoch]
        cer_results[:start_epoch] = package['cer_results'][:start_epoch]
        wer_results[:start_epoch] = package['wer_results'][:start_epoch]
        print(loss_results)

    else:
        avg_loss = 0
        start_epoch = 0
        start_iter = 0
    if params.cuda:
        model = torch.nn.DataParallel(model).cuda()

    print(model)
    print("Number of parameters: {}".format(DeepSpeech.get_param_size(model)))

    batch_time = AverageMeter()
    data_time = AverageMeter()
    losses = AverageMeter()
    ctc_time = AverageMeter()

    for epoch in range(start_epoch, params.epochs):
        model.train()
        end = time.time()
        for i, (data) in enumerate(train_loader, start=start_iter):
            if i == len(train_loader):
                break
            inputs, targets, input_percentages, target_sizes = data
            # measure data loading time
            data_time.update(time.time() - end)
            inputs = torch.Tensor(inputs, requires_grad=False)
            target_sizes = torch.Tensor(target_sizes, requires_grad=False)
            targets = torch.Tensor(targets, requires_grad=False)

            if params.cuda:
                inputs = inputs.cuda()

            out = model(inputs)
            out = out.transpose(0, 1)  # TxNxH

            seq_length = out.size(0)
            sizes = torch.Tensor(input_percentages.mul_(int(seq_length)).int(), requires_grad=False)

            ctc_start_time = time.time()
            loss = criterion(out, targets, sizes, target_sizes)
            ctc_time.update(time.time() - ctc_start_time)

            loss = loss / inputs.size(0)  # average the loss by minibatch

            loss_sum = loss.data.sum()
            inf = float("inf")
            if loss_sum == inf or loss_sum == -inf:
                print("WARNING: received an inf loss, setting loss value to 0")
                loss_value = 0
            else:
                loss_value = loss.data[0]

            avg_loss += loss_value
            losses.update(loss_value, inputs.size(0))

            # compute gradient
            optimizer.zero_grad()
            loss.backward()

            torch.nn.utils.clip_grad_norm(model.parameters(), params.max_norm)
            # SGD step
            optimizer.step()

            if params.cuda:
                torch.cuda.synchronize()

            # measure elapsed time
            batch_time.update(time.time() - end)
            end = time.time()

            print('Epoch: [{0}][{1}/{2}]\t'
                  'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                  'Data {data_time.val:.3f} ({data_time.avg:.3f})\t'
                  'CTC Time {ctc_time.val:.3f} ({ctc_time.avg:.3f})\t'
                  'Loss {loss.val:.4f} ({loss.avg:.4f})\t'.format(
                (epoch + 1), (i + 1), len(train_loader), batch_time=batch_time,
                data_time=data_time, ctc_time=ctc_time, loss=losses))

        avg_loss /= len(train_loader)

        print('Training Summary Epoch: [{0}]\t'
              'Average Loss {loss:.3f}\t'
              .format(epoch + 1, loss=avg_loss, ))

        start_iter = 0  # Reset start iteration for next epoch
        model.eval()

        try:
            model.eval()
            wer, cer = eval_model(model, test_loader, decoder)
        except RuntimeError:
            print("skipping eval model checkpoint.... ")

        loss_results[epoch] = avg_loss
        wer_results[epoch] = wer
        cer_results[epoch] = cer
        print('Validation Summary Epoch: [{0}]\t'
              'Average WER {wer:.3f}\t'
              'Average CER {cer:.3f}\t'.format(
            epoch + 1, wer=wer, cer=cer))

        if args.checkpoint:
            file_path = '{}/deepspeech_{}.pth'.format(args.save_folder, epoch + 1)
            torch.save(DeepSpeech.serialize(model, optimizer=optimizer, epoch=epoch, loss_results=loss_results,
                                            wer_results=wer_results, cer_results=cer_results),
                       file_path)
        # anneal lr
        optim_state = optimizer.state_dict()
        optim_state['param_groups'][0]['lr'] = optim_state['param_groups'][0]['lr'] / params.learning_anneal
        optimizer.load_state_dict(optim_state)
        print('Learning rate annealed to: {lr:.6f}'.format(lr=optim_state['param_groups'][0]['lr']))

        if best_wer is None or best_wer > wer:
            print("Found better validated model, saving to {}".format(args.model_path))
            torch.save(DeepSpeech.serialize(model,
                                            optimizer=optimizer,
                                            epoch=epoch,
                                            loss_results=loss_results,
                                            wer_results=wer_results,
                                            cer_results=cer_results),
                       args.model_path)
            best_wer = wer

        avg_loss = 0
        model.train()

        # If set to exit at a given accuracy, exit
        if params.exit_at_acc and (best_wer <= args.acc):
            break

    print("=======================================================")
    print("***Best WER = ", best_wer)
    for arg in vars(args):
        print("***{} = {} ".format(arg.ljust(25), getattr(args, arg)))
    print("=======================================================")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DeepSpeech training')
    parser.add_argument('--checkpoint', dest='checkpoint',
                        action='store_true', help='Enables checkpoint saving of model')
    parser.add_argument('--save_folder', default='./',
                        type=str, help='Location to save epoch models')
    parser.add_argument('--model_path', default='./deepspeech_final.pth',
                        type=str, help='Location to save best validation model')
    parser.add_argument('--continue_from', default='',
                        type=str, help='Continue from checkpoint model')
    parser.add_argument('--seed', default=0xdeadbeef,
                        type=int, help='Random Seed')
    parser.add_argument('--acc', default=23.0,
                        type=float, help='Target WER')
    parser.add_argument('--start_epoch', default=-1,
                        type=int, help='Number of epochs at which to start from')
    parser.add_argument('--checks_per_epoch', default=4,
                        type=int, help='Number of checkpoints to evaluate and save per epoch')
    args = parser.parse_args()
    main(args)
