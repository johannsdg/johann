# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import time
from typing import TYPE_CHECKING, Any, Dict

import celery.exceptions
from marshmallow import EXCLUDE

from johann.host import Host, HostSchema
from johann.host_control_util import get_host_control_class
from johann.shared.config import JohannConfig, celery_app
from johann.shared.logger import JohannLogger
from johann.util import get_codehash

if TYPE_CHECKING:
    from celery import Task

    from johann.host_control import HostControl


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


@celery_app.task(bind=True, autoretry_for=[Exception], retry_backoff=3, max_retries=2)
def tune_host(self: "Task", target_host_dict: Dict[str, Any] = None) -> None:
    score_name = player_name = "_"

    codehash = get_codehash()
    host_copy: "Host" = HostSchema().load(target_host_dict, unknown=EXCLUDE)

    prefix = self.request.shadow
    if host_copy.name == config.CONDUCTOR_LOCAL_HOST_NAME:
        logger.info(f"{prefix}: unnecessary to tune {host_copy.name}")
        return
    elif host_copy.control_method not in config.HOST_CONTROL_CLASS_NAMES:
        msg = f"{prefix}: unrecognized control method '{host_copy.control_method}'"
        self.max_retries = 0
        raise Exception(msg)
    else:
        host_control_class, msg = get_host_control_class(host_copy.control_method)
        if not host_control_class:
            msg = f"{prefix}: {msg}"
            self.max_retries = 0
            raise Exception(msg)
        else:
            host_controller: "HostControl" = host_control_class(host_copy)

        logger.debug(
            f"{prefix}: tuning host '{host_copy.name}', which has"
            f" parameters:\n{host_copy}"
        )
        logger.debug(host_copy)

    # check for Johann on actual host
    do_johann_install = False
    do_johann_update = False
    blank_args = ()
    try:
        sig = host_copy.get_task_signature(
            score_name,
            player_name,
            "tune_orchestra.remote_codehash.pre",
            config.REMOTE_CODEHASH_FUNC,
            0,
            *blank_args,
        )
        remote_codehash = sig.apply_async().get(10, disable_sync_subtasks=False)

        logger.debug(f"{host_copy.name}: received codehash '{remote_codehash}'")
        if remote_codehash != codehash:
            logger.info(f"{host_copy.name}: code hash mismatch")
            do_johann_update = True
    except celery.exceptions.TimeoutError:
        logger.info(f"{host_copy.name}: timed out waiting for remote codehash")
        logger.info(
            f"{host_copy.name}: assuming Johann missing or not running (will perform"
            " install)"
        )
        if config.HOST_AUTO_INSTALL:
            do_johann_install = True

    # install/update latest Johann to player
    if not (do_johann_install or do_johann_update):
        logger.debug(f"{host_copy.name} does not require update or install")
        return

    if host_copy.is_playing():
        raise Exception(
            f"{host_copy.name} is currently playing a score and cannot update Johann"
        )

    success, error_message = host_controller.push_johann(update_only=do_johann_update)

    if not success:
        raise Exception(error_message.strip())

    logger.debug(f"{host_copy.name}: Giving Celery some time to start up")
    time.sleep(15)

    # validate Johann install
    try:
        sig = host_copy.get_task_signature(
            score_name,
            player_name,
            "tune_orchestra.remote_codehash.post",
            config.REMOTE_CODEHASH_FUNC,
            0,
            *blank_args,
        )
        logger.debug(f"{host_copy.name}: requesting post-push codehash")
        remote_codehash = sig.apply_async().get(10, disable_sync_subtasks=False)
        logger.debug(
            f"{host_copy.name}: received post-push codehash '{remote_codehash}'"
        )

        if remote_codehash != codehash:
            logger.debug(f"{codehash} vs {remote_codehash}")
            msg = f"{host_copy.name}: push failed; code hash mismatch"
            logger.warning(msg)
            raise Exception(msg)
    except celery.exceptions.TimeoutError:
        msg = f"{host_copy.name}: push failed; timed out waiting for remote codehash"
        logger.warning(msg)
        raise Exception(msg)

    logger.info(f"{host_copy.name} successfully tuned")
    return
