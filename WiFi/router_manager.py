# router_manager.py
import time
import logging
import paramiko
from tenacity import retry, stop_after_attempt, wait_fixed
from config import NetworkConfig, Timings, Limits

logger = logging.getLogger("RouterMgr")


class RouterManager:
    """
    Handles SSH communication with the router to change settings.
    """

    def __init__(self):
        self.ssh_client = None

    @retry(stop=stop_after_attempt(Limits.SSH_RETRIES), wait=wait_fixed(Limits.CONNECTION_RETRY_DELAY))
    def connect_ssh(self):
        if self.ssh_client:
            self.ssh_client.close()

        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        self.ssh_client.connect(
            NetworkConfig.ROUTER_IP,
            username=NetworkConfig.SSH_USER,
            password=NetworkConfig.SSH_PASSWORD,
            timeout=Timings.SSH_TIMEOUT,
            look_for_keys=False,
            allow_agent=False
        )

        stdin, stdout, stderr = self.ssh_client.exec_command('echo "SSH connection test"')
        if "SSH connection test" in stdout.read().decode():
            return True
        else:
            raise Exception("SSH test command failed")

    def close(self):
        if self.ssh_client:
            self.ssh_client.close()

    def _exec_uci(self, commands, description):
        logger.info(description)
        for cmd in commands:
            self.ssh_client.exec_command(cmd)

        self.ssh_client.exec_command("wifi reload")
        time.sleep(Timings.WIFI_APPLY_DELAY)

    def change_channel(self, device, channel):
        self._exec_uci(
            [f"uci set wireless.{device}.channel={channel}", "uci commit wireless"],
            f"Setting channel for {device} to {channel}"
        )

    def change_standard(self, device, standard):
        self._exec_uci(
            [f"uci set wireless.{device}.hwmode={standard}", "uci commit wireless"],
            f"Setting standard for {device} to {standard}"
        )

    def get_current_setting(self, device, setting_name):
        stdin, stdout, stderr = self.ssh_client.exec_command(f"uci get wireless.{device}.{setting_name}")
        output = stdout.read().decode().strip()
        logger.info(f"Current {setting_name} for {device}: {output}")
        return output

    def set_channel_auto(self):
        try:
            self.connect_ssh()
            logger.info("Resetting channels to auto")
            self.ssh_client.exec_command(f"uci set wireless.{NetworkConfig.DEVICE_2G}.channel=auto")
            self.ssh_client.exec_command(f"uci set wireless.{NetworkConfig.DEVICE_5G}.channel=auto")
            self.ssh_client.exec_command("uci commit wireless")
            self.ssh_client.exec_command("wifi reload")
        except Exception as e:
            logger.error(f"Error setting channels to auto: {e}")

    def set_standard_auto(self):
        try:
            self.connect_ssh()
            logger.info("Resetting hwmodes to default (11n)")
            self.ssh_client.exec_command(f"uci set wireless.{NetworkConfig.DEVICE_2G}.hwmode=11n")
            self.ssh_client.exec_command(f"uci set wireless.{NetworkConfig.DEVICE_5G}.hwmode=11n")
            self.ssh_client.exec_command("uci commit wireless")
            self.ssh_client.exec_command("wifi reload")
        except Exception as e:
            logger.error(f"Error resetting hwmodes: {e}")