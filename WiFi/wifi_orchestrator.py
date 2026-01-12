# wifi_orchestrator.py
import time
import logging
from config import setup_logging, NETWORKS, WIFI_STANDARDS_2G, WIFI_STANDARDS_5G, Timings, Limits, DUTConfig
from router_manager import RouterManager
from remote_executor import RemoteDeviceExecutor

# Initialize logging
logger = setup_logging()


class WiFiTestOrchestrator:
    def __init__(self):
        self.router = RouterManager()

    def run_full_suite(self):
        logger.info("=== Starting Multi-Device Orchestrated WiFi Test ===")

        for device_conf in DUTConfig.DEVICES:
            device_name = device_conf["name"]
            logger.info(f"\n>>> TARGETING DEVICE: {device_name} ({device_conf['ip']}) <<<\n")

            try:
                # Initialize Remote Executor for this device
                executor = RemoteDeviceExecutor(device_conf)
                executor.connect()

                # Run tests for this device
                self.run_device_tests(executor)

                executor.close()

            except Exception as e:
                logger.error(f"Failed to test device {device_name}: {e}")
                continue

        self._cleanup()

    def run_device_tests(self, device_executor):
        try:
            self.test_band(device_executor, "2G", NETWORKS["2G"])
            time.sleep(Timings.BAND_TEST_DELAY)
            self.test_band(device_executor, "5G", NETWORKS["5G"])
        except Exception as e:
            logger.error(f"Device test sequence aborted: {e}")

    def _cleanup(self):
        logger.info("Global Cleanup...")
        self.router.set_channel_auto()
        self.router.set_standard_auto()
        self.router.close()
        logger.info("=== All Tests Finished ===")

    def test_band(self, device_executor, band_name, net_config):
        logger.info(f"--- Starting {band_name} Band Tests ---")

        ssid = net_config["ssid"]
        standards = WIFI_STANDARDS_2G if band_name == "2G" else WIFI_STANDARDS_5G

        # Initial connection
        device_executor.forget_all_networks()
        device_executor.connect_wifi(ssid, net_config["password"])

        for standard in standards:
            logger.info(f"Testing Standard: {standard}")

            # Switch Router Standard
            if not self._safe_switch_router(lambda: self.router.change_standard(net_config["device"], standard),
                                            net_config["device"], "hwmode", standard):
                continue

            # Re-verify connection (Remote)
            # Since remote executor doesn't keep persistent state, we might blindly try to reconnect or just ping
            # For robustness, let's just force connect command again, agent handles logic
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

                device_executor.run_iperf()

    def _safe_switch_router(self, action_func, device, setting_name, expected_value):
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