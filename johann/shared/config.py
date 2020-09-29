# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import logging
import os
import socket
from ipaddress import IPv4Interface
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional
from uuid import uuid4

import dotenv
from celery import Celery
from pydantic import (
    UUID4,
    AnyUrl,
    BaseSettings,
    ByteSize,
    DirectoryPath,
    Field,
    PositiveInt,
    ValidationError,
    conint,
    constr,
    validator,
)

if TYPE_CHECKING:
    from johann.host import Host
    from johann.score import Score


_ENV_FILE = os.getenv("JOHANN_ENV_FILE", ".env")


class JohannConfig(BaseSettings):
    __instance__: "JohannConfig" = None

    def __init__(__pydantic_self__):
        if JohannConfig.__instance__ is not None:
            raise Exception("You cannot create another SingletonSettings instance")
        JohannConfig.__instance__ = __pydantic_self__
        super().__init__()

    @staticmethod
    def get_config() -> "JohannConfig":
        if not JohannConfig.__instance__:
            JohannConfig()
        return JohannConfig.__instance__

    ENV_FILE: Path = _ENV_FILE
    SRC_ROOT: DirectoryPath = Path(__file__).parent.parent.absolute()
    PROJECT_ROOT: DirectoryPath = SRC_ROOT.parent
    TARBALL_PATH: Path = "/srv/johann"

    DEPLOY_PATH_POSIX: Path = "/opt/johann/johann"
    DEPLOY_PYTHONPATH_POSIX: Path = "/opt/johann"
    TEMP_PATH_POSIX: Path = "/tmp"

    SUPPORTED_PYTHON_VERSIONS: List[str] = Field(["3.6", "3.7"], const=True)

    CODEHASH: Optional[str] = Field(None, env=None)
    REMOTE_CODEHASH_FUNC: str = "johann.tasks_util.remote_codehash"
    JOHANN_ID: UUID4 = Field(default_factory=uuid4)
    JOHANN_MODE: constr(regex=r"^(conductor|player)$") = "player"  # noqa: F722

    JOHANN_HOST: IPv4Interface = "0.0.0.0"
    CONDUCTOR_PORT: conint(gt=0, le=65535) = 5000

    REDIS_HOST: str = "redis"
    REDIS_HOST_EXTERNAL: Optional[str] = None  # only needs be specified to Conductor
    REDIS_PORT: conint(gt=0, le=65535) = 6379
    REDIS_DB: PositiveInt = 1
    REDIS_URL: AnyUrl = (  # RedisDSN needs user
        f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    )
    SKIP_REDIS: bool = False

    PYPI_INDEX_PROTO: str = "http"
    PYPI_INDEX_HOST: str = "pypiserver"
    PYPI_INDEX_PORT: PositiveInt = 8080
    PYPI_INDEX_URL: str = f"{PYPI_INDEX_PROTO}://{PYPI_INDEX_HOST}:{PYPI_INDEX_PORT}"

    CREDS_FILE: Path = ".creds"

    @validator("CREDS_FILE")
    def load_creds_file(cls, v, values):
        try:
            if v.is_file():
                dotenv.load_dotenv(v)
        except Exception:
            raise ValidationError(f"Failed to load CREDS_FILE '{v}'")
        return v

    HOSTS_FILE: Optional[Path] = None
    HOST_CONFIRMED_ON_VALID_SECS: PositiveInt = 30
    ALLOW_EMPTY_PLAYER_HOSTS: bool = True
    SPECIAL_ARG_TYPES: List[str] = Field(["random", "stored"], const=True)

    DEBUG: bool = False
    TESTING: bool = False
    TRACE: bool = False

    CELERY_TASKS_MODULE: str = "johann.tasks_main"
    SKIP_CELERY: bool = False
    CELERY_DETACH: bool = False
    CELERY_USER: Optional[str] = None
    CELERY_QUEUE_ID: str = socket.gethostname()
    CELERY_WORKERS_MIN: int = 3
    CELERY_WORKERS_MAX: int = 10

    CONDUCTOR_ALLHOSTS_PLAYER_NAME: str = "conductor_allhosts"
    CONDUCTOR_LOCAL_HOST_NAME: str = "johann_conductor"
    PID_FILE: str = f"johann.{CELERY_QUEUE_ID}.pid"

    LOG_FILE: str = f"johann.{CELERY_QUEUE_ID}.log"
    LOG_FORMAT: str = (
        "%(color)s[%(levelname)1.1s %(asctime)s %(process)d"
        " %(module)s:%(lineno)d]%(end_color)s %(message)s"
    )
    LOG_LEVEL: int = -1
    LOG_MAX_BYTES: ByteSize = "5MiB"
    LOG_BACKUP_COUNT: int = 3
    LOG_FILE_LEVEL: int = -1

    @validator("LOG_LEVEL")
    def default_log_level(cls, v, values):
        if v >= 0:
            return v
        elif "DEBUG" in values and values["DEBUG"]:
            return logging.DEBUG
        else:
            return logging.INFO

    @validator("LOG_FILE_LEVEL")
    def default_file_log_level(cls, v, values):
        if v >= 0:
            return v
        elif "LOG_LEVEL" in values:
            return values["LOG_LEVEL"]
        else:
            return logging.INFO

    HOST_IMAGE_PARAMS: Dict = {
        "johann_player": {
            "user": None,
            "env_pwd": None,
            "os": "LINUX",
            "python_path": "/usr/local/bin/python3",
            "pmtr_variant": "DEVUDP",
            "control_method": "DOCKER",
            "pip_offline_install": False,
        },
    }
    INITIAL_HOSTS: Dict = {
        "blank_3.6_buster": {
            "image": None,
            "user": None,
            "python_path": "/usr/local/bin/python3",
            "pmtr_variant": "NONE",
            "control_method": "DOCKER",
            "pip_offline_install": False,
        },
        "blank_3.7_buster": {
            "image": None,
            "user": None,
            "python_path": "/usr/local/bin/python3",
            "pmtr_variant": "NONE",
            "control_method": "DOCKER",
            "pip_offline_install": False,
        },
        "johann_player_1": {"image": "johann_player"},
    }

    DEFAULT_PLAYER_IMAGE: Optional[str] = None
    DEFAULT_PLAYER_USER: Optional[str] = None
    DEFAULT_PLAYER_PWD_ENV: Optional[str] = None
    DEFAULT_PLAYER_OS: str = "LINUX"
    DEFAULT_PYTHON_PATH: Path = "/usr/local/bin/python3"
    DEFAULT_PMTR_VARIANT: str = "NONE"
    DEFAULT_HOST_CONTROL: str = "DOCKER"
    DEFAULT_PIP_OFFLINE_INSTALL: bool = False

    HOST_AUTO_INSTALL: bool = True
    PLAYER_HOSTS_DUMP_KEY: str = "hosts"
    PLUGINS_EXCLUDE: List[str] = []  # PLANNED: this implenentation is still WIP

    # keys should be all upper-case
    HOST_CONTROL_CLASS_NAMES: Dict[str, Optional[str]] = {
        "DOCKER": None
    }  # docker_host_control.DockerHostControl
    HOST_CONTROL_EXTERNAL_REDIS: Dict[str, bool] = {"DOCKER": False}

    CODEHASH_FILES: List[str] = [
        "celeryconfig.py",
        "shared/config.py",
        "host.py",  # run_shell_command, etc
        "host_control.py",  # run_cmd, etc
        "johann_main.py",
        "tasks*.py",
        "run_player_pmtr*.sh",
        "util.py",
        "requirements.txt",
    ]

    class Config(BaseSettings.Config):
        arbitrary_types_allowed = True
        case_sensitive = True
        env_file = _ENV_FILE
        validate_assignment = True


def get_settings() -> JohannConfig:
    return JohannConfig()


config = get_settings()
celery_app = Celery(config.CELERY_TASKS_MODULE)
celery_app.config_from_object("johann.celeryconfig")
scores: Dict[str, "Score"] = {}
workers = []
hosts: Dict[str, "Host"] = {}
active_plugins: List[str] = []
