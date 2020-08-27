# Copyright (c) 2019-present, The Johann Authors. All Rights Reserved.
# Use of this source code is governed by a BSD-3-clause license that can
# be found in the LICENSE file. See the AUTHORS file for names of contributors.
import hashlib
import os
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from johann.shared.config import JohannConfig
from johann.shared.logger import JohannLogger
from johann.util import (
    HostOS,
    PmtrVariant,
    create_johann_tarball,
    get_codehash,
    py_to_clistr,
)

if TYPE_CHECKING:
    from johann.host import Host
    from johann.util import PathLikeObj


config = JohannConfig.get_config()
logger = JohannLogger(__name__).logger


class HostControl(ABC):
    @abstractmethod
    def __init__(self, host_copy: "Host"):
        self.control_method = host_copy.control_method
        self.control_name = host_copy.control_name
        self.name = host_copy.name
        self.os = host_copy.os
        self.pip_offline_install = host_copy.pip_offline_install
        self.pmtr_variant = host_copy.pmtr_variant
        self.pwd_env = host_copy.pwd_env
        self.python_path = host_copy.python_path
        self.python_ver = host_copy.python_ver
        self.user = host_copy.user

    @staticmethod
    @abstractmethod
    def get_host_names() -> List[str]:
        pass

    @staticmethod
    @abstractmethod
    def host_exists(host_name: str) -> bool:
        pass

    @abstractmethod
    def put_archive(
        self,
        archive_path: "PathLikeObj",
        dest_path_inc_filename: "PathLikeObj",
        remove_archive_file: bool = True,
    ) -> bool:
        pass

    @abstractmethod
    def run_cmd(
        self,
        cmd: str,
        environment: Optional[Dict[str, str]] = None,
        detach: bool = False,
        privileged: bool = False,
        workpath: Optional["PathLikeObj"] = None,
        finish_timeout: int = -1,
        strip_output: bool = True,
    ) -> Tuple[Optional[int], Optional[str]]:
        """

        Args:
            cmd:
            environment:
            detach:
            privileged:
            workpath:
            finish_timeout:
            strip_output:

        Returns:
            Tuple of:
                exit code or None
                command output or None

        """
        pass

    def get_python_version(self) -> Tuple[bool, Optional[str]]:
        cmd = (
            f"/bin/sh -ec \"{self.python_path} --version | cut -d ' ' -f2 | cut -d."
            ' -f1-2"'
        )
        exit_code, output = self.run_cmd(cmd)
        if not output:
            return False, None
        else:
            return exit_code == 0, output

    def get_os(self) -> Tuple[bool, Optional[str]]:
        cmd = (
            f'{self.python_path} -c "'
            f"""exec(\\"import platform\\nprint(platform.system())\\")\""""
        )
        exit_code, output = self.run_cmd(cmd)
        if output:
            output = output.upper()

        if not exit_code:
            return True, output
        else:
            return False, output

    # returns None on success, or error str on failure
    def install_pip_dependencies(
        self, workpath: "PathLikeObj", python_verstr: str
    ) -> Tuple[bool, Optional[str]]:
        cmd = f'/bin/sh -ec "{self.python_path} -m pip install'
        if self.user:
            cmd += " --user"
        if self.pip_offline_install:
            # offline install
            logger.debug(f"{self.name}: Using offline pip install")
            # cmd += f" --no-index -f minirepo -r {workpath}/requirements.{python_verstr}.txt"
            cmd += (
                f" --no-index -f minirepo -r {str(workpath)}/requirements_base.txt"
                f" -r {str(workpath)}/requirements_plugins.txt"
            )
        else:
            # online install:
            logger.debug(f"{self.name}: Using online pip install")
            # cmd += f" -r {workpath}/requirements.{python_verstr}.txt --retries=2 --timeout=5"
            cmd += (
                f" --retries=2 --timeout=5 -r {str(workpath)}/requirements_base.txt"
                f" -r {str(workpath)}/requirements_plugins.txt"
            )
        cmd += ' --disable-pip-version-check"'

        exit_code, output = self.run_cmd(cmd, workpath=workpath)
        if exit_code:
            errmsg = f"possible issues installing pip requirements: {output}"
            return False, errmsg

        return True, None

    def johann_control(
        self, disable: bool, workpath: "PathLikeObj"
    ) -> Tuple[bool, Optional[str]]:
        environment = None
        if self.pmtr_variant == PmtrVariant.DEVUDP:
            cmd = (
                f"/bin/bash -c \"echo -n '{'disable' if disable else 'enable'}"
                " johann_player' > /dev/udp/127.0.0.1/31337\""
            )
            detach = False
        elif self.pmtr_variant == PmtrVariant.NONE:
            if disable:
                return self.python_pkill("johann_main.py")
            else:
                # cmd = f"CELERY_QUEUE_ID={self.name} {self.python_path} johann_main.py"
                cmd = f"{self.python_path} johann_main.py"
                environment = {
                    "CELERY_DETACH": "True",
                    "PYTHONPATH": config.DEPLOY_PYTHONPATH_POSIX,
                }
                detach = True
        else:
            print(self.pmtr_variant == PmtrVariant.NONE)
            raise NotImplementedError
        exit_code, output = self.run_cmd(
            cmd, environment=environment, detach=detach, workpath=workpath
        )  # TODO make sure environment in all subclass implementations
        if not detach and exit_code:
            errmsg = (
                f"possible issues {'disabling' if disable else 'enabling'} Johann:"
                f" {output}"
            )
            logger.debug(f"{self.name}: {errmsg}")
            return False, errmsg
        return True, ""

    def python_pgrep(
        self, program_str: str, require_output=False
    ) -> Tuple[Optional[bool], Optional[str]]:
        scriptstr = f"""import os
import sys
try:
    import psutil
except ImportError:
    print('psutil not installed yet')
    sys.exit(2)

procs = []
for proc in psutil.process_iter():
    cmd = ' '.join(proc.cmdline())
    if '{program_str}' in cmd and not 'IGNORE_THIS_PROGRAM' in cmd:
        procs.append(proc)

this_proc = [proc for proc in procs if proc.pid == os.getpid()]
for proc in this_proc:
    procs.remove(proc)

if len(procs) > 0:
    for proc in procs:
        print(str(proc.pid) + ': ' + ' '.join(proc.cmdline()))
    sys.exit(0)
else:
    print("no matching processes found")
    sys.exit(1)"""
        cmd = f"{self.python_path} -c {py_to_clistr(scriptstr)}"
        exit_code, output = self.run_cmd(cmd)
        logger.debug(f"{self.name}: pgrep for {program_str}: {output}")

        if exit_code == 2:
            return None, "psutil not installed yet"
        else:
            return exit_code == 0, output

    def python_pkill(
        self, program_str: str, require_output=False
    ) -> Tuple[Optional[bool], Optional[str]]:
        scriptstr = f"""import os
import sys
try:
    import psutil
except ImportError:
    print('psutil not installed yet')
    sys.exit(2)

procs = []
for proc in psutil.process_iter():
    cmd = ' '.join(proc.cmdline())
    if '{program_str}' in cmd and not 'IGNORE_THIS_PROGRAM' in cmd:
        procs.append(proc)

this_proc = [proc for proc in procs if proc.pid == os.getpid()]
for proc in this_proc:
    procs.remove(proc)

if len(procs) > 0:
    print('Matching procs: ' + ', '.join([str(proc.pid) for proc in procs]))
    for proc in procs:
        print('Terminating ' + str(proc.pid) + ': ' + ' '.join(proc.cmdline()))
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=5)
    if alive:
        print('One or more processes alive after terminate')
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
        gone, alive = psutil.wait_procs(alive, timeout=5)
        if alive:
            print("One or more processes alive after kill; giving up")
            sys.exit(1)
        else:
            print("All matching processes ended after kill")
            sys.exit(0)
    else:
        print("Matching processes successfully terminated")
else:
    print("no matching processes found")
    sys.exit(0)"""
        cmd = f"{self.python_path} -c {py_to_clistr(scriptstr)}"
        exit_code, output = self.run_cmd(cmd, privileged=True)
        logger.debug(f"{self.name}: pkill for {program_str}: {output}")

        if exit_code == 2:
            return None, "psutil not installed yet"
        else:
            return exit_code == 0, output

    def push_johann(self, update_only: bool = False) -> Tuple[bool, Optional[str]]:
        name_is_control = self.control_name == self.name
        control_name = self.control_name or self.name

        if update_only:
            logger.info(
                f"{self.name}: updating Johann"
                f"""{f" using control_name '{control_name}'" if name_is_control else ''}"""
            )
        else:
            logger.info(
                f"{self.name}: installing Johann"
                f"""{f" using control_name '{control_name}'" if name_is_control else ''}"""
            )

        try:
            python_verstr = self.python_ver
            if not python_verstr:
                # make sure we have a supported version of python installed
                success, python_verstr = self.get_python_version()
                if not success:
                    msg = (
                        f"{self.name}: failed to get valid python version; output:"
                        f" {python_verstr}"
                    )
                    logger.warning(msg)
                    return False, msg

            if python_verstr not in config.SUPPORTED_PYTHON_VERSIONS:
                msg = f"{self.name}: invalid python version: {python_verstr}"
                logger.warning(msg)
                return False, msg

            host_os = self.os
            if not self.os:
                # get host os (i.e. 'Linux' or 'Windows')
                success, host_os = self.get_os()
                if not success:
                    msg = f"{self.name}: failed to determine OS and none provided"
                    logger.warning(msg)
                    return False, msg

            if host_os == HostOS.LINUX:
                workpath = config.DEPLOY_PATH_POSIX
                temppath = config.TEMP_PATH_POSIX
            else:
                msg = f"{self.name}: Unsupported OS '{host_os}'"
                logger.warning(msg)
                return False, msg

            logger.debug(
                f"{self.name}: workdir {str(workpath)}; tempdir {str(temppath)}"
            )

            codehash = get_codehash()
            johann_tarball_path = f"{config.TARBALL_PATH}/johann.{codehash}.tar.gz"
            pip_tarball_path = f"{config.TARBALL_PATH}/minirepo.tar.gz"

            # check for tarball for current code
            if not os.path.isfile(johann_tarball_path):
                # create tarball for current code
                if not create_johann_tarball():
                    return False, "Failed to create tarball for current code"
            else:
                logger.debug(
                    f"Conductor (pushing to {self.name}): Johann tarball already"
                    " present locally"
                )

            if not update_only:
                logger.debug(f"{self.name}: Ensuring target directories exist")
                # make sure target workdir exists
                target_dir_1 = str(workpath.joinpath("minirepo"))
                target_dir_2 = str(temppath)
                script_template_str = """import os
os.makedirs('TARGET_DIR_HERE', exist_ok=True)"""
                for target_dir in [target_dir_1, target_dir_2]:
                    script_str = script_template_str.replace(
                        "TARGET_DIR_HERE", target_dir
                    )
                    cmd = f"{self.python_path} -c {py_to_clistr(script_str)}"
                    exit_code, output = self.run_cmd(cmd, detach=False, privileged=True)
                    if exit_code is None or exit_code != 0:
                        msg = f"{self.name}: failed to make dir for Johann: {output}"
                        logger.warning(msg)
                        return False, msg

                # make sure we have permissions on the workpath and minirepo subdir
                if self.user is not None:
                    logger.debug(
                        f"{self.name}: Ensuring user '{self.user}' has ownership of"
                        f" {workpath}"
                    )
                    script_str = f"""import os
import pwd
os.chown('{workpath}', pwd.getpwnam('{self.user}').pw_uid, -1)
os.chown('{workpath.joinpath('minirepo')}', pwd.getpwnam('{self.user}').pw_uid, -1)
                    """
                    cmd = f"{self.python_path} -c {py_to_clistr(script_str)}"
                    exit_code, output = self.run_cmd(cmd, detach=False, privileged=True)
                    if exit_code is None or exit_code != 0:
                        msg = (
                            f"{self.name}: failed to set dir permissions for Johann"
                            f" workpath: {output}"
                        )
                        logger.warning(msg)
                        return False, msg

            johann_was_running, pgrep_output = self.python_pgrep("johann_main.py")
            if johann_was_running:
                logger.debug(f"{self.name}: Johann is running")

            # disable/kill Johann
            if johann_was_running or self.pmtr_variant not in [PmtrVariant.NONE]:
                self.johann_control(True, workpath)
                logger.debug(f"{self.name}: giving johann a couple seconds to close")
                time.sleep(3)

            # kill workers
            self.python_pkill(f"celery -A {config.CELERY_TASKS_MODULE}")

            # push Johann tarball to host
            logger.debug(f"{self.name}: pushing Johann tarball")
            dest_path_inc_filename = workpath.joinpath(
                os.path.basename(johann_tarball_path)
            )
            success = self.put_archive(johann_tarball_path, str(dest_path_inc_filename))
            if not success:
                msg = f"{self.name}: pushing of Johann tarball failed"
                logger.warning(msg)
                return False, msg

            # doing docker commands too close together seems to cause issues
            time.sleep(1)

            # dev versions of pmtr conf's have 'depends' blocks
            # and will restart Johann when the file changes (Johann tarball)
            if config.DEBUG and self.pmtr_variant not in [PmtrVariant.NONE]:
                logger.debug(
                    f"{self.name}: disabling Johann again because of debug mode/pmtr"
                    " depends/johann tarball issue"
                )
                self.johann_control(True, workpath)
                logger.debug(f"{self.name}: giving johann a couple seconds to close")
                time.sleep(3)

            # install Johann requirements if needed
            if not update_only:
                if self.pip_offline_install:
                    dest_path_inc_filename = workpath.joinpath(
                        "minirepo", os.path.basename(pip_tarball_path)
                    )

                    # get hash of pip tarball
                    with open(pip_tarball_path, "rb") as f:
                        md5 = hashlib.md5()
                        for chunk in iter(lambda: f.read(4096), b""):
                            md5.update(chunk)
                        pip_hash = md5.hexdigest()
                        logger.debug(
                            f"{self.name}: Local player pip tarball hash: {pip_hash}"
                        )
                    script_str = f"""import hashlib
with open("{dest_path_inc_filename}", 'rb') as f:
    md5 = hashlib.md5()
    for chunk in iter(lambda:f.read(4096), b""):
        md5.update(chunk)
    print(md5.hexdigest())
                    """
                    cmd = f"{self.python_path} -c {py_to_clistr(script_str)}"
                    exit_code, output = self.run_cmd(cmd)
                    if exit_code == 0 and output == pip_hash:
                        logger.info(
                            f"{self.name}: Player pip tarball already present with"
                            f" matching hash {pip_hash}"
                        )
                    else:
                        if exit_code == 0:
                            logger.debug(
                                f"{self.name}: Player pip tarball hash mismatch:"
                                f" {output} instead of {pip_hash}"
                            )
                        # push pip tarball to host
                        logger.info(
                            f"{self.name}: pushing Player pip tarball; this can take"
                            " some time"
                        )
                        success = self.put_archive(
                            pip_tarball_path,
                            str(dest_path_inc_filename),
                            remove_archive_file=False,
                        )
                        if not success:
                            msg = f"{self.name}: pushing of Player Pip tarball failed"
                            logger.warning(msg)
                            return False, msg

                        # doing commands too close together seems to cause Docker issues
                        time.sleep(1)

                # install dependencies with pip
                success, errmsg = self.install_pip_dependencies(workpath, python_verstr)
                if not success:
                    logger.warning(f"{self.name}: {errmsg}")
                    return False, errmsg

                # doing docker commands too close together seems to cause issues
                time.sleep(1)

            # make sure Johann is dead
            running, pgrep_output = self.python_pgrep("johann_main.py")
            if not update_only and running is None:
                msg = (
                    f"{self.name}: psutil package is still not installed; something is"
                    f" wrong ('{pgrep_output}')"
                )
                logger.warning(msg)
                return False, msg
            elif johann_was_running and running:
                msg = (
                    f"{self.name}: Johann is still running after disable/kill;"
                    " something is wrong"
                )
                logger.warning(msg)
                return False, msg

            # generate and push a .env file with host-specific configuration information
            conf = {
                "CELERY_QUEUE_ID": self.name,
                "DEBUG": 1,
            }
            if self.user:  # don't want to store 'None' string
                conf["CELERY_USER"] = self.user
            # TODO
            # if config.PLUGINS_EXCLUDE:
            #     conf["PLUGINS_EXCLUDE"] = json.dumps(config.PLUGINS_EXCLUDE)
            try:
                if config.HOST_CONTROL_EXTERNAL_REDIS[self.control_method]:
                    conf["REDIS_HOST"] = config.REDIS_HOST_EXTERNAL
            except KeyError:
                pass
            conf_str = "\n".join([f"{k}={v}" for k, v in conf.items()])
            if self.os == HostOS.WINDOWS:
                raise NotImplementedError()
            cmd = (  # sh's echo doesn't need -e
                f"/bin/sh -c \"echo '{conf_str}' > .env\""
            )
            exit_code, output = self.run_cmd(cmd, workpath=workpath)
            if exit_code:
                msg = f"{self.name}: writing .env for johann player failed: {output}"
                logger.warning(msg)
                return False, msg

            # enable/start Johann
            self.johann_control(False, workpath)

            # give Johann some time to start
            count = 15
            interval = 2
            logger.debug(
                f"{self.name}: Waiting up to up to {count} seconds for Johann to start"
            )
            for _ in range(int(count / interval)):
                time.sleep(interval)
                started, pgrep_output = self.python_pgrep("johann_main.py")
                if started:
                    break

            started, pgrep_output = self.python_pgrep("johann_main.py")
            if not started:
                msg = f"{self.name} had issues starting Johann: {pgrep_output}"
                logger.warning(msg)
                return False, msg
            else:
                logger.debug(f"{self.name}: Johann re-enabled")
                return True, None

        except Exception as e:
            msg = f"{self.name}: unexpected exception while pushing Johann ({type(e)})"
            logger.exception(msg)
            return False, msg
