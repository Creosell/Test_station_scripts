"""
Agent Device Manager - Base OS operations for test agents.
Provides OS-agnostic system management functions.
"""

import logging
import platform
import subprocess

logger = logging.getLogger("AgentDeviceMgr")


class AgentDeviceManager:
    """
    Manages base OS operations on the local device (Agent side).
    OS-agnostic power management and system info.
    """

    def __init__(self):
        """
        Initializes the AgentDeviceManager and detects OS.
        """
        self.os_type = platform.system()
        logger.info(f"Detected OS: {self.os_type}")

    def prevent_sleep(self):
        """
        Prevent system sleep and screen timeout during testing.

        :return: True if successful, False otherwise
        """
        try:
            if self.os_type == "Windows":
                # Set power plan to High Performance and disable sleep/display timeout
                commands = [
                    'powercfg /change monitor-timeout-ac 0',
                    'powercfg /change standby-timeout-ac 0',
                    'powercfg /change monitor-timeout-dc 0',
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
                    logger.warning("Could not re-enable DPMS")
                    return False

            return False

        except Exception as e:
            logger.warning(f"Failed to allow sleep: {e}")
            return False

    def get_system_product_name(self):
        """
        Retrieve system product name (hardware model).

        :return: Product name string or "Unknown"
        """
        try:
            if self.os_type == "Windows":
                result = subprocess.run(
                    ['wmic', 'computersystem', 'get', 'model'],
                    capture_output=True,
                    text=True,
                    check=True
                )
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:
                    product_name = lines[1].strip()
                    logger.info(f"System Product: {product_name}")
                    return product_name

            elif self.os_type == "Linux":
                # Try reading from DMI
                try:
                    with open('/sys/class/dmi/id/product_name', 'r') as f:
                        product_name = f.read().strip()
                        logger.info(f"System Product: {product_name}")
                        return product_name
                except:
                    pass

            return "Unknown"

        except Exception as e:
            logger.warning(f"Could not get system product name: {e}")
            return "Unknown"

    def _verify_connection(self, expected_ssid):
        """
        Verify WiFi connection to expected SSID.

        :param expected_ssid: Expected network SSID
        :return: True if connected to expected SSID
        """
        try:
            current_ssid = self.get_wifi_connection_info()
            if current_ssid and expected_ssid.lower() in current_ssid.lower():
                logger.info(f"Connected to expected SSID: {expected_ssid}")
                return True
            else:
                logger.warning(f"Not connected to {expected_ssid}. Current: {current_ssid}")
                return False
        except Exception as e:
            logger.error(f"Connection verification failed: {e}")
            return False

    def get_wifi_connection_info(self) -> str:
        """
        Get current WiFi connection info (SSID).

        :return: Current SSID or empty string if not connected
        """
        try:
            if self.os_type == "Windows":
                result = subprocess.run(
                    ['netsh', 'wlan', 'show', 'interfaces'],
                    capture_output=True,
                    text=True
                )
                for line in result.stdout.split('\n'):
                    if 'SSID' in line and 'BSSID' not in line:
                        ssid = line.split(':', 1)[1].strip()
                        return ssid

            elif self.os_type == "Linux":
                result = subprocess.run(
                    ['iwgetid', '-r'],
                    capture_output=True,
                    text=True
                )
                return result.stdout.strip()

            return ""

        except Exception as e:
            logger.error(f"Failed to get WiFi info: {e}")
            return ""
