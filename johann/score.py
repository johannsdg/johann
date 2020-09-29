# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import asyncio
import copy
import pprint
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from marshmallow import Schema
from marshmallow import ValidationError as MarshmallowValidationError
from marshmallow import fields, post_load

from johann.docker_host_control import DockerHostControl
from johann.host import HostSchema
from johann.host_control_util import get_host_control_class, get_host_names
from johann.measure import MeasureSchema
from johann.player import PlayerSchema
from johann.shared.config import JohannConfig, hosts, scores
from johann.shared.enums import TaskState
from johann.shared.fields import NameField, StateField
from johann.shared.logger import JohannLogger
from johann.util import (
    create_johann_tarball,
    gudexc,
    gudlog,
    task_state_priority,
    transform_arg,
    transform_args,
    wrap_future,
)

if TYPE_CHECKING:
    from johann.host import Host
    from johann.measure import Measure
    from johann.player import Player


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


conductor_local_measure_dicts = [
    {
        "name": "tune_orchestra",
        "players": [config.CONDUCTOR_ALLHOSTS_PLAYER_NAME],
        "task": "johann.conductor_tasks.tune_host",
        "start_delay": 0,
        # 'depends_on': ['create_hosts']
    },
    # {
    # 'name': 'create_hosts',
    # 'players': [config.CONDUCTOR_ALLHOSTS_PLAYER_NAME],
    # 'task': 'johann.conductor_tasks.create_hosts',
    # 'start_delay': 10
    # },
]


class ScoreSchema(Schema):
    class Meta:
        ordered = True

    name = NameField(required=True)
    version = fields.Str(missing="1.0")
    category = fields.Str(missing="none")
    description = fields.Str(missing="")
    players = fields.Dict(
        keys=fields.Str(), values=fields.Nested(PlayerSchema), required=True
    )
    measures = fields.List(fields.Nested(MeasureSchema), required=True)
    create_hosts = fields.Boolean(missing=False)
    discard_hosts = fields.Boolean(missing=False)

    # do not remove any of these three without careful consideration
    state = StateField(dump_only=True)
    status = fields.Dict(
        keys=fields.Str(),  # measure
        values=fields.Dict(
            keys=fields.Str(),  # player
            values=fields.Dict(
                keys=fields.Str(), values=fields.Str()  # host, host status
            ),
        ),
        dump_only=True,
    )
    finished = fields.Boolean(dump_only=True)

    package = fields.Str(dump_only=True)
    started_at = fields.DateTime(dump_only=True)
    finished_at = fields.DateTime(dump_only=True)
    stored_data = fields.Dict(keys=fields.Str(), values=fields.Raw(), dump_only=True)
    task_map = fields.Dict(
        keys=fields.Str(),
        values=fields.Dict(keys=fields.Str(), values=fields.Str()),
        dump_only=True,
    )
    last_successful_roll_call = fields.DateTime(dump_only=True)

    # depends on post_load, so we do not use @validates_schema here
    def validate_score(self, score: "Score", **kwargs) -> None:
        self.validate_unique(score)
        self.validate_measure_player_names(score)
        self.validate_measure_depends_on(score)

    def validate_unique(self, score: "Score", **kwargs) -> None:
        if score.name in scores:
            raise MarshmallowValidationError(
                f"There is already a score with name '{score.name}'"
            )

    def validate_measure_player_names(self, score: "Score", **kwargs) -> None:
        # no duplicate measure names
        measure_names = [x.name for x in score.measures]
        if len(set(measure_names)) != len(measure_names):
            raise MarshmallowValidationError("Duplicate measure name(s)")

        for measure in score.measures:
            # all players specified in measure also exist in score's list of players
            for player_name in measure.player_names:
                if player_name not in score.players:
                    raise MarshmallowValidationError(
                        f"Unrecognized player '{player_name}' in measure"
                        f" '{measure.name}'"
                    )

    def validate_measure_depends_on(self, score: "Score", **kwargs) -> None:
        measure_names = [x.name for x in score.measures]
        bad_deps = []
        for measure in score.measures:
            for dep in measure.depends_on:
                if dep not in measure_names:
                    bad_deps.append(dep)

        if len(bad_deps) > 0:
            raise MarshmallowValidationError(
                f"Invalid values for depends_on:\n{bad_deps}"
            )

    @post_load(pass_original=True)
    def make_score(self, data: Dict, original_data: Dict, **kwargs) -> "Score":
        score = Score(**data, original_data=original_data)
        self.validate_score(score)
        return score


class Score(object):
    def __init__(
        self,
        name,
        version,
        category,
        description,
        players,
        measures,
        create_hosts,
        discard_hosts,
        original_data,
    ) -> None:
        self.name: str = name
        self.version: str = version
        self.category: str = category
        self.description: str = description
        self.players: Dict[str, "Player"] = players
        self.measures: List["Measure"] = measures
        self.create_hosts: bool = create_hosts
        self.discard_hosts: bool = discard_hosts
        self.original_data: Dict[str, Any] = original_data

        self.state: TaskState = TaskState.PENDING
        self.status: Dict[str, Dict[str, Dict[str, str]]] = {}
        self.package: Optional[str] = None
        self.finished: bool = False
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.stored_data: Dict[str, Any] = {}
        self.task_map: Dict[
            str, Dict[str, str]
        ] = {}  # {<TASK_ID>: {"measure_name":<>, "player_name":<>, "host_name":<>}}
        # this is used to map tasks within a celery group back to the
        # player/host that they 'belong' to
        self.last_successful_roll_call: Optional[datetime] = None

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(name={self.name},version={self.version},"
            f"category={self.category},description={self.description},"
            f"players={'|'.join(list(self.players.keys()))},"
            f"measures={'|'.join([m.name for m in self.measures])})"
        )

    def dump(
        self, exclude_local: bool = True, yaml_fields_only: bool = True
    ) -> Dict[str, Any]:
        data = ScoreSchema().dump(self)

        if exclude_local:
            # remove local player
            try:
                del data["players"][config.CONDUCTOR_ALLHOSTS_PLAYER_NAME]
            except KeyError:
                pass

            local_measure_names = [x["name"] for x in conductor_local_measure_dicts]
            to_remove = []
            for m in data["measures"]:
                # remove local measure dependencies
                for local_name in local_measure_names:
                    if local_name in m["depends_on"]:
                        m["depends_on"].remove(local_name)
                # find local measures to remove
                if m["name"] in local_measure_names:
                    to_remove.append(m)
            # actually remove the measures (no modify while iterating)
            for m in to_remove:
                data["measures"].remove(m)

        if yaml_fields_only:
            # remove score-level fields
            dump_only_fields = []
            for k, v in ScoreSchema().fields.items():
                if "dump_only=True" in str(v):
                    dump_only_fields.append(k)
            for f in dump_only_fields:
                del data[f]

            # remove measure-level fields
            dump_only_fields = []
            for k, v in MeasureSchema().fields.items():
                if "dump_only=True" in str(v):
                    dump_only_fields.append(k)
            for m in data["measures"]:
                for f in dump_only_fields:
                    del m[f]

        return data

    def get_status(self, short: bool = False) -> Dict[str, Any]:
        status = {"state": self.state, "finished": self.finished}
        if self.status or not short:
            status["status"] = self.status

        if not short:
            status["started_at"] = self.started_at
            status["finished_at"] = self.finished_at
            if self.started_at is not None:
                status["started_at"] = self.started_at.isoformat()
            if self.finished_at is not None:
                status["finished_at"] = self.finished_at.isoformat()
            if self.last_successful_roll_call is not None:
                status[
                    "last_successful_roll_call"
                ] = self.last_successful_roll_call.isoformat()

        status["measures"] = {x.name: x.get_status(short=short) for x in self.measures}

        if not short:
            status["stored_data"] = self.stored_data

        return status

    def get_host_totals(self) -> Dict[str, Any]:
        ret = {"total": 0}
        for m in self.measures:
            ret[m.name] = {}
            ret[m.name]["total"] = 0
            for p_name in m.player_names:
                p = self.players[p_name]
                subtotal = p.scale
                ret[m.name][p.name] = subtotal
                ret[m.name]["total"] += subtotal
                ret["total"] += subtotal
        return ret

    def get_measure(self, name: str) -> Optional["Measure"]:
        for m in self.measures:
            if m.name == name:
                return m
        return None

    # param taken_hostnames should be supplied by host_control.get_host_names()
    def map_missing_hosts(self, taken_hostnames: List[str]) -> None:
        for player in self.players.values():
            count = player.scale - len(player.hostnames)
            for _ in range(count):
                hostname = f"{player.name}_{str(uuid4())[:6]}"
                while hostname in taken_hostnames:  # no duplicates
                    hostname = f"{player.name}_{str(uuid4())[:6]}"
                player.hostnames.append(hostname)
                taken_hostnames.append(hostname)

        return

    def queue_measure(self, measure: "Measure") -> None:
        measure.state = TaskState.QUEUED
        logger.debug(f"Queueing measure {measure.name}")
        for player_name in measure.player_names:
            task = asyncio.ensure_future(
                self.play_the_player(measure, self.players[player_name])
            )
            task = wrap_future(
                task,
                f"{self.name}.{player_name}.{measure.name}",
                None,
                score_or_measure=measure,
            )
            asyncio.ensure_future(task)

    def get_all_hosts(self) -> Tuple[Set["Host"], List[str]]:
        all_hosts = set()
        error_msgs = []
        for p in self.players.values():
            if p.name == config.CONDUCTOR_ALLHOSTS_PLAYER_NAME:
                continue
            for hostname in p.hostnames:
                if hostname not in hosts:
                    error_msgs.append(f"host '{hostname}' not found in orchestra")
                    continue
                else:
                    all_hosts.add(hosts[hostname])

        return all_hosts, error_msgs

    def evaluate_state(self) -> None:
        # make sure we are current on measure state
        for m in self.measures:
            if not m.finished:
                m.evaluate_state(self)

        not_success = 0
        unfinished = 0
        for m in self.measures:
            if not m.finished:
                unfinished += 1
            if m.state is not TaskState.SUCCESS:
                not_success += 1

            if task_state_priority(m.state) > task_state_priority(self.state):
                self.state = m.state

            if m.status:

                # replace task id with host name if known
                status_copy = copy.copy(m.status)
                for player_name, player_status in m.status.items():
                    new_player_status = {}
                    for task_id, status in player_status.items():
                        if task_id in self.task_map:
                            tmi = self.task_map[task_id]
                            new_player_status[tmi["host_name"]] = status
                        else:
                            new_player_status[task_id] = status
                    status_copy[player_name] = new_player_status

                self.status[m.name] = status_copy

        if not_success == 0:
            self.state = TaskState.SUCCESS

        if unfinished == 0:
            self.finished = True
            self.finished_at = datetime.utcnow()
            msg = f"finished with state {self.state}"
            logger.info(gudlog(msg, self))

    def smart_queue(self, unqueued_measures: List["Measure"]) -> Optional["Measure"]:
        to_queue = (
            None  # queue one at a time to ensure we catch any dependency failures
        )
        dependency_failed = []
        for m in unqueued_measures:
            # no dependencies
            if not m.depends_on:
                to_queue = m
                break
            else:
                # make sure dependencies finished before queueing
                # also if any dependencies failed, dependent measures fail too
                ready_to_queue = True  # until/unless False below

                for dep_name in m.depends_on:
                    depm = self.get_measure(dep_name)
                    if not depm.finished:
                        ready_to_queue = False
                        m.state = TaskState.DEFERRED

                    if depm.state == TaskState.FAILURE and not m.dependency_proof:
                        ready_to_queue = False
                        m.state = TaskState.FAILURE
                        m.finished = True
                        m.status["all"]["all"] = f"dependency failed ({dep_name})"
                        dependency_failed.append(m)

                if ready_to_queue:
                    to_queue = m
                    break

        if to_queue is not None:
            self.queue_measure(to_queue)
            if to_queue in unqueued_measures:
                unqueued_measures.remove(to_queue)

        for to_remove in dependency_failed:
            msg = (
                f"measure '{to_remove.name}' failed because one or more of its"
                " dependencies failed"
            )
            logger.warning(gudlog(msg, self))
            if to_remove in unqueued_measures:
                unqueued_measures.remove(to_remove)

        return to_queue

    async def play(self) -> None:
        # add local measures
        for md in conductor_local_measure_dicts:
            if md["name"] not in [
                x.name for x in self.measures
            ]:  # make sure it's not already there
                local_measure: "Measure" = MeasureSchema().load(md)
                local_measure.local_measure = True
                self.measures.insert(0, local_measure)

        # add local measure dependencies
        local_measure_names = [x["name"] for x in conductor_local_measure_dicts]
        for m in self.measures:
            if m.name in local_measure_names:
                continue

            if "tune_orchestra" not in m.depends_on and m.name != "tune_orchestra":
                msg = "adding 'tune_orchestra' dependency"
                logger.debug(gudlog(msg, self, None, m.name))
                m.depends_on.insert(0, "tune_orchestra")

        # add conductor allhosts player
        if config.CONDUCTOR_ALLHOSTS_PLAYER_NAME not in self.players.keys():
            local_player: "Player" = PlayerSchema().load(
                {"name": config.CONDUCTOR_ALLHOSTS_PLAYER_NAME}
            )
            self.players[config.CONDUCTOR_ALLHOSTS_PLAYER_NAME] = local_player

        # conductor player will operate over all hosts, including those not created yet
        all_hosts, err_msgs = self.get_all_hosts()
        for err_msg in err_msgs:
            logger.warning(gudlog(err_msg, self))
        self.players[config.CONDUCTOR_ALLHOSTS_PLAYER_NAME].hostnames = [
            h.name for h in all_hosts
        ]

        # make johann tarball in prep for tuning
        create_johann_tarball()

        self.state = TaskState.STARTED
        self.started_at = datetime.utcnow()
        # self.started_at = datetime.now(tz=pytz.utc)

        unqueued_measures = copy.copy(self.measures)
        while True:
            self.evaluate_state()

            if self.finished:
                break

            self.smart_queue(unqueued_measures)

            await asyncio.sleep(1)

    def fetch_stored_data(
        self,
        key: str = None,
        subkey: str = None,
        subsubkey: str = None,
        subsubsubkey: str = None,
    ) -> Tuple[bool, Optional[str], int, Any]:
        # variable name level
        if not key:
            return True, None, 200, self.stored_data
        elif key in self.stored_data.keys():
            data = self.stored_data[key]
            if not subkey:
                msg = f"fetched stored data key {key} as {data}"
                return True, msg, 200, data
            # player name level (if not store_singleton)
            elif subkey in data:
                data = data[subkey]

                #  check if iterable
                try:
                    iter(data)
                    iterable = True
                except TypeError:
                    iterable = False

                if not subsubkey:
                    msg = f"fetched stored data key {key}.{subkey} as {data}"
                    return True, msg, 200, data
                elif not iterable:
                    msg = (
                        f"failed to fetch data subkey {subsubkey} from stored data at"
                        f" {key}.{subkey} (data not iterable)"
                    )
                    return False, msg, 400, None
                # host name level (if not store_singleton)
                elif subsubkey in data or (
                    subsubkey.isdigit() and type(data) in [list, dict]
                ):
                    if subsubkey in data:
                        data = data[subsubkey]
                    else:  # fetch dictionary value (host) by index
                        subsubkey = int(subsubkey)
                        try:
                            if type(data) is list:
                                data = data[subsubkey]
                            else:
                                data = list(data.values())[subsubkey]
                        except IndexError:
                            msg = (
                                f"failed to fetch data subkey {subsubkey} from stored"
                                f" data at {key}.{subkey} (index out of bounds)"
                            )
                            return False, msg, 400, None

                    # stored data value level
                    # check if iterable
                    try:
                        iter(data)
                        iterable = True
                    except TypeError:
                        iterable = False

                    if not subsubsubkey:
                        msg = (
                            f"fetched stored data key {key}.{subkey}.{subsubkey} as"
                            f" {data}"
                        )
                        return True, msg, 200, data
                    elif not iterable:
                        msg = (
                            f"failed to fetch data subkey {subsubsubkey} from stored"
                            f" data at {key}.{subkey}.{subsubkey} (data not iterable)"
                        )
                        return False, msg, 400, None
                    elif subsubsubkey in data or (
                        subsubsubkey.isdigit() and type(data) in [list, dict]
                    ):
                        if subsubsubkey in data:
                            data = data[subsubsubkey]
                            msg = (
                                "fetched stored data key"
                                f" {key}.{subkey}.{subsubkey}.{subsubsubkey} as {data}"
                            )
                            return True, msg, 200, data
                        else:
                            subsubsubkey = int(subsubsubkey)
                            try:
                                if type(data) is list:
                                    data = data[subsubsubkey]
                                else:
                                    data = list(data.values())[subsubsubkey]
                            except IndexError:
                                msg = (
                                    f"failed to fetch data subkey {subsubsubkey} from"
                                    f" stored data at {key}.{subkey}.{subsubkey} (index"
                                    " out of bounds)"
                                )
                                return False, msg, 400, None

                            msg = (
                                "fetched stored data key"
                                f" {key}.{subkey}.{subsubkey}.{subsubsubkey} as {data}"
                            )
                            return True, msg, 200, data
                    else:
                        msg = (
                            f"failed to fetch data subkey {subsubsubkey} from stored"
                            f" data at {key}.{subkey}.{subsubkey}"
                        )
                        return False, msg, 400, None
                else:
                    msg = (
                        f"failed to fetch data subkey {subsubkey} from stored data at"
                        f" {key}.{subkey}"
                    )
                    return False, msg, 400, None
            else:
                msg = f"failed to fetch data subkey {subkey} from stored data at {key}"
                return False, msg, 400, None
        else:
            msg = f"failed to fetch stored data key {key}"
            return False, msg, 400, None

    def store_results(
        self, measure: "Measure", player_name: Optional[str], results: Dict
    ) -> None:
        key = measure.store_as
        # initialize the store_as object if we need to
        if key not in self.stored_data:
            self.stored_data[key] = {}

        if player_name:  # i.e. not store_singleton
            if (
                player_name in self.stored_data[key]
                and results != self.stored_data[key][player_name]
            ):
                msg = (
                    f"Overwriting stored data key {key}[{player_name}]. Previous"
                    f" value:\n{self.stored_data[key][player_name]}"
                )
                logger.info(gudlog(msg, self))

            self.stored_data[key][player_name] = results

            msg = f"Stored {key}[{player_name}]:\n{results}"
            logger.debug(gudlog(msg, self))
        else:
            if self.stored_data[key] and results != self.stored_data[key]:
                msg = (
                    f"Overwriting stored data key {key}. Previous"
                    f" value:\n{self.stored_data[key]}"
                )
                logger.info(gudlog(msg, self))

            self.stored_data[key] = results

            msg = f"Stored {key}:\n{results}"
            logger.debug(gudlog(msg, self))

    # this should run in an executor, as it can block an arbitrarily long time
    async def play_the_player(self, measure: "Measure", player: "Player") -> bool:
        delay = measure.start_delay

        # handle special arguments like random numbers and stored values
        try:
            if isinstance(delay, str):
                delay = transform_arg(self, measure, player, delay)
                assert isinstance(delay, int)
            new_args = transform_args(self, measure, player, measure.args)
        except (AssertionError, KeyError, ValueError):
            msg = gudexc(
                "Failed to queue measure -- bad special argument(s)",
                self,
                player,
                measure,
            )
            logger.error(msg)
            measure.state = TaskState.FAILURE
            # we can't set measure.finished yet; other players for this measure may be running
            measure.status["all"]["all"] = f"{msg}; see logs for details"
            return False

        msg = f"queueing measure {measure.name} with a delay of {delay} seconds"
        logger.info(gudlog(msg, self, player))

        msg = f"(transformed) args:\n{pprint.pformat(new_args, indent=4)}"
        logger.debug(gudlog(msg, self, player, measure))

        success, err_msg, group_task = player.enqueue(
            self, measure, measure.task_name, delay, *new_args
        )
        if success:
            measure.celery_group_tasks[player.name] = group_task
        else:
            msg = f"failed to play measure {measure.name}: {err_msg}"
            logger.warning(gudlog(msg, self, player))
            measure.state = TaskState.FAILURE
            measure.status[player.name]["all"] = msg
            return False

    def validate_create_host_mappings(self) -> Tuple[bool, List[str]]:
        success = True
        err_msgs = []

        # map missing (to be created) hostnames to players
        if self.create_hosts:
            self.map_missing_hosts(get_host_names())

        for p in self.players.values():
            # skip local player
            if p.name == config.CONDUCTOR_ALLHOSTS_PLAYER_NAME:
                continue

            # validate hostnames length
            if p.hostnames == [] and not self.create_hosts:
                msg = f"{p.name}: no hosts mapped"
                if p.scale == 0 and config.ALLOW_EMPTY_PLAYER_HOSTS:
                    logger.debug(gudlog(msg, self))
                else:
                    success = False
                    logger.warning(gudlog(msg, self))
                    err_msgs.append(msg)
                    continue
            elif len(p.hostnames) != p.scale and not self.create_hosts:
                success = False
                msg = f"{p.name}: length of hosts does not match scale ({p.scale})"
                logger.warning(gudlog(msg, self))
                err_msgs.append(msg)
                continue

            for host_name in p.hostnames:
                if host_name not in hosts:
                    # create host obj
                    host_dict = {
                        "hostname": host_name,
                        "image": p.image,
                    }
                    try:
                        msg = (
                            f"Temporarily creating Host object for {host_name} with"
                            f" image {p.image}"
                        )
                        logger.debug(gudlog(msg, self, p))
                        host_obj = HostSchema().load(host_dict)
                    except MarshmallowValidationError:
                        success = False
                        msg = f"{host_name}: error creating Host object"
                        logger.warning(gudlog(msg, self, p))
                        err_msgs.append(msg)
                        continue
                elif hosts[host_name].get_image() not in [None, p.image]:
                    success = False
                    msg = (
                        f"{host_name}: Host object already exists with conflicting"
                        " image"
                    )
                    logger.warning(gudlog(msg, self, p))
                    err_msgs.append(msg)
                    continue
                elif hosts[host_name].tuning:
                    success = False
                    msg = (
                        f"host '{host_name}' is tuning or pending tuning; try again"
                        " soon"
                    )
                    logger.warning(gudlog(msg, self, p))
                    err_msgs.append(msg)
                    continue
                else:
                    host_obj = hosts[host_name]

                # make sure we have externally-accessible Redis if needed
                if host_obj.control_method not in config.HOST_CONTROL_EXTERNAL_REDIS:
                    success = False
                    msg = (
                        f"{host_name}: control_method '{host_obj.control_method}'"
                        " is not properly registered in"
                        " config.HOST_CONTROL_EXTERNAL_REDIS"
                    )
                    logger.warning(gudlog(msg, self, p))
                    err_msgs.append(msg)
                    continue
                elif (
                    not config.REDIS_HOST_EXTERNAL
                    and config.HOST_CONTROL_EXTERNAL_REDIS[host_obj.control_method]
                ):
                    success = False
                    msg = (
                        f"host ('{host_name}') requires externally-accessible Redis,"
                        " but environment variable REDIS_HOST_EXTERNAL is not"
                        " specified -- it is usually easiest to include this in the"
                        " conductor's"
                        f" '{config.SRC_ROOT.joinpath(config.ENV_FILE)}'"
                    )
                    logger.warning(gudlog(msg, self, p))
                    err_msgs.append(msg)
                    continue

                # get HostControl object for this Host
                if host_obj.control_method.upper() == "DOCKER":
                    host_control_class = DockerHostControl
                else:
                    host_control_class, msg = get_host_control_class(
                        host_obj.control_method
                    )
                    if not host_control_class:
                        success = False
                        msg = f"{host_name}: {msg}"
                        logger.warning(gudlog(msg, self, p))
                        err_msgs.append(msg)
                        continue

                # check if the host was recently confirmed to be turned on
                host_recently_confirmed_on = False
                if host_obj.last_confirmed_on:
                    check_age = (datetime.utcnow() - host_obj.last_confirmed_on).seconds
                    if check_age < config.HOST_CONFIRMED_ON_VALID_SECS:
                        host_recently_confirmed_on = True
                        msg = (
                            f"Host '{host_name}' with control_name"
                            f" '{host_obj.control_name}' confirmed to be on via"
                            f" {host_obj.control_method} {check_age}s ago; not checking"
                            " again"
                        )
                        logger.debug(msg)

                host_confirmed_on = False
                if not host_recently_confirmed_on:
                    control_name = host_obj.control_name or host_obj.name
                    host_confirmed_on = host_control_class.host_exists(control_name)
                    if host_confirmed_on:
                        host_obj.last_confirmed_on = datetime.utcnow()
                        msg = (
                            f"Host '{host_name}' with control_name"
                            f" '{host_obj.control_name}' appears to exist via"
                            f" {host_obj.control_method}"
                        )
                        logger.debug(msg)

                if host_recently_confirmed_on or host_confirmed_on:
                    if (
                        host_name not in hosts
                    ):  # we may have created host_obj above and not yet in config.hosts
                        hosts[host_name] = host_obj
                        msg = (
                            f"Added new Host object for {host_name} with image"
                            f" {p.image}"
                        )
                        logger.info(gudlog(msg, self, p))
                elif not self.create_hosts:
                    success = False
                    msg = (
                        f"host '{host_name}' with control_name"
                        f" '{host_obj.control_name}' not found via (possibly default)"
                        f" control method '{host_obj.control_method}'"
                    )
                    logger.warning(gudlog(msg, self, p))
                    err_msgs.append(msg)
                    continue
                else:
                    # mark hosts that need to be created
                    hosts[host_name].pending_create = True
                    msg = f"host {host_name} marked for creation"
                    logger.debug(gudlog(msg, self, p))

        return success, err_msgs
