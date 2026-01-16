import paramiko
import os
import time
import socket
import logging
from scp import SCPClient
from pathlib import Path
from typing import List
from core.config import Paths, Timings

logger = logging.getLogger("RemoteExec")


class RemoteDeviceExecutor:
    """
    Manages remote execution of test agents on Device Under Test (DUT).
    Handles SSH connection, script deployment, and plugin-based command execution.
    """

    # Constants for internal logic
    POLLING_TIMEOUT = 60
    POLLING_INTERVAL = 3
    WIFI_SWITCH_TIMEOUT = 45

    def __init__(self, device_config):
        """
        Initialize remote executor with device configuration.

        :param device_config: Dictionary containing device connection parameters
        """
        self.config = device_config
        self.name = device_config["name"]
        self.ip = device_config["ip"]
        self.user = device_config["user"]
        self.password = device_config["password"]
        self.os_type = device_config["os"]
        self.python_cmd = device_config.get("python_path", "python")

        self.ssh = None
        self.remote_dir = Paths.REMOTE_WINDOWS_WORK_DIR if self.os_type == "Windows" else Paths.REMOTE_LINUX_WORK_DIR
        self.deployed_plugins = []  # Track deployed plugins

    def connect(self, plugins: List[str] = None):
        """
        Establish SSH connection to DUT and deploy test scripts.

        :param plugins: List of plugin names to deploy (e.g., ['wifi'])
        """
        if plugins is None:
            plugins = ['wifi']  # Default to wifi for backward compatibility

        logger.info(f"{self.name}: Connecting to DUT {self.ip}...")
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(self.ip, username=self.user, password=self.password, timeout=Timings.SSH_TIMEOUT)
        self.deploy_agent(plugins)

    def close(self):
        """
        Close SSH connection to DUT.
        """
        if self.ssh:
            self.ssh.close()

    def _exec_command_verbose(self, cmd, description):
        """
        Execute SSH command and log output verbosely.

        :param cmd: Command string to execute
        :param description: Human-readable description for logging
        :return: Tuple of (exit_status, stdout, stderr)
        """
        logger.debug(f"{self.name}: {description}: {cmd}")
        stdin, stdout, stderr = self.ssh.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        out_str = stdout.read().decode().strip()
        err_str = stderr.read().decode().strip()

        if exit_status != 0:
            logger.error(f"{self.name}: {description} Failed (Exit {exit_status})")
            if out_str:
                logger.error(f"{self.name}: STDOUT: {out_str}")
            if err_str:
                logger.error(f"{self.name}: STDERR: {err_str}")
        else:
            logger.debug(f"{self.name}: {description}")
            if out_str:
                logger.debug(f"{self.name}: STDOUT: {out_str}")

        return exit_status, out_str, err_str

    def deploy_agent(self, plugins: List[str]):
        """
        Deploy universal agent with specified plugins.

        :param plugins: List of plugin names (e.g., ['wifi'])
        """
        logger.info(f"{self.name}: Deploying agent with plugins: {plugins}")

        # Create remote root directory
        logger.debug(f"{self.name}: Verifying/Creating remote root directory: {self.remote_dir}")
        self._create_remote_dir(self.remote_dir)

        # Core files to upload (local_path, remote_filename)
        core_files = [
            ("agent/agent.py", "agent.py"),
            ("agent/agent_device_manager.py", "agent_device_manager.py"),
            ("core/config.py", "config.py"),
            ("core/core_report.py", "core_report.py")
        ]

        # Plugin files to upload
        plugin_files = []
        for plugin_name in plugins:
            plugin_files.append((f"agent/plugins/{plugin_name}_plugin.py", f"plugins/{plugin_name}_plugin.py"))

        # Execute transfer
        try:
            with SCPClient(self.ssh.get_transport()) as scp:
                logger.debug(f"{self.name}: Transport session established.")

                # Upload core files
                for local_rel_path, remote_filename in core_files:
                    local_path = Paths.BASE_DIR / local_rel_path

                    if not local_path.exists():
                        logger.warning(f"{self.name}: Missing file: {local_rel_path}")
                        continue

                    file_size = local_path.stat().st_size
                    remote_dest_path = f"{self.remote_dir}/{remote_filename}".replace("\\", "/")

                    logger.debug(f"{self.name}: Uploading: {local_rel_path} -> {remote_filename} ({file_size} bytes)")
                    scp.put(str(local_path), remote_path=remote_dest_path)

                # Create plugins directory on remote
                if plugin_files:
                    self._create_remote_dir(f"{self.remote_dir}/plugins")

                    # Upload plugin files
                    for local_rel_path, remote_file_path in plugin_files:
                        local_path = Paths.BASE_DIR / local_rel_path

                        if not local_path.exists():
                            logger.warning(f"{self.name}: Missing plugin: {local_rel_path}")
                            continue

                        file_size = local_path.stat().st_size
                        remote_dest_path = f"{self.remote_dir}/{remote_file_path}".replace("\\", "/")

                        logger.debug(f"{self.name}: Uploading plugin: {local_rel_path} ({file_size} bytes)")
                        scp.put(str(local_path), remote_path=remote_dest_path)

                # Upload resources folder (manual recursion)
                if Paths.RESOURCES_DIR.exists():
                    logger.debug(f"{self.name}: Starting resource transfer...")

                    remote_res_dir = f"{self.remote_dir}/resources".replace("\\", "/")
                    created_remote_dirs = set()

                    for root, _, files in os.walk(str(Paths.RESOURCES_DIR)):
                        for file in files:
                            local_file_path = Path(root) / file
                            rel_path = local_file_path.relative_to(Paths.RESOURCES_DIR)
                            remote_file_path = f"{remote_res_dir}/{rel_path.as_posix()}"
                            remote_parent_dir = str(Path(remote_file_path).parent).replace("\\", "/")

                            if remote_parent_dir not in created_remote_dirs:
                                if self.os_type == "Linux":
                                    dir_cmd = f"mkdir -p {remote_parent_dir}"
                                else:
                                    dir_cmd = f'powershell -Command "New-Item -Path \'{remote_parent_dir}\' -ItemType Directory -Force"'

                                self._exec_command_verbose(dir_cmd, f"Create Remote Dir: {remote_parent_dir}")
                                created_remote_dirs.add(remote_parent_dir)

                            logger.debug(f"{self.name}: Uploading: {file} -> resources/{rel_path.as_posix()}")
                            scp.put(str(local_file_path), remote_path=remote_file_path)

                    logger.debug(f"{self.name}: Resource transfer completed.")

            self.deployed_plugins = plugins
            logger.info(f"{self.name}: Deployment sequence finished successfully.")

        except Exception as e:
            logger.error(f"{self.name}: Critical SCP Transfer failed: {e}")
            raise

    def _deploy_scripts(self):
        """
        Legacy deployment method for backward compatibility.
        Deploys WiFi plugin by default.
        """
        self.deploy_agent(['wifi'])

    def run_plugin_command(self, plugin: str, command: str, **kwargs):
        """
        Execute plugin command via new agent.py plugin system.

        :param plugin: Plugin name (e.g., 'wifi')
        :param command: Command name (e.g., 'connect', 'iperf')
        :param kwargs: Command arguments
        :return: Tuple of (success: bool, output: str)
        """
        # Build args string
        args_str = ' '.join([f'--{k} "{v}"' if ' ' in str(v) else f'--{k} {v}' for k, v in kwargs.items()])
        cmd_args = f"{plugin} {command} {args_str}"

        return self._run_agent_command(cmd_args)

    def _run_agent_command(self, cmd_args, timeout=None):
        """
        Execute agent.py command on remote device (private method).
        Uses polling to avoid blocking indefinitely on dead SSH links.
        """
        if self.os_type == "Windows":
            full_cmd = f'cmd /c "cd /d {self.remote_dir} && {self.python_cmd} agent.py {cmd_args}"'
        else:
            full_cmd = f"cd {self.remote_dir} && {self.python_cmd} agent.py {cmd_args}"

        logger.debug(f"{self.name}: Executing Agent Command: {full_cmd}")

        try:
            if timeout:
                self.ssh.get_transport().set_keepalive(5)

            stdin, stdout, stderr = self.ssh.exec_command(full_cmd, timeout=timeout)

            start_time = time.time()
            while not stdout.channel.exit_status_ready():
                if timeout and (time.time() - start_time > timeout):
                    raise socket.timeout("Command execution timed out")
                time.sleep(0.5)

            exit_status = stdout.channel.recv_exit_status()
            out_str = stdout.read().decode().strip()
            err_str = stderr.read().decode().strip()

            if exit_status != 0:
                logger.error(f"{self.name}: Remote Agent Error (Exit {exit_status}):\nSTDOUT: {out_str}\nSTDERR: {err_str}")
                return False, None

            if "RESULT:SUCCESS" in out_str:
                return True, out_str
            else:
                logger.error(f"{self.name}: Agent Logic Failure: {out_str}")
                return False, out_str

        except socket.timeout:
            logger.warning(f"{self.name}: Agent command timed out after {timeout}s")
            return False, None
        except Exception as e:
            logger.debug(f"{self.name}: Command execution interrupted: {e}")
            raise

    def run_agent_command(self, cmd_args, timeout=None):
        """
        Execute agent.py command on remote device (public method for backward compatibility).

        :param cmd_args: Arguments to pass to agent.py
        :param timeout: Optional timeout in seconds
        :return: Tuple of (stdout: str, stderr: str)
        """
        success, output = self._run_agent_command(cmd_args, timeout)

        if success and output:
            return output, ""
        else:
            return output or "", ""

    def forget_all_networks(self):
        """Remove all WiFi network profiles on remote device."""
        logger.info(f"{self.name}: Forgetting networks...")
        self.run_plugin_command('wifi', 'forget')

    def connect_wifi(self, ssid, password):
        """
        Connect remote device to specified WiFi network.

        :param ssid: Target network SSID
        :param password: Network password
        :return: True if connection successful
        """
        logger.info(f"{self.name}: Connecting to {ssid}...")

        try:
            success, output = self.run_plugin_command('wifi', 'connect', ssid=ssid, password=password, cleanup='true')

            if success:
                logger.info(f"{self.name}: Connected to {ssid}")
                return True

        except Exception as e:
            logger.info(f"{self.name}: SSH dropped during WiFi switch. Waiting for recovery...")

        return self._wait_for_reconnection()

    def _wait_for_reconnection(self):
        """
        Poll device via SSH until available again.

        :return: True if reconnected, False if timeout
        """
        logger.info(f"{self.name}: Polling device availability...")
        start_time = time.time()

        while time.time() - start_time < self.POLLING_TIMEOUT:
            try:
                if self.ssh:
                    self.ssh.close()

                self.ssh = paramiko.SSHClient()
                self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.ssh.connect(self.ip, username=self.user, password=self.password, timeout=5)

                logger.info(f"{self.name}: Device is back online!")
                return True
            except (socket.error, paramiko.SSHException):
                time.sleep(self.POLLING_INTERVAL)
                print(".", end="", flush=True)
            except Exception as e:
                logger.error(f"{self.name}: Unexpected error during polling: {e}")
                time.sleep(self.POLLING_INTERVAL)

        logger.error(f"{self.name}: Timed out waiting for device to reconnect.")
        return False

    def _create_remote_dir(self, path):
        if self.os_type == "Linux":
            create_cmd = f"mkdir -p {path}"
        else:
            create_cmd = f'powershell -Command "New-Item -Path \'{path}\' -ItemType Directory -Force"'

        exit_status, out, err = self._exec_command_verbose(create_cmd, f"{self.name}: Created Remote dir - {path}")
        if exit_status != 0:
            logger.error(f"{self.name}: Directory {path} creation failed. Error: {err}")
            raise RuntimeError(f"{self.name}: Mkdir failed for {path}. Error: {err}")

    def run_iperf(self):
        """
        Execute iperf3 test on remote device.

        :return: Raw iperf output string or None
        """
        port = self.config.get('iperf_port', 5201)
        logger.info(f"{self.name}: Running iperf on port {port}...")

        success, output = self.run_plugin_command('wifi', 'iperf', port=port)
        if success and "IPERF_OUTPUT_START" in output:
            try:
                start = output.find("IPERF_OUTPUT_START") + len("IPERF_OUTPUT_START")
                end = output.find("IPERF_OUTPUT_END")
                iperf_log = output[start:end].strip()
                logger.info(f"{self.name}: Iperf completed")
                return iperf_log
            except Exception:
                logger.error(f"{self.name}: Failed to parse iperf output")
        return None

    def init_remote_report(self, device_name, ip_address):
        """
        Initialize HTML report on DUT.

        :param device_name: System product name
        :param ip_address: Device IP address
        :return: Remote report path or None
        """
        report_dir = Paths.REMOTE_WINDOWS_WORK_DIR + "\\reports" if self.os_type == "Windows" else Paths.REMOTE_LINUX_WORK_DIR + "/reports"

        success, output = self.run_plugin_command('wifi', 'init_report',
                                                   device_name=device_name,
                                                   ip_address=ip_address,
                                                   report_dir=report_dir)

        if success and "REPORT_PATH:" in output:
            for line in output.split('\n'):
                if line.startswith("REPORT_PATH:"):
                    remote_path = line.split(":", 1)[1].strip()
                    logger.info(f"{self.name}: Remote report initialized: {remote_path}")
                    return remote_path

        logger.error(f"{self.name}: Failed to initialize remote report")
        return None

    def add_remote_test_result(self, report_path, band, ssid, standard, channel, iperf_output):
        """
        Add test result to remote HTML report.

        :param report_path: Remote path to report file
        :param band: Frequency band
        :param ssid: Network SSID
        :param standard: WiFi standard
        :param channel: Channel number
        :param iperf_output: Raw iperf output
        :return: True if successful
        """
        import base64
        iperf_b64 = base64.b64encode(iperf_output.encode()).decode()

        success, output = self.run_plugin_command('wifi', 'add_result',
                                                   report_path=report_path,
                                                   band=band,
                                                   ssid=ssid,
                                                   standard=standard,
                                                   channel=channel,
                                                   iperf_output=iperf_b64)

        if success:
            logger.info(f"{self.name}: Test result added: {band}/{standard}/Ch{channel}")
            return True
        else:
            logger.warning(f"{self.name}: Failed to add result to report")
            return False

    def download_report(self, remote_path, local_dir):
        """
        Download HTML report from DUT.

        :param remote_path: Remote path to report file
        :param local_dir: Local directory to save report
        :return: Local file path or None
        """
        try:
            from pathlib import Path
            local_path = Path(local_dir) / Path(remote_path).name

            with SCPClient(self.ssh.get_transport()) as scp:
                scp.get(remote_path.replace("\\", "/"), str(local_path))
                logger.info(f"{self.name}: Report downloaded: {local_path.name}")
                return str(local_path)

        except Exception as e:
            logger.error(f"{self.name}: Failed to download report: {e}")
            return None

