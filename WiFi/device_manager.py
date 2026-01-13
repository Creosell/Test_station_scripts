import os
import time
import subprocess
import logging
import platform
from config import NetworkConfig, Timings, Limits, NETWORKS, Paths

logger = logging.getLogger("DeviceMgr")


class DeviceManager:
    """
    Manages network interfaces and connections on the local device (Agent side).
    """

    def __init__(self):
        """
        Initializes the DeviceManager, detects OS, and prepares necessary directories.
        """
        self.os_type = platform.system()
        logger.info(f"Detected OS: {self.os_type}")

        # Ensure profiles directory exists for Windows
        if self.os_type == "Windows":
            os.makedirs(str(Paths.PROFILES_DIR), exist_ok=True)

    def get_system_product_name(self):
        """
        Retrieves system product name (manufacturer model).

        :return: System product name string or "Unknown" if retrieval fails
        """
        try:
            if self.os_type == "Windows":
                # Use PowerShell to get system product name
                cmd = ['powershell', '-Command',
                       'Get-CimInstance -ClassName Win32_ComputerSystemProduct | Select-Object -ExpandProperty Name']
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()

            elif self.os_type == "Linux":
                # Try dmidecode first (requires root/sudo)
                try:
                    res = subprocess.run(['sudo', 'dmidecode', '-s', 'system-product-name'],
                                         capture_output=True, text=True, timeout=5)
                    if res.returncode == 0 and res.stdout.strip():
                        return res.stdout.strip()
                except:
                    pass

                # Fallback: read from /sys filesystem
                try:
                    with open('/sys/devices/virtual/dmi/id/product_name', 'r') as f:
                        product = f.read().strip()
                        if product:
                            return product
                except:
                    pass

            # Fallback: use hostname
            return platform.node()

        except Exception as e:
            logger.warning(f"Could not retrieve system product name: {e}")
            return "Unknown"

    def _create_windows_profile(self, ssid, password):
        """
        Generates XML profile for Windows in the resources/wifi_profiles folder.

        :param ssid: The SSID of the network.
        :param password: The password for the network.
        :return: Path to the created XML profile as a string.
        """
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

        file_path = Paths.PROFILES_DIR / f"{ssid}.xml"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(xml_content)
            logger.info(f"Generated Windows profile: {file_path}")
            return str(file_path)
        except Exception as e:
            logger.error(f"Failed to create profile {file_path}: {e}")
            raise

    def forget_all_networks(self):
        """
        Removes profiles for all configured networks based on the OS.
        """
        for config in NETWORKS.values():
            name = config["ssid"]
            if self.os_type == "Linux":
                subprocess.run(["nmcli", "connection", "delete", "id", name], capture_output=True)
            elif self.os_type == "Windows":
                subprocess.run(["netsh", "wlan", "delete", "profile", f"name={name}"], capture_output=True)

    def connect_wifi(self, ssid, password, cleanup=False):
        """
        Connects to WiFi. If cleanup=True, it removes other profiles explicitly.
        CRITICAL: On Windows, removing the active profile drops the connection immediately.
        This script assumes it continues running locally even if SSH drops.

        :param ssid: Target network SSID.
        :param password: Network password.
        :param cleanup: If True, remove all other network profiles before connecting.
        :return: True if connection successful, False otherwise.
        """
        last_exception = None

        # Step 1: Pre-create target profile (ensures we have something to connect to after cleanup)
        if self.os_type == "Windows":
            # Always create/update target network profile BEFORE removing others
            self._create_windows_profile(ssid, password)
            profile_path = Paths.PROFILES_DIR / f"{ssid}.xml"
            subprocess.run(['netsh', 'wlan', 'add', 'profile', f'filename={str(profile_path)}'], capture_output=True)

        # Step 2: Cleanup old network profiles (if requested)
        if cleanup:
            logger.info("Cleaning up old profiles...")
            # Important: Do not delete the profile we just created (ssid)
            if self.os_type == "Windows":
                # Get list of all profiles
                res = subprocess.run(['netsh', 'wlan', 'show', 'profiles'], capture_output=True, text=True)
                for line in res.stdout.split('\n'):
                    if "All User Profile" in line:
                        p_name = line.split(":", 1)[1].strip()
                        if p_name != ssid:  # Don't delete target network
                            subprocess.run(["netsh", "wlan", "delete", "profile", f"name={p_name}"],
                                           capture_output=True)
            elif self.os_type == "Linux":
                # For Linux (NetworkManager) - simplified implementation
                pass

        # Step 3: Connection retry loop (profile already created above, no need to recreate inside loop)
        for attempt in range(1, Limits.WIFI_CONNECT_RETRIES + 1):
            try:
                # Safe logging (handle potential OSError if SSH pipe broken)
                try:
                    logger.info(f"Connecting to {ssid} (Attempt {attempt})...")
                except OSError:
                    pass

                if self.os_type == "Linux":
                    # Linux connection sequence
                    subprocess.run(['nmcli', 'radio', 'wifi', 'off'], capture_output=True)
                    time.sleep(Timings.WIFI_TOGGLE_DELAY)
                    subprocess.run(['nmcli', 'radio', 'wifi', 'on'], capture_output=True)
                    time.sleep(Timings.WIFI_TOGGLE_DELAY)
                    res = subprocess.run(['nmcli', 'device', 'wifi', 'connect', ssid, 'password', password],
                                         capture_output=True, text=True)

                elif self.os_type == "Windows":
                    # Profile already created in Step 1
                    time.sleep(Timings.PROFILE_ADD_DELAY)
                    res = subprocess.run(['netsh', 'wlan', 'connect', f'name={ssid}'], capture_output=True, text=True)

                # Wait for connection to establish
                time.sleep(Timings.WIFI_CONNECTION_TIMEOUT)

                if self._verify_connection(ssid):
                    logger.info(f"Connected to {ssid}")
                    return True
                else:
                    error_msg = res.stderr if res.stderr else 'Unknown error'
                    logger.warning(f"Connection attempt {attempt} failed. Output: {res.stdout}")
                    raise Exception(f"Connection failed: {ssid}")

            except Exception as e:
                last_exception = e
                time.sleep(2)

        if last_exception:
            raise last_exception
        return False

    def _verify_connection(self, expected_ssid):
        """
        Verifies if the current active connection matches the expected SSID.

        :param expected_ssid: The SSID to check against.
        :return: True if connected to expected_ssid, False otherwise.
        """
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
        """
        Runs iperf3 test using the executable from resources folder on Windows or system iperf3 on Linux.

        :return: The stdout output of the iperf command or None if failed.
        """
        if self.os_type == "Windows":
            iperf_path = Paths.IPERF_EXE_WIN
            if not iperf_path.exists():
                logger.error(f"Iperf executable not found at: {iperf_path}")
                logger.error(f"Please place 'iperf.exe' (and 'cygwin1.dll') in the '{Paths.RESOURCES_DIR}' folder.")
                return None
            cmd_base = [str(iperf_path)]
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