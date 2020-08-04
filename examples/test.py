from __future__ import print_function, absolute_import
import argparse
import os.path as osp
import random
import numpy as np
import sys

import torch
from torch import nn
from torch.backends import cudnn
from torch.utils.data import DataLoader

from visda import datasets
from visda import models
from visda.models.dsbn import convert_dsbn, convert_bn
from visda.evaluators import Evaluator, extract_features
from visda.utils.data import transforms as T
from visda.utils.data.preprocessor import Preprocessor
from visda.utils.logging import Logger
from visda.utils.serialization import load_checkpoint, save_checkpoint, copy_state_dict


def get_data(name, data_dir, height, width, batch_size, workers, trainset=False, flip=False):
    dataset = datasets.create(name, data_dir)

    normalizer = T.Normalize(mean=[0.485, 0.456, 0.406],
                             std=[0.229, 0.224, 0.225])

    test_transformer = T.Compose([
             T.Resize((height, width), interpolation=3),
             T.ToTensor(),
             normalizer
         ])

    test_set = sorted(dataset.train) if trainset else list(set(dataset.query) | set(dataset.gallery))
    test_loader = DataLoader(Preprocessor(test_set, root=dataset.images_dir, transform=test_transformer, flip=flip),
                            batch_size=batch_size, num_workers=workers, shuffle=False, pin_memory=True)

    return dataset, test_loader


def main():
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
        np.random.seed(args.seed)
        torch.manual_seed(args.seed)
        cudnn.deterministic = True

    main_worker(args)


def main_worker(args):
    cudnn.benchmark = True

    log_dir = osp.dirname(args.resume)
    if args.dataset!='target_test':
        sys.stdout = Logger(osp.join(log_dir, 'log_test.txt'))
    print("==========\nArgs:{}\n==========".format(args))

    # Create data loaders
    dataset, test_loader = get_data(args.dataset, args.data_dir, args.height,
                                    args.width, args.batch_size, args.workers, flip=args.flip)

    # Create model
    model = models.create(args.arch, pretrained=False, num_features=args.features, dropout=args.dropout, num_classes=0)
    if args.dsbn:
        print("==> Load the model with domain-specific BNs")
        convert_dsbn(model)

    # Create camera model
    if args.camera:
        cam_model = models.create(args.arch_c, pretrained=False, num_features=args.features, dropout=args.dropout, num_classes=0)
        checkpoint = load_checkpoint(args.camera)
        copy_state_dict(checkpoint['state_dict'], cam_model, strip='module.')
        cam_model.cuda()
        cam_model = nn.DataParallel(cam_model)

    # Load from checkpoint
    checkpoint = load_checkpoint(args.resume)
    copy_state_dict(checkpoint['state_dict'], model, strip='module.')

    if args.dsbn:
        print("==> Test with {}-domain BNs".format("source" if args.test_source else "target"))
        convert_bn(model, use_target=(not args.test_source))

    model.cuda()
    model = nn.DataParallel(model)

    # Evaluator
    evaluator = Evaluator(model, cam_model=cam_model if args.camera else None, cam_weight=0.1, flip=args.flip)
    print("Test on {}:".format(args.dataset))
    evaluator.evaluate(test_loader, dataset.query, dataset.gallery,
                        rerank=args.rerank, k1=args.k1, k2=args.k2, lambda_value=args.lambda_value,
                        submit_file=osp.join(log_dir, 'result.txt'), qe=False,
                        only_submit=(True if args.dataset=='target_test' else False))

    return

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Testing a single model")
    # data
    parser.add_argument('-d', '--dataset', type=str, required=True,
                        choices=datasets.names())
    parser.add_argument('-b', '--batch-size', type=int, default=256)
    parser.add_argument('-j', '--workers', type=int, default=4)
    parser.add_argument('--height', type=int, default=384, help="input height")
    parser.add_argument('--width', type=int, default=128, help="input width")
    # model
    parser.add_argument('-a', '--arch', type=str, required=True,
                        choices=models.names())
    parser.add_argument('-ac', '--arch-c', type=str, default='resnet_ibn50a',
                        choices=models.names())
    parser.add_argument('--features', type=int, default=0)
    parser.add_argument('--dropout', type=float, default=0)
    parser.add_argument('--resume', type=str, required=True, metavar='PATH')
    parser.add_argument('--camera', type=str, default='', metavar='PATH')
    # testing configs
    parser.add_argument('--rerank', action='store_true',
                        help="evaluation only")
    parser.add_argument('--flip', action='store_true',
                        help="evaluation only")
    parser.add_argument('--dsbn', action='store_true',
                        help="test on the model with domain-specific BN")
    parser.add_argument('--test-source', action='store_true',
                        help="test on the source domain")
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--k1', type=int, default=30)
    parser.add_argument('--k2', type=int, default=6)
    parser.add_argument('--lambda-value', type=float, default=0.3)
    # path
    working_dir = osp.dirname(osp.abspath(__file__))
    parser.add_argument('--data-dir', type=str, metavar='PATH',
                        default=osp.join(working_dir, 'data'))
    main()
