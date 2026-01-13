# ... (Imports remain unchanged) ...
from datetime import time
from pathlib import Path

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
        Ensures all parent directories exist before uploading files.
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
            "agent.py"
        ]

        # 3. Execution of Transfer
        try:
            with SCPClient(self.ssh.get_transport()) as scp:
                logger.info("[DEPLOY] [SCP] Transport session established.")

                # --- STEP A: Upload Core Scripts ---
                for filename in files_to_sync:
                    if not Paths.get_validated_path(filename).exists():
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

                    # Define the base remote resources directory
                    remote_res_dir = f"{self.remote_dir}/resources".replace("\\", "/")

                    # Track created directories to avoid redundant SSH calls
                    created_remote_dirs = set()

                    # B.1. Walk through local files and upload individually
                    for root, _, files in os.walk(str(Paths.RESOURCES_DIR)):
                        for file in files:
                            local_file_path = Path(root) / file
                            # Calculate relative path to keep structure
                            rel_path = local_file_path.relative_to(Paths.RESOURCES_DIR)

                            # Construct full remote path
                            remote_file_path = f"{remote_res_dir}/{rel_path.as_posix()}"

                            # Determine remote parent directory for the current file
                            remote_parent_dir = str(Path(remote_file_path).parent).replace("\\", "/")

                            # B.2. Ensure the parent directory exists remotely (if not already created)
                            if remote_parent_dir not in created_remote_dirs:
                                if self.os_type == "Linux":
                                    dir_cmd = f"mkdir -p {remote_parent_dir}"
                                else:
                                    dir_cmd = f'powershell -Command "New-Item -Path \'{remote_parent_dir}\' -ItemType Directory -Force"'

                                self._exec_command_verbose(dir_cmd, f"Create Remote Dir: {remote_parent_dir}")
                                created_remote_dirs.add(remote_parent_dir)

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
        logger.info(f"Remote: transitioning to {ssid} (with cleanup)...")

        # Формируем команду с флагом cleanup
        # ВАЖНО: На Windows используем start /b или просто ожидаем разрыва,
        # но надежнее просто запустить и поймать исключение.
        cmd = f"connect --ssid {ssid} --password {password} --cleanup"

        try:
            # Мы ожидаем, что этот вызов может упасть с исключением (Broken Pipe),
            # так как сеть передернется.
            success, output = self._run_agent_command(cmd)

            # Если команда вернула результат без разрыва связи (редко, но бывает)
            if success:
                logger.info(f"Remote: Connected to {ssid} (Link maintained)")
                return True

        except Exception as e:
            # Это НОРМАЛЬНОЕ поведение при смене сети по WiFi
            logger.info(f"SSH connection dropped as expected during WiFi switch ({e}). Waiting for device recovery...")

        # Если мы здесь, связь разорвана. Начинаем поллинг (ожидание) восстановления.
        return self._wait_for_reconnection()

    def _wait_for_reconnection(self):
        """
        Polls the device via SSH until it becomes available again.
        """
        logger.info("Polling device availability...")
        start_time = time.time()

        # Ждем 60 секунд (или больше, см. Timings)
        while time.time() - start_time < 60:
            try:
                # Пытаемся пересоздать SSH подключение
                self.ssh.close()  # Закрываем старый сокет
                self.ssh = paramiko.SSHClient()
                self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                # Короткий таймаут для проверки
                self.ssh.connect(self.ip, username=self.user, password=self.password, timeout=5)

                logger.info("Device is back online! Verifying agent status...")
                return True
            except Exception:
                time.sleep(3)
                print(".", end="", flush=True)  # Визуальный индикатор

        logger.error("Timed out waiting for device to reconnect.")
        return False

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