# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
from pydantic import _PathValueError


class PathNotSafeError(_PathValueError):
    code = "path.not_safe"
    msg_template = 'path "{path}" is not safe'


class JohannError(Exception):
    def __init__(self, msg):
        self.msg = msg
