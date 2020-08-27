# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import asyncio
import traceback
from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional

import aiohttp
from natsort import natsorted

from johann import api_conductor as api
from johann.shared.config import JohannConfig
from johann.shared.logger import JohannLogger
from johann.util import johann_response

if TYPE_CHECKING:
    from aiohttp.web import Request, Response

config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


@aiohttp.web.middleware
async def error_middleware(
    request: "Request", handler: Callable[["Request"], Awaitable]
) -> "Response":
    try:
        response = await handler(request)
        return response
    except aiohttp.web.HTTPError as e:
        return johann_response(False, str(e), e.status_code)
    except Exception:
        msg = (
            "Error handling API request"
            f" ({handler.__name__}):\n{traceback.format_exc()}"
        )
        logger.error(msg)
        if config.DEBUG:
            return aiohttp.web.Response(body=msg, status=500)
        else:
            return johann_response(False, "internal error", 500)


def get_routes(path_substr: Optional[str] = None) -> List:
    ret = []

    for r in app.router.routes():
        ri = r.get_info()
        rip = None
        if "path" in list(ri.keys()):
            rip = ri["path"]
        elif "formatter" in list(ri.keys()):
            rip = ri["formatter"]

        if rip and (not path_substr or path_substr in rip) and rip not in ret:
            ret.append(rip)

    return natsorted(ret)


async def api_get_routes(request: "Request") -> "Response":
    if config.DEBUG:
        logger.debug(f"{request.url}")
    return johann_response(True, [], data=get_routes())


def init_conductor() -> None:
    # routes
    app.router.add_get("/", api_get_routes)
    app.router.add_get("/routes", api_get_routes)
    app.router.add_get("/codehash", api.api_get_codehash)
    app.router.add_routes(
        [
            aiohttp.web.get("/affrettando/{score_name}", api.affrettando),
            aiohttp.web.post("/affrettando/{score_name}", api.affrettando),
        ]
    )
    app.router.add_get("/read_score/{score_name}", api.api_read_score)
    app.router.add_get("/scores", api.get_scores)
    app.router.add_get("/scores/{score_name}", api.get_score)
    app.router.add_get("/scores/{score_name}/get_raw", api.get_score_raw)
    app.router.add_get("/scores/{score_name}/status", api.get_score_status)
    app.router.add_get("/scores/{score_name}/status_short", api.get_score_status_short)
    app.router.add_get("/scores/{score_name}/status_alt", api.get_score_status_alt)
    app.router.add_get("/scores/{score_name}/measures", api.get_score_measures)
    app.router.add_get("/scores/{score_name}/measures/{measure_name}", api.get_measure)
    app.router.add_get(
        "/scores/{score_name}/measures/{measure_name}/status", api.get_measure_status
    )
    app.router.add_get(
        "/scores/{score_name}/measures/{measure_name}/play", api.manually_play_measure
    )
    app.router.add_get("/scores/{score_name}/stored_data", api.retrieve_stored_data_all)
    app.router.add_get(
        "/scores/{score_name}/stored_data/{key}", api.retrieve_stored_data_1
    )
    app.router.add_get(
        "/scores/{score_name}/stored_data/{key}/{subkey}", api.retrieve_stored_data_2
    )
    app.router.add_get(
        "/scores/{score_name}/stored_data/{key}/{subkey}/{subsubkey}",
        api.retrieve_stored_data_3,
    )
    app.router.add_get(
        "/scores/{score_name}/stored_data/{key}/{subkey}/{subsubkey}/{subsubsubkey}",
        api.retrieve_stored_data_4,
    )
    app.router.add_routes(
        [
            aiohttp.web.get("/scores/{score_name}/roll_call", api.roll_call),
            aiohttp.web.post("/scores/{score_name}/roll_call", api.roll_call),
        ]
    )
    app.router.add_get("/scores/{score_name}/cue_the_music", api.cue_the_music)
    app.router.add_get("/hosts", api.get_hosts)
    app.router.add_get("/hosts/{host_name}", api.get_host)
    app.router.add_post("/add_hosts", api.add_hosts)
    # app.router.add_get('/create_player/{score_name}/{player_name}', api.api_create_player)

    logger.info("********** Reading Score Files **********")
    api.read_scores()

    logger.info("********** Adding Initial Hosts **********")
    api._update_hosts(config.INITIAL_HOSTS, allow_invalid=True)

    if config.HOSTS_FILE:
        logger.info("********** Reading Hosts File **********")
        api.read_hosts_file(config.HOSTS_FILE)

    logger.info("********** Starting Web Server **********")

    runner = aiohttp.web.AppRunner(app)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(runner.setup())
    site = aiohttp.web.TCPSite(
        runner, str(config.JOHANN_HOST.ip), config.CONDUCTOR_PORT
    )
    loop.run_until_complete(site.start())


app = aiohttp.web.Application(middlewares=[error_middleware])
