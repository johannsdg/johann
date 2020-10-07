# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import asyncio
import json
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import marshmallow
import ruamel.yaml
from ruamel.yaml.error import YAMLError, YAMLFutureWarning, YAMLWarning

from johann import util
from johann.host import HostSchema
from johann.player import PlayerSchema
from johann.score import ScoreSchema
from johann.shared.config import JohannConfig, hosts, scores
from johann.shared.logger import JohannLogger

if TYPE_CHECKING:
    from aiohttp.web import Request, Response

    from johann.host import Host
    from johann.measure import Measure
    from johann.player import Player
    from johann.score import Score


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger

yaml = ruamel.yaml.YAML(typ="safe")
yaml.default_flow_style = False


# roll_call and cue_the_music in one call
async def affrettando(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    r = await roll_call(request)
    if r.status >= 300:
        return r

    r = await cue_the_music(request)
    return r


def read_scores() -> None:
    for package_name, score_names in util.get_score_resources("scores").items():
        for s in score_names:
            if not config.TESTING and s.startswith("test"):
                msg = f"Skipping '{s}' from package {package_name}"
                logger.info(msg)
                continue

            read_score(s, package_name)


# read a 'score' (yaml file describing experiment/scenario) from python package resources
def read_score(
    score_name: str,
    package_name: str = None,
    score_dir: str = "scores",
) -> Tuple[Optional[Dict[str, Any]], Optional[str], int]:
    if package_name in config.PLUGINS_EXCLUDE:
        msg = f"Skipping {score_name} due to package '{package_name}' exclusion"
        logger.info(msg)
        return None, msg, 400

    try:
        # util._validate_score_name_dir
        score_str = util.get_score_str(score_name, package_name, score_dir)
        if not score_str:
            return None, "score not found", 404

        score_dict = yaml.load(score_str)
        score: "Score" = ScoreSchema().load(score_dict)
        score.package = package_name
    except (YAMLError, YAMLWarning, YAMLFutureWarning) as e:
        msg = f"score '{score_name}' YAML failed to parse:\n{str(e)}"
        logger.warning(msg)
        return None, msg, 400
    except marshmallow.ValidationError as e:
        msg = f"score '{score_name}' failed validation:\n{str(e)}"
        logger.warning(msg)
        return None, msg, 400
    except OSError as e:
        msg = f"failed to read score '{score_name}': {e.strerror}"
        logger.warning(msg)
        return None, msg, 500
    except Exception as e:
        logger.exception(e)
        msg = f"unexpected error reading score '{score_name}'; see logs for details"
        logger.warning(msg)
        return None, msg, 500

    scores[score.name] = score
    logger.debug(
        f"read score '{score_name}'{' from package {package_name}' if package_name else ''}"
    )

    return score_dict, None, 200


async def api_read_score(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]

    # check for force
    params = request.rel_url.query
    if "force" in params:
        if score_name not in scores:
            logger.warning("Force resetting a score that isn't loaded")
        else:
            del scores[score_name]

    score_dict, err_msg, status_code = read_score(score_name)

    if score_dict:
        return util.johann_response(True, [], data=score_dict, status_code=status_code)
    else:
        return util.johann_response(False, err_msg, status_code=status_code)


async def get_scores(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")

    ret = {}

    for name, score in scores.items():
        if score.category not in ret:
            ret[score.category] = []
        ret[score.category].append({"name": name, "description": score.description})
        ret[score.category].sort(key=lambda t: t["name"])

    ret = OrderedDict(sorted(ret.items(), key=lambda t: t[0]))  # sort by category
    return util.johann_response(True, [], data=ret)


async def get_hosts(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    ret = {}

    for name, host in hosts.items():
        ret[name] = {"name": host.name, "image": host.get_image()}

    return util.johann_response(True, [], data=ret)


async def get_score(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    if score_name not in scores:
        return util.johann_response(False, f"unrecognized score '{score_name}'", 404)

    score = scores[score_name]
    return util.johann_response(True, [], data=score.dump())


async def get_host(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    host_name = request.match_info["host_name"]
    if host_name not in hosts:
        return util.johann_response(False, f"unrecognized host '{host_name}'", 404)

    host = hosts[host_name]
    return util.johann_response(True, [], data=host.dump())


# read json file providing hosts to seed Johann with (vice adding via /add_hosts or via the GUI)
def read_hosts_file(file_path: Path) -> Tuple[bool, List[str]]:
    file_path: Path = Path(util.safe_name(str(file_path)))
    if not file_path.is_file():
        msg = f"Provided hosts file does not exist: '{file_path}'"
        logger.warning(msg)
        return False, [msg]

    try:
        with file_path.open() as infile:
            hosts_obj = json.load(infile)
            if type(hosts_obj) is not dict or "hosts" not in hosts_obj:
                msg = f"invalid format for hosts file '{file_path}'"
                logger.warning(msg)
                return False, [msg]

            success, err_msgs, successful_hostnames = _update_hosts(
                hosts_obj["hosts"], allow_invalid=True
            )
            if success:
                logger.debug(
                    f"Hosts successfully added/updated from '{file_path}':"
                    f" {successful_hostnames}"
                )
            return success, err_msgs
    except json.JSONDecodeError as e:
        msg = f"invalid JSON in hosts file '{file_path}'"
        logger.warning(e)
        return False, [msg]
    except AttributeError as e:
        msg = "unable to parse hosts file '{}': {}".format(file_path, str(e))
        logger.warning(msg)
        return False, [msg]
    except marshmallow.ValidationError as e:
        msg = "one or more hosts in '{}' failed validation:\n{}".format(
            file_path, str(e)
        )
        logger.warning(msg)
        return False, [msg]
    except Exception as e:
        logger.exception(e)
        msg = "unexpectedly failed to read hosts file '{}'".format(file_path)
        logger.exception(msg)
        return False, [msg]


# assumes some validation has been done prior
# should be called within try/except
def _update_hosts(
    hosts_dict: Dict[str, Any], allow_invalid: bool = False
) -> Tuple[bool, Optional[List[str]], List[str]]:
    valid_hosts = []
    err_msgs = []
    success = True
    for h_name, h_data in hosts_dict.items():
        if "hostname" not in h_data:
            h_data["hostname"] = h_name
        try:
            h: "Host" = HostSchema().load(h_data)
            valid_hosts.append(h)
        except marshmallow.ValidationError as e:
            msg = f"invalid host data provided ({h_name}): {str(e)}"
            logger.warning(msg)
            err_msgs.append(msg)
            success = False

    # if any failures, bail before modifying Hosts
    if not success and not allow_invalid:
        return False, err_msgs, []

    to_remove = []
    for h in valid_hosts:
        if h.name in hosts:
            extant_host: Host = hosts[h.name]
            success, err_msg = extant_host.copy_from(h)
            if success:
                logger.debug(f"updated host '{h.name}' to:\n{extant_host.dump()}")
            else:
                msg = f"failed to update host '{h.name}': {err_msg}"
                logger.warning(msg)
                err_msgs.append(msg)
                to_remove.append(h)
                success = False
        else:
            hosts[h.name] = h
            logger.debug(f"added host '{h.name}'")

    for h in to_remove:
        valid_hosts.remove(h)

    return success, err_msgs, [h.name for h in valid_hosts]


async def add_hosts(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    try:
        data = await request.json()
    except json.JSONDecodeError as e:
        msg = "add_hosts: invalid json"
        logger.warning(f"{msg}\n{str(e)}")
        return util.johann_response(False, msg, 400)

    logger.debug("add_hosts endpoint called")
    if "hosts" in data and isinstance(data["hosts"], dict):
        success, err_msgs, successful_hostnames = _update_hosts(
            data["hosts"], allow_invalid=False
        )

        if not success:
            return util.johann_response(False, err_msgs, 400)
        else:
            return util.johann_response(True, [], data=successful_hostnames)
    else:
        msg = "invalid format for key 'hosts'"
        logger.warning(msg)
        return util.johann_response(False, msg, 400)


async def get_score_raw(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    if score_name not in scores:
        return util.johann_response(False, f"unrecognized score '{score_name}'", 404)

    score = scores[score_name]
    return util.johann_response(
        True, [], data=score.dump(exclude_local=False, yaml_fields_only=False)
    )


async def get_score_status(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    if score_name not in scores:
        return util.johann_response(False, f"unrecognized score '{score_name}'", 404)

    score = scores[score_name]
    return util.johann_response(True, [], data=score.get_status())


async def get_score_status_short(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    if score_name not in scores:
        return util.johann_response(False, f"unrecognized score '{score_name}'", 404)

    score = scores[score_name]
    return util.johann_response(True, [], data=score.get_status(short=True))


async def get_score_status_alt(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    if score_name not in scores:
        return util.johann_response(False, f"unrecognized score '{score_name}'", 404)

    score = scores[score_name]
    status = score.get_status(short=False)

    ret = {}
    measures = {}
    current = 0
    totals = score.get_host_totals()
    total = totals["total"]
    fails = 0
    for m_name, m_status in status["measures"].items():
        measure_ret = {}
        m_current = 0
        m_fails = 0
        for p_status in m_status["task_status"].values():
            m_fails += p_status["failed_count"]
            m_current += p_status["meta"]["current"]
        measure_ret["total"] = totals[m_name]["total"]
        measure_ret["current"] = m_current
        measure_ret["failed_count"] = m_fails
        current += m_current
        fails += m_fails
        measure_ret["state"] = m_status["state"]
        measures[m_name] = measure_ret

    ret["current"] = current
    ret["total"] = total
    ret["failed_count"] = fails
    ret["state"] = status["state"]
    ret["status"] = ret["state"]
    ret["measures"] = measures
    ret["raw"] = status
    return util.johann_response(True, [], data=ret)


async def get_score_measures(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    if score_name not in scores:
        return util.johann_response(False, f"unrecognized score '{score_name}'", 404)

    score = scores[score_name]
    return util.johann_response(True, [], data=[x.name for x in score.measures])


def _get_measure_helper(
    request: "Request",
) -> Tuple[Optional["Measure"], Optional[str]]:
    score_name = request.match_info["score_name"]
    measure_name = request.match_info["measure_name"]
    if score_name not in scores:
        return None, f"unrecognized score '{score_name}'"

    score = scores[score_name]
    measure_names = [x.name for x in score.measures]
    if measure_name not in measure_names:
        return None, f"unrecognized measure '{measure_name}'"

    measure = [x for x in score.measures if x.name == measure_name][0]
    return measure, None


async def get_measure(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    measure, msg = _get_measure_helper(request)
    if not measure:
        return util.johann_response(False, msg, 404)

    return util.johann_response(True, [], data=measure.dump())


async def get_measure_status(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    measure, msg = _get_measure_helper(request)
    if not measure:
        return util.johann_response(False, msg, 404)

    return util.johann_response(True, [], data=measure.get_status())


async def manually_play_measure(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    measure_name = request.match_info["measure_name"]
    if score_name not in scores:
        return util.johann_response(False, f"unrecognized score '{score_name}'", 404)

    score = scores[score_name]
    measure_names = [x.name for x in score.measures]
    if measure_name not in measure_names:
        return util.johann_response(
            False, f"unrecognized measure '{measure_name}'", 404
        )

    measure = [x for x in score.measures if x.name == measure_name][0]
    m = measure

    if m.started():
        if (
            "force" in request.rel_url.query
            and request.rel_url.query["force"].lower() != "false"
        ):
            msg = f"Forcing re-play of measure '{m.name}'"
            logger.warning(msg)
        else:
            return util.johann_response(
                False,
                "measure already played/playing; to run anyway, include query param"
                " 'force=true'",
                400,
            )
    else:
        msg = f"Manually playing measure '{m.name}'"
        logger.info(msg)

    score.queue_measure(m)
    return util.johann_response(True, msg)


async def roll_call(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    if score_name not in scores:
        return util.johann_response(False, f"unrecognized score '{score_name}'", 404)

    logger.debug(f"{request.method} roll_call for score '{score_name}'")
    score = scores[score_name]

    # make sure score isn't already running (e.g. by another user)
    if score.started_at and not score.finished:
        return util.johann_response(False, "score already playing", 400)

    # update score/players if changed
    if request.method == "POST":
        data = await request.json()
        score.create_hosts = data["create_hosts"]  # PLANNED: REMOVE
        score.discard_hosts = data["discard_hosts"]  # PLANNED: REMOVE

        if "players" in data and isinstance(data["players"], dict):
            posted_players = {}
            for p_name, p_data in data["players"].items():
                try:
                    p: "Player" = PlayerSchema().load(p_data)
                except marshmallow.ValidationError as e:
                    msg = f"invalid player data provided ({p_name}): {str(e)}"
                    logger.warning(f"roll_call: {msg}")
                    return util.johann_response(False, msg, 400)

                posted_players[p.name] = p
                if p.name in score.players:
                    score_player = score.players[p.name]
                    success, err_msg = score_player.copy_from(p, score)
                    if success is None:
                        pass  # no changes required
                    elif success:
                        logger.debug(
                            f"roll_call: updated player '{p.name}'"
                            f" to:\n{score_player.dump()}"
                        )
                    else:
                        msg = f"failed to update player '{p.name}': {err_msg}"
                        logger.warning(f"roll_call: {msg}")
                        return util.johann_response(False, msg, 400)
                else:
                    msg = f"unrecognized player: '{p.name}'"
                    logger.warning(f"roll_call: {msg}")
                    return util.johann_response(False, msg, 400)
        else:
            logger.warning(
                "roll_call: POST missing or invalid format for key 'players'"
            )

    # actual roll call
    success, err_msgs = score.validate_create_host_mappings()
    if not success:
        logger.warning(f"{score_name}: errors validating host mappings:\n{err_msgs}")
        return util.johann_response(False, err_msgs, 400)
    else:
        score.last_successful_roll_call = datetime.utcnow()
        return util.johann_response(
            True, "roll_call successful; you are now free to cue the music"
        )


async def cue_the_music(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    if score_name not in scores:
        return util.johann_response(False, f"unrecognized score '{score_name}'", 404)
    score = scores[score_name]

    # make sure score isn't already running (e.g. by another user)
    if score.started_at and not score.finished:
        return util.johann_response(False, "score already playing", 400)

    # make sure each Player in the Score is matched to actual entities/containers
    success, err_msgs = score.validate_create_host_mappings()
    if not success:
        logger.warning(
            f"{score.name}: tuning failed; errors validating host mappings:\n{err_msgs}"
        )
        return util.johann_response(False, err_msgs, 400)

    task = asyncio.ensure_future(score.play())
    task = util.wrap_future(task, score.name, None, score_or_measure=score)
    asyncio.ensure_future(task)

    return util.johann_response(True, "score is playing")


async def retrieve_stored_data_all(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    return await _retrieve_stored_data(score_name)


async def retrieve_stored_data_1(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    key = request.match_info["key"]
    return await _retrieve_stored_data(score_name, key)


async def retrieve_stored_data_2(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    key = request.match_info["key"]
    subkey = request.match_info["subkey"]
    return await _retrieve_stored_data(score_name, key, subkey)


async def retrieve_stored_data_3(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    key = request.match_info["key"]
    subkey = request.match_info["subkey"]
    subsubkey = request.match_info["subsubkey"]
    return await _retrieve_stored_data(score_name, key, subkey, subsubkey)


async def retrieve_stored_data_4(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    score_name = request.match_info["score_name"]
    key = request.match_info["key"]
    subkey = request.match_info["subkey"]
    subsubkey = request.match_info["subsubkey"]
    subsubsubkey = request.match_info["subsubsubkey"]

    # convenience conversion of 'None' or 'none' keys to actual None
    # this is used in tasks_main.py for lazy/J.I.T. fetching
    if key.lower() == "none":
        key = None
    if subkey.lower() == "none":
        subkey = None
    if subsubkey.lower() == "none":
        subsubkey = None
    if subsubsubkey.lower() == "none":
        subsubsubkey = None

    return await _retrieve_stored_data(score_name, key, subkey, subsubkey, subsubsubkey)


async def _retrieve_stored_data(
    score_name: str,
    key: Optional[str] = None,
    subkey: Optional[str] = None,
    subsubkey: Optional[str] = None,
    subsubsubkey: Optional[str] = None,
) -> "Response":
    if score_name not in scores:
        return util.johann_response(False, f"unrecognized score '{score_name}'", 404)
    score = scores[score_name]

    success, msg, code, data = score.fetch_stored_data(
        key, subkey, subsubkey, subsubsubkey
    )
    logger.debug(f"retrieve_stored_data API call: {msg}")
    return util.johann_response(success, msg, code, data=data)


async def api_get_codehash(request: "Request") -> "Response":
    if config.TRACE:
        logger.debug(f"{request.url}")
    return util.johann_response(True, [], data=util.get_codehash())
