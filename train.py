#!/usr/bin/env python3
from argparse import ArgumentParser
from datetime import timedelta
from importlib import import_module
import logging.config
import os
from signal import SIGINT, SIGTERM
import sys
import time

import json
import numpy as np
import tensorflow as tf
from tensorflow.contrib import slim

import output_losses
from networks import NETWORK_CHOICES
import tf_utils
import utils


parser = ArgumentParser(description='Train a Semantic Segmentation network.')

parser.add_argument(
    '--experiment_root', required=True, type=utils.writeable_directory,
    help='Location used to store checkpoints and dumped data.')

parser.add_argument(
    '--train_set', type=str,
    help='Path to the train_set csv file.')

parser.add_argument(
    '--dataset_root', type=utils.readable_directory,
    help='Path that will be pre-pended to the filenames in the train_set csv.')

parser.add_argument(
    '--dataset_config', type=str,
    help='Path to the json file containing the dataset config.')

parser.add_argument(
    '--resume', action='store_true', default=False,
    help='When this flag is provided, all other arguments apart from the '
         'experiment_root are ignored and a previously saved set of arguments '
         'is loaded.')

parser.add_argument(
    '--checkpoint_frequency', default=2000, type=utils.nonnegative_int,
    help='After how many iterations a checkpoint is stored. Set this to 0 to '
         'disable intermediate storing. This will result in only one final '
         'checkpoint.')

parser.add_argument(
    '--learning_rate', default=1e-3, type=utils.positive_float,
    help='The initial value of the learning-rate, before the decay kicks in.')

parser.add_argument(
    '--train_iterations', default=70000, type=utils.positive_int,
    help='Number of training iterations.')

parser.add_argument(
    '--decay_start_iteration', default=40000, type=int,
    help='At which iteration the learning-rate decay should kick-in.'
         'Set to -1 to disable decay completely.')

parser.add_argument(
    '--decay_multiplier', default=1e-3, type=float,
    help='How much the exponential decay, should reduce the learning rate.')

parser.add_argument(
    '--batch_size', default=5, type=utils.positive_int,
    help='The batch size used during training.')

parser.add_argument(
    '--loading_threads', default=8, type=utils.positive_int,
    help='Number of threads used for parallel loading.')

parser.add_argument(
    '--model_type', default='res_net_style', choices=NETWORK_CHOICES,
    help='Type of the model to use.')

parser.add_argument(
    '--model_params', default=None, type=str,
    help='Comma separated list of model parameters and values. Valid '
         'parameters differ from model to model.')

parser.add_argument(
    '--loss_type', default='cross_entropy_loss', choices=output_losses.LOSS_CHOICES,
    help='Loss used to train the network.')

parser.add_argument(
    '--flip_augment', action='store_true', default=False,
    help='When provided, random flip augmentation is used during training.')

parser.add_argument(
    '--gamma_augment', action='store_true', default=False,
    help='When provided, random gamma augmentation is used during training.')

parser.add_argument(
    '--crop_augment', default=0, type=utils.nonnegative_int,
    help='When not 0, a randomly located crop is taken from the input images, '
         'so that the removed border has the width and height as specified by '
         'this value. This clashes with `fixed_crop_augment_width` and '
         '`fixed_crop_augment_height`.')

parser.add_argument(
    '--fixed_crop_augment_height', default=0, type=utils.nonnegative_int,
    help='Perform a crop augmentation where a crop of fixed height and width is'
         ' taken from the input images with the provided height. If this is used'
         ' fixed_crop_augment_height also needs to be specified. This clashes '
         'with `crop_augment`.')

parser.add_argument(
    '--fixed_crop_augment_width', default=0, type=utils.nonnegative_int,
    help='Perform a crop augmentation where a crop of fixed height and width is'
         ' taken from the input images with the provided width. If this is used'
         ' fixed_crop_augment_height also needs to be specified. This clashes '
         'with `crop_augment`.')


# TODO(pandoro): loss parameters


def main():
    args = parser.parse_args()

    # We store all arguments in a json file. This has two advantages:
    # 1. We can always get back and see what exactly that experiment was
    # 2. We can resume an experiment as-is without needing to remember all flags.
    args_file = os.path.join(args.experiment_root, 'args.json')
    if args.resume:
        if not os.path.isfile(args_file):
            raise IOError('`args.json` not found in {}'.format(args_file))

        print('Loading args from {}.'.format(args_file))
        with open(args_file, 'r') as f:
            args_resumed = json.load(f)
        args_resumed['resume'] = True  # This would be overwritten.

        # When resuming, we not only want to populate the args object with the
        # values from the file, but we also want to check for some possible
        # conflicts between loaded and given arguments.
        for key, value in args.__dict__.items():
            if key in args_resumed:
                resumed_value = args_resumed[key]
                if resumed_value != value:
                    print('Warning: For the argument `{}` we are using the'
                          ' loaded value `{}`. The provided value was `{}`'
                          '.'.format(key, resumed_value, value))
                    args.__dict__[key] = resumed_value
            else:
                print('Warning: A new argument was added since the last run:'
                      ' `{}`. Using the new value: `{}`.'.format(key, value))

    else:
        # Make sure the required arguments are provided:
        # train_set, dataset_root, dataset_config
        if not args.train_set:
            parser.print_help()
            print('You did not specify the `train_set` argument!')
            exit(1)
        if not args.dataset_root:
            parser.print_help()
            print('You did not specify the required `dataset_root` argument!')
            exit(1)
        if not args.dataset_config:
            parser.print_help()
            print('You did not specify the required `dataset_config` argument!')
            exit(1)

        # If the experiment directory exists already, we bail in fear.
        if os.path.exists(args.experiment_root):
            if os.listdir(args.experiment_root):
                print('The directory {} already exists and is not empty.'
                      ' If you want to resume training, append --resume to'
                      ' your call.'.format(args.experiment_root))
                exit(1)
        else:
            os.makedirs(args.experiment_root)

        # Parse the model parameters. This could be a bit cleaner in the future,
        # but it will do for now.
        if args.model_params is not None:
            model_params = args.model_params.split(',')
            if len(model_params) % 2 != 0:
                raise ValueError('`model_params` has to be a comma separated '
                                 'list of even length.')
            it = iter(model_params)
            args.model_params = {p: int(v) for p, v in zip(it,it)}
        else:
            args.model_params = {}

        # Check some parameter clashes.
        if args.crop_augment > 0 and (args.fixed_crop_augment_width > 0 or
                args.fixed_crop_augment_height > 0):
            print('You cannot specified the use of both types of crop '
                  'augmentations. Either use the `crop_augment` argument to '
                  'remove a fixed amount of pixel from the borders, or use the '
                  '`fixed_crop_augment_height` arguments to provide a fixed '
                  'size window that will be cropped from the input images.')
            exit(1)
        if ((args.fixed_crop_augment_height > 0) !=
                (args.fixed_crop_augment_width > 0)):
            print('You need to specify both the `fixed_crop_augment_width` and '
                  '`fixed_crop_augment_height` arguments for a valid '
                  'augmentation.')
            exit(1)

        # Store the passed arguments for later resuming and grepping in a nice
        # and readable format.
        with open(args_file, 'w') as f:
            json.dump(vars(args), f, ensure_ascii=False, indent=2, sort_keys=True)

    log_file = os.path.join(args.experiment_root, 'train')
    logging.config.dictConfig(utils.get_logging_dict(log_file))
    log = logging.getLogger('train')

    # Also show all parameter values at the start, for ease of reading logs.
    log.info('Training using the following parameters:')
    for key, value in sorted(vars(args).items()):
        log.info('{}: {}'.format(key, value))

    # Load the config for the dataset.
    with open(args.dataset_config, 'r') as f:
        dataset_config = json.load(f)
    log.info('Training based on a `{}` configuration.'.format(
        dataset_config['dataset_name']))

    # Load the data from the CSV file.
    image_files, label_files = utils.load_dataset(
        args.train_set, args.dataset_root)

    # Setup a tf.Dataset where one "epoch" loops over all images.
    # images are shuffled after every epoch and continue indefinitely.
    images = tf.data.Dataset.from_tensor_slices(image_files)
    labels = tf.data.Dataset.from_tensor_slices(label_files)
    dataset = tf.data.Dataset.zip((images, labels))
    dataset = dataset.shuffle(len(image_files))

    dataset = dataset.repeat(None)  # Repeat forever.

    # Convert filenames to actual image and label id tensors.
    dataset = dataset.map(
        lambda x,y: tf_utils.string_tuple_to_image_pair(
            x, y, dataset_config.get('original_to_train_mapping', None)),
        num_parallel_calls=args.loading_threads)

    # Possible augmentations
    if args.flip_augment:
        dataset = dataset.map(tf_utils.flip_augment)
    if args.gamma_augment:
        dataset = dataset.map(tf_utils.gamma_augment)
    if args.crop_augment > 0:
        dataset = dataset.map(
            lambda x, y: tf_utils.crop_augment(
                x, y, args.crop_augment, args.crop_augment))
    if args.fixed_crop_augment_width > 0 and args.fixed_crop_augment_height > 0:
        dataset = dataset.map(
            lambda x, y: tf_utils.fixed_crop_augment(
                x, y, args.fixed_crop_augment_height,
                args.fixed_crop_augment_width))

    # Re scale the input images
    dataset = dataset.map(lambda x, y: ((x - 128.0) / 128.0, y))

    # Group it into batches.
    dataset = dataset.batch(args.batch_size)

    # Overlap producing and consuming for parallelism.
    dataset = dataset.prefetch(1)

    # Since we repeat the data infinitely, we only need a one-shot iterator.
    image_batch, label_batch = dataset.make_one_shot_iterator().get_next()

    model = import_module('networks.' + args.model_type)

    # Feed the image through a model.
    with tf.name_scope('model'):
        net = model.network(image_batch, is_training=True, **args.model_params)
        logits = slim.conv2d(net, len(dataset_config['class_names']),
            [3,3], scope='output_conv', activation_fn=None,
            weights_initializer=slim.variance_scaling_initializer(),
            biases_initializer=tf.zeros_initializer())

    # Create the loss, for now we use a simple cross entropy loss.
    with tf.name_scope('loss'):
        loss_function = getattr(output_losses, args.loss_type)
        losses = loss_function(
            logits, label_batch, void=dataset_config['void_label'])

    # Count the total batch loss.
    loss_mean = tf.reduce_mean(losses)

    # Some logging for tensorboard.
    tf.summary.histogram('loss_distribution', losses)
    tf.summary.scalar('loss', loss_mean)

    # Define the optimizer and the learning-rate schedule.
    # Unfortunately, we get NaNs if we don't handle no-decay separately.
    global_step = tf.Variable(0, name='global_step', trainable=False)
    if 0 <= args.decay_start_iteration < args.train_iterations:
        learning_rate = tf.train.exponential_decay(
            args.learning_rate,
            tf.maximum(0, global_step - args.decay_start_iteration),
            args.train_iterations - args.decay_start_iteration,
            args.decay_multiplier)
    else:
        learning_rate = args.learning_rate
    tf.summary.scalar('learning_rate', learning_rate)
    optimizer = tf.train.AdamOptimizer(learning_rate)

    # Update_ops are used to update batchnorm stats.
    with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS)):
        train_op = optimizer.minimize(loss_mean, global_step=global_step)

    # Define a saver for the complete model.
    checkpoint_saver = tf.train.Saver(max_to_keep=0)

    with tf.Session() as sess:
        if args.resume:
            # In case we're resuming, simply load the full checkpoint to init.
            last_checkpoint = tf.train.latest_checkpoint(args.experiment_root)
            log.info('Restoring from checkpoint: {}'.format(last_checkpoint))
            checkpoint_saver.restore(sess, last_checkpoint)
        else:
            # Initialize all variables
            sess.run(tf.global_variables_initializer())

            # We also store this initialization as a checkpoint, such that we
            # could run exactly reproduceable experiments.
            checkpoint_saver.save(sess, os.path.join(
                args.experiment_root, 'checkpoint'), global_step=0)

        merged_summary = tf.summary.merge_all()
        summary_writer = tf.summary.FileWriter(args.experiment_root, sess.graph)

        start_step = sess.run(global_step)
        log.info('Starting training from iteration {}.'.format(start_step))

        # Finally, here comes the main-loop. This `Uninterrupt` is a handy
        # utility such that an iteration still finishes on Ctrl+C and we can
        # stop the training cleanly.
        with utils.Uninterrupt(sigs=[SIGINT, SIGTERM], verbose=True) as u:
            for i in range(start_step, args.train_iterations):

                # Compute gradients, update weights, store logs!
                start_time = time.time()
                _, summary, step = sess.run(
                    [train_op, merged_summary, global_step])
                elapsed_time = time.time() - start_time

                # Compute the iteration speed and add it to the summary.
                # We did observe some weird spikes that we couldn't track down.
                summary2 = tf.Summary()
                summary2.value.add(tag='secs_per_iter', simple_value=elapsed_time)
                summary_writer.add_summary(summary2, step)
                summary_writer.add_summary(summary, step)

                # Save a checkpoint of training every so often.
                if (args.checkpoint_frequency > 0 and
                        step % args.checkpoint_frequency == 0):
                    checkpoint_saver.save(sess, os.path.join(
                        args.experiment_root, 'checkpoint'), global_step=step)

                # Stop the main-loop at the end of the step, if requested.
                if u.interrupted:
                    log.info('Interrupted on request!')
                    break

        # Store one final checkpoint. This might be redundant, but it is crucial
        # in case intermediate storing was disabled and it saves a checkpoint
        # when the process was interrupted.
        checkpoint_saver.save(sess, os.path.join(
            args.experiment_root, 'checkpoint'), global_step=step)


if __name__ == '__main__':
    main()
