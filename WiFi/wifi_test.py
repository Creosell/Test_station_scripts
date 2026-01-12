# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "paramiko",
#     "tenacity",
# ]
# ///

import time
import os
import subprocess
import logging
import platform
from tenacity import retry, stop_after_attempt, wait_fixed
import paramiko
from datetime import datetime

# --- Configuration ---
ROUTER_IP = "192.168.9.1"
ROUTER_USER = "root"
IPERF_SERVER = "192.168.8.1"
SSH_TIMEOUT = 10
CONNECTION_RETRY_DELAY = 5
WIFI_CONNECTION_TIMEOUT = 30
ENCRYPTIONS = ["psk", "psk2", "psk-mixed", "sae", "sae-mixed"]
WIFI_STANDARDS_2G = ["11b", "11g", "11n", "11ax"]
WIFI_STANDARDS_5G = ["11a", "11n", "11ac", "11ax"]

NETWORKS = {
    "2G": {
        "ssid": "QA_Test_2G",
        "password": "66668888",
        "device": "mt798111",
        "encryption": "psk2",
        # Russia allows channels 1-13
        "channels": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    },
    "5G": {
        "ssid": "QA_Test_5G",
        "password": "66668888",
        "device": "mt798112",
        "encryption": "sae-mixed",
        "channels": [36, 40, 44, 48, 149, 153, 157, 161, 165]
    }
}

# --- Logging ---

logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class WiFiChannelTester:
    """
    Handles WiFi connection testing, channel/standard switching via SSH, and iperf throughput verification.
    Supports both Linux (nmcli) and Windows (netsh) platforms.
    """

    def __init__(self):
        """
        Initializes the WiFiChannelTester and detects the operating system.
        """
        self.ssh_client = None
        self.current_network = None
        self.os_type = platform.system()
        logger.info(f"Detected OS: {self.os_type}")

    def forget_wifi_networks(self):
        """
        Removes WiFi connection profiles for the configured networks.
        Uses 'nmcli' on Linux and 'netsh' on Windows.
        """
        profile_names = ["QA_Test_2G", "QA_Test_5G"]
        for name in profile_names:
            if self.os_type == "Linux":
                subprocess.run(["nmcli", "connection", "delete", "id", name], capture_output=True)
            elif self.os_type == "Windows":
                subprocess.run(["netsh", "wlan", "delete", "profile", f"name={name}"], capture_output=True)
            logger.info(f"Attempted to delete profile: {name}")

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def connect_to_wifi(self, ssid, password, encryption_type="psk2"):
        """
        Establishes a WiFi connection to the specified SSID.

        :param ssid: The SSID of the network to connect to.
        :param password: The password for the network.
        :param encryption_type: The encryption method (default: "psk2").
        :return: True if connection is successful.
        :raises Exception: If connection fails after retries.
        :raises FileNotFoundError: If the XML profile is missing on Windows.
        """
        logger.info(f"Connecting to WiFi network: {ssid} on {self.os_type}")

        if self.os_type == "Linux":
            subprocess.run(['nmcli', 'radio', 'wifi', 'off'], capture_output=True, text=True)
            time.sleep(5)
            subprocess.run(['nmcli', 'radio', 'wifi', 'on'], capture_output=True, text=True)
            time.sleep(5)
            result = subprocess.run(
                ['nmcli', 'device', 'wifi', 'connect', ssid, 'password', password],
                capture_output=True, text=True
            )
        elif self.os_type == "Windows":
            profile_file = f"{ssid}.xml"
            if not os.path.exists(profile_file):
                logger.error(f"WiFi profile {profile_file} not found!")
                raise FileNotFoundError(f"{profile_file} not found. Windows requires an XML profile for connection.")

            subprocess.run(['netsh', 'wlan', 'add', 'profile', f'filename={profile_file}'], capture_output=True,
                           text=True)
            time.sleep(2)
            result = subprocess.run(['netsh', 'wlan', 'connect', f'name={ssid}'], capture_output=True, text=True)
        else:
            raise NotImplementedError(f"OS {self.os_type} is not supported")

        time.sleep(WIFI_CONNECTION_TIMEOUT)

        if self.verify_wifi_connection(ssid):
            logger.info(f"Successfully connected to {ssid}")
            self.current_network = ssid
            return True
        else:
            if result.stderr:
                logger.error(f"Connection command stderr: {result.stderr}")
            logger.error(f"Failed to connect to {ssid}")
            raise Exception(f"WiFi connection failed: {ssid}")

    def verify_wifi_connection(self, expected_ssid):
        """
        Verifies if the active WiFi connection matches the expected SSID.

        :param expected_ssid: The SSID to verify against.
        :return: True if the active connection matches the expected SSID, False otherwise.
        """
        if self.os_type == "Linux":
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line.startswith('yes:'):
                        ssid = line.split(':', 1)[1]
                        if ssid == expected_ssid:
                            return True
        elif self.os_type == "Windows":
            result = subprocess.run(['netsh', 'wlan', 'show', 'interfaces'],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'SSID' in line and expected_ssid in line:
                        return True
        return False

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(CONNECTION_RETRY_DELAY))
    def connect_ssh(self):
        """
        Establishes an SSH connection to the router.

        :return: True if the connection and test command are successful.
        :raises Exception: If the SSH connection or test command fails.
        """
        if self.ssh_client:
            self.ssh_client.close()
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        logger.info(f"SSH: {ROUTER_USER}@{ROUTER_IP}")
        self.ssh_client.connect(
            ROUTER_IP,
            username=ROUTER_USER,
            password="66668888",
            timeout=SSH_TIMEOUT,
            look_for_keys=False,
            allow_agent=False
        )
        stdin, stdout, stderr = self.ssh_client.exec_command('echo "SSH connection test"')
        if "SSH connection test" in stdout.read().decode():
            logger.info("SSH connection established")
            return True
        else:
            raise Exception("SSH test command failed")

    def change_wifi_channel(self, device, channel):
        """
        Changes the WiFi channel for a specific device via UCI commands over SSH.

        :param device: The interface/device name (e.g., mt798111).
        :param channel: The target channel number.
        """
        logger.info(f"Changing channel for {device} to {channel}")
        self.ssh_client.exec_command(f"uci set wireless.{device}.channel={channel}")
        self.ssh_client.exec_command("uci commit wireless")
        self.ssh_client.exec_command("wifi reload")
        logger.info(f"Channel {channel} set, waiting for WiFi restart")
        time.sleep(15)

    def change_wifi_encryption(self, device, encryption, password):
        """
        Changes the WiFi encryption settings via UCI commands over SSH.

        :param device: The interface/device name.
        :param encryption: The encryption type.
        :param password: The network key.
        """
        logger.info(f"Changing encryption for {device} to {encryption}")
        self.ssh_client.exec_command(f"uci set wireless.{device}.encryption={encryption}")
        self.ssh_client.exec_command(f"uci set wireless.{device}.key='{password}'")
        self.ssh_client.exec_command("uci commit wireless")
        self.ssh_client.exec_command("wifi reload")
        logger.info(f"Encryption {encryption} set, waiting for WiFi restart")
        time.sleep(15)

    def change_wifi_standard(self, device, standard):
        """
        Changes the WiFi hardware mode (standard) via UCI commands over SSH.

        :param device: The interface/device name.
        :param standard: The hardware mode.
        """
        logger.info(f"Changing standard for {device} to {standard}")
        self.ssh_client.exec_command(f"uci set wireless.{device}.hwmode={standard}")
        self.ssh_client.exec_command("uci commit wireless")
        self.ssh_client.exec_command("wifi reload")
        logger.info(f"Standard {standard} set, waiting for WiFi restart")
        time.sleep(15)

    def get_current_channel(self, device):
        """
        Retrieves the current channel setting from the router via SSH.

        :param device: The interface/device name.
        :return: The current channel as a string.
        """
        stdin, stdout, stderr = self.ssh_client.exec_command(f"uci get wireless.{device}.channel")
        output = stdout.read().decode().strip()
        logger.info(f"Current channel for {device}: {output}")
        return output

    def get_current_hwmode(self, device):
        """
        Retrieves the current hardware mode (standard) from the router via SSH.

        :param device: The interface/device name.
        :return: The current hwmode as a string.
        """
        stdin, stdout, stderr = self.ssh_client.exec_command(f"uci get wireless.{device}.hwmode")
        output = stdout.read().decode().strip()
        logger.info(f"Current hwmode for {device}: {output}")
        return output

    def run_iperf_test(self):
        """
        Executes an iperf3 client test against the configured server.
        Detects OS to determine the correct executable command.

        :return: The stdout output of the iperf3 command if successful, None otherwise.
        """
        logger.info(f"Running iperf3 to {IPERF_SERVER} on {self.os_type}")

        iperf_cmd = []
        if self.os_type == "Linux":
            iperf_cmd = ['iperf3', '-c', IPERF_SERVER, '-t', '10']
        elif self.os_type == "Windows":
            iperf_cmd = ['./iperf.exe', '-c', IPERF_SERVER, '-t', '10']

        result = subprocess.run(iperf_cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            logger.info(f"iperf3 completed successfully:\n{result.stdout}")
            return result.stdout
        else:
            logger.error(f"iperf3 error: {result.stderr}")
            return None

    def set_channel_auto(self):
        """
        Resets both 2G and 5G radios to automatic channel selection via SSH.
        """
        try:
            self.connect_ssh()
            logger.info("Switching 2G channel to auto")
            self.ssh_client.exec_command("uci set wireless.mt798111.channel=auto")
            logger.info("Switching 5G channel to auto")
            self.ssh_client.exec_command("uci set wireless.mt798112.channel=auto")
            self.ssh_client.exec_command("uci commit wireless")
            self.ssh_client.exec_command("wifi reload")
            logger.info("Channels for both radios set to auto")
        except Exception as e:
            logger.error(f"Error setting channels to auto: {e}")

    def set_standard_auto(self):
        """
        Resets both radios' hwmode to a default (11n) for compatibility via SSH.
        """
        try:
            self.connect_ssh()
            logger.info("Resetting 2G hwmode to 11n")
            self.ssh_client.exec_command("uci set wireless.mt798111.hwmode=11n")
            logger.info("Resetting 5G hwmode to 11n")
            self.ssh_client.exec_command("uci set wireless.mt798112.hwmode=11n")
            self.ssh_client.exec_command("uci commit wireless")
            self.ssh_client.exec_command("wifi reload")
            logger.info("Hwmodes for both radios reset to default")
        except Exception as e:
            logger.error(f"Error resetting hwmodes: {e}")

    def test_network_channels(self, band, network_config):
        """
        Orchestrates the testing process for a specific frequency band.
        Iterates through all configured standards and then through all channels for each standard.

        :param band: The frequency band label (e.g., "2G", "5G").
        :param network_config: A dictionary containing network configuration details.
        """
        ssid = network_config["ssid"]
        password = network_config["password"]
        device = network_config["device"]
        encryption = network_config["encryption"]
        channels = network_config["channels"]
        standards = WIFI_STANDARDS_2G if band == "2G" else WIFI_STANDARDS_5G

        self.forget_wifi_networks()
        time.sleep(5)
        self.connect_to_wifi(ssid, password, encryption)

        for standard in standards:
            logger.info(f"Testing standard {standard} ({band})")
            try:
                self.connect_ssh()
                self.change_wifi_standard(device, standard)
                self.ssh_client.close()
                for attempt in range(15):
                    try:
                        self.connect_ssh()
                        current_hwmode = self.get_current_hwmode(device)
                        if current_hwmode == standard:
                            logger.info(f"Standard switched successfully to {standard}")
                            break
                        self.ssh_client.close()
                    except Exception as e:
                        logger.info(f"Waiting for SSH after standard change ({attempt + 1}/15): {e}")
                    time.sleep(4)
                else:
                    logger.error(f"Standard did not switch to {standard} within the allotted time")
                    continue

                if not self.verify_wifi_connection(ssid):
                    logger.warning(f"WiFi disconnected after standard change, reconnecting to {ssid}")
                    self.connect_to_wifi(ssid, password, encryption)

                for channel in channels:
                    logger.info(f"Testing channel {channel} with standard {standard} ({band})")
                    try:
                        self.connect_ssh()
                        self.change_wifi_channel(device, channel)
                        self.ssh_client.close()
                        for attempt in range(15):
                            try:
                                self.connect_ssh()
                                current_channel = self.get_current_channel(device)
                                if str(current_channel) == str(channel):
                                    logger.info(f"Channel switched successfully to {channel}")
                                    break
                                self.ssh_client.close()
                            except Exception as e:
                                logger.info(f"Waiting for SSH after channel change ({attempt + 1}/15): {e}")
                            time.sleep(4)
                        else:
                            logger.error(f"Channel did not switch to {channel} within the allotted time")
                            continue

                        if not self.verify_wifi_connection(ssid):
                            logger.warning(f"WiFi disconnected, reconnecting to {ssid}")
                            self.connect_to_wifi(ssid, password, encryption)

                        self.run_iperf_test()
                    except Exception as e:
                        logger.error(f"Error on channel {channel} with standard {standard}: {e}")
                        continue
            except Exception as e:
                logger.error(f"Error on standard {standard}: {e}")
                continue
        logger.info(f"Testing for {band} completed")

    def run_full_test(self):
        """
        Executes the full suite of WiFi tests for both 2G and 5G bands.
        """
        logger.info("Starting full WiFi channels, standards, and encryption test")
        try:
            self.test_network_channels("2G", NETWORKS["2G"])
            time.sleep(30)
            self.test_network_channels("5G", NETWORKS["5G"])
            self.set_channel_auto()
            self.set_standard_auto()
            logger.info("Switched to auto channel and default standard settings")
        except Exception as e:
            logger.error(f"Test finished with error: {e}")
        finally:
            if self.ssh_client:
                self.ssh_client.close()
            logger.info("Shutting down")


def main():
    """
    Entry point for the script.
    """
    tester = WiFiChannelTester()
    tester.run_full_test()


if __name__ == "__main__":
    main()