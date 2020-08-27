# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import copy
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from celery.canvas import signature
from marshmallow import Schema
from marshmallow import ValidationError as MarshmallowValidationError
from marshmallow import fields, post_load, validates
from marshmallow_enum import EnumField

from johann.shared.config import JohannConfig, celery_app
from johann.shared.logger import JohannLogger
from johann.util import (
    HostOS,
    LaxStringField,
    NameField,
    PmtrVariant,
    TaskState,
    gudlog,
    safe_name,
)

if TYPE_CHECKING:
    from celery.canvas import Signature


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


class HostSchema(Schema):
    class Meta:
        ordered = True

    name = NameField(
        required=True, data_key="hostname"
    )  # hostname for network use (i.e. fqdn, hostname, or, if necessary, IP)
    control_name = fields.Str(
        allow_none=True, missing=None
    )  # i.e., container ID for DOCKER (optional)
    johann_id = fields.UUID(allow_none=True, missing=None)
    image = fields.Str(allow_none=True, missing=None)
    user = fields.Str(allow_none=True, missing=None)
    pwd_env = fields.Str(allow_none=True, missing=None)
    os = EnumField(HostOS, allow_none=True, missing=None)
    python_path = fields.Str(allow_none=True, missing=None)
    python_ver = LaxStringField(allow_none=True, missing=None)
    pmtr_variant = EnumField(PmtrVariant, allow_none=True, missing=None)
    control_method = fields.Str(allow_none=True, missing=None)
    pip_offline_install = fields.Boolean(allow_none=True, missing=None)

    tuning = fields.Boolean(dump_only=True)
    pending_create = fields.Boolean(dump_only=True)
    celery_task_ids = fields.List(
        fields.Str(), dump_only=True
    )  # note that finished tasks may be cleared from this list at any time
    last_checked_exists = fields.DateTime(dump_only=True)

    @validates("python_ver")
    def validate_python_ver(self, value):
        if value is not None and value not in config.SUPPORTED_PYTHON_VERSIONS:
            raise MarshmallowValidationError(f"Unsupported python version '{value}'")

    @validates("control_method")
    def validate_control_method(self, value):
        if value is not None and value not in config.HOST_CONTROL_CLASS_NAMES:
            raise MarshmallowValidationError(f"Unrecognized control method '{value}'")

    @post_load
    def make_host(self, data: Dict[str, Any], **kwargs) -> "Host":
        name = data["name"]
        control_name = data["control_name"]

        # validate name and control_name
        if name != safe_name(name):
            raise MarshmallowValidationError(
                f"Name '{name}' does not appear to be a valid hostname"
            )
        if control_name and control_name != safe_name(control_name):
            raise MarshmallowValidationError(
                f"Control name '{control_name}' does not appear to be valid"
            )

        # validate pmtr variant
        if data["pmtr_variant"]:
            try:
                PmtrVariant(data["pmtr_variant"])
            except ValueError:
                raise MarshmallowValidationError(
                    f"PMTR variant '{data['pmtr_variant']}' is invalid"
                )

        # validate control method
        if (
            data["control_method"]
            and data["control_method"] not in config.HOST_CONTROL_CLASS_NAMES
        ):
            raise MarshmallowValidationError(
                f"Control method '{data['control_method']}' is invalid"
            )

        return Host(**data)


class Host(object):
    def __init__(
        self,
        name,
        control_name,
        johann_id,
        image,
        user,
        pwd_env,
        os,
        python_path,
        python_ver,
        pmtr_variant,
        control_method,
        pip_offline_install,
    ) -> None:
        self.name: str = name
        self.control_name: Optional[str] = control_name
        self.johann_id: Optional[UUID] = johann_id
        self._image: Optional[str] = image
        self.user: Optional[str] = user
        self.pwd_env: Optional[str] = pwd_env
        self.os: Optional[HostOS] = None  # initialized below
        self.python_path: Optional[str] = python_path
        self.python_ver: Optional[str] = python_ver
        self.pmtr_variant: Optional[PmtrVariant] = None  # initialized below
        self.control_method: Optional[str] = None  # initialized below
        self.pip_offline_install: bool = pip_offline_install

        self.tuning: bool = False
        self.pending_create: bool = False
        self.celery_task_ids: List[
            str
        ] = []  # note that finished tasks may be cleared from this list at any time
        # only tasks that are currently running can be relied upon to be here
        self.last_confirmed_on: Optional[datetime] = None

        logger.debug(f"Creating Host object for '{name}'")

        if pmtr_variant:
            try:
                self.pmtr_variant = PmtrVariant(pmtr_variant)
            except ValueError:
                msg = f"{pmtr_variant} is not a valid PMTR variant; ignoring..."
                logger.warning(msg)
                self.pmtr_variant = None  # will try to match to image below

        if os:
            try:
                self.os = HostOS(os)
            except ValueError:
                msg = (
                    f"{os} is not a valid OS option for Johann at this time;"
                    " ignoring..."
                )
                logger.warning(msg)
                self.os = None  # will try to match to image below

        if (
            not self.user
            and self._image in config.HOST_IMAGE_PARAMS
            and "user" in config.HOST_IMAGE_PARAMS[self._image]
        ):
            self.match_user_to_image()
        if not self.pwd_env:
            self.match_pwd_env_to_image()
        if (
            not self.os
            and self._image in config.HOST_IMAGE_PARAMS
            and "os" in config.HOST_IMAGE_PARAMS[self._image]
        ):
            self.match_os_to_image()
        if not self.python_path:
            self.match_python_path_to_image()
        if not self.python_ver:
            self.match_python_ver_to_image()
        if not self.pmtr_variant:
            self.match_pmtr_variant_to_image()
        if not self.control_method:
            self.match_control_method_to_image()
        if not self.pip_offline_install:
            self.match_pip_offline_install_to_image()

    def get_image(self) -> Optional[str]:
        return self._image

    def set_image(self, image: Optional[str]) -> bool:
        if self._image == image:
            return True

        self._image = image
        ret = True

        ret &= self.match_user_to_image()
        ret &= self.match_pwd_env_to_image()
        ret &= self.match_os_to_image()
        ret &= self.match_python_path_to_image()
        ret &= self.match_pmtr_variant_to_image()
        ret &= self.match_control_method_to_image()
        ret &= self.match_pip_offline_install_to_image()

        return ret

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(hostname={self.name},"
            f"control_name={self.control_name},image={self._image},os={self.os},"
            f"control={self.control_method})"
        )

    def dump(self) -> Dict[str, Any]:
        selfcopy = copy.deepcopy(self)
        selfcopy.image = self._image
        return HostSchema().dump(selfcopy)

    def dumps(self) -> str:
        selfcopy = copy.deepcopy(self)
        selfcopy.image = self._image
        return HostSchema().dumps(selfcopy)

    def copy_from(self, host: "Host") -> Tuple[bool, Optional[str]]:
        if not isinstance(host, Host):
            return False, "not a valid Host object"

        if host.name != self.name:
            logger.warning(
                "copy_from() called with a host with a different name...was this"
                " intentional?"
            )

        self.name = host.name

        if host.get_image() != self._image:
            msg = f"updating image from {self._image} to {host.get_image()}"
            logger.debug(msg)
            self.set_image(host.get_image())

        if host.user != self.user:
            msg = f"updating user from {self.user} to {host.user}"
            logger.debug(msg)
            self.user = host.user

        if host.pwd_env != self.pwd_env:
            msg = f"updating pwd_env from {self.pwd_env} to {host.pwd_env}"
            logger.debug(msg)
            self.pwd_env = host.pwd_env

        if host.os != self.os:
            msg = f"updating os from {self.os} to {host.os}"
            logger.debug(msg)
            self.os = host.os

        if host.python_path != self.python_path:
            msg = f"updating python path from {self.python_path} to {host.python_path}"
            logger.debug(msg)
            self.python_path = host.python_path

        if host.python_ver != self.python_ver:
            msg = f"updating python version from {self.python_ver} to {host.python_ver}"
            logger.debug(msg)
            self.python_ver = host.python_ver

        if host.pmtr_variant != self.pmtr_variant:
            msg = (
                f"updating pmtr variant from {self.pmtr_variant.value} to"
                f" {host.pmtr_variant.value}"
            )
            logger.debug(msg)
            self.pmtr_variant = host.pmtr_variant

        if host.control_method != self.control_method:
            msg = (
                f"updating control method from {self.control_method} to"
                f" {host.control_method}"
            )
            logger.debug(msg)
            self.control_method = host.control_method

        if host.pip_offline_install != self.pip_offline_install:
            msg = (
                f"updating pip offline install from {self.pip_offline_install} to"
                f" {host.pip_offline_install}"
            )
            logger.debug(msg)
            self.pip_offline_install = host.pip_offline_install

        return True, None

    def match_user_to_image(self) -> bool:
        try:
            self.user = config.HOST_IMAGE_PARAMS[self._image]["user"]
            logger.debug(f"matched user '{self.user}' to image '{self._image}'")
            return True
        except KeyError:
            logger.info(
                f"{self.name}: unable to find matching user for image '{self._image}'"
                " in config; using default"
            )
            self.user = config.DEFAULT_PLAYER_USER
            return False

    def match_pwd_env_to_image(self) -> bool:
        try:
            self.pwd_env = config.HOST_IMAGE_PARAMS[self._image]["pwd_env"]
            logger.debug(f"matched pwd_env '{self.pwd_env}' to image '{self._image}'")
            return True
        except KeyError:
            logger.debug(
                f"{self.name}: unable to find matching pwd_env for image"
                f" '{self._image}' in config; using default"
            )
            self.pwd_env = config.DEFAULT_PLAYER_PWD_ENV
            return False

    def match_os_to_image(self) -> bool:
        try:
            image_value = config.HOST_IMAGE_PARAMS[self._image]["os"]
            try:
                self.os = HostOS(image_value)
                logger.debug(f"matched os '{self.os}' to image '{self._image}'")
                return True
            except ValueError:
                msg = (
                    f"{image_value} is not a valid OS option at this time; using"
                    " default"
                )
                logger.info(msg)
                self.os = HostOS(config.DEFAULT_PLAYER_OS)
                return False
        except KeyError:
            logger.info(
                f"{self.name}: unable to find matching os for image '{self._image}' in"
                " config; using default"
            )
            self.os = config.DEFAULT_PLAYER_OS
            return False

    def match_python_path_to_image(self) -> bool:
        try:
            self.python_path = config.HOST_IMAGE_PARAMS[self._image]["python_path"]
            logger.debug(
                f"matched python '{self.python_path}' to image '{self._image}'"
            )
            return True
        except KeyError:
            logger.info(
                f"{self.name}: unable to find matching python path for image"
                f" '{self._image}' in config; using default"
            )
            self.python_path = config.DEFAULT_PYTHON_PATH
            return False

    def match_python_ver_to_image(self) -> bool:
        try:
            self.python_ver = config.HOST_IMAGE_PARAMS[self._image]["python_ver"]
            logger.debug(
                f"matched python version '{self.python_ver}' to image '{self._image}'"
            )
            return True
        except KeyError:
            logger.debug(
                f"{self.name}: unable to find matching python version for image"
                f" '{self._image}' in config; will be discovered at runtime"
            )
            self.python_ver = None
            return False

    def match_pmtr_variant_to_image(self) -> bool:
        try:
            image_value = config.HOST_IMAGE_PARAMS[self._image]["pmtr_variant"]
            try:
                self.pmtr_variant = PmtrVariant(image_value)
                logger.debug(
                    f"matched pmtr variant '{self.pmtr_variant.value}' to image"
                    f" '{self._image}'"
                )
                return True
            except ValueError:
                msg = f"{image_value} is not a valid PMTR variant; using default"
                logger.warning(msg)
                self.pmtr_variant = PmtrVariant(config.DEFAULT_PMTR_VARIANT)
                return False
        except KeyError:
            logger.info(
                f"{self.name}: unable to find matching pmtr variant for image"
                f" '{self._image}' in config; using default"
            )
            self.pmtr_variant = PmtrVariant(config.DEFAULT_PMTR_VARIANT)
            return False

    def match_control_method_to_image(self) -> bool:
        try:
            image_value = config.HOST_IMAGE_PARAMS[self._image]["control_method"]
            if image_value not in config.HOST_CONTROL_CLASS_NAMES:
                msg = f"{image_value} is not a valid control method; using default"
                logger.warning(msg)
                self.control_method = config.DEFAULT_HOST_CONTROL
                return False
            else:
                self.control_method = image_value
                logger.debug(
                    f"matched control method '{self.control_method}' to image"
                    f" '{self._image}'"
                )
                return True
        except KeyError:
            logger.debug(
                f"{self.name}: unable to find matching control method for image"
                f" '{self._image}' in config; using default"
            )
            self.control_method = config.DEFAULT_HOST_CONTROL
            return False

    def match_pip_offline_install_to_image(self) -> bool:
        try:
            self.pip_offline_install = config.HOST_IMAGE_PARAMS[self._image][
                "pip_offline_install"
            ]
            logger.debug(
                f"matched pip offline install '{self.pip_offline_install}' to image"
                f" '{self._image}'"
            )
            return True
        except KeyError:
            logger.debug(
                f"{self.name}: unable to find matching pip offline install for image"
                f" '{self._image}' in config; using default"
            )
            self.pip_offline_install = config.DEFAULT_PIP_OFFLINE_INSTALL
            return False

    def is_playing(self) -> bool:
        self.clear_finished_celery_task_ids()
        if len(self.celery_task_ids) > 0:
            return True
        else:
            return False

    def clear_finished_celery_task_ids(self) -> None:
        to_remove = []
        for cti in self.celery_task_ids:
            ct = celery_app.AsyncResult(cti)
            if ct.state in [TaskState.SUCCESS, TaskState.FAILURE]:  # final states
                to_remove.append(ct)

        for cti in to_remove:
            self.celery_task_ids.remove(cti)

    def get_task_signature(
        self,
        score_name: str,
        player_name: str,
        measure_name: str,
        func: str,
        delay: int,
        *task_args,
        **task_kwargs,
    ) -> "Signature":
        if self.tuning:
            msg = (
                "host is tuning or pending tuning; strongly advise against running new"
                " tasks on it or they may be interrupted without warning or recovery"
            )
            logger.warning(gudlog(msg, score_name, player_name, None, self.name))
        description = f"{score_name}.{player_name}.{self.name}.{measure_name}"
        task_id = str(
            uuid4()
        )  # we need to know the task_id a priori for score.task_map
        sig_opts = {
            "queue": self.name,
            "shadow": description,
            "task_id": task_id,
            "countdown": delay,
        }
        sig = signature(func, args=task_args, kwargs=task_kwargs, options=sig_opts)
        msg = (
            f"task signature created for {func}{task_args} with"
            f" kwargs:\n{task_kwargs}\nand options:\n{sig_opts}"
        )
        logger.log(5, gudlog(msg, score_name, player_name, measure_name, self.name))

        return sig
