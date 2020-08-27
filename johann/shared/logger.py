# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
from logging import Logger

import logzero
from pydantic import BaseModel, ByteSize

from johann.shared.config import JohannConfig


class LoggerModel(BaseModel):
    config: JohannConfig = JohannConfig.get_config()
    name: str
    file: str = config.LOG_FILE
    level: int = config.LOG_LEVEL
    max_bytes: ByteSize = config.LOG_MAX_BYTES
    backup_count: int = config.LOG_BACKUP_COUNT
    file_level: int = config.LOG_FILE_LEVEL
    format: str = config.LOG_FORMAT


class JohannLogger:
    def __init__(self, name, **kwargs):
        self.model = LoggerModel(name=name, **kwargs)
        self.logger: Logger = logzero.setup_logger(
            name=self.model.name,
            logfile=self.model.file,
            level=self.model.level,
            maxBytes=self.model.max_bytes,
            backupCount=self.model.backup_count,
            fileLoglevel=self.model.file_level,
            formatter=logzero.LogFormatter(fmt=self.model.format),
        )
