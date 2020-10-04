# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from uuid import uuid4

from celery import group, signature
from marshmallow import Schema, fields, post_load

from johann.shared.config import JohannConfig, hosts
from johann.shared.fields import NameField
from johann.shared.logger import JohannLogger
from johann.util import gudlog

if TYPE_CHECKING:
    from celery.canvas import Signature
    from celery.result import GroupResult

    from johann.host import Host
    from johann.measure import Measure
    from johann.score import Score


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


class PlayerSchema(Schema):
    class Meta:
        ordered = True

    name = NameField(required=True)
    image = fields.Str(allow_none=True, missing=None)
    hostnames = fields.List(
        fields.Str(), data_key=config.PLAYER_HOSTS_DUMP_KEY, missing=list
    )
    scale = fields.Integer(missing=-1)

    @post_load
    def make_player(self, data: Dict[str, Any], **kwargs: Any) -> "Player":
        if data["scale"] < 0:
            data["scale"] = max(1, len(data["hostnames"]))
        return Player(**data)


class Player(object):
    def __init__(self, name, image, hostnames, scale) -> None:
        self.name: str = name
        self.image: str = image
        self.hostnames: List[str] = hostnames
        self.scale: int = scale

    def __repr__(self) -> str:
        return "{}(name={},image={},hostnames={},scale={})".format(
            self.__class__.__name__,
            self.name,
            self.image,
            "|".join(self.hostnames),
            self.scale,
        )

    def dump(self) -> Dict[str, Any]:
        return PlayerSchema().dump(self)

    def dumps(self) -> str:
        return PlayerSchema().dumps(self)

    def copy_from(
        self, player: "Player", score: "Score"
    ) -> Tuple[Optional[bool], Optional[str]]:
        """

        Args:
            player:
            score:

        Returns:
            None if no changes
            True if changed successfully
            False if player is not a Player object

        """
        if not isinstance(player, Player):
            return False, "not a Player object"

        if player.name != self.name:
            logger.warning(
                "copy_from() called with a player with a different name...was this"
                " intentional?"
            )

        self.name = player.name
        changed = False

        if player.hostnames != self.hostnames:
            changed = True
            msg = "updating hostnames from {} to {}".format(
                self.hostnames, player.hostnames
            )
            logger.debug(gudlog(msg, score, self))
            self.hostnames = player.hostnames

        if player.scale != self.scale:
            changed = True
            msg = "updating scale from {} to {}".format(self.scale, player.scale)
            logger.debug(gudlog(msg, score, self))
            self.scale = player.scale

        if player.image != self.image:
            changed = True
            msg = "updating image from {} to {}".format(self.image, player.image)
            logger.debug(gudlog(msg, score, self))
            self.image = player.image

        if not changed:
            return None, None
        else:
            return True, None

    @staticmethod
    def get_local_task_signature(
        score_name: str,
        measure_name: str,
        host: "Host",
        func: str,
        delay: int,
        *args: Any,
    ) -> "Signature":
        description = f"{score_name}.LOCAL.{measure_name}.{host.name}"
        task_id = str(
            uuid4()
        )  # we need to know the task_id a priori for score.task_map
        sig_opts = {
            "queue": config.CELERY_QUEUE_ID,
            "shadow": description,
            "task_id": task_id,
            "countdown": delay,
        }

        sig = signature(
            func, args=args, kwargs={"target_host_dict": host.dump()}, options=sig_opts
        )
        msg = "task signature created for {}{} with options:\n{}".format(
            func, args, sig_opts
        )
        logger.log(5, f"{description}| {msg}")

        return sig

    def enqueue(
        self, score: "Score", measure: "Measure", func: str, delay: int, *args: Any
    ) -> Tuple[bool, Optional[str], Optional["GroupResult"]]:
        signatures = []

        for hostname in self.hostnames:
            if hostname not in hosts:
                msg = "{} not found in dictionary of hosts".format(hostname)
                logger.warning(gudlog(msg, score, self, measure.name))
                return False, msg, None
            host = hosts[hostname]

            if measure.local_measure:
                sig = Player.get_local_task_signature(
                    score.name, measure.name, host, func, delay, *args
                )
            else:
                if host.pending_create:
                    msg = "{} still pending creation".format(host.name)
                    logger.warning(gudlog(msg, score, self, measure.name))
                    return False, msg, None

                sig = host.get_task_signature(
                    score.name, self.name, measure.name, func, delay, *args
                )
                host.clear_finished_celery_task_ids()
                host.celery_task_ids.append(sig.id)

            if sig is None:
                msg = "task signature creation failed for hostname {}".format(host.name)
                logger.warning(gudlog(msg, score, self, measure.name))
                return False, msg, None

            score.task_map[sig.id] = {
                "measure_name": measure.name,
                "player_name": self.name,
                "host_name": host.name,
            }
            signatures.append(sig)

        task_group = group(signatures)

        group_result = task_group.apply_async()

        return True, None, group_result
