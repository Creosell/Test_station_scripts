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

    def prevent_sleep(self):
        """
        Prevent system sleep and screen timeout during testing.
        
        :return: True if successful, False otherwise
        """
        try:
            if self.os_type == "Windows":
                # Set power plan to High Performance and disable sleep/display timeout
                commands = [
                    # Set active power scheme to High Performance
                    'powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c',
                    # Disable monitor timeout (AC)
                    'powercfg /change monitor-timeout-ac 0',
                    # Disable sleep timeout (AC)
                    'powercfg /change standby-timeout-ac 0',
                    # Disable monitor timeout (DC/battery)
                    'powercfg /change monitor-timeout-dc 0',
                    # Disable sleep timeout (DC/battery)
                    'powercfg /change standby-timeout-dc 0'
                ]
                
                for cmd in commands:
                    subprocess.run(cmd.split(), capture_output=True)
                
                logger.info("Sleep prevention enabled (High Performance mode)")
                return True
            
            elif self.os_type == "Linux":
                # Disable DPMS screen blanking
                try:
                    subprocess.run(['xset', 's', 'off'], capture_output=True)
                    subprocess.run(['xset', '-dpms'], capture_output=True)
                    logger.info("Sleep prevention enabled (DPMS disabled)")
                    return True
                except:
                    logger.warning("Could not disable screen blanking (xset not available)")
                    return False
            
            return False
        
        except Exception as e:
            logger.warning(f"Failed to prevent sleep: {e}")
            return False

    def allow_sleep(self):
        """
        Re-enable system sleep and screen timeout after testing.
        
        :return: True if successful, False otherwise
        """
        try:
            if self.os_type == "Windows":
                # Restore default timeouts (15 min display, 30 min sleep for AC)
                commands = [
                    'powercfg /change monitor-timeout-ac 15',
                    'powercfg /change standby-timeout-ac 30',
                    'powercfg /change monitor-timeout-dc 5',
                    'powercfg /change standby-timeout-dc 15'
                ]
                
                for cmd in commands:
                    subprocess.run(cmd.split(), capture_output=True)
                
                logger.info("Sleep prevention disabled (timeouts restored)")
                return True
            
            elif self.os_type == "Linux":
                # Re-enable DPMS
                try:
                    subprocess.run(['xset', 's', 'on'], capture_output=True)
                    subprocess.run(['xset', '+dpms'], capture_output=True)
                    logger.info("Sleep prevention disabled (DPMS restored)")
                    return True
                except:
                    return False
            
            return False
        
        except Exception as e:
            logger.warning(f"Failed to allow sleep: {e}")
            return False

    def initialize_report(self, device_name, ip_address, report_dir):
        """
        Initialize HTML report on DUT for incremental updates.
        Creates empty report file with device info.
        
        :param device_name: System product name
        :param ip_address: Device IP address
        :param report_dir: Directory path for report storage
        :return: Report file path or None if failed
        """
        try:
            # Import ReportGenerator locally (only available on DUT after deployment)
            from report_generator import ReportGenerator
            from pathlib import Path
            
            # Ensure report directory exists
            report_path = Path(report_dir)
            report_path.mkdir(parents=True, exist_ok=True)
            
            # Generate report filename
            report_filename = ReportGenerator.generate_report_filename(device_name, ip_address)
            full_path = report_path / report_filename
            
            # Find template (should be deployed to resources/)
            if self.os_type == "Windows":
                template_path = Path(__file__).parent / "resources" / "report_template.html"
            else:
                template_path = Path("/tmp/wifi_test_agent/resources/report_template.html")
            
            # Create initial report
            report_gen = ReportGenerator(template_path, full_path)
            report_gen.generate(device_name, ip_address)
            
            logger.info(f"Report initialized: {full_path}")
            return str(full_path)
        
        except Exception as e:
            logger.error(f"Failed to initialize report: {e}")
            return None

    def add_test_result(self, report_path, band, ssid, standard, channel, iperf_output):
        """
        Add test result to existing HTML report and regenerate.
        
        :param report_path: Path to existing report file
        :param band: Frequency band (e.g., "2.4 GHz")
        :param ssid: Network SSID
        :param standard: WiFi standard (e.g., "802.11n")
        :param channel: Channel number
        :param iperf_output: Raw iperf output string (may be base64 encoded)
        :return: True if successful, False otherwise
        """
        try:
            from report_generator import ReportGenerator
            from pathlib import Path
            import base64
            import json
            
            # Decode iperf output if base64 encoded
            try:
                iperf_decoded = base64.b64decode(iperf_output).decode()
            except:
                # Not base64, use as-is
                iperf_decoded = iperf_output
            
            report_file = Path(report_path)
            if not report_file.exists():
                logger.error(f"Report file not found: {report_path}")
                return False
            
            # Extract device name and IP from filename
            # Format: DeviceName_IP_Timestamp.html
            filename = report_file.stem
            parts = filename.rsplit('_', 2)
            device_name = parts[0].replace('-', ' ')
            ip_parts = parts[1].split('-')
            ip_address = '.'.join(ip_parts)
            
            # Find template
            if self.os_type == "Windows":
                template_path = Path(__file__).parent / "resources" / "report_template.html"
            else:
                template_path = Path("/tmp/wifi_test_agent/resources/report_template.html")
            
            # Load existing report data (parse from HTML or use separate JSON)
            # For simplicity, recreate ReportGenerator and rebuild
            report_gen = ReportGenerator(template_path, report_file)
            
            # Load existing results from JSON sidecar file
            json_path = report_file.with_suffix('.json')
            if json_path.exists():
                with open(json_path, 'r') as f:
                    data = json.load(f)
                    serialized_results = data.get('wifi_results', {})
                    
                    # Deserialize back to proper structure with IperfResult objects
                    from report_generator import IperfResult
                    
                    for band_key, band_data in serialized_results.items():
                        # Initialize band if not exists
                        report_gen.wifi_results[band_key] = {
                            'ssid': band_data.get('ssid', ''),
                            'tests': []
                        }
                        
                        # Restore all tests for this band
                        for test in band_data.get('tests', []):
                            result_data = test.get('result')
                            iperf_result = None
                            
                            if result_data and result_data.get('transfer') and result_data.get('bandwidth'):
                                iperf_result = IperfResult(
                                    transfer=result_data['transfer'],
                                    bandwidth=result_data['bandwidth']
                                )
                            
                            report_gen.wifi_results[band_key]['tests'].append({
                                'standard': test.get('standard'),
                                'channel': test.get('channel'),
                                'result': iperf_result
                            })


            
            # Add new test result
            report_gen.add_wifi_test(band, ssid, standard, channel, iperf_decoded)
            
            # Serialize wifi_results to JSON-compatible format
            def serialize_results(results):
                """Convert IperfResult objects to dicts for JSON serialization."""
                serialized = {}
                for band_key, band_data in results.items():
                    serialized[band_key] = {
                        'ssid': band_data.get('ssid', ''),
                        'tests': []
                    }
                    for test in band_data.get('tests', []):
                        result = test.get('result')
                        serialized[band_key]['tests'].append({
                            'standard': test.get('standard'),
                            'channel': test.get('channel'),
                            'result': {
                                'transfer': result.transfer if result else None,
                                'bandwidth': result.bandwidth if result else None
                            } if result else None
                        })
                return serialized
            
            # Save results to JSON sidecar
            with open(json_path, 'w') as f:
                json.dump({
                    'device_name': device_name,
                    'ip_address': ip_address,
                    'wifi_results': serialize_results(report_gen.wifi_results)
                }, f, indent=2)
            
            # Regenerate HTML
            report_gen.generate(device_name, ip_address)
            
            logger.info(f"Test result added: {band} / {standard} / Ch{channel}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to add test result: {e}")
            return False

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
