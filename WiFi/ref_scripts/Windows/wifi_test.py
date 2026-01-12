# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "paramiko",
#     "tenacity",
# ]
# ///

import time
import os
import re
import subprocess
import logging
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
WIFI_STANDARDS_2G = ["11b", "11g", "11n"]
WIFI_STANDARDS_5G = ["11a", "11n", "11ac", "11ax"]

NETWORKS = {
    "2G": {
        "ssid": "QC_11_2G",
        "password": "66668888",
        "device": "mt798111",
        "encryption": "psk2",
        "channels": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    },
    "5G": {
        "ssid": "QC_11_5G",
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
    Handles WiFi connection testing, channel switching via SSH, and iperf throughput verification on Windows systems.
    """

    def __init__(self):
        """
        Initializes the WiFiChannelTester with default state.
        """
        self.ssh_client = None
        self.current_network = None

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def connect_to_wifi(self, ssid, password, encryption_type="psk2"):
        """
        Establishes a WiFi connection to the specified SSID using netsh and XML profiles.

        :param ssid: The SSID of the network to connect to.
        :param password: The password for the network.
        :param encryption_type: The encryption method (default: "psk2").
        :return: True if connection is successful.
        :raises FileNotFoundError: If the XML profile file is missing.
        :raises Exception: If connection fails.
        """
        profile_file = f"{ssid}.xml"
        if not os.path.exists(profile_file):
            logger.error(f"WiFi profile {profile_file} not found!")
            raise FileNotFoundError(f"{profile_file} not found")
        logger.info(f"Adding WiFi profile: {profile_file}")
        subprocess.run(['netsh', 'wlan', 'add', 'profile', f'filename={profile_file}'], capture_output=True, text=True)
        time.sleep(5)
        logger.info(f"Connecting to network: {ssid}")
        subprocess.run(['netsh', 'wlan', 'connect', f'name={ssid}'], capture_output=True, text=True)
        time.sleep(WIFI_CONNECTION_TIMEOUT)
        if self.verify_wifi_connection(ssid):
            logger.info(f"Successfully connected to {ssid}")
            self.current_network = ssid
            return True
        else:
            logger.error(f"Failed to connect to {ssid}")
            raise Exception(f"WiFi connection failed: {ssid}")

    def verify_wifi_connection(self, expected_ssid):
        """
        Verifies if the active WiFi connection matches the expected SSID using netsh.

        :param expected_ssid: The SSID to verify against.
        :return: True if the active connection matches the expected SSID, False otherwise.
        """
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

    def run_iperf_test(self):
        """
        Executes an iperf client test against the configured server.

        :return: The stdout output of the iperf command if successful, None otherwise.
        """
        logger.info(f"Running iperf to {IPERF_SERVER}")
        result = subprocess.run(['./iperf.exe', '-c', IPERF_SERVER, '-t', '10'],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            logger.info(f"Iperf completed successfully:\n{result.stdout}")
            return result.stdout
        else:
            logger.error(f"Iperf error: {result.stderr}")
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

    def test_network_channels(self, band, network_config):
        """
        Orchestrates the channel testing process for a specific frequency band.

        :param band: The frequency band label.
        :param network_config: A dictionary containing network configuration details.
        """
        ssid = network_config["ssid"]
        password = network_config["password"]
        device = network_config["device"]
        encryption = network_config["encryption"]
        channels = network_config["channels"]
        self.connect_to_wifi(ssid, password, encryption)
        for channel in channels:
            logger.info(f"Testing channel {channel} ({band})")
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
                logger.error(f"Error on channel {channel}: {e}")
                continue
        logger.info(f"Testing for {band} completed")

    def run_full_test(self):
        """
        Executes the full suite of WiFi tests for both 2G and 5G bands.
        """
        logger.info("Starting full WiFi channels and encryption test")
        try:
            self.test_network_channels("2G", NETWORKS["2G"])
            time.sleep(30)
            self.test_network_channels("5G", NETWORKS["5G"])
            self.set_channel_auto()
            logger.info("Switched to auto channel settings")
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