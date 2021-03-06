# Semantic Segmentation
A somewhat clean implementation of a straight forward semantic segmentation network in TensorFlow. This is mainly used for the BMBF project PARIS.

Large parts correspond to the ResNet baseline described in: [Full-Resolution Residual Networks for Semantic Segmentation in Street Scenes](https://arxiv.org/abs/1611.08323) Tobias Pohlen, Alexander Hermans, Markus Mathias, and Bastian Leibe. CVPR 2017
The original Theano code can be found [here](https://github.com/TobyPDE/FRRN).

A big chunk of this code has been copied or adapted from our [person re-identification project](https://github.com/VisualComputingInstitute/triplet-reid).
This code was written by Lucas Beyer and me, however, he did most of the code polishing.

## Usage
Some quick pointers on how to train and evaluate the network. Most arguments have a decent documentation. Please refer to those for more details

### Dataset Generation
First downscale the images for realistic training. Be aware this will be resizing a lot of images. Since resizing of labels was not implemented super efficiently this will take several hours, but it is less wasteful than doing it on the fly during training.

Example call:
```
python resize_cityscapes.py --downscale_factor 4 \
        --cityscapes_root /work/hermans/cityscapes \
        --target_root /work/hermans/cityscapes/down4
```

### Training
Make sure the dataset is available in the desired downsample rate. My currently best working setup for CityScapes is the following:
```
python train.py --experiment_root /root/to/store/experiment_data \
        --train_set datasets/cityscape_fine/train.csv \
        --dataset_root /root/to/downsampled/data \
        --dataset_config datasets/cityscapes.json \
        --flip_augment \
        --gamma_augment \
        --crop_augment 64 \
        --loss_type bootstrapped_cross_entropy_loss \
```
This should give about ~70% mean IoU on the validation set.

### Evaluation
The evaluation script is used for two things. It passes all the images provided in a csv file through the network and it performs the numerical evaluation. At the same time it can be used to save color coded or grayscale result images. The typical CityScapes evaluation (+ storing RGB images) can be done using:
```
python evaluate.py --experiment_root /root/to/store/experiment_data \
        --eval_set datasets/cityscape_fine/val.csv \
        --rgb_input_root /root/to/downsampled/data \
        --full_res_label_root /root/to/raw/cityscapes \
        --save_predictions full
```
It's important to notice that this script also needs the path to the original CityScapes images. The original image size is used for evaluation and this is parse from the original images, as well as the ground truth labels.

Additionally, there is a little tool to simply run the network on arbitrary images:
```
python predict.py --experiment_root /root/to/store/experiment_data \
        --image_glob /some/image/pattern*.jpg \
        --result_directory /path/to/store/images \
        --rescale_w 512
        --rescale_h 256
```

## Software requirements
This code was tested with both TensorFlow 1.4 and 1.8. The latter, using up to date CUDA and cudnn versions runs significantly faster (about 1.4 on a Titan X Pascal). It was only tested with Python 3.5/3.6. Some of the code defenitely doesn't work like expected with Python 2.7. Other packages aren't critical as long as they are up-to-date enough to run with the used TensorFlow version.

## ROS nodes
There is a very thin wrapper around a trained frozen network. Please check the corresponding [README](https://github.com/VisualComputingInstitute/PARIS-sem-seg/blob/master/ros_nodes/ROS_NODES_README.md). This of course runs on Python 2.7, but there are no guarantees for the rest of the code.

## Design space
A list of things to try eventually to see how the performance and speed changes.

* Convolution type
    * normal conv
    * depthwise seperable
    * bottle neck resblocks.
* depth multiplier
* Resblock count
* Pooling counts
* PSP net style in bottle neck.


## Main Todos
* Add an input loader and augmentation visualization script.
* Add parametrization to the network (which convs, which blocks etc. as seen above.)
* Add an E-net, or a mobile net, this might also include loading pretrained weights.
* Zoom augmentation?
