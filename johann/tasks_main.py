# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.

import pprint
import subprocess
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from celery.signals import celeryd_init, task_failure, task_retry

import johann.util
from johann.shared.config import JohannConfig, celery_app
from johann.shared.logger import JohannLogger
from johann.tasks_util import RetryableException

try:
    import johann.conductor_tasks  # noqa: F401
except ImportError:
    pass

if TYPE_CHECKING:
    from logging import Logger
    from traceback import StackSummary

    from billiard.einfo import ExceptionInfo
    from celery import Task
    from celery.worker.request import Request


config: Optional[JohannConfig] = None
logger: Optional["Logger"] = None


@celeryd_init.connect
def configure_workers(sender=None, conf=None, **kwar):
    global config
    global logger

    config = JohannConfig.get_config()
    logger = JohannLogger(__name__).logger

    johann.util.load_plugins()
    logger.debug(
        "Celery registered"
        f" tasks:\n{pprint.pformat(johann.util.celery_registered_tasks())}"
    )

    logger.debug(config.json(indent=2, sort_keys=True))


@task_failure.connect
def log_failure(
    sender: Optional["Task"] = None,
    task_id: str = None,
    exception: Exception = None,
    args: Optional[List] = None,
    kwargs: Optional[Dict] = None,
    traceback: Optional["StackSummary"] = None,
    einfo: Optional["ExceptionInfo"] = None,
    **celery_kwargs: Any,
) -> None:
    msg_id = (
        ((sender.request.shadow if sender.request else sender.name) if sender else None)
        or task_id
        or "From unknown task"
    )
    if exception:
        msg = f"{msg_id}: TASK FAILED -- ({type(exception)}): {exception}"
        logger.exception(msg)
    else:
        msg = f"{msg_id}: TASK FAILED -- unknown Exception"
        logger.error(msg)


@task_retry.connect
def log_retry(
    sender: Optional["Task"] = None,
    request: Optional["Request"] = None,
    reason: Optional[Union[Exception, str]] = None,
    einfo: Optional["ExceptionInfo"] = None,
    **celery_kwargs: Any,
) -> None:
    msg_id = (
        (sender.request.shadow if sender.request else sender.name) if sender else None
    ) or "From unknown task"
    if reason:
        if isinstance(reason, Exception):
            logger.exception(f"{msg_id}: TASK RETRY -- {reason}")
        else:
            logger.warning(f"{msg_id}: TASK_RETRY -- {reason}")
    else:
        logger.warning(f"{msg_id}: TASK RETRY -- unknown reason")


@celery_app.task
def run_shell_command(
    command: str, shell: str = "sh", allow_errors: bool = True
) -> str:
    logger.debug(f"Running command '{command}'")
    proc = subprocess.run(
        [shell, "-c", command],
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    exit_code = proc.returncode
    output = proc.stdout

    logger.debug(f"Command output:\n{output}")
    if exit_code:
        msg = "Non-zero exit code"
        if not allow_errors:
            logger.error(msg)
            raise Exception(msg)
        else:
            logger.warning(msg)
    return output


@celery_app.task
def pmtr_enable(pmtr_job_name: str) -> str:
    cmd = f"echo -n 'enable {pmtr_job_name}' > /dev/udp/127.0.0.1/31337"
    return run_shell_command(cmd, "bash")


@celery_app.task
def pmtr_disable(pmtr_job_name: str) -> str:
    cmd = f"echo -n 'disable {pmtr_job_name}' > /dev/udp/127.0.0.1/31337"
    return run_shell_command(cmd, "bash")


@celery_app.task(
    bind=True, autoretry_for=[RetryableException], retry_backoff=3, max_retries=3
)
def test_retry(self: "Task") -> None:
    time.sleep(5)
    if self.request.retries > 0:
        logger.info("This is a retry")
        if self.request.retries % 2 == 1:
            logger.info("This is an odd numbered retry -- fail")
            raise RetryableException("purposefully failed")
        else:
            logger.info("This is an even numbered retry -- pass")
            return
    else:
        logger.info("This is not a retry -- fail")
        raise RetryableException("purposefully failed")
