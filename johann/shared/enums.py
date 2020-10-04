# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
from enum import Enum


class StrEnum(str, Enum):
    pass


class TaskState(StrEnum):
    # shared with State
    FAILURE = "FAILURE"
    SUCCESS = "SUCCESS"
    PENDING = "PENDING"
    STARTED = "STARTED"
    RETRY = "RETRY"
    PROGRESS = "PROGRESS"
    QUEUED = "QUEUED"
    DEFERRED = "DEFERRED"  # waiting on a dependency before being queued


class PmtrVariant(StrEnum):
    NONE = "NONE"
    DEVUDP = "DEVUDP"  # NOTE: requires bash (e.g., won't work on vanilla Alpine)


class HostOS(StrEnum):
    LINUX = "LINUX"
    WINDOWS = "WINDOWS"
    # DARWIN = 'DARWIN'
