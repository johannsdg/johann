# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
from johann.shared.config import JohannConfig

config = JohannConfig.get_config()
broker_url = config.REDIS_URL
result_backend = config.REDIS_URL
task_track_started = True
