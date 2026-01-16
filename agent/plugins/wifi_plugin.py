"""
WiFi test plugin for agent.
Contains WiFi-specific test commands.
"""

import os
import time
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger("WifiPlugin")


class WifiPlugin:
    """WiFi test plugin for agent."""

    def __init__(self):
        from agent.agent_device_manager import AgentDeviceManager
        self.device_mgr = AgentDeviceManager()
        self.os_type = self.device_mgr.os_type

        # Import config after deployment
        from core.config import NetworkConfig, Timings, Limits, Paths
        try:
            self.NetworkConfig = NetworkConfig
            self.Timings = Timings
            self.Limits = Limits
            self.Paths = Paths
        except ImportError:
            logger.warning("Config not yet available (will be deployed)")

        # Ensure profiles directory exists for Windows
        if self.os_type == "Windows":
            try:
                profiles_dir = Path(__file__).parent.parent / "resources" / "wifi_profiles"
                os.makedirs(str(profiles_dir), exist_ok=True)
            except:
                pass

    def execute(self, command, args):
        """
        Execute WiFi command.

        :param command: Command name
        :param args: Parsed arguments
        :return: Command result
        """
        kwargs = self._parse_args(args)

        if command == 'connect':
            return self._connect(**kwargs)
        elif command == 'iperf':
            return self._run_iperf(**kwargs)
        elif command == 'init_report':
            return self._init_report(**kwargs)
        elif command == 'add_result':
            return self._add_result(**kwargs)
        elif command == 'forget':
            return self._forget_all()
        elif command == 'prevent_sleep':
            return self.device_mgr.prevent_sleep()
        elif command == 'allow_sleep':
            return self.device_mgr.allow_sleep()
        else:
            raise ValueError(f"Unknown command: {command}")

    @staticmethod
    def _parse_args(args):
        """Convert ['--key', 'value'] to {'key': 'value'}."""
        kwargs = {}
        i = 0
        while i < len(args):
            if args[i].startswith('--'):
                key = args[i][2:]
                value = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith('--') else 'true'
                kwargs[key] = value
                i += 2 if value != 'true' else 1
            else:
                i += 1
        return kwargs

    def _connect(self, ssid, password, cleanup='false'):
        """Connect to WiFi network."""
        cleanup_bool = cleanup.lower() in ['true', '1', 'yes']
        success = self.connect_wifi(ssid, password, cleanup=cleanup_bool)
        return success

    def _run_iperf(self, port='5201'):
        """Run iperf test."""
        output = self.run_iperf(port=int(port))
        if output:
            return f"IPERF_OUTPUT_START\n{output}\nIPERF_OUTPUT_END"
        return None

    def _init_report(self, device_name, ip_address, report_dir):
        """Initialize HTML report."""
        report_path = self.initialize_report(device_name, ip_address, report_dir)
        if report_path:
            return f"REPORT_PATH:{report_path}"
        return None

    def _add_result(self, report_path, band, ssid, standard, channel, iperf_output):
        """Add test result to report."""
        success = self.add_test_result(report_path, band, ssid, standard, int(channel), iperf_output)
        return success

    def _forget_all(self):
        """Forget all WiFi networks."""
        self.forget_all_networks()
        return True

    def initialize_report(self, device_name, ip_address, report_dir):
        """
        Initialize HTML report on DUT for incremental updates.

        :param device_name: System product name
        :param ip_address: Device IP address
        :param report_dir: Directory path for report storage
        :return: Report file path or None
        """
        try:
            from core.core_report import CoreReportGenerator as ReportGenerator
            from pathlib import Path

            report_path = Path(report_dir)
            report_path.mkdir(parents=True, exist_ok=True)

            report_filename = ReportGenerator.generate_report_filename(device_name, ip_address)
            full_path = report_path / report_filename

            if self.os_type == "Windows":
                template_path = Path(__file__).parent.parent / "resources" / "report_template.html"
            else:
                template_path = Path("/tmp/wifi_test_agent/resources/report_template.html")

            report_gen = ReportGenerator(template_path, full_path)
            report_gen.generate(device_name, ip_address)

            logger.info(f"Report initialized: {full_path}")
            return str(full_path)

        except Exception as e:
            logger.error(f"Failed to initialize report: {e}")
            return None

    def add_test_result(self, report_path, band, ssid, standard, channel, iperf_output):
        """
        Add test result to existing HTML report.

        :param report_path: Path to existing report file
        :param band: Frequency band
        :param ssid: Network SSID
        :param standard: WiFi standard
        :param channel: Channel number
        :param iperf_output: Raw iperf output (may be base64)
        :return: True if successful
        """
        try:
            from core.core_report import CoreReportGenerator as ReportGenerator, IperfResult
            from pathlib import Path
            import base64
            import json

            # Decode iperf output if base64 encoded
            try:
                iperf_decoded = base64.b64decode(iperf_output).decode()
            except:
                iperf_decoded = iperf_output

            report_file = Path(report_path)
            if not report_file.exists():
                logger.error(f"Report file not found: {report_path}")
                return False

            # Extract device info from filename
            filename = report_file.stem
            parts = filename.rsplit('_', 2)
            device_name = parts[0].replace('-', ' ')
            ip_parts = parts[1].split('-')
            ip_address = '.'.join(ip_parts)

            if self.os_type == "Windows":
                template_path = Path(__file__).parent.parent / "resources" / "report_template.html"
            else:
                template_path = Path("/tmp/wifi_test_agent/resources/report_template.html")

            report_gen = ReportGenerator(template_path, report_file)

            # Load existing results from JSON sidecar
            json_path = report_file.with_suffix('.json')
            if json_path.exists():
                with open(json_path, 'r') as f:
                    data = json.load(f)
                    serialized_results = data.get('wifi_results', {})

                    for band_key, band_data in serialized_results.items():
                        report_gen.wifi_results[band_key] = {
                            'ssid': band_data.get('ssid', ''),
                            'tests': []
                        }

                        for test in band_data.get('tests', []):
                            result_data = test.get('result')
                            iperf_result = None

                            if result_data and result_data.get('bandwidth'):
                                iperf_result = IperfResult(bandwidth=result_data['bandwidth'])

                            report_gen.wifi_results[band_key]['tests'].append({
                                'standard': test.get('standard'),
                                'channel': test.get('channel'),
                                'result': iperf_result
                            })

            # Add new test result
            report_gen.add_wifi_test(band, ssid, standard, channel, iperf_decoded)

            # Serialize results to JSON
            def serialize_results(results):
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
                            'result': {'bandwidth': result.bandwidth if result else None} if result else None
                        })
                return serialized

            # Save to JSON sidecar
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

    @staticmethod
    def _create_windows_profile(ssid, password):
        """
        Generate XML profile for Windows WiFi connection.

        :param ssid: Network SSID
        :param password: Network password
        :return: Path to XML profile
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

        profiles_dir = Path(__file__).parent.parent / "resources" / "wifi_profiles"
        file_path = profiles_dir / f"{ssid}.xml"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(xml_content)
            logger.info(f"Generated Windows profile: {file_path}")
            return str(file_path)
        except Exception as e:
            logger.error(f"Failed to create profile: {e}")
            raise

    def forget_all_networks(self):
        """Remove all WiFi network profiles."""
        try:
            from modules.wifi.wifi_config import NETWORKS
            for config in NETWORKS.values():
                name = config["ssid"]
                if self.os_type == "Linux":
                    subprocess.run(["nmcli", "connection", "delete", "id", name], capture_output=True)
                elif self.os_type == "Windows":
                    subprocess.run(["netsh", "wlan", "delete", "profile", f"name={name}"], capture_output=True)
        except:
            pass

    def connect_wifi(self, ssid, password, cleanup=False):
        """
        Connect to WiFi network.

        :param ssid: Target network SSID
        :param password: Network password
        :param cleanup: Remove old profiles before connecting
        :return: True if successful
        """
        last_exception = None

        # Pre-create target profile (Windows)
        if self.os_type == "Windows":
            self._create_windows_profile(ssid, password)
            profile_path = Path(__file__).parent.parent / "resources" / "wifi_profiles" / f"{ssid}.xml"
            subprocess.run(['netsh', 'wlan', 'add', 'profile', f'filename={str(profile_path)}'], capture_output=True)

        # Cleanup old profiles
        if cleanup:
            logger.info("Cleaning up old profiles...")
            if self.os_type == "Windows":
                res = subprocess.run(['netsh', 'wlan', 'show', 'profiles'], capture_output=True, text=True)
                for line in res.stdout.split('\n'):
                    if "All User Profile" in line:
                        p_name = line.split(":", 1)[1].strip()
                        if p_name != ssid:
                            subprocess.run(["netsh", "wlan", "delete", "profile", f"name={p_name}"], capture_output=True)

        # Connection retry loop
        for attempt in range(1, self.Limits.WIFI_CONNECT_RETRIES + 1):
            try:
                try:
                    logger.info(f"Connecting to {ssid} (Attempt {attempt})...")
                except OSError:
                    pass

                if self.os_type == "Linux":
                    subprocess.run(['nmcli', 'radio', 'wifi', 'off'], capture_output=True)
                    time.sleep(self.Timings.WIFI_TOGGLE_DELAY)
                    subprocess.run(['nmcli', 'radio', 'wifi', 'on'], capture_output=True)
                    time.sleep(self.Timings.WIFI_TOGGLE_DELAY)
                    res = subprocess.run(['nmcli', 'device', 'wifi', 'connect', ssid, 'password', password],
                                        capture_output=True, text=True)

                elif self.os_type == "Windows":
                    time.sleep(self.Timings.PROFILE_ADD_DELAY)
                    res = subprocess.run(['netsh', 'wlan', 'connect', f'name={ssid}'], capture_output=True, text=True)

                time.sleep(self.Timings.WIFI_CONNECTION_TIMEOUT)

                if self.device_mgr._verify_connection(ssid):
                    logger.info(f"Connected to {ssid}")
                    return True
                else:
                    logger.warning(f"Connection attempt {attempt} failed")
                    raise Exception(f"Connection failed: {ssid}")

            except Exception as e:
                last_exception = e
                time.sleep(2)

        if last_exception:
            raise last_exception
        return False

    def run_iperf(self, port=5201):
        """
        Run iperf3 test.

        :param port: iperf server port
        :return: iperf output or None
        """
        cmd = ['iperf3', '-c', self.NetworkConfig.IPERF_SERVER_IP, '-p', str(port), '-t', self.Timings.IPERF_DURATION]

        logger.info(f"Running iperf3 -> {self.NetworkConfig.IPERF_SERVER_IP}:{port}")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=self.Timings.IPERF_TIMEOUT)

            if res.returncode == 0:
                logger.info("Iperf success")
                return res.stdout
            else:
                logger.error(f"Iperf failed: {res.stderr}")
                return None
        except Exception as e:
            logger.error(f"Iperf execution error: {e}")
            return None
