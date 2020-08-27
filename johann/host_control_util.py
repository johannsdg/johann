# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
from typing import TYPE_CHECKING, List, Optional, Tuple, Type

from johann.docker_host_control import DockerHostControl
from johann.shared.config import JohannConfig
from johann.shared.logger import JohannLogger
from johann.util import get_attr

if TYPE_CHECKING:
    from johann.host_control import HostControl


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


def get_host_control_class(
    control_method: str,
) -> Tuple[Optional[Type["HostControl"]], Optional[str]]:
    if control_method.upper() == "DOCKER":
        return DockerHostControl, None
    elif control_method not in config.HOST_CONTROL_CLASS_NAMES:
        msg = f"unrecognized control method '{control_method}'"
        return None, msg

    host_control_class, msg = get_attr(config.HOST_CONTROL_CLASS_NAMES[control_method])
    if not host_control_class:
        msg = f"failed to find class for control method '{control_method}'"
        return None, msg

    return host_control_class, None


def get_host_names(control_methods: Optional[List[str]] = None,) -> List[str]:
    if not control_methods:
        control_methods = config.HOST_CONTROL_CLASS_NAMES.keys()
    ret = []
    for cm in control_methods:
        cm_class, msg = get_host_control_class(cm)
        if not cm_class:
            logger.error(msg)
            continue
        try:
            cm_hosts = cm_class.get_host_names()
        except AttributeError as e:
            logger.error(f"{cm_class}: {str(e)}")
        else:
            ret += cm_hosts
            logger.debug(f"Found {len(cm_hosts)} hosts via {cm}")

    return ret
