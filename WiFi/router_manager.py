# router_manager.py
import time
import logging
import paramiko
from tenacity import retry, stop_after_attempt, wait_fixed
from config import NetworkConfig, Timings, Limits

logger = logging.getLogger("RouterMgr")

# WiFi mode mappings based on GUI settings
WIFI_MODES_2G = {
    "11b/g/n": {
        "hwmode": "11g",
        "htmode": "HT40",
        "legacy_rates": "1"
    },
    "11b/g/n/ax": {
        "hwmode": "11g",
        "htmode": "HE40",
        "legacy_rates": "1"
    },
    "11g/n/ax": {
        "hwmode": "11g",
        "htmode": "HE40",
        "legacy_rates": "0"
    },
    "11n/ax": {
        "hwmode": "11g",
        "htmode": "HE40",
        "legacy_rates": "0",
        "require_mode": "n"
    }
}

WIFI_MODES_5G = {
    "11a/n/ac/ax": {
        "hwmode": "11a",
        "htmode": "HE80",
        "legacy_rates": "0"
    },
    "11a/n/ac": {
        "hwmode": "11a",
        "htmode": "VHT80",
        "legacy_rates": "0"
    },
    "11n/ac/ax": {
        "hwmode": "11a",
        "htmode": "HE80",
        "legacy_rates": "0",
        "require_mode": "n"
    },
    "11ac/ax": {
        "hwmode": "11a",
        "htmode": "HE80",
        "legacy_rates": "0",
        "require_mode": "ac"
    }
}


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
        """
        Changes the WiFi channel for a device.

        Args:
            device: Device identifier (e.g., 'mt798111', 'mt798112')
            channel: Channel number or 'auto'
        """

        if not self.ssh_client:
            self.connect_ssh()

        self._exec_uci(
            [f"uci set wireless.{device}.channel={channel}", "uci commit wireless"],
            f"Setting channel for {device} to {channel}"
        )

    def get_current_setting(self, device, setting_name):
        """
        Retrieves the current value of a wireless setting.

        Args:
            device: Device identifier
            setting_name: Name of the UCI setting (e.g., 'channel', 'hwmode', 'htmode')

        Returns:
            str: Current setting value
        """
        stdin, stdout, stderr = self.ssh_client.exec_command(f"uci get wireless.{device}.{setting_name}")
        output = stdout.read().decode().strip()
        logger.info(f"Current {setting_name} for {device}: {output}")
        return output

    def _verify_setting(self, device, setting_name, expected_value, retries=15):
        """
        Verifies that a setting has been applied.

        Args:
            device: Device identifier
            setting_name: UCI setting name
            expected_value: Expected value
            retries: Number of verification attempts

        Returns:
            bool: True if verified, False otherwise
        """
        for _ in range(retries):
            try:
                current = self.get_current_setting(device, setting_name)
                if current == expected_value:
                    return True
                time.sleep(1)
            except Exception:
                time.sleep(1)
        return False

    def change_standard(self, device, mode):
        """
        Changes the WiFi mode (standard) for a device.

        Args:
            device: Device identifier (e.g., 'mt798111' for 2.4GHz, 'mt798112' for 5GHz)
            mode: WiFi mode string (e.g., '11b/g/n', '11a/n/ac/ax')

        Raises:
            ValueError: If mode is not supported for the device band
            Exception: If mode verification fails
        """

        if not self.ssh_client:
            self.connect_ssh()


        band = "2g" if device == NetworkConfig.DEVICE_2G else "5g"
        mode_map = WIFI_MODES_2G if band == "2g" else WIFI_MODES_5G

        if mode not in mode_map:
            raise ValueError(f"Unsupported mode '{mode}' for {band} band")

        config = mode_map[mode]
        commands = [
            f"uci set wireless.{device}.hwmode={config['hwmode']}",
            f"uci set wireless.{device}.htmode={config['htmode']}",
            f"uci set wireless.{device}.legacy_rates={config['legacy_rates']}"
        ]

        if "require_mode" in config:
            commands.append(f"uci set wireless.{device}.require_mode={config['require_mode']}")
        else:
            commands.append(f"uci delete wireless.{device}.require_mode")

        commands.append("uci commit wireless")

        self._exec_uci(commands, f"Setting mode for {device} to {mode}")

        if not self._verify_setting(device, "htmode", config["htmode"]):
            raise Exception(f"Failed to apply mode {mode}: htmode verification failed")

    def set_channel_auto(self):
        """
        Resets both 2.4GHz and 5GHz channels to automatic selection.
        """
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
        """
        Resets WiFi modes to default (maximum compatibility: 11b/g/n/ax for 2.4GHz, 11a/n/ac/ax for 5GHz).
        """
        try:
            self.connect_ssh()
            logger.info("Resetting modes to default")

            self.change_standard(NetworkConfig.DEVICE_2G, "11b/g/n/ax")
            self.change_standard(NetworkConfig.DEVICE_5G, "11a/n/ac/ax")
        except Exception as e:
            logger.error(f"Error resetting modes: {e}")