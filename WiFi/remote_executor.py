# ... (Imports remain unchanged) ...
import paramiko
import os
import logging
from scp import SCPClient
from config import Paths, Timings

logger = logging.getLogger("RemoteExec")

class RemoteDeviceExecutor:
    # ... (init, connect, close, _exec_command_verbose methods remain unchanged) ...
    def __init__(self, device_config):
        self.config = device_config
        self.ip = device_config["ip"]
        self.user = device_config["user"]
        self.password = device_config["password"]
        self.os_type = device_config["os"]
        self.python_cmd = device_config.get("python_path", "python")

        self.ssh = None
        self.remote_dir = Paths.REMOTE_WINDOWS_WORK_DIR if self.os_type == "Windows" else Paths.REMOTE_LINUX_WORK_DIR

    def connect(self):
        logger.info(f"Connecting to DUT {self.ip}...")
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(self.ip, username=self.user, password=self.password, timeout=Timings.SSH_TIMEOUT)
        self._deploy_scripts()

    def close(self):
        if self.ssh:
            self.ssh.close()

    def _exec_command_verbose(self, cmd, description):
        """Helper to execute SSH command and log output."""
        logger.debug(f"[SSH CMD] {description}: {cmd}")
        stdin, stdout, stderr = self.ssh.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        out_str = stdout.read().decode().strip()
        err_str = stderr.read().decode().strip()

        if exit_status != 0:
            logger.error(f"[SSH ERR] {description} Failed (Exit {exit_status})")
            if out_str: logger.error(f"STDOUT: {out_str}")
            if err_str: logger.error(f"STDERR: {err_str}")
        else:
            logger.debug(f"[SSH OK] {description}")
            if out_str: logger.debug(f"STDOUT: {out_str}")

        return exit_status, out_str, err_str

    def _deploy_scripts(self):
        """
        Orchestrates the deployment of scripts and resources to the remote device via SCP.
        Manually handles directory structures to avoid SCP recursive limitations on Windows.
        """
        logger.info(f"[DEPLOY] Initiating deployment sequence to target: {self.remote_dir}")

        # 0. Debug Info
        if self.os_type == "Windows":
            self._exec_command_verbose("whoami", "Check remote user identity")

        # 1. Create remote ROOT directory
        logger.info(f"[DEPLOY] [DIR] Verifying/Creating remote root directory: {self.remote_dir}")

        if self.os_type == "Linux":
            create_cmd = f"mkdir -p {self.remote_dir}"
        else:
            ps_command = f"New-Item -Path '{self.remote_dir}' -ItemType Directory -Force"
            create_cmd = f'powershell -Command "{ps_command}"'

        exit_status, out, err = self._exec_command_verbose(create_cmd, "Create Remote Root Dir")
        if exit_status != 0:
            logger.error(f"[DEPLOY] [ERROR] Root creation failed: {err}")
            raise Exception(f"Mkdir failed: {err}")

        # 2. Files to upload (Core Scripts)
        files_to_sync = [
            "config.py",
            "device_manager.py",
            "agent.py",
            "__init__.py"
        ]

        # 3. Execution of Transfer
        try:
            with SCPClient(self.ssh.get_transport()) as scp:
                logger.info("[DEPLOY] [SCP] Transport session established.")

                # --- STEP A: Upload Core Scripts ---
                for filename in files_to_sync:
                    # Check existence quietly to avoid log spam for optional files
                    if not Paths.get_validated_path(filename).exists():
                        if filename != "__init__.py":  # Only warn for critical files
                            logger.warning(f"[DEPLOY] [WARN] Missing file: {filename}")
                        continue

                    local_path = Paths.get_validated_path(filename)
                    file_size = os.path.getsize(str(local_path))

                    # Force forward slashes for SCP compatibility
                    remote_dest_path = f"{self.remote_dir}/{filename}".replace("\\", "/")

                    logger.info(f"[DEPLOY] [FILE] Uploading: {filename} ({file_size} bytes)")
                    scp.put(str(local_path), remote_path=remote_dest_path)

                # --- STEP B: Upload Resources Folder (Manual Recursion) ---
                if Paths.RESOURCES_DIR.exists():
                    logger.info(f"--------------------------------------------------")
                    logger.info(f"[DEPLOY] [RESOURCES] Starting manual resource transfer...")

                    # B.1. Create 'resources' directory on remote explicitly
                    remote_res_dir = f"{self.remote_dir}/resources".replace("\\", "/")

                    if self.os_type == "Linux":
                        res_create_cmd = f"mkdir -p {remote_res_dir}"
                    else:
                        ps_res_cmd = f"New-Item -Path '{remote_res_dir}' -ItemType Directory -Force"
                        res_create_cmd = f'powershell -Command "{ps_res_cmd}"'

                    self._exec_command_verbose(res_create_cmd, "Create Remote Resources Dir")

                    # B.2. Walk through local files and upload individually
                    for root, _, files in os.walk(str(Paths.RESOURCES_DIR)):
                        for file in files:
                            local_file_path = Path(root) / file
                            # Calculate relative path to keep structure if needed (e.g. resources/subdir/file)
                            rel_path = local_file_path.relative_to(Paths.RESOURCES_DIR)

                            # Construct full remote path
                            # Note: using forward slashes for Path joining to ensure SCP compatibility
                            remote_file_path = f"{remote_res_dir}/{rel_path.as_posix()}"

                            logger.info(f"[DEPLOY] [RES-FILE] Uploading: {file} -> resources/{rel_path.as_posix()}")
                            scp.put(str(local_file_path), remote_path=remote_file_path)

                    logger.info(f"[DEPLOY] [RESOURCES] Resource transfer completed.")
                else:
                    logger.warning(f"[DEPLOY] [WARN] No resources directory found.")

            logger.info("[DEPLOY] [COMPLETED] Deployment sequence finished successfully.")
            logger.info(f"--------------------------------------------------")

        except Exception as e:
            logger.error(f"[DEPLOY] [CRITICAL] SCP Transfer failed: {e}")
            raise

    # ... (rest of the file: _run_agent_command, forget_all_networks, etc. remain unchanged) ...
    def _run_agent_command(self, cmd_args):
        # Force CMD logic for Windows execution to support && and cd /d
        if self.os_type == "Windows":
            full_cmd = f'cmd /c "cd /d {self.remote_dir} && {self.python_cmd} agent.py {cmd_args}"'
        else:
            full_cmd = f"cd {self.remote_dir} && {self.python_cmd} agent.py {cmd_args}"

        logger.debug(f"Executing Agent Command: {full_cmd}")

        stdin, stdout, stderr = self.ssh.exec_command(full_cmd)

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

    def forget_all_networks(self):
        logger.info("Remote: Forgetting networks...")
        self._run_agent_command("forget")

    def connect_wifi(self, ssid, password):
        logger.info(f"Remote: Connecting to {ssid}...")
        success, _ = self._run_agent_command(f"connect --ssid {ssid} --password {password}")
        if success:
            logger.info(f"Remote: Connected to {ssid}")
        return success

    def run_iperf(self):
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