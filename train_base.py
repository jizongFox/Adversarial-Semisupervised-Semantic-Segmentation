from __future__ import unicode_literals

from collections import OrderedDict
import torch
from datasets.pascalvoc import PascalVOC
import generators.deeplabv2 as deeplabv2
import discriminators.discriminator as dis
from torchvision import transforms
from torch.autograd import Variable
from torch.utils.data import DataLoader
from utils.transforms import RandomSizedCrop, IgnoreLabelClass, ToTensorLabel, NormalizeOwn,ZeroPadding
from utils.lr_scheduling import poly_lr_scheduler
import torch.nn.functional as F
import torch.nn as nn
from functools import reduce
import torch.optim as optim
import os
import argparse
from torchvision.transforms import ToTensor,Compose
from utils.validate import val
from utils.helpers import pascal_palette_invert
import torchvision.transforms as transforms
import PIL.Image as Image
def main():

    home_dir = os.path.dirname(os.path.realpath(__file__))

    # Parse arguments
    parser = argparse.ArgumentParser()

    parser.add_argument("prefix",help="Prefix to identify current experiment")
    parser.add_argument("dataset_dir",help="A directory containing img (Images) \
                        and cls (GT Segmentation) folder")
    parser.add_argument("--max_epoch",help="Maximum iterations.",default=20,\
                        type=int)
    parser.add_argument("--start_epoch",help="Resume training from this epoch",\
                        default=1,type=int)
    parser.add_argument("--snapshot",help="Snapshot to resume training")
    parser.add_argument("--snapshot_dir",help="Location to store the snapshot", \
                        default=os.path.join(home_dir,'data','snapshots'))
    parser.add_argument("--batch_size",help="Batch size for training",default=10,\
                        type=int)
    parser.add_argument("--val_orig", help="Do Inference on original size image.\
                        Otherwise, crop to 321x321 like in training ",action='store_true')
    args = parser.parse_args()

    # Load the trainloader
    img_transform = [ToTensor()]
    label_transform = [IgnoreLabelClass(),ToTensorLabel()]
    co_transform = [RandomSizedCrop((321,321))]


    trainset = PascalVOC(home_dir,args.dataset_dir,img_transform=Compose(img_transform), label_transform=Compose(label_transform), \
        co_transform=Compose(co_transform))
    trainloader = DataLoader(trainset,batch_size=args.batch_size,shuffle=True,num_workers=2,drop_last=True)
    print("Training Data Loaded")
    # import pdb; pdb.set_trace()
    # Load the valoader
    if args.val_orig:
        img_transform = [ZeroPadding(),ToTensor()]
        label_transform = [IgnoreLabelClass(),ToTensorLabel()]
        co_transform = []
    else:
        img_transform = [ToTensor()]
        label_transforms = [IgnoreLabelClass(),ToTensorLabel()]
        co_transforms = [RandomSizedCrop((321,321))]

    valset = PascalVOC(home_dir,args.dataset_dir,img_transform=Compose(img_transform), \
        label_transform = Compose(label_transform),co_transform=Compose(co_transform),train_phase=False)

    valoader = DataLoader(valset,batch_size=1)

    print("Validation Data Loaded")
    # import pdb; pdb.set_trace()
    generator = deeplabv2.Res_Deeplab()
    print("Generator Loaded!")

    optimizer = optim.SGD(filter(lambda p: p.requires_grad, \
        generator.parameters()),lr=0.00025,momentum=0.9,\
        weight_decay=0.0001,nesterov=True)

    # Load the snapshot if available
    if  args.snapshot and os.path.isfile(args.snapshot):
        print("Snapshot Available at {} ".format(args.snapshot))
        snapshot = torch.load(args.snapshot)
        new_state = generator.state_dict()
        saved_net = {k.partition('module.')[2]: v for i, (k,v) in enumerate(snapshot['state_dict'].items())}
        new_state.update(saved_net)
        generator.load_state_dict(new_state)
        # optimizer.load_state_dict(snapshot['optimizer'])

    else:
        print("No Snapshot. Loading '{}'".format("MS_DeepLab_resnet_pretrained_COCO_init.pth"))
        saved_net = torch.load(os.path.join(home_dir,'data',\
            'MS_DeepLab_resnet_pretrained_COCO_init.pth'))
        new_state = generator.state_dict()
        saved_net = {k.partition('Scale.')[2]: v for i, (k,v) in enumerate(saved_net.items())}
        new_state.update(saved_net)
        generator.load_state_dict(new_state)

    generator = nn.DataParallel(generator).cuda()
    print("Generator Setup for Parallel Training")

    print("Optimizer Loaded")
    best_miou = -1
    print('Training Going to Start')
    for epoch in range(args.start_epoch,args.max_epoch+1):
        generator.train()
        for batch_id, (img,mask) in enumerate(trainloader):
            img,mask = Variable(img.cuda()),Variable(mask.cuda())
            # import pdb; pdb.set_trace()
            out_img_map = generator(img)
            out_img_map = nn.LogSoftmax()(out_img_map)
            L_ce = nn.NLLLoss2d()
            loss = L_ce(out_img_map,mask)
            i = len(trainloader)*(epoch-1) + batch_id
            poly_lr_scheduler(optimizer, 0.00025, i)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        print("Epoch {} Finished!".format(epoch))

        snapshot = {
            'epoch': epoch,
            'state_dict': generator.state_dict(),
            'optimizer': optimizer.state_dict(),

        }
        miou = val(generator,valoader)

        snapshot['miou'] = miou
        if miou > best_miou:
            print("Best miou: {}, at epoch: {}".format(miou,epoch))
            best_miou = miou
            torch.save(snapshot,os.path.join(args.snapshot_dir,'{}.pth.tar'.format(args.prefix)))
            print("Snapshot written")


if __name__ == '__main__':
    main()
