# wifi_orchestrator.py
import time
import logging
from pathlib import Path
from config import (setup_logging, NETWORKS, WIFI_STANDARDS_2G, WIFI_STANDARDS_5G,
                    Timings, Limits, DUTConfig, ReportPaths)
from router_manager import RouterManager
from remote_executor import RemoteDeviceExecutor
from report_generator import ReportGenerator

# Initialize logging
logger = setup_logging()


class WiFiTestOrchestrator:
    """
    Orchestrates WiFi performance tests across multiple devices with automatic HTML report generation.
    """

    def __init__(self):
        """
        Initialize orchestrator with router manager and ensure report directories exist.
        """
        self.router = RouterManager()

        # Ensure local reports directory exists
        ReportPaths.LOCAL_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def run_full_suite(self):
        """
        Execute complete test suite for all configured devices.
        Generates and collects HTML reports automatically.
        """
        logger.info("=== Starting Multi-Device Orchestrated WiFi Test ===")

        for device_conf in DUTConfig.DEVICES:
            device_name = device_conf["name"]
            logger.info(f"\n>>> TARGETING DEVICE: {device_name} ({device_conf['ip']}) <<<\n")

            try:
                # Initialize Remote Executor for this device
                executor = RemoteDeviceExecutor(device_conf)
                executor.connect()

                # Run tests with report generation
                self.run_device_tests(executor, device_conf)

                executor.close()

            except Exception as e:
                logger.error(f"Failed to test device {device_name}: {e}")
                continue

        self._cleanup()

    def run_device_tests(self, device_executor, device_conf):
        """
        Execute all tests for a single device and generate HTML report.

        :param device_executor: RemoteDeviceExecutor instance for the target device
        :param device_conf: Device configuration dictionary
        """
        report_gen = None
        device_name = device_conf['name']
        device_ip = device_conf['ip']

        try:
            # Initialize report generator
            report_gen = self._initialize_report(device_executor, device_ip)

            if report_gen:
                logger.info(f"Report initialized for device: {report_gen.device_product_name}")

            # Run band tests
            self.test_band(device_executor, "2G", NETWORKS["2G"], report_gen)
            time.sleep(Timings.BAND_TEST_DELAY)
            self.test_band(device_executor, "5G", NETWORKS["5G"], report_gen)

            # Generate final HTML report
            if report_gen:
                report_gen.generate(report_gen.device_product_name, device_ip)
                logger.info(f"✓ Report saved: {report_gen.output_path.name}")

        except Exception as e:
            logger.error(f"Device test sequence aborted: {e}")

            # Still try to generate partial report
            if report_gen:
                try:
                    report_gen.generate(report_gen.device_product_name or device_name, device_ip)
                    logger.warning(f"Partial report generated: {report_gen.output_path.name}")
                except:
                    pass

    def _initialize_report(self, executor, device_ip):
        """
        Initialize report generator for a device by retrieving system information.

        :param executor: RemoteDeviceExecutor instance
        :param device_ip: Device IP address
        :return: ReportGenerator instance or None if initialization fails
        """
        try:
            # Get system product name from DUT
            stdout, stderr = executor.run_agent_command("sysinfo")

            # Parse output: "SYSTEM_PRODUCT:ThinkPad X1 Carbon"
            device_name = "Unknown"
            for line in stdout.split('\n'):
                if line.startswith("SYSTEM_PRODUCT:"):
                    device_name = line.split(":", 1)[1].strip()
                    break

            # Generate report filename
            report_filename = ReportGenerator.generate_report_filename(device_name, device_ip)

            # Local path where report will be saved
            local_report_path = ReportPaths.LOCAL_REPORTS_DIR / report_filename

            # Create report generator
            report_gen = ReportGenerator(
                template_path=ReportPaths.REPORT_TEMPLATE,
                output_path=local_report_path
            )

            # Store device name for later use
            report_gen.device_product_name = device_name

            return report_gen

        except Exception as e:
            logger.warning(f"Could not initialize report generator: {e}")
            return None

    def _cleanup(self):
        """
        Perform global cleanup: reset router to default settings.
        """
        logger.info("Global Cleanup...")
        self.router.set_channel_auto()
        self.router.set_standard_auto()
        self.router.close()
        logger.info("=== All Tests Finished ===")

    def test_band(self, device_executor, band_name, net_config, report_gen=None):
        """
        Test a specific frequency band (2.4 GHz or 5 GHz) across all standards and channels.

        :param device_executor: RemoteDeviceExecutor instance
        :param band_name: Band identifier ("2G" or "5G")
        :param net_config: Network configuration dictionary
        :param report_gen: ReportGenerator instance (optional)
        """
        logger.info(f"--- Starting {band_name} Band Tests ---")

        ssid = net_config["ssid"]
        standards = WIFI_STANDARDS_2G if band_name == "2G" else WIFI_STANDARDS_5G

        # Determine band display name for report
        band_display = "2.4 GHz" if band_name == "2G" else "5 GHz"

        # Initial connection
        if not device_executor.connect_wifi(ssid, net_config["password"]):
            logger.error("Failed to establish initial connection")
            return

        for standard in standards:
            logger.info(f"Testing Standard: {standard}")

            # Switch Router Standard
            if not self._safe_switch_router(lambda: self.router.change_standard(net_config["device"], standard),
                                            net_config["device"], "hwmode", standard):
                continue

            # Re-verify connection
            if not device_executor.connect_wifi(ssid, net_config["password"]):
                logger.warning("Could not reconnect after standard switch")
                continue

            for channel in net_config["channels"]:
                logger.info(f"Testing Channel: {channel} ({standard})")

                if not self._safe_switch_router(lambda: self.router.change_channel(net_config["device"], channel),
                                                net_config["device"], "channel", str(channel)):
                    continue

                if not device_executor.connect_wifi(ssid, net_config["password"]):
                    logger.warning("Could not reconnect after channel switch")
                    continue

                # Run iperf test
                iperf_output = device_executor.run_iperf()

                # Add result to report if available
                if report_gen and iperf_output:
                    try:
                        # Convert standard format (11n -> 802.11n)
                        standard_formatted = f"802.{standard}"

                        report_gen.add_wifi_test(
                            band=band_display,
                            ssid=ssid,
                            standard=standard_formatted,
                            channel=channel,
                            iperf_output=iperf_output
                        )
                    except Exception as e:
                        logger.debug(f"Failed to add test to report: {e}")

    def _safe_switch_router(self, action_func, device, setting_name, expected_value):
        """
        Safely execute router configuration change with verification.

        :param action_func: Callable that performs the router change
        :param device: Device identifier for the router interface
        :param setting_name: Name of the setting to verify
        :param expected_value: Expected value after change
        :return: True if successful, False otherwise
        """
        try:
            self.router.connect_ssh()
            action_func()
            self.router.close()

            for attempt in range(Limits.MAX_CHECK_ATTEMPTS):
                try:
                    self.router.connect_ssh()
                    current = self.router.get_current_setting(device, setting_name)
                    if str(current) == str(expected_value):
                        self.router.close()
                        return True
                    self.router.close()
                except Exception as e:
                    logger.debug(f"Verification attempt {attempt + 1} failed: {e}")
                time.sleep(Timings.CHECK_INTERVAL)

            logger.error(f"Failed to apply {setting_name}={expected_value}")
            return False

        except Exception as e:
            logger.error(f"Router interaction error: {e}")
            return False


if __name__ == "__main__":
    orchestrator = WiFiTestOrchestrator()
    orchestrator.run_full_suite()