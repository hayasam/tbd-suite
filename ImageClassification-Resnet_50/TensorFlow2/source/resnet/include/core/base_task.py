# Lint as: python3
# Copyright 2020 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Defines the base task abstraction."""
import abc
import functools
from typing import Any, Callable, Optional

import six
import tensorflow as tf

from modeling.hyperparams import config_definitions as cfg
from utils import registry


@six.add_metaclass(abc.ABCMeta)
class Task(tf.Module):
  """A single-replica view of training procedure.

  Tasks provide artifacts for training/evalution procedures, including
  loading/iterating over Datasets, initializing the model, calculating the loss
  and customized metrics with reduction.
  """

  # Special keys in train/validate step returned logs.
  loss = "loss"

  def __init__(self, params: cfg.TaskConfig):
    self._task_config = params

  @property
  def task_config(self) -> cfg.TaskConfig:
    return self._task_config

  def initialize(self, model: tf.keras.Model):
    """A callback function used as CheckpointManager's init_fn.

    This function will be called when no checkpoint found for the model.
    If there is a checkpoint, the checkpoint will be loaded and this function
    will not be called. You can use this callback function to load a pretrained
    checkpoint, saved under a directory other than the model_dir.

    Args:
      model: The keras.Model built or used by this task.
    """
    pass

  @abc.abstractmethod
  def build_model(self) -> tf.keras.Model:
    """Creates the model architecture.

    Returns:
      A model instance.
    """

  def compile_model(self,
                    model: tf.keras.Model,
                    optimizer: tf.keras.optimizers.Optimizer,
                    loss=None,
                    train_step: Optional[Callable[..., Any]] = None,
                    validation_step: Optional[Callable[..., Any]] = None,
                    **kwargs) -> tf.keras.Model:
    """Compiles the model with objects created by the task.

    The method should not be used in any customized training implementation.

    Args:
      model: a keras.Model.
      optimizer: the keras optimizer.
      loss: a callable/list of losses.
      train_step: optional train step function defined by the task.
      validation_step: optional validation_step step function defined by the
        task.
      **kwargs: other kwargs consumed by keras.Model compile().

    Returns:
      a compiled keras.Model.
    """
    if bool(loss is None) == bool(train_step is None):
      raise ValueError("`loss` and `train_step` should be exclusive to "
                       "each other.")
    model.compile(optimizer=optimizer, loss=loss, **kwargs)

    if train_step:
      model.train_step = functools.partial(
          train_step, model=model, optimizer=model.optimizer)
    if validation_step:
      model.test_step = functools.partial(validation_step, model=model)
    return model

  @abc.abstractmethod
  def build_inputs(self,
                   params: cfg.DataConfig,
                   input_context: Optional[tf.distribute.InputContext] = None):
    """Returns a dataset or a nested structure of dataset functions.

    Dataset functions define per-host datasets with the per-replica batch size.

    Args:
      params: hyperparams to create input pipelines.
      input_context: optional distribution input pipeline context.

    Returns:
      A nested structure of per-replica input functions.
    """

  def build_losses(self, labels, model_outputs, aux_losses=None) -> tf.Tensor:
    """Standard interface to compute losses.

    Args:
      labels: optional label tensors.
      model_outputs: a nested structure of output tensors.
      aux_losses: auxiliarly loss tensors, i.e. `losses` in keras.Model.

    Returns:
      The total loss tensor.
    """
    del model_outputs, labels

    if aux_losses is None:
      losses = [tf.constant(0.0, dtype=tf.float32)]
    else:
      losses = aux_losses
    total_loss = tf.add_n(losses)
    return total_loss

  def build_metrics(self, training: bool = True):
    """Gets streaming metrics for training/validation."""
    del training
    return []

  def process_metrics(self, metrics, labels, model_outputs):
    """Process and update metrics. Called when using custom training loop API.

    Args:
      metrics: a nested structure of metrics objects.
        The return of function self.build_metrics.
      labels: a tensor or a nested structure of tensors.
      model_outputs: a tensor or a nested structure of tensors.
        For example, output of the keras model built by self.build_model.
    """
    for metric in metrics:
      metric.update_state(labels, model_outputs)

  def process_compiled_metrics(self, compiled_metrics, labels, model_outputs):
    """Process and update compiled_metrics. call when using compile/fit API.

    Args:
      compiled_metrics: the compiled metrics (model.compiled_metrics).
      labels: a tensor or a nested structure of tensors.
      model_outputs: a tensor or a nested structure of tensors.
        For example, output of the keras model built by self.build_model.
    """
    compiled_metrics.update_state(labels, model_outputs)

  def train_step(self,
                 inputs,
                 model: tf.keras.Model,
                 optimizer: tf.keras.optimizers.Optimizer,
                 metrics=None):
    """Does forward and backward.

    Args:
      inputs: a dictionary of input tensors.
      model: the model, forward pass definition.
      optimizer: the optimizer for this training step.
      metrics: a nested structure of metrics objects.

    Returns:
      A dictionary of logs.
    """
    if isinstance(inputs, tuple) and len(inputs) == 2:
      features, labels = inputs
    else:
      features, labels = inputs, inputs
    with tf.GradientTape() as tape:
      outputs = model(features, training=True)
      # Computes per-replica loss.
      loss = self.build_losses(
          labels=labels, model_outputs=outputs, aux_losses=model.losses)
      # Scales loss as the default gradients allreduce performs sum inside the
      # optimizer.
      scaled_loss = loss / tf.distribute.get_strategy().num_replicas_in_sync

      # For mixed precision, when a LossScaleOptimizer is used, the loss is
      # scaled to avoid numeric underflow.
      if isinstance(optimizer,
                    tf.keras.mixed_precision.experimental.LossScaleOptimizer):
        scaled_loss = optimizer.get_scaled_loss(scaled_loss)

    tvars = model.trainable_variables
    grads = tape.gradient(scaled_loss, tvars)

    if isinstance(optimizer,
                  tf.keras.mixed_precision.experimental.LossScaleOptimizer):
      grads = optimizer.get_unscaled_gradients(grads)
    optimizer.apply_gradients(list(zip(grads, tvars)))
    logs = {self.loss: loss}
    if metrics:
      self.process_metrics(metrics, labels, outputs)
      logs.update({m.name: m.result() for m in metrics})
    elif model.compiled_metrics:
      self.process_compiled_metrics(model.compiled_metrics, labels, outputs)
      logs.update({m.name: m.result() for m in model.metrics})
    return logs

  def validation_step(self, inputs, model: tf.keras.Model, metrics=None):
    """Validatation step.

    Args:
      inputs: a dictionary of input tensors.
      model: the keras.Model.
      metrics: a nested structure of metrics objects.

    Returns:
      A dictionary of logs.
    """
    if isinstance(inputs, tuple) and len(inputs) == 2:
      features, labels = inputs
    else:
      features, labels = inputs, inputs
    outputs = self.inference_step(features, model)
    loss = self.build_losses(
        labels=labels, model_outputs=outputs, aux_losses=model.losses)
    logs = {self.loss: loss}
    if metrics:
      self.process_metrics(metrics, labels, outputs)
      logs.update({m.name: m.result() for m in metrics})
    elif model.compiled_metrics:
      self.process_compiled_metrics(model.compiled_metrics, labels, outputs)
      logs.update({m.name: m.result() for m in model.metrics})
    return logs

  def inference_step(self, inputs, model: tf.keras.Model):
    """Performs the forward step."""
    return model(inputs, training=False)


_REGISTERED_TASK_CLS = {}


# TODO(b/158268740): Move these outside the base class file.
# TODO(b/158741360): Add type annotations once pytype checks across modules.
def register_task_cls(task_config_cls):
  """Decorates a factory of Tasks for lookup by a subclass of TaskConfig.

  This decorator supports registration of tasks as follows:

  ```
  @dataclasses.dataclass
  class MyTaskConfig(TaskConfig):
    # Add fields here.
    pass

  @register_task_cls(MyTaskConfig)
  class MyTask(Task):
    # Inherits def __init__(self, task_config).
    pass

  my_task_config = MyTaskConfig()
  my_task = get_task(my_task_config)  # Returns MyTask(my_task_config).
  ```

  Besisdes a class itself, other callables that create a Task from a TaskConfig
  can be decorated by the result of this function, as long as there is at most
  one registration for each config class.

  Args:
    task_config_cls: a subclass of TaskConfig (*not* an instance of TaskConfig).
      Each task_config_cls can only be used for a single registration.

  Returns:
    A callable for use as class decorator that registers the decorated class
    for creation from an instance of task_config_cls.
  """
  return registry.register(_REGISTERED_TASK_CLS, task_config_cls)


# The user-visible get_task() is defined after classes have been registered.
# TODO(b/158741360): Add type annotations once pytype checks across modules.
def get_task_cls(task_config_cls):
  task_cls = registry.lookup(_REGISTERED_TASK_CLS, task_config_cls)
  return task_cls
