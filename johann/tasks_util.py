# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import random
import time
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

import requests

from johann.shared.config import JohannConfig, celery_app
from johann.shared.enums import TaskState
from johann.shared.logger import JohannLogger
from johann.util import get_attr, get_codehash

if TYPE_CHECKING:
    from celery import Task
    from celery.worker.request import Request


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


# used when you want to retry a Task
# inherits from BaseException so not caught by 'except Exception'
# but rather is passed to Celery who can then retry
class RetryableException(BaseException):
    pass


@celery_app.task()
def remote_codehash() -> str:
    return get_codehash()


def get_hostname(celery_request: "Request") -> Optional[str]:
    ret = celery_request.hostname
    if "celery@" in ret:
        ret = celery_request.hostname.split("celery@")[1]
        if "_" in ret:
            return ret.split("_")[0]

    return None


@celery_app.task(bind=True)
def select_random(
    self: "Task", select_from: Union[List, Dict], num_select: int, values_only=False
) -> Dict[int, Any]:
    assert type(select_from) in [list, dict]

    ret = []
    indexes_used = []
    rand = random.Random()
    while len(ret) < num_select:
        if len(indexes_used) == len(select_from):
            raise Exception(
                f"{self.request.shadow}: less than requested {num_select} unique items"
                " to choose from"
            )

        index = rand.randint(0, len(select_from) - 1)
        if index in indexes_used:
            continue

        indexes_used.append(index)
        item = (
            list(select_from.values())[index]
            if type(select_from) is dict
            else select_from[index]
        )
        if item not in ret:
            ret.append(item)

    dict_ret = {index: selection for index, selection in enumerate(ret)}
    if num_select == 1:
        ret = list(dict_ret.values())[0]
    elif values_only:
        ret = list(dict_ret.values())
    else:
        ret = dict_ret

    return ret


# assumes arg is str of form 'johann.stored.<SCORE_NAME>[.<SOMETHING>]'
# note that the johann conductor will add the <SCORE_NAME> portion for you
def fetch_stored_data_helper(arg: str) -> Any:
    logger.debug(f"fetching stored_data '{arg}' from conductor")

    keyparts = arg.split("johann.stored.")[1].split(".")
    if len(keyparts) == 1:
        key = "None"
        subkey = "None"
        subsubkey = "None"
        subsubsubkey = "None"
    elif len(keyparts) == 2:
        key = keyparts[1]
        subkey = "None"
        subsubkey = "None"
        subsubsubkey = "None"
    elif len(keyparts) == 3:
        key = keyparts[1]
        subkey = keyparts[2]
        subsubkey = "None"
        subsubsubkey = "None"
    elif len(keyparts) == 4:
        key = keyparts[1]
        subkey = keyparts[2]
        subsubkey = keyparts[3]
        subsubsubkey = "None"
    elif len(keyparts) == 5:
        key = keyparts[1]
        subkey = keyparts[2]
        subsubkey = keyparts[3]
        subsubsubkey = keyparts[4]
    else:
        raise Exception(f"arg {arg} has too many subkeys")
    rjson = requests.get(
        f"http://{config.CONDUCTOR_LOCAL_HOST_NAME}:{config.CONDUCTOR_PORT}/scores/"
        f"{keyparts[0]}/stored_data/{key}/{subkey}/{subsubkey}/{subsubsubkey}"
    ).json()

    if not rjson["success"]:
        raise Exception(f"failed to fetch value for {arg}:\n{rjson['messages']}")
    else:
        data = rjson["data"]
        logger.debug(f"fetched {arg} as:\n{data}")
        return data


# decorator
# fetches any 'johann.stored.<SOMETHING>' args or kwargs to func
def resolve_johann_args(func: Callable) -> Callable:
    @wraps(func)
    def func_wrapper(self, *args: Any, **kwargs: Any) -> Callable:
        new_args = ()
        for arg in args:
            if type(arg) is not str or "johann.stored." not in arg:
                logger.debug(f"ignoring arg {arg}")
                new_args += (arg,)
            elif len(arg.split("johann.stored.")) != 2:
                raise Exception(f"arg {arg} is malformed")
            else:
                logger.debug(f"fetching arg {arg}")
                new_args += (fetch_stored_data_helper(arg),)
        new_kwargs = {}
        for kw, arg in kwargs.items():
            if type(arg) is not str or "johann.stored." not in arg:
                logger.debug(f"ignoring kwarg {arg}")
                new_kwargs[kw] = arg
            elif len(arg.split("johann.stored.")) != 2:
                raise Exception(f"kwarg {kw}={arg} is malformed")
            else:
                logger.debug(f"fetching kwarg {arg}")
                new_kwargs[kw] = fetch_stored_data_helper(arg)

        return func(self, *new_args, **new_kwargs)

    return func_wrapper


@celery_app.task(bind=True)
def do_until(
    self: "Task",
    func_name: str,
    args: Optional[List],
    interval: int,
    timeout: int,
    expected_value: Any = None,
    fetch_inner_func_args: bool = True,
) -> Dict[str, Any]:
    func = get_func_helper(func_name, self)

    start = time.time()
    now = start
    self.update_state(
        state=TaskState.PROGRESS, meta={"current": now - start, "total": timeout}
    )
    result = None
    while (now - start) < timeout:
        time.sleep(interval)
        # if fetch_inner_func_args:
        #    result = resolve_johann_args(func)(self, *args)
        # else:
        if args is None:
            args = ()
        result = func(*args)
        if expected_value is not None and result == expected_value:
            return {
                "current": timeout,
                "total": timeout,
                "status": "Task completed!",
                "result": result,
            }
        else:
            now = time.time()
            self.update_state(
                state=TaskState.PROGRESS,
                meta={
                    "current": now - start,
                    "total": timeout,
                    "interim_result": result,
                },
            )

    if expected_value is not None:
        msg = (
            f"{self.request.shadow}: Timed out waiting for '{expected_value}' from"
            f"{func_name}; last result: {result}"
        )
        logger.warning(msg)
        raise Exception(msg)
    else:
        return {
            "current": timeout,
            "total": timeout,
            "status": "Task completed!",
            "result": result,
        }


def get_func_helper(func_name: str, celery_task: "Task") -> Callable:
    func, msg = get_attr(func_name)

    if not func:
        msg = f"{celery_task.name}: {msg}"
        logger.error(msg)
        celery_task.max_retries = 0
        raise Exception(msg)
    else:
        return func


@celery_app.task(bind=True)
def do_on_random_interval(
    self: "Task",
    func_name: str,
    args: Optional[List],
    interval_min: int,
    interval_max: int,
    duration: int,
) -> Optional[Dict[str, Any]]:
    func = get_func_helper(func_name, self)

    if args is None:
        args = ()

    start = time.time()
    now = start
    self.update_state(
        state=TaskState.PROGRESS, meta={"current": now - start, "total": duration}
    )
    count = 0
    result = None
    while (now - start) < duration:
        count += 1
        sleeptime = random.random() * (interval_max - interval_min) + interval_min
        time.sleep(sleeptime)
        logger.info(
            f"{self.request.shadow}: Calling {func_name} after sleeping {sleeptime}"
        )

        # result = func(*args, **kwargs)
        result = func(*args)
        now = time.time()
        self.update_state(
            state=TaskState.PROGRESS, meta={"current": now - start, "total": duration}
        )
    logger.info(f"{self.request.shadow}: Finished calling {func_name}")

    return {
        "current": duration,
        "total": duration,
        "status": "Task completed!",
        "result": result,
    }
