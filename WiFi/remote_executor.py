import paramiko
import os
import time
import socket
import logging
from scp import SCPClient
from pathlib import Path
from config import Paths, Timings

logger = logging.getLogger("RemoteExec")


class RemoteDeviceExecutor:
    """
    Manages remote execution of test agents on Device Under Test (DUT).
    Handles SSH connection, script deployment, and command execution.
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
        self.ip = device_config["ip"]
        self.user = device_config["user"]
        self.password = device_config["password"]
        self.os_type = device_config["os"]
        self.python_cmd = device_config.get("python_path", "python")

        self.ssh = None
        self.remote_dir = Paths.REMOTE_WINDOWS_WORK_DIR if self.os_type == "Windows" else Paths.REMOTE_LINUX_WORK_DIR

    def connect(self):
        """
        Establish SSH connection to DUT and deploy test scripts.
        """
        logger.info(f"Connecting to DUT {self.ip}...")
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(self.ip, username=self.user, password=self.password, timeout=Timings.SSH_TIMEOUT)
        self._deploy_scripts()

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
        logger.debug(f"[SSH CMD] {description}: {cmd}")
        stdin, stdout, stderr = self.ssh.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        out_str = stdout.read().decode().strip()
        err_str = stderr.read().decode().strip()

        if exit_status != 0:
            logger.error(f"[SSH ERR] {description} Failed (Exit {exit_status})")
            if out_str:
                logger.error(f"STDOUT: {out_str}")
            if err_str:
                logger.error(f"STDERR: {err_str}")
        else:
            logger.debug(f"[SSH OK] {description}")
            if out_str:
                logger.debug(f"STDOUT: {out_str}")

        return exit_status, out_str, err_str

    def _deploy_scripts(self):
        """
        Deploy test scripts and resources to remote device via SCP.
        Manually handles directory structures to avoid SCP recursive limitations on Windows.
        """
        logger.info(f"[DEPLOY] Initiating deployment sequence to target: {self.remote_dir}")

        # Debug info
        if self.os_type == "Windows":
            self._exec_command_verbose("whoami", "Check remote user identity")

        # Create remote root directory
        logger.debug(f"[DEPLOY] Verifying/Creating remote root directory: {self.remote_dir}")

        # Abstraction for directory creation
        if self.os_type == "Linux":
            create_cmd = f"mkdir -p {self.remote_dir}"
        else:
            create_cmd = f'powershell -Command "New-Item -Path \'{self.remote_dir}\' -ItemType Directory -Force"'

        exit_status, out, err = self._exec_command_verbose(create_cmd, "Create Remote Root Dir")
        if exit_status != 0:
            logger.error(f"[DEPLOY] Root creation failed: {err}")
            raise RuntimeError(f"Mkdir failed: {err}")  # Changed Exception to RuntimeError

        # Files to upload (Core Scripts)
        files_to_sync = [
            "config.py",
            "device_manager.py",
            "agent.py",
            "report_generator.py"
        ]

        # Execute transfer
        try:
            with SCPClient(self.ssh.get_transport()) as scp:
                logger.debug("[DEPLOY] Transport session established.")

                # Upload core scripts
                for filename in files_to_sync:
                    local_path = Paths.get_validated_path(filename)
                    if not local_path.exists():
                        logger.warning(f"[DEPLOY] Missing file: {filename}")
                        continue

                    file_size = local_path.stat().st_size  # Use pathlib

                    # Force forward slashes for SCP compatibility regardless of OS
                    remote_dest_path = f"{self.remote_dir}/{filename}".replace("\\", "/")

                    logger.debug(f"[DEPLOY] Uploading: {filename} ({file_size} bytes)")
                    scp.put(str(local_path), remote_path=remote_dest_path)

                # Upload resources folder (manual recursion)
                if Paths.RESOURCES_DIR.exists():
                    logger.info("-" * 50)
                    logger.debug("[DEPLOY] Starting manual resource transfer...")

                    # Base remote resources directory
                    remote_res_dir = f"{self.remote_dir}/resources".replace("\\", "/")

                    # Track created directories to avoid redundant SSH calls
                    created_remote_dirs = set()

                    # Walk through local files and upload individually
                    for root, _, files in os.walk(str(Paths.RESOURCES_DIR)):
                        for file in files:
                            local_file_path = Path(root) / file
                            # Calculate relative path to keep structure
                            rel_path = local_file_path.relative_to(Paths.RESOURCES_DIR)

                            # Construct full remote path
                            remote_file_path = f"{remote_res_dir}/{rel_path.as_posix()}"

                            # Determine remote parent directory for the current file
                            remote_parent_dir = str(Path(remote_file_path).parent).replace("\\", "/")

                            # Ensure the parent directory exists remotely (if not already created)
                            if remote_parent_dir not in created_remote_dirs:
                                if self.os_type == "Linux":
                                    dir_cmd = f"mkdir -p {remote_parent_dir}"
                                else:
                                    dir_cmd = f'powershell -Command "New-Item -Path \'{remote_parent_dir}\' -ItemType Directory -Force"'

                                self._exec_command_verbose(dir_cmd, f"Create Remote Dir: {remote_parent_dir}")
                                created_remote_dirs.add(remote_parent_dir)

                            logger.debug(f"[DEPLOY] Uploading: {file} -> resources/{rel_path.as_posix()}")
                            scp.put(str(local_file_path), remote_path=remote_file_path)

                    logger.debug(f"[DEPLOY] Resource transfer completed.")
                else:
                    logger.warning(f"[DEPLOY] No resources directory found.")

            logger.info("[DEPLOY] Deployment sequence finished successfully.")
            logger.info("-" * 50)

        except Exception as e:
            logger.error(f"[DEPLOY] [CRITICAL] SCP Transfer failed: {e}")
            raise

    def _run_agent_command(self, cmd_args, timeout=None):
        """
        Execute agent.py command on remote device (private method).
        Uses polling to avoid blocking indefinitely on dead SSH links.
        """
        # Force CMD logic for Windows execution to support && and cd /d
        if self.os_type == "Windows":
            full_cmd = f'cmd /c "cd /d {self.remote_dir} && {self.python_cmd} agent.py {cmd_args}"'
        else:
            full_cmd = f"cd {self.remote_dir} && {self.python_cmd} agent.py {cmd_args}"

        logger.debug(f"Executing Agent Command: {full_cmd}")

        try:
            # Set transport timeout explicitly
            if timeout:
                self.ssh.get_transport().set_keepalive(5)

            stdin, stdout, stderr = self.ssh.exec_command(full_cmd, timeout=timeout)

            # NON-BLOCKING WAIT LOOP
            # Solves the "hang" issue when WiFi switches and drops link
            start_time = time.time()
            while not stdout.channel.exit_status_ready():
                if timeout and (time.time() - start_time > timeout):
                    raise socket.timeout("Command execution timed out")
                time.sleep(0.5)  # Short sleep allows catching CTRL+C

            exit_status = stdout.channel.recv_exit_status()
            out_str = stdout.read().decode().strip()
            err_str = stderr.read().decode().strip()

            if exit_status != 0:
                logger.error(f"Remote Agent Error (Exit {exit_status}):\nSTDOUT: {out_str}\nSTDERR: {err_str}")
                return False, None

            if "RESULT:SUCCESS" in out_str:
                return True, out_str
            else:
                logger.error(f"Agent Logic Failure: {out_str}")
                return False, out_str

        except socket.timeout:
            logger.warning(f"Agent command timed out after {timeout}s (Expected behavior during WiFi switch)")
            return False, None
        except Exception as e:
            # Let caller handle connection drops (e.g. for WiFi switching)
            logger.debug(f"Command execution interrupted: {e}")
            raise

    def run_agent_command(self, cmd_args, timeout=None):
        """
        Execute agent.py command on remote device (public method).

        :param cmd_args: Arguments to pass to agent.py
        :param timeout: Optional timeout in seconds for command execution
        :return: Tuple of (stdout: str, stderr: str)
        """
        success, output = self._run_agent_command(cmd_args, timeout)

        if success and output:
            return output, ""
        else:
            return output or "", ""

    def forget_all_networks(self):
        """
        Remove all WiFi network profiles on remote device.
        """
        logger.info("Remote: Forgetting networks...")
        self._run_agent_command("forget")

    def connect_wifi(self, ssid, password):
        """
        Connect remote device to specified WiFi network with cleanup.

        IMPORTANT: This command may cause SSH disconnection when switching networks.
        The method handles reconnection automatically via polling.

        :param ssid: Target network SSID
        :param password: Network password
        :return: True if connection successful, False otherwise
        """
        logger.info(f"Remote: transitioning to {ssid} (with cleanup)...")

        # Build command with cleanup flag
        cmd = f"connect --ssid {ssid} --password {password} --cleanup"

        try:
            # Execute with generous timeout defined in constants
            success, output = self._run_agent_command(cmd, timeout=self.WIFI_SWITCH_TIMEOUT)

            # If command returned result without link drop (rare but possible)
            if success:
                logger.info(f"Remote: Connected to {ssid} (Link maintained)")
                return True

        except Exception as e:
            # This is NORMAL behavior when switching WiFi networks
            logger.info(f"SSH connection dropped as expected during WiFi switch ({e}). Waiting for device recovery...")

        # If we reach here, link was dropped. Begin polling (waiting) for recovery.
        return self._wait_for_reconnection()

    def _wait_for_reconnection(self):
        """
        Poll the device via SSH until it becomes available again.
        Used after WiFi network switches that break SSH connection.

        :return: True if device reconnected, False if timeout
        """
        logger.info("Polling device availability...")
        start_time = time.time()

        while time.time() - start_time < self.POLLING_TIMEOUT:
            try:
                # Attempt to recreate SSH connection
                if self.ssh:
                    self.ssh.close()

                self.ssh = paramiko.SSHClient()
                self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                # Short timeout for connectivity check
                self.ssh.connect(self.ip, username=self.user, password=self.password, timeout=5)

                logger.info("Device is back online! Verifying agent status...")
                return True
            except (socket.error, paramiko.SSHException):
                time.sleep(self.POLLING_INTERVAL)
                print(".", end="", flush=True)
            except Exception as e:
                logger.error(f"Unexpected error during polling: {e}")
                time.sleep(self.POLLING_INTERVAL)

        logger.error("Timed out waiting for device to reconnect.")
        return False

    def run_iperf(self):
        """
        Execute iperf3 test on remote device and return raw output.

        :return: Raw iperf output string or None if test failed
        """
        logger.info("Remote: Running iperf...")
        success, output = self._run_agent_command("iperf")
        if success and "IPERF_OUTPUT_START" in output:
            try:
                start = output.find("IPERF_OUTPUT_START") + len("IPERF_OUTPUT_START")
                end = output.find("IPERF_OUTPUT_END")
                iperf_log = output[start:end].strip()
                logger.info(f"Remote Iperf Result:\n{iperf_log}")
                return iperf_log
            except Exception:
                logger.error("Failed to parse Iperf output markers")
        return None

    def init_remote_report(self, device_name, ip_address):
        """
        Initialize HTML report on DUT for incremental updates.

        :param device_name: System product name
        :param ip_address: Device IP address
        :return: Remote report path or None if failed
        """
        report_dir = Paths.REMOTE_WINDOWS_WORK_DIR + "\\reports" if self.os_type == "Windows" else Paths.REMOTE_LINUX_WORK_DIR + "/reports"

        cmd = f'init_report --device_name "{device_name}" --ip_address {ip_address} --report_dir "{report_dir}"'
        success, output = self._run_agent_command(cmd)

        if success and "REPORT_PATH:" in output:
            for line in output.split('\n'):
                if line.startswith("REPORT_PATH:"):
                    remote_path = line.split(":", 1)[1].strip()
                    logger.info(f"Remote report initialized: {remote_path}")
                    return remote_path

        logger.error("Failed to initialize remote report")
        return None

    def add_remote_test_result(self, report_path, band, ssid, standard, channel, iperf_output):
        """
        Add test result to remote HTML report (incremental update).

        :param report_path: Remote path to report file
        :param band: Frequency band
        :param ssid: Network SSID
        :param standard: WiFi standard
        :param channel: Channel number
        :param iperf_output: Raw iperf output
        :return: True if successful, False otherwise
        """
        # Escape iperf output for command line (base64 encode)
        import base64
        iperf_b64 = base64.b64encode(iperf_output.encode()).decode()

        cmd = f'add_result --report_path "{report_path}" --band "{band}" --ssid "{ssid}" --standard "{standard}" --channel {channel} --iperf_output "{iperf_b64}"'
        success, output = self._run_agent_command(cmd, timeout=10)

        if success:
            logger.info(f"Test result added to remote report: {band}/{standard}/Ch{channel}")
            return True
        else:
            logger.warning(f"Failed to add result to remote report")
            return False

    def download_report(self, remote_path, local_dir):
        """
        Download HTML report from DUT to local machine.

        :param remote_path: Remote path to report file
        :param local_dir: Local directory to save report
        :return: Local file path or None if failed
        """
        try:
            from pathlib import Path
            local_path = Path(local_dir) / Path(remote_path).name

            with SCPClient(self.ssh.get_transport()) as scp:
                scp.get(remote_path.replace("\\", "/"), str(local_path))
                logger.info(f"Report downloaded: {local_path.name}")
                return str(local_path)

        except Exception as e:
            logger.error(f"Failed to download report: {e}")
            return None