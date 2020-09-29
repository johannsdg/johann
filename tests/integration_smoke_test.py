# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import json
import os
import time

import pytest
import requests

from johann.shared.config import JohannConfig
from johann.shared.enums import TaskState
from johann.shared.logger import JohannLogger

CONDUCTOR_URL = os.getenv("CONDUCTOR_URL", "http://johann_conductor:5000")


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


@pytest.fixture(autouse=True)
# fix an annoying artifact of pytest's otherwise useful '-s' mode
def prettier_output():
    print()
    yield
    print()


def wait_for_score(score, expect_success=True, timeout=300, check_interval=5):
    logger.info(f"Waiting for {score} to complete...")

    now = time.time()
    end = now + timeout
    last_state = None

    while now < end:
        r = requests.get(f"{CONDUCTOR_URL}/scores/{score}/status")
        rjson = r.json()
        finished = rjson["data"]["finished"]
        state = rjson["data"]["state"]

        if state != last_state:
            logger.debug(f"Score {score} changed to state {state}")

        if finished:
            logger.info(f"Score {score} finished with state {state}")

            if state != TaskState.SUCCESS and expect_success:
                logger.info(f"Full status:\n{json.dumps(rjson['data'], indent=2)}")

            return state
        else:
            last_state = state
            time.sleep(check_interval)
            now = time.time()

    raise TimeoutError


def launch_score(score, reset=True):
    # check if the score has already been launched
    url = f"{CONDUCTOR_URL}/scores/{score}/status_short"
    r = requests.get(url)
    assert r.status_code == 200
    assert r.ok
    rjson = r.json()
    if rjson["data"]["finished"] is True:
        if not reset:
            raise Exception(
                "f{score} already finished but reset=False in launch_score()"
            )

        logger.info(f"{score} already finished; resetting and running again...")
        url = f"{CONDUCTOR_URL}/read_score/{score}?force=1"
        r = requests.get(url)
        assert r.ok

    # launch the score
    url = f"{CONDUCTOR_URL}/affrettando/{score}"
    params = {
        "create_hosts": False,
        "discard_hosts": False,
    }
    r = requests.post(url, json=params)
    assert r.ok
    result = wait_for_score(score)
    assert result == TaskState.SUCCESS


def test_johann_pushing():
    launch_score("test_johann_pushing")
