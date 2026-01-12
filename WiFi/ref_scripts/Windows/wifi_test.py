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
log_filename = f"wifi_channel_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
handler = logging.FileHandler(log_filename, encoding='utf-8')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

class WiFiChannelTester:
    def __init__(self):
        self.ssh_client = None
        self.current_network = None

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def connect_to_wifi(self, ssid, password, encryption_type="psk2"):
        """Connect to WiFi using netsh and XML profile"""
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
        """Check current WiFi connection"""
        result = subprocess.run(['netsh', 'wlan', 'show', 'interfaces'],
                               capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'SSID' in line and expected_ssid in line:
                    return True
        return False

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(CONNECTION_RETRY_DELAY))
    def connect_ssh(self):
        """Connect to router via SSH"""
        if self.ssh_client:
            self.ssh_client.close()
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        logger.info(f"SSH: {ROUTER_USER}@{ROUTER_IP}")
        self.ssh_client.connect(
            ROUTER_IP,
            username=ROUTER_USER,
            password="66668888",  # <-- password here
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
        """Change WiFi channel via UCI"""
        logger.info(f"Changing channel for {device} to {channel}")
        self.ssh_client.exec_command(f"uci set wireless.{device}.channel={channel}")
        self.ssh_client.exec_command("uci commit wireless")
        self.ssh_client.exec_command("wifi reload")
        logger.info(f"Channel {channel} set, waiting for WiFi restart")
        time.sleep(15)

    def change_wifi_encryption(self, device, encryption, password):
        """Change WiFi encryption via UCI"""
        logger.info(f"Changing encryption for {device} to {encryption}")
        self.ssh_client.exec_command(f"uci set wireless.{device}.encryption={encryption}")
        self.ssh_client.exec_command(f"uci set wireless.{device}.key='{password}'")
        self.ssh_client.exec_command("uci commit wireless")
        self.ssh_client.exec_command("wifi reload")
        logger.info(f"Encryption {encryption} set, waiting for WiFi restart")
        time.sleep(15)

    def change_wifi_standard(self, device, standard):
        """Change WiFi standard (a/b/g/n/ac/ax) via UCI."""
        logger.info(f"Changing standard for {device} to {standard}")
        self.ssh_client.exec_command(f"uci set wireless.{device}.hwmode={standard}")
        self.ssh_client.exec_command("uci commit wireless")
        self.ssh_client.exec_command("wifi reload")
        logger.info(f"Standard {standard} set, waiting for WiFi restart")
        time.sleep(15)
    
    def get_current_channel(self, device):
        """Get current channel from router via SSH"""
        stdin, stdout, stderr = self.ssh_client.exec_command(f"uci get wireless.{device}.channel")
        output = stdout.read().decode().strip()
        logger.info(f"Current channel for {device}: {output}")
        return output    

    def run_iperf_test(self):
        """Run iperf"""
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
        Set both radios' channels (2G and 5G) to auto via SSH.
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
                        logger.info(f"Waiting for SSH after channel change ({attempt+1}/15): {e}")
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
    tester = WiFiChannelTester()
    tester.run_full_test()

if __name__ == "__main__":
    main()
