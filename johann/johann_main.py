# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.

import asyncio
import atexit
import os
import signal
import subprocess
from typing import TYPE_CHECKING, Tuple
from uuid import uuid4

import redis

import johann.tasks_main  # noqa
import johann.tasks_util  # noqa
from johann import util
from johann.conductor_app import init_conductor
from johann.shared.config import JohannConfig, workers
from johann.shared.logger import JohannLogger

if TYPE_CHECKING:
    from types import FrameType

config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


def trap_signal(sig: signal.Signals, frame: "FrameType") -> None:
    logger.info(f"Received signal {sig}")
    raise SystemExit


def remove_pidfile() -> None:
    try:
        os.remove(config.PID_FILE)
        logger.info(f"Removed '{config.PID_FILE}'")
    except OSError as e:
        logger.exception(f"Error removing pidfile '{config.PID_FILE}': {e.strerror}")


def add_workers_helper(
    user: str,
    workers_min: int = config.CELERY_WORKERS_MIN,
    workers_max: int = config.CELERY_WORKERS_MAX,
    purge: bool = False,
    queue_id: str = config.CELERY_QUEUE_ID,
) -> Tuple[bool, str]:
    worker_id = str(uuid4())[:6]
    worker_name = f"{queue_id}_{worker_id}"

    cmd = (
        f'/bin/sh -ec ". $HOME/.profile && PYTHONPATH={config.PROJECT_ROOT} celery '
        f"-A {config.CELERY_TASKS_MODULE} worker {'-D' if config.CELERY_DETACH else ''}"
        f" {'--purge ' if purge else ''}--autoscale={workers_max},{workers_min} -Q"
        f' {queue_id} -n {worker_name} -Ofair"'
    )
    logger.debug(f"celery command: {cmd}")

    try:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=str(config.SRC_ROOT), encoding="utf-8"  # nosec
        )
    except Exception:
        logger.exception("Error starting celery")
        return False, "Error starting celery"

    try:
        proc.wait(timeout=5)  # give it a chance to error out
        if proc.returncode is not None and proc.returncode != 0:
            msg = (
                f"there was likely an issue adding worker(s) to {queue_id}; see logs"
                " for details"
            )
            logger.warning(msg)
            return False, msg
    except subprocess.TimeoutExpired:
        pass  # presumably no error

    workers.append(proc)

    msg = (
        f"added worker {worker_name} with autoscale to"
        f" {queue_id}; worker process count = {len(workers)}"
    )
    logger.debug(msg)
    return True, msg


def cleanup() -> None:
    logger.info("Cleaning up workers")
    for w in workers:
        w.kill()


if __name__ == "__main__":
    logger.info("********** Starting Johann **********")

    logger.info("********** Loading Plugins **********")
    util.load_plugins()

    logger.info(f"********** Queue ID: {config.CELERY_QUEUE_ID} **********")

    if config.JOHANN_MODE not in ["player", "conductor"]:
        logger.warning(
            f"Unrecognized mode '{config.JOHANN_MODE}'; starting in player mode"
        )
        config.JOHANN_MODE = "player"
    logger.info(f"********** Mode: {config.JOHANN_MODE} **********")

    if config.DEBUG:
        logger.debug("********** DEBUG **********")

    logger.debug("********** Config **********")
    logger.debug(config.json(indent=2, sort_keys=True))

    # do this at the beginning in case files are mounted in a docker volume
    util.get_codehash()

    with open(config.PID_FILE, "w") as pidfile:
        print(f"{os.getpid()}", file=pidfile)
        logger.debug(f"Wrote PID to '{config.PID_FILE}'")

    # check Redis connection
    if not config.SKIP_REDIS:
        logger.debug("********** Pinging Redis **********")
        r = redis.StrictRedis(
            config.REDIS_HOST,
            config.REDIS_PORT,
            config.REDIS_DB,
            socket_connect_timeout=10,
        )
        if r.ping():
            logger.debug("********** Redis Ping Successful **********")
        else:
            logger.error(f"unable to ping Redis server at {config.REDIS_URL}")
            raise SystemError

    # celery workers
    if not config.SKIP_CELERY:
        logger.debug(
            f"Killing any extant celery workers for {config.CELERY_TASKS_MODULE}"
        )
        cmd = [
            "pkill",
            "-f",
            f"'celery -A {config.CELERY_TASKS_MODULE}'",
        ]
        subprocess.run(cmd, encoding="utf-8")

        logger.info("********** Starting Workers **********")
        success, msg = add_workers_helper(config.CELERY_USER, purge=True)
        if not success:
            logger.error("failed to start local workers")
            raise SystemExit

    # get asyncio loop
    loop = asyncio.get_event_loop()

    atexit.register(remove_pidfile)
    atexit.register(cleanup)

    # signals
    signal.signal(signal.SIGTERM, trap_signal)

    if config.JOHANN_MODE == "player":
        pass
    elif config.JOHANN_MODE == "conductor":
        init_conductor()

    logger.info("********** Ready **********")
    loop.run_forever()
