# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import json
import random
import re
import subprocess
import sys
import unicodedata
from enum import Enum
from glob import glob
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union

import aiohttp.web
from marshmallow import ValidationError as MarshmallowValidationError
from marshmallow import fields
from pydantic.errors import _PathValueError

from johann.shared.config import JohannConfig
from johann.shared.logger import JohannLogger

if TYPE_CHECKING:
    from asyncio import Future
    from pathlib import PurePath
    from typing import TypeVar

    from celery.result import AsyncResult, GroupResult

    from johann.host import Host
    from johann.measure import Measure
    from johann.player import Player
    from johann.score import Score

    PathLikeObj = TypeVar("PathLikeObj", str, bytes, PurePath)


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


class PathNotSafeError(_PathValueError):
    code = "path.not_safe"
    msg_template = 'path "{path}" is not safe'


class JohannError(Exception):
    def __init__(self, msg):
        self.msg = msg


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


class NameField(fields.String):
    """A name field."""

    default_error_messages = {
        "invalid_name": (
            "Names may only consist of letters, numbers, underscores, and hyphens"
        )
    }

    def _validated(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str) and re.match(r"[\w\-]+", value):
            return value
        else:
            raise self.make_error("invalid_name")

    def _deserialize(self, value, attr, data, **kwargs) -> Optional[str]:
        return self._validated(value)


class LaxStringField(fields.String):
    """A string field that will attempt to cast non-strings."""

    default_error_messages = {"invalid_string": "neither a string nor castable to one"}

    def _validated(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        try:
            return str(value)
        except (ValueError, AttributeError, TypeError) as e:
            raise self.make_error("invalid_string") from e

    def _deserialize(self, value, attr, data, **kwargs) -> Optional[str]:
        return self._validated(value)


class StateField(fields.Field):
    def _serialize(self, value, attr, obj, **kwargs):
        if type(value) is str:
            return value
        else:
            return value.value

    def _deserialize(self, value, attr, data, **kwargs):
        try:
            return TaskState(value)
        except ValueError:
            raise MarshmallowValidationError("not a valid TaskState")


def task_state_priority(state: TaskState) -> int:
    try:
        state = TaskState(state)
    except ValueError:
        msg = f"{state} is not a valid TaskState"
        logger.error(msg)
        raise

    sorted_pri = [  # low to high
        TaskState.SUCCESS,
        TaskState.PENDING,
        TaskState.DEFERRED,
        TaskState.QUEUED,
        TaskState.STARTED,
        TaskState.PROGRESS,
        TaskState.RETRY,
        TaskState.FAILURE,
    ]

    try:
        return sorted_pri.index(state)
    except ValueError:
        msg = f"{state} is not a valid TaskState"
        logger.exception(msg)
        raise


def get_codehash() -> str:
    if not config.CODEHASH:
        config.CODEHASH = calculate_codehash()
        logger.debug(f"Codehash: {config.CODEHASH}")

    return config.CODEHASH


def calculate_codehash() -> str:
    logger.debug("Getting codehash...")
    hashes = {}
    for pathname in config.CODEHASH_FILES:
        filenames = glob(pathname)
        for filename in filenames:
            try:
                with open(filename, "rb") as f:
                    filehash = sha256()
                    filehash.update(f.read())
                    filehash = filehash.hexdigest()
                    logger.debug(f"{filehash} {filename}")
                    hashes[filename] = filehash
            except OSError as e:
                logger.warning(
                    f"Error while getting codehash ('{filename}'): {e.strerror}"
                )
                continue
    codehash = sha256()
    codehash.update(json.dumps(sorted(list(hashes.values()))).encode("utf-8"))
    codehash = codehash.hexdigest()
    logger.debug(f"Codehash: {codehash}")
    return codehash


async def wrap_future(
    fut: "Future",
    future_name: str,
    callback: Optional[Callable],
    *callback_args: Any,
    score_or_measure: Union["Score", "Measure", None] = None,
) -> Any:
    if not future_name:
        future_name = "?"
    try:
        result = await fut
        if callback:
            try:
                callback_result = await callback(result, *callback_args)
                logger.debug(f"{future_name}| callback result: {callback_result}")
            except Exception as e:
                msg = f"{future_name}| callback raised an exception: {str(e)}"
                logger.exception(msg)
        return result
    except Exception as e:
        msg = f"{future_name}| raised an exception: {str(e)}"
        logger.exception(msg)

        if score_or_measure is not None:
            score_or_measure.state = TaskState.FAILURE
            score_or_measure.finished = True
            logger.info(f"{future_name}| successfully updated state to failure")
        return None


def safe_name(value: str) -> str:
    value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    value = re.sub(r"[^\w\s.-]", "", value).strip()
    value = re.sub(r"[-\s]+", "_", value)
    return value


def gudexc(
    msg: str,
    score: Union["Score", str, None] = None,
    player: Union["Player", str, None] = None,
    measure: Union["Measure", str, None] = None,
    host: Union["Host", str, None] = None,
) -> str:
    exc_type, exc_value = sys.exc_info()[:2]
    if exc_type or exc_value:
        msg = f"{msg} ({exc_type.__name__}: {exc_value})"
    return gudlog(msg, score, player, measure, host)


def gudlog(
    msg: str,
    score: Union["Score", str, None] = None,
    player: Union["Player", str, None] = None,
    measure: Union["Measure", str, None] = None,
    host: Union["Host", str, None] = None,
) -> str:
    prefix = gudprefix(score, player, measure, host)
    return f"{prefix}| {msg}"


def gudprefix(
    score: Union["Score", str, None] = None,
    player: Union["Player", str, None] = None,
    measure: Union["Measure", str, None] = None,
    host: Union["Host", str, None] = None,
) -> str:
    prefix = ""
    if score:
        if isinstance(score, str):
            prefix += f".{score}"
        else:
            prefix += f".{score.name}"
    if measure:
        if isinstance(measure, str):
            prefix += f".{measure}"
        else:
            prefix += f".{measure.name}"
    if player:
        if isinstance(player, str):
            prefix += f".{player}"
        else:
            prefix += f".{player.name}"
    if host:
        if isinstance(host, str):
            prefix += f".{host}"
        else:
            prefix += f".{host.name}"

    if not prefix:
        prefix = "?"
    elif prefix and prefix[0] == ".":
        prefix = prefix[1:]

    return prefix


def celery_group_status(group_result: "GroupResult", short: bool = False) -> Dict:
    ret = {}

    if not group_result:
        return ret

    ret["id"] = group_result.id
    ret["state"] = TaskState.PENDING  # will change below
    ret["finished"] = group_result.ready()
    ret["completed_count"] = group_result.completed_count()
    ret["failed_count"] = 0
    ret["status"] = {}

    progress_current = 0
    tasks = {}
    for task in group_result.children:
        task_status = celery_task_status(task, short=short)

        # update overall state
        if task_state_priority(task_status["state"]) > task_state_priority(
            ret["state"]
        ):
            ret["state"] = task_status["state"]

        if task_status["state"] == TaskState.FAILURE:
            ret["failed_count"] += 1
            if "status" in task_status:
                ret["status"][task.task_id] = task_status["status"]
        elif task_status["state"] == TaskState.SUCCESS:
            progress_current += 1
        elif task_status["state"] == TaskState.PROGRESS:
            if (
                "meta" in task_status
                and task_status["meta"]
                and "current" in task_status["meta"]
                and "total" in task_status["meta"]
            ):
                c = task_status["meta"]["current"]
                t = task_status["meta"]["total"]
                cur_fraction = c / t
                if cur_fraction > 1:
                    logger.warning(
                        f"{task_status['name']}: got task status current > total;"
                        " something is weird"
                    )
                else:
                    progress_current += cur_fraction
            else:
                logger.warning(
                    f"{task_status['name']}: task has status of 'progress' but "
                    "missing or improper 'meta' dict "
                )

        tasks[task.task_id] = task_status

    if len(group_result.children) == 0:
        normalized_progress = 0
    else:
        normalized_progress = progress_current / len(group_result.children)

    if group_result.successful():
        ret["state"] = TaskState.SUCCESS
        normalized_progress = len(group_result.children)

    if not short:
        ret["meta"] = {
            "current": normalized_progress,
        }
        ret["tasks"] = tasks

    return ret


def celery_task_status(task: "AsyncResult", short: bool = False) -> Dict:
    task_status = {"name": task.name, "state": task.status, "meta": {}}

    if task.status == TaskState.FAILURE:
        # task.result is probably an exception
        task_status["traceback"] = task.traceback
        if isinstance(task.result, BaseException):
            exc_type = type(task.result)
            exc_type_str = f"{exc_type.__module__}.{exc_type.__name__}"
            task_status["status"] = f"Exception ({exc_type_str}): {str(task.result)}"
        else:
            task_status["status"] = str(task.result)
    elif task.status == TaskState.SUCCESS:
        task_status["result"] = task.result
    elif task.status == TaskState.PROGRESS:
        task_status["meta"] = task.result
    else:
        pass

    if not short:
        task_status["retries"] = task.retries
        task_status["id"] = task.task_id
        if task.date_done is not None:
            task_status["finished_at"] = task.date_done.isoformat()

    return task_status


def create_johann_tarball() -> bool:
    codehash = get_codehash()
    tarball_name = f"johann.{codehash}.tar.gz"
    tarball_process = subprocess.run(
        [
            "tar",
            "-C",
            str(config.SRC_ROOT),
            "-czf",
            tarball_name,
            "--exclude=__pycache__",
            "--exclude=scores",
            "--exclude=minirepo*",
            "--exclude=*.log",
            "--exclude=.[^/]*",
            "./",
        ],
        cwd=str(config.TARBALL_PATH),
    )

    if tarball_process.returncode != 0:
        logger.warning(
            f"Failed to create tarball for current code: {str(tarball_process.stderr)}"
        )
        return False
    else:
        logger.debug(f"Successfully created Johann tarball {tarball_name}")
        return True


def py_to_clistr(script_str: str) -> str:
    exec_str = "exec(%r)" % re.sub("\r\n|\r", "\n", script_str.rstrip())
    return '"%s"' % exec_str.replace('"', r"\"")


def get_attr(attr_name: str) -> Tuple[Any, Optional[str]]:
    try:
        module_name, attribute = attr_name.rsplit(".", 1)
    except ValueError:
        msg = f"invalid attribute: '{attr_name}'; did you specify a module?"
        return None, msg

    if module_name not in sys.modules:
        msg = f"no such module in current context: '{module_name}'"
        return None, msg

    attr = getattr(sys.modules[module_name], attribute, None)

    if not attr:
        msg = f"no such attribute: '{attr_name}'"
        return None, msg
    else:
        return attr, None


# Note: descends recursively into lists and dicts
def transform_args(
    score: "Score", measure: "Measure", player: "Player", args: List
) -> List:
    new_args = []
    for a in args:
        if isinstance(a, str):
            new_arg = transform_arg(score, measure, player, a)
            new_args.append(new_arg)
        elif isinstance(a, list):
            new_arg = transform_args(score, measure, player, a)
            new_args.append(new_arg)
        elif isinstance(a, dict):
            new_arg = {}
            for k, v in a:
                new_arg[k] = transform_args(score, measure, player, v)
            new_args.append(new_arg)
        else:
            new_args.append(a)

    return new_args


def parse_special_arg(a: str) -> Tuple[str, List[str]]:
    if not isinstance(a, str):
        raise ValueError("not a string")

    arg_type = re.match(r"johann\.(\w+)", a)
    stored = re.fullmatch(r"johann\.stored((\.\w+(-\w+)*)+)", a)
    rand = re.fullmatch(r"johann\.random\.(\d+)-(\d+)", a)

    if not arg_type:
        raise ValueError("not a special argument")
    elif arg_type.group(1) not in config.SPECIAL_ARG_TYPES:
        raise ValueError(f"unrecognized special argument type '{arg_type.group(1)}'")
    elif stored:
        keyparts = stored.group(1)[1:].split(".")
        if len(keyparts) == 1:
            key = keyparts[0]
            subkey = None
            subsubkey = None
            subsubsubkey = None
        elif len(keyparts) == 2:
            key = keyparts[0]
            subkey = keyparts[1]
            subsubkey = None
            subsubsubkey = None
        elif len(keyparts) == 3:
            key = keyparts[0]
            subkey = keyparts[1]
            subsubkey = keyparts[2]
            subsubsubkey = None
        elif len(keyparts) == 4:
            key = keyparts[0]
            subkey = keyparts[1]
            subsubkey = keyparts[2]
            subsubsubkey = keyparts[3]
        else:
            raise KeyError("too many subkeys")
        return arg_type.group(1), [key, subkey, subsubkey, subsubsubkey]
    elif rand:
        if rand.group(1) > rand.group(2):
            raise ValueError("range's lower bound must be less than its upper bound")
        else:
            return arg_type.group(1), [rand.group(1), rand.group(2)]
    else:
        raise ValueError("invalid special argument")


def transform_arg(score: "Score", measure: "Measure", player: "Player", a: str) -> Any:
    if type(a) == str and a.startswith("johann."):
        try:
            arg_type, arg_split = parse_special_arg(a)
            if arg_type == "stored":
                if measure.lazy_fetch_stored:
                    # add the score_name so the player knows the right URL to use
                    new_a = f"johann.stored.{score.name}.{'.'.join(arg_split)}"
                    return (
                        new_a  # the player will fetch from conductor at measure runtime
                    )
                else:
                    success, msg, code, data = score.fetch_stored_data(
                        *tuple(arg_split)
                    )
                    if not success:
                        raise KeyError(msg)
                    logger.debug(gudlog(msg, score, player, measure))
                    return data
            elif arg_type == "random":
                new_arg = random.randint(int(arg_split[0]), int(arg_split[1]))
                msg = f"'{a}' randomized to {new_arg}"
                logger.debug(gudlog(msg, score, player, measure))
                return new_arg
        except ValueError:
            msg = gudexc(
                f"Failed to transform special argument '{a}'", score, player, measure
            )
            logger.debug(msg)
            raise
    else:
        return a


def boolystring(value: Union[bool, int, str]) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    elif isinstance(value, int):
        return value > 0
    elif isinstance(value, str):
        if value.lower() in ["true", "t", "1", "y", "yes"]:
            return True
        elif value.lower() in ["false", "f", "0", "-1", "n", "no"]:
            return False
        else:
            return None
    else:
        logger.debug(f"boolystring fail: ({type(value)}) {value}")
        return None


def get_score_paths() -> List[str]:
    return glob(f"{config.SRC_ROOT}/plugins/*/scores/*.y*ml") + glob(
        f"{config.SCORE_PATH}/*.y*ml"
    )


def johann_response(
    success: bool,
    msgs: Union[str, List, None] = None,
    status_code: int = 200,
    data: Any = None,
    sort_keys: bool = False,
) -> aiohttp.web.Response:
    if msgs is None:
        msgs = []
    if isinstance(msgs, str):
        msgs = [msgs]

    ret = {"success": success, "messages": msgs, "data": data}

    return aiohttp.web.Response(
        body=json.dumps(ret, indent=4, sort_keys=sort_keys).encode("utf-8"),
        content_type="application/json",
        status=status_code,
    )
