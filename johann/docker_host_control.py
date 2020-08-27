# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import os
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import docker
from docker.errors import DockerException

from johann.host_control import HostControl
from johann.shared.config import JohannConfig
from johann.shared.logger import JohannLogger

if TYPE_CHECKING:
    from docker.models.containers import Container

    from johann.host import Host
    from johann.util import PathLikeObj


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger

api_client = docker.APIClient()
client = docker.from_env()


class DockerHostControl(HostControl):
    def __init__(self, host_copy: "Host"):
        super().__init__(host_copy)
        attempted_name = self.control_name or self.name
        self.container = self.get_container_from_name(attempted_name)
        if not self.container:
            raise Exception(
                f"{self.name}: Unable to find container for '{attempted_name}'"
            )

    @staticmethod
    def get_host_names() -> List[str]:
        ret = []
        for c in api_client.containers():
            for n in c["Names"]:
                ret.append(n)
        return ret

    @staticmethod
    def host_exists(name: str) -> bool:
        for c in api_client.containers():
            for n in c["Names"]:
                if name == n.replace("/", "", 1):
                    return True
        return False

    @staticmethod
    def get_container_from_name(name: str) -> Optional["Container"]:
        for c in api_client.containers():
            for n in c["Names"]:
                if name == n.replace("/", "", 1):
                    return client.containers.get(c["Id"])

        return None

    def put_archive(
        self,
        archive_path: "PathLikeObj",
        dest_path_inc_filename: "PathLikeObj",
        remove_archive_file: bool = True,
    ) -> bool:
        with open(archive_path, "rb") as f:
            archive_bytes = f.read()
            return self.container.put_archive(
                str(os.path.dirname(dest_path_inc_filename)), archive_bytes
            )

    def run_cmd(
        self,
        cmd: str,
        environment: Optional[Dict[str, str]] = None,
        detach: bool = False,
        privileged: bool = False,
        workpath: "PathLikeObj" = None,
        finish_timeout: int = -1,
        strip_output: bool = True,
    ) -> Tuple[Optional[int], Optional[str]]:
        # docker exec_run does not support a timeout
        if finish_timeout != -1 and not detach:
            logger.warning(
                f"{self.name}: run_cmd: docker exec_run does not support a timeout;"
                " ignoring..."
            )

        logger.debug(
            f"{self.name}: {cmd} (detach={detach}, privileged={privileged},"
            f" workpath={workpath}), environment={environment}"
        )

        kwargs = {"detach": detach, "privileged": privileged}
        if workpath:
            kwargs["workdir"] = str(workpath)
        if self.user:
            kwargs["user"] = self.user
        if environment:
            kwargs["environment"] = environment

        try:
            r = self.container.exec_run(cmd, **kwargs)

            # r.output, against documentation specs I might add, is type str when detach=True
            output = r.output.decode() if isinstance(r.output, bytes) else r.output

            if detach:
                return None, None
            else:
                if strip_output:
                    output = output.strip()
                return r.exit_code, output
        except DockerException:
            raise
