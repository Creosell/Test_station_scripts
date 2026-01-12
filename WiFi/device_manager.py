# ... (Imports unchanged) ...
import os
import time
import subprocess
import logging
import platform
from tenacity import retry, stop_after_attempt, wait_fixed
from config import NetworkConfig, Timings, Limits, NETWORKS, Paths

logger = logging.getLogger("DeviceMgr")


class DeviceManager:
    # ... (init unchanged) ...
    def __init__(self):
        self.os_type = platform.system()
        logger.info(f"Detected OS: {self.os_type}")

        # Ensure profiles directory exists
        if self.os_type == "Windows":
            # Paths.PROFILES_DIR is now a Path object, os.makedirs accepts it in Py3.6+
            # but str() ensures compatibility
            os.makedirs(str(Paths.PROFILES_DIR), exist_ok=True)

    def _create_windows_profile(self, ssid, password):
        """Generates XML profile for Windows in the resources/wifi_profiles folder."""
        # ... (XML string content unchanged) ...
        xml_content = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>auto</connectionMode>
  <MSM>
    <security>
      <authEncryption>
        <authentication>WPA2PSK</authentication>
        <encryption>AES</encryption>
        <useOneX>false</useOneX>
      </authEncryption>
      <sharedKey>
        <keyType>passPhrase</keyType>
        <protected>false</protected>
        <keyMaterial>{password}</keyMaterial>
      </sharedKey>
    </security>
  </MSM>
</WLANProfile>"""

        # Use pathlib operator /
        file_path = Paths.PROFILES_DIR / f"{ssid}.xml"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(xml_content)
            logger.info(f"Generated Windows profile: {file_path}")
            return str(file_path)  # Return string for subprocess usage
        except Exception as e:
            logger.error(f"Failed to create profile {file_path}: {e}")
            raise

    # ... (forget_all_networks unchanged) ...
    def forget_all_networks(self):
        """Removes profiles for all configured networks."""
        for config in NETWORKS.values():
            name = config["ssid"]
            if self.os_type == "Linux":
                subprocess.run(["nmcli", "connection", "delete", "id", name], capture_output=True)
            elif self.os_type == "Windows":
                subprocess.run(["netsh", "wlan", "delete", "profile", f"name={name}"], capture_output=True)

    @retry(stop=stop_after_attempt(Limits.WIFI_CONNECT_RETRIES), wait=wait_fixed(2))
    def connect_wifi(self, ssid, password):
        # ... (Linux part unchanged) ...
        logger.info(f"Connecting to {ssid}...")

        if self.os_type == "Linux":
            subprocess.run(['nmcli', 'radio', 'wifi', 'off'], capture_output=True)
            time.sleep(Timings.WIFI_TOGGLE_DELAY)
            subprocess.run(['nmcli', 'radio', 'wifi', 'on'], capture_output=True)
            time.sleep(Timings.WIFI_TOGGLE_DELAY)
            res = subprocess.run(['nmcli', 'device', 'wifi', 'connect', ssid, 'password', password],
                                 capture_output=True, text=True)

        elif self.os_type == "Windows":
            # Check if profile exists
            profile_path = Paths.PROFILES_DIR / f"{ssid}.xml"

            if not profile_path.exists():
                self._create_windows_profile(ssid, password)

            # Convert Path to string for subprocess
            subprocess.run(['netsh', 'wlan', 'add', 'profile', f'filename={str(profile_path)}'], capture_output=True)
            time.sleep(Timings.PROFILE_ADD_DELAY)
            res = subprocess.run(['netsh', 'wlan', 'connect', f'name={ssid}'], capture_output=True, text=True)
        else:
            raise NotImplementedError("OS not supported")

        time.sleep(Timings.WIFI_CONNECTION_TIMEOUT)

        if self._verify_connection(ssid):
            logger.info(f"Connected to {ssid}")
            return True
        else:
            logger.error(f"Failed to connect. Stderr: {res.stderr if res.stderr else 'None'}")
            raise Exception(f"Connection failed: {ssid}")

    # ... (_verify_connection unchanged) ...
    def _verify_connection(self, expected_ssid):
        if self.os_type == "Linux":
            cmd = ['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi']
        elif self.os_type == "Windows":
            cmd = ['netsh', 'wlan', 'show', 'interfaces']

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=Timings.CMD_VERIFY_TIMEOUT)
            if res.returncode == 0:
                if self.os_type == "Linux":
                    for line in res.stdout.strip().split('\n'):
                        if line.startswith('yes:') and line.split(':', 1)[1] == expected_ssid:
                            return True
                elif self.os_type == "Windows":
                    if expected_ssid in res.stdout:
                        return True
        except Exception:
            pass
        return False

    def run_iperf(self):
        """Runs iperf3 test using the executable from resources folder on Windows."""

        if self.os_type == "Windows":
            iperf_path = Paths.IPERF_EXE_WIN
            if not iperf_path.exists():
                logger.error(f"Iperf executable not found at: {iperf_path}")
                logger.error(f"Please place 'iperf.exe' (and 'cygwin1.dll') in the '{Paths.RESOURCES_DIR}' folder.")
                return None
            cmd_base = [str(iperf_path)]  # Explicit conversion
        else:
            cmd_base = ['iperf3']

        cmd = cmd_base + ['-c', NetworkConfig.IPERF_SERVER_IP, '-t', Timings.IPERF_DURATION]

        logger.info(f"Running iperf3 -> {NetworkConfig.IPERF_SERVER_IP}")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=Timings.IPERF_TIMEOUT)

            if res.returncode == 0:
                logger.info(f"Iperf success:\n{res.stdout.strip()}")
                return res.stdout
            else:
                logger.error(f"Iperf failed: {res.stderr}")
                return None
        except Exception as e:
            logger.error(f"Iperf execution error: {e}")
            return None