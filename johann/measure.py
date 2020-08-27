# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import json
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from marshmallow import Schema
from marshmallow import ValidationError as MarshmallowValidationError
from marshmallow import fields, post_load, validates

from johann.shared.config import JohannConfig
from johann.shared.logger import JohannLogger
from johann.util import (
    NameField,
    StateField,
    TaskState,
    celery_group_status,
    get_attr,
    gudlog,
    parse_special_arg,
    task_state_priority,
)

if TYPE_CHECKING:
    from celery.result import GroupResult

    from johann.score import Score


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


class MeasureSchema(Schema):
    class Meta:
        ordered = True

    name = NameField(required=True)
    player_names = fields.List(fields.Str(), required=True, data_key="players")
    task_name = fields.Str(required=True, data_key="task")
    args = fields.List(fields.Raw(allow_none=True), missing=list)
    store_as = fields.Str(allow_none=True, missing=None)
    store_singleton = fields.Boolean(missing=False)
    store_interim_results = fields.Boolean(missing=False)
    lazy_fetch_stored = fields.Boolean(missing=False)

    start_delay = fields.Raw(allow_none=True, missing=None)  # must be integer or string
    depends_on = fields.List(fields.Str(), missing=list)
    dependency_proof = fields.Boolean(missing=False)

    # do not remove any of these three without careful consideration
    state = StateField(dump_only=True)
    status = fields.Dict(
        keys=fields.Str(),  # player
        values=fields.Dict(
            keys=fields.Str(), values=fields.Str(),  # host, host status
        ),
        dump_only=True,
    )
    finished = fields.Boolean(dump_only=True)

    celery_group_tasks = fields.Method(
        "get_celery_group_tasks_serializable", dump_only=True
    )
    local_measure = fields.Boolean(dump_only=True)

    @validates("task_name")
    def validate_task_name(self, value):
        attr, msg = get_attr(value)
        if not attr:
            raise MarshmallowValidationError(msg)

    @validates("start_delay")
    def validate_start_delay(self, value):
        msg = "start_delay must be an integer or a special argument string"
        if isinstance(value, str):
            try:
                parse_special_arg(value)
            except ValueError:
                raise MarshmallowValidationError(msg)
        elif value is not None and not isinstance(value, int):
            raise MarshmallowValidationError(msg)

    def get_celery_group_tasks_serializable(self, obj: "Measure"):
        return obj.get_task_status(
            short=True
        )  # short excludes task results, which may not be serializable

    def validate_start_params(self, measure: "Measure"):
        if measure.start_delay is None and measure.depends_on == []:
            raise MarshmallowValidationError(
                "Input data must specify one or both of 'start_delay' and 'depends_on'"
            )

    def validate_store_params(self, measure: "Measure"):
        if measure.store_interim_results and not measure.store_as:
            raise MarshmallowValidationError(
                "'store_as' must be specified if 'store_interim_results' is True"
            )
        if measure.store_singleton:
            if len(measure.player_names) > 1:
                logger.warning(
                    "'store_singleton' with more than one player will likely cause data"
                    " loss"
                )
            if not measure.store_as:
                raise MarshmallowValidationError(
                    "'store_as' must be specified if 'store_singleton' is True"
                )

    def cant_depend_on_yourself(self, measure: "Measure"):
        for depname in measure.depends_on:
            if depname == measure.name:
                raise MarshmallowValidationError("You cannot depend on yourself")

    @post_load(pass_original=True)
    def make_measure(
        self, data: Dict[str, Any], original_data: Dict[str, Any], **kwargs: Any
    ) -> "Measure":
        measure = Measure(**data, original_data=original_data)
        self.validate_start_params(measure)
        self.validate_store_params(measure)
        self.cant_depend_on_yourself(measure)
        return measure


class Measure(object):
    def __init__(
        self,
        name,
        player_names,
        task_name,
        args,
        store_as,
        store_singleton,
        store_interim_results,
        lazy_fetch_stored,
        start_delay,
        depends_on,
        dependency_proof,
        original_data=None,
    ) -> None:
        self.name: str = name
        self.player_names: List[str] = player_names
        self.task_name: str = task_name
        self.args: List = args
        self.store_as: str = store_as
        self.store_singleton: bool = store_singleton
        self.store_interim_results: bool = store_interim_results
        self.lazy_fetch_stored: bool = lazy_fetch_stored
        self.start_delay: int = start_delay
        self.depends_on: List[str] = depends_on
        self.dependency_proof: bool = dependency_proof
        self.original_data: Dict[str, Any] = original_data

        self.state: TaskState = TaskState.PENDING
        self.status: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.finished: bool = False
        self.celery_group_tasks: Dict[str, "GroupResult"] = {}  # keys are Player names
        self.local_measure: bool = False  # whether or not run locally by conductor

    def __repr__(self) -> str:
        format_str = (
            f"{self.__class__.__name__}(name={self.name},"
            f"player_names={'|'.join(self.player_names)},task_name={self.task_name},"
            f"start_delay={self.start_delay},depends_on={'|'.join(self.depends_on)},"
            f"dependency_proof={self.dependency_proof})"
        )
        return format_str

    def dump(self) -> Dict[str, Any]:
        return MeasureSchema().dump(self)

    def dumps(self) -> str:
        return MeasureSchema().dumps(self)

    def store_results(
        self, score: "Score", task_status: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not self.store_as:
            return None

        results = {}
        for player_name, tstat in task_status.items():
            results_p = {}
            for t_id, t in tstat["tasks"].items():
                t_result = None

                if "result" in t:
                    t_result = t["result"]
                elif (
                    self.store_interim_results
                    and "meta" in t
                    and t["meta"]
                    and "interim_result" in t["meta"]
                ):
                    t_result = t["meta"]["interim_result"]

                if t_result is not None:
                    if self.store_singleton:
                        score.store_results(self, None, t_result)

                    if t_id not in score.task_map:
                        msg = (
                            f"task {t_id} not found in task_map. This is probably bad."
                        )
                        logger.info(gudlog(msg, score, player_name, self))
                        results_p[t_id] = t_result
                    else:
                        results_p[score.task_map[t_id]["host_name"]] = t_result

            results[player_name] = results_p

            if results_p and not self.store_singleton:
                score.store_results(self, player_name, results_p)

        return results

    def evaluate_state(self, score: "Score") -> None:
        task_status = self.get_task_status(short=False)

        prior_state = self.state

        success = 0
        finished = 0
        for player_name, tstat in task_status.items():
            if tstat["finished"]:
                finished += 1
            if tstat["state"] == TaskState.SUCCESS:
                success += 1

            if task_state_priority(tstat["state"]) > task_state_priority(self.state):
                self.state = tstat["state"]

            if tstat["status"]:
                self.status[player_name] = tstat["status"]

        if self.state == TaskState.FAILURE and finished == len(task_status):
            self.finished = True

        if success == len(self.player_names):
            self.state = TaskState.SUCCESS
        if finished == len(self.player_names):
            self.finished = True

        if self.state == TaskState.FAILURE and self.state != prior_state:
            msg = f"measure '{self.name}' failed:\n{json.dumps(self.status, indent=2)}"
            logger.warning(gudlog(msg, score))

        self.store_results(score, task_status)

    def started(self) -> bool:
        if self.state not in [TaskState.PENDING]:
            return True
        else:
            return False

    def get_status(self, short: bool = False) -> Dict[str, Any]:
        status = {}
        if self.start_delay is not None:
            status["start_delay"] = self.start_delay
        if self.depends_on is not []:
            status["depends_on"] = self.depends_on
        status["dependency_proof"] = self.dependency_proof
        status["state"] = self.state
        if self.status or not short:
            status["status"] = self.status
        status["finished"] = self.finished
        status["task_status"] = self.get_task_status(short=short)
        status["local_measure"] = self.local_measure

        return status

    def get_task_status(self, short: bool = False) -> Dict[str, Any]:
        status = {}

        for player_name, group_task in self.celery_group_tasks.items():
            status[player_name] = celery_group_status(group_task, short=short)

        return status
