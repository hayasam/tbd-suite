# Lint as: python3
# Copyright 2019 The TensorFlow Authors. All Rights Reserved.
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
"""Dataclasses for optimization configs.

This file define the dataclass for optimization configs (OptimizationConfig).
It also has two helper functions get_optimizer_config, and get_lr_config from
an OptimizationConfig class.
"""
from typing import Optional

import dataclasses

from modeling.hyperparams import base_config
from modeling.hyperparams import oneof
from modeling.optimization.configs import learning_rate_config as lr_cfg
from modeling.optimization.configs import optimizer_config as opt_cfg


@dataclasses.dataclass
class OptimizerConfig(oneof.OneOfConfig):
  """Configuration for optimizer.

  Attributes:
    type: 'str', type of optimizer to be used, on the of fields below.
    sgd: sgd optimizer config.
    adam: adam optimizer config.
    adamw: adam with weight decay.
    lamb: lamb optimizer.
    rmsprop: rmsprop optimizer.
  """
  type: Optional[str] = None
  sgd: opt_cfg.SGDConfig = opt_cfg.SGDConfig()
  adam: opt_cfg.AdamConfig = opt_cfg.AdamConfig()
  adamw: opt_cfg.AdamWeightDecayConfig = opt_cfg.AdamWeightDecayConfig()
  lamb: opt_cfg.LAMBConfig = opt_cfg.LAMBConfig()
  rmsprop: opt_cfg.RMSPropConfig = opt_cfg.RMSPropConfig()


@dataclasses.dataclass
class LrConfig(oneof.OneOfConfig):
  """Configuration for lr schedule.

  Attributes:
    type: 'str', type of lr schedule to be used, on the of fields below.
    stepwise: stepwise learning rate config.
    exponential: exponential learning rate config.
    polynomial: polynomial learning rate config.
    cosine: cosine learning rate config.
  """
  type: Optional[str] = None
  stepwise: lr_cfg.StepwiseLrConfig = lr_cfg.StepwiseLrConfig()
  exponential: lr_cfg.ExponentialLrConfig = lr_cfg.ExponentialLrConfig()
  polynomial: lr_cfg.PolynomialLrConfig = lr_cfg.PolynomialLrConfig()
  cosine: lr_cfg.CosineLrConfig = lr_cfg.CosineLrConfig()


@dataclasses.dataclass
class WarmupConfig(oneof.OneOfConfig):
  """Configuration for lr schedule.

  Attributes:
    type: 'str', type of warmup schedule to be used, on the of fields below.
    linear: linear warmup config.
    polynomial: polynomial warmup config.
  """
  type: Optional[str] = None
  linear: lr_cfg.LinearWarmupConfig = lr_cfg.LinearWarmupConfig()
  polynomial: lr_cfg.PolynomialWarmupConfig = lr_cfg.PolynomialWarmupConfig()


@dataclasses.dataclass
class OptimizationConfig(base_config.Config):
  """Configuration for optimizer and learning rate schedule.

  Attributes:
    optimizer: optimizer oneof config.
    learning_rate: learning rate oneof config.
    warmup: warmup oneof config.
  """
  optimizer: OptimizerConfig = OptimizerConfig()
  learning_rate: LrConfig = LrConfig()
  warmup: WarmupConfig = WarmupConfig()
