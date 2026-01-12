import logging
import sys
import os
from pathlib import Path  # Introduced pathlib


# --- PATH CONFIGURATION ---
class Paths:
    """
    Centralized path configuration using pathlib for robust cross-platform resolution.
    """
    # Absolute path to the directory containing this script
    BASE_DIR = Path(__file__).resolve().parent

    RESOURCES_DIR = BASE_DIR / "resources"
    PROFILES_DIR = RESOURCES_DIR / "wifi_profiles"

    # Executable paths
    IPERF_EXE_WIN = RESOURCES_DIR / "iperf.exe"

    # Remote paths for deployment (Strings are sufficient here as they are destination literals)
    REMOTE_LINUX_WORK_DIR = "/tmp/wifi_test_agent"
    REMOTE_WINDOWS_WORK_DIR = "C:\\Temp\\wifi_test_agent"

    @staticmethod
    def get_validated_path(filename: str) -> Path:
        """
        Resolves a filename relative to the BASE_DIR and validates its existence.

        :param filename: The name of the file or relative path string.
        :return: A resolved Path object.
        :raises FileNotFoundError: If the file does not exist locally.
        """
        target = Paths.BASE_DIR / filename
        if not target.exists():
            # Log immediately to console before raising, ensuring visibility
            logging.error(f"[Config] CRITICAL: Required file not found at: {target}")
            raise FileNotFoundError(f"Missing required file: {filename}")
        return target


# --- NETWORK CONFIGURATION ---
class NetworkConfig:
    # ... (Остальной код NetworkConfig, DUTConfig, Timings, Limits, ENCRYPTIONS, NETWORKS остается без изменений) ...
    SSID_2G = "QA_Test_2G"
    SSID_5G = "QA_Test_5G"
    WIFI_PASSWORD = "66668888"
    SSH_PASSWORD = "66668888"
    SSH_USER = "root"
    DEVICE_2G = "mt798111"
    DEVICE_5G = "mt798112"
    ROUTER_IP = "192.168.50.1"
    IPERF_SERVER_IP = "192.168.50.1"


class DUTConfig:
    DEVICES = [
        {
            "name": "Laptop_Dell_Win",
            "ip": "192.168.50.178",
            "user": "slave",
            "password": "66668888",
            "os": "Windows",
            "python_path": "python"
        }
    ]


class Timings:
    SSH_TIMEOUT = 10
    WIFI_CONNECTION_TIMEOUT = 30
    CMD_VERIFY_TIMEOUT = 10
    IPERF_TIMEOUT = 30
    WIFI_TOGGLE_DELAY = 5
    WIFI_APPLY_DELAY = 15
    PROFILE_ADD_DELAY = 2
    CHECK_INTERVAL = 4
    BAND_TEST_DELAY = 30
    IPERF_DURATION = "10"


class Limits:
    SSH_RETRIES = 5
    WIFI_CONNECT_RETRIES = 3
    MAX_CHECK_ATTEMPTS = 15
    CONNECTION_RETRY_DELAY = 5


ENCRYPTIONS = ["psk", "psk2", "psk-mixed", "sae", "sae-mixed"]
WIFI_STANDARDS_2G = ["11b", "11g", "11n", "11ax"]
WIFI_STANDARDS_5G = ["11a", "11n", "11ac", "11ax"]

NETWORKS = {
    "2G": {
        "ssid": NetworkConfig.SSID_2G,
        "password": NetworkConfig.WIFI_PASSWORD,
        "device": NetworkConfig.DEVICE_2G,
        "encryption": "psk2",
        "channels": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    },
    "5G": {
        "ssid": NetworkConfig.SSID_5G,
        "password": NetworkConfig.WIFI_PASSWORD,
        "device": NetworkConfig.DEVICE_5G,
        "encryption": "sae-mixed",
        "channels": [36, 40, 44, 48, 149, 153, 157, 161, 165]
    }
}


# --- LOGGING SYSTEM (Unchanged) ---
# ... (Оставьте классы ConsoleOverwriterHandler, MinimalFormatter и setup_logging без изменений) ...
class ConsoleOverwriterHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            stream.write('\r' + ' ' * 80 + '\r')
            stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


class MinimalFormatter(logging.Formatter):
    GREY = "\x1b[38;5;240m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    RED = "\x1b[31m"
    RESET = "\x1b[0m"

    def format(self, record):
        if record.levelno == logging.DEBUG:
            prefix = f"{self.GREY}d{self.RESET}"
            msg_color = self.GREY
        elif record.levelno == logging.INFO:
            prefix = f"{self.GREEN}•{self.RESET}"
            msg_color = self.RESET
        elif record.levelno == logging.WARNING:
            prefix = f"{self.YELLOW}⚠{self.RESET}"
            msg_color = self.YELLOW
        elif record.levelno >= logging.ERROR:
            prefix = f"{self.RED}✖{self.RESET}"
            msg_color = self.RED
        else:
            prefix = ""
            msg_color = self.RESET

        logger_name = f"{self.GREY}[{record.name}]{self.RESET} " if record.name != "root" else ""
        timestamp = f"{self.GREY}{self.formatTime(record, '%H:%M:%S')}{self.RESET}"
        return f"{timestamp} {prefix} {logger_name}{msg_color}{record.getMessage()}{self.RESET}"


def setup_logging(verbose: bool = False):
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler = ConsoleOverwriterHandler(sys.stdout)
    console_handler.setFormatter(MinimalFormatter())
    root_logger.addHandler(console_handler)
    return root_logger