#!/usr/bin/env python
import sys
import numpy as np
import tensorflow as tf
from input_velodyne import *

def batch_norm(inputs, is_training, decay=0.9, eps=1e-5):
    """Batch Normalization

       Args:
           inputs: input data(Batch size) from last layer
           is_training: when you test, please set is_training "None"
       Returns:
           output for next layer
    """
    gamma = tf.Variable(tf.ones(inputs.get_shape()[1:]), name="gamma")
    beta = tf.Variable(tf.zeros(inputs.get_shape()[1:]), name="beta")
    pop_mean = tf.Variable(tf.zeros(inputs.get_shape()[1:]), trainable=False, name="pop_mean")
    pop_var = tf.Variable(tf.ones(inputs.get_shape()[1:]), trainable=False, name="pop_var")

    if is_training != None:
        batch_mean, batch_var = tf.nn.moments(inputs, [0])
        train_mean = tf.assign(pop_mean, pop_mean * decay + batch_mean*(1 - decay))
        train_var = tf.assign(pop_var, pop_var * decay + batch_var * (1 - decay))
        with tf.control_dependencies([train_mean, train_var]):
            return tf.nn.batch_normalization(inputs, batch_mean, batch_var, beta, gamma, eps)
    else:
        return tf.nn.batch_normalization(inputs, pop_mean, pop_var, beta, gamma, eps)

def convBNLayer(input_layer, use_batchnorm, is_training, input_dim, output_dim, \
                kernel_size, stride, activation=tf.nn.relu, padding="SAME", name="", atrous=False, rate=1):
    with tf.variable_scope("convBN" + name):
        w = tf.get_variable("weights", \
            shape=[kernel_size, kernel_size, input_dim, output_dim], initializer=tf.contrib.layers.xavier_initializer())

        if atrous:
            conv = tf.nn.atrous_conv2d(input_layer, w, rate, padding="SAME")
        else:
            conv = tf.nn.conv2d(input_layer, w, strides=[1, stride, stride, 1], padding=padding)

        if use_batchnorm:
            bn = batch_norm(conv, is_training)
            if activation != None:
                return activation(conv, name="activation")
            return bn

        b = tf.get_variable("bias", \
            shape=[output_dim], initializer=tf.constant_initializer(0.0))
        bias = tf.nn.bias_add(conv, b)
        if activation != None:
            return activation(bias, name="activation")
        return bias

def maxpool2d(x, kernel=2, stride=1, name="", padding="SAME"):
    """define max pooling layer"""
    with tf.variable_scope("pool" + name):
        return tf.nn.max_pool(
            x,
            ksize = [1, kernel, kernel, 1],
            strides = [1, stride, stride, 1],
            padding=padding)

def conv3DLayer(input_layer, input_dim, output_dim, height, width, length, stride, activation=tf.nn.relu, padding="SAME", name=""):
    #[batch, 32, 32, 32, channel]
    with tf.variable_scope("conv3D" + name):
        kernel = tf.get_variable("weights", shape=[length, height, width, input_dim, output_dim], \
            dtype=tf.float32, initializer=tf.truncated_normal_initializer(stddev=0.1))
        b = tf.get_variable("bias", shape=[output_dim], dtype=tf.float32, initializer=tf.constant_initializer(0.0))
        conv = tf.nn.conv3d(input_layer, kernel, stride, padding=padding)
        bias = tf.nn.bias_add(conv, b)
        if activation:
            bias = activation(bias, name="activation")
    return bias

def conv3D_to_output(input_layer, input_dim, output_dim, height, width, length, stride, activation=tf.nn.relu, padding="SAME", name=""):
    #[batch, 32, 32, 32, channel]
    with tf.variable_scope("conv3D" + name):
        kernel = tf.get_variable("weights", shape=[length, height, width, input_dim, output_dim], \
            dtype=tf.float32, initializer=tf.truncated_normal_initializer(stddev=0.1))
        conv = tf.nn.conv3d(input_layer, kernel, stride, padding=padding)
    return conv

class BNBLayer(object):
    def __init__(self):
        pass

    def build_graph(self, voxel, activation=tf.nn.relu):
        self.layer1 = conv3DLayer(voxel, 1, 10, 5, 5, 5, [1, 2, 2, 2, 1], name="layer1", activation=activation)
        self.layer2 = conv3DLayer(self.layer1, 10, 20, 5, 5, 5, [1, 2, 2, 2, 1], name="layer2", activation=activation)

        self.objectness = conv3D_to_output(self.layer2, 20, 2, 1, 1, 1, [1, 1, 1, 1, 1], name="objectness", activation=None)
        self.cordinate = conv3D_to_output(self.layer2, 20, 24, 3, 3, 3, [1, 1, 1, 1, 1], name="cordinate", activation=None)

def ssd_model(sess, voxel, voxel_shape=(300, 300, 300),activation=tf.nn.relu):
    voxel = tf.placeholder(tf.float32, [None, voxel_shape[1], voxel_shape[2], voxel_shape[3], 1])
    with tf.variable_scope("3D_CNN_model") as scope:
        bnb_model = BNBLayer()
        bnb_model.build_graph(voxel, activation=activation)

    initialized_var = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope="3D_CNN_model")
    sess.run(tf.variables_initializer(initialized_var))
    return bnb_model

def loss_func(model, map_shape=(90, 100, 10)):
    # center = tf.placeholder(tf.float32, [batch_num, None, 3])
    g_map = tf.placeholder(tf.float32, [None, map_shape[0], map_shape[1], map_shape[2]])
    g_cord = tf.placeholder(tf.float32, model.cordinate.get_shape().as_list())
    object_loss = tf.multiply(g_map, model.objectness[:, :, :, :, 0])
    non_gmap = tf.subtract(tf.ones_like(g_map, dtype=tf.float32), g_map)
    nonobject_loss = tf.multiply(non_gmap, model.objectness[:, :, :, :, 1])
    sum_object_loss = tf.add(object_loss, nonobject_loss)
    bunbo = tf.add(tf.exp(model.objectness[:, :, :, :, 0]), tf.exp(model.objectness[:, :, :, :, 1]))
    obj_loss = tf.reduce_sum(tf.div(sum_object_loss, bunbo))

    # g_cord   [batch, num, 24]
    # cord_loss  [batch, num, 24]
    cord_diff = tf.multiply(g_map, tf.reduce_sum(tf.square(tf.subtract(model.cordinate, g_cord)), 4))
    cord_loss = tf.reduce_sum(cord_diff)
    return tf.add(obj_loss, cord_loss)

def create_optimizer(all_loss):
    opt = tf.train.AdamOptimizer(0.01)
    optimizer = opt.minimize(all_loss)
    return optimizer

def process(velodyne_path, label_path=None, calib_path=None, resolution=0.2, dataformat="pcd", label_type="txt", is_velo_cam=False):
    p = []
    pc = None
    bounding_boxes = None
    places = None
    rotates = None
    size = None
    proj_velo = None

    if dataformat == "bin":
        pc = load_pc_from_bin(velodyne_path)
    elif dataformat == "pcd":
        pc = load_pc_from_pcd(velodyne_path)

    if calib_path:
        calib = read_calib_file(calib_path)
        proj_velo = proj_to_velo(calib)[:, :3]

    if label_path:
        places, rotates, size = read_labels(label_path, label_type, calib_path=calib_path, is_velo_cam=is_velo_cam, proj_velo=proj_velo)

    corners = get_boxcorners(places, rotates, size)
    filter_car_data(corners)
    pc = filter_camera_angle(pc)

    voxel =  raw_to_voxel(pc, resolution=resolution)
    center_sphere = center_to_sphere(places, size, resolution=resolution)
    corner_label = corner_to_train(corners, center_sphere, resolution=resolution)
    g_map = create_objectness_label(center_sphere, resolution=resolution)
    g_cord = corner_label.reshape(corner_label.shape[0], -1)

    voxel = voxel.reshape(1, voxel.shape[0], voxel.shape[1], voxel.shape[2], 1)
    g_map = g_map[np.newaxis,:, :, :]
    g_cord = g_cord[np.newaxis, :]
    center_sphere = center_sphere[np.newaxis, :]

    with tf.Session() as sess:
        model = ssd_model(sess, voxel, voxel_shape=voxel.shape, activation=tf.nn.relu)
        print(vars(model))
        total_loss = loss_func(model)
        optimizer = create_optimizer(total_loss)

if __name__ == '__main__':
    pcd_path = "/home/katou01/download/training/velodyne/000700.bin"
    label_path = "/home/katou01/download/training/label_2/000700.txt"
    calib_path = "/home/katou01/download/training/calib/000700.txt"
    process(pcd_path, label_path, resolution=0.25, calib_path=calib_path, dataformat="bin", is_velo_cam=True)