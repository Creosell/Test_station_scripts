import logging
import re
import sys
from pathlib import Path


# --- PATH CONFIGURATION ---
class Paths:
    """
    Centralized path configuration using pathlib for robust cross-platform resolution.
    """
    # Absolute path to the directory containing this script
    BASE_DIR = Path(__file__).resolve().parent.parent

    RESOURCES_DIR = BASE_DIR / "resources"
    PROFILES_DIR = RESOURCES_DIR / "wifi_profiles"

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

    @staticmethod
    def sanitize_name(name: str) -> str:
        """
        Sanitize device/product name for safe filesystem and command line usage.
        Replaces spaces and special chars with underscores.

        :param name: Original name
        :return: Sanitized name safe for paths and commands
        """
        # Replace spaces and special chars with underscore
        safe = re.sub(r'[^\w\-]', '_', name)
        # Remove consecutive underscores
        safe = re.sub(r'_+', '_', safe)
        # Remove leading/trailing underscores
        return safe.strip('_')


# --- NETWORK CONFIGURATION ---
class NetworkConfig:
    """
    Network and device configuration constants.
    """
    SSID_2G = "QA_Test_2G"
    SSID_5G = "QA_Test_5G"
    WIFI_PASSWORD = "66668888"
    ROUTER_IP = "192.168.50.1"
    IPERF_SERVER_IP = "192.168.50.1"
    SSH_USER = "root"
    SSH_PASSWORD = "66668888"
    DEVICE_2G = "mt798111"
    DEVICE_5G = "mt798112"

    # Pool of ports available for parallel testing (5201-5221)
    IPERF_PORTS = list(range(5201, 5222))


class DUTConfig:
    """
    Device Under Test (DUT) configuration.
    """
    DEVICES = [
        {
            "name": "Laptop_Dell_Win",
            "ip": "192.168.50.178",
            "user": "slave",
            "password": "66668888",
            "os": "Windows",
            "python_path": "python",
            "system_product": "Unknown"
        }
    ]


class RouterConfig:
    """
    Router configuration for device discovery and management.
    Used by --auto-discover feature to query DHCP clients.
    """
    IP = NetworkConfig.ROUTER_IP
    USER = NetworkConfig.SSH_USER
    PASSWORD = NetworkConfig.SSH_PASSWORD


class ReportPaths:
    """
    Paths for HTML report generation and collection.
    """
    # Local reports directory (orchestrator machine)
    LOCAL_REPORTS_DIR = Path(__file__).parent.parent / "reports"

    # Report template location
    REPORT_TEMPLATE = Path(__file__).parent.parent / "resources" / "report_template.html"

    # Remote report directory (on DUT)
    REMOTE_REPORT_DIR = "C:\\Temp\\wifi_test_agent\\reports"  # Windows
    REMOTE_REPORT_DIR_LINUX = "/tmp/wifi_test_agent/reports"  # Linux


class Timings:
    """
    Timeout and delay constants (in seconds).
    """
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
    """
    Retry and attempt limits for operations.
    """
    SSH_RETRIES = 5
    WIFI_CONNECT_RETRIES = 3
    MAX_CHECK_ATTEMPTS = 15
    CONNECTION_RETRY_DELAY = 5


# --- LOGGING SYSTEM ---
class ConsoleOverwriterHandler(logging.StreamHandler):
    """
    Custom logging handler that overwrites the current console line.
    """

    def emit(self, record):
        """
        Emit a record, clearing the line before writing.

        :param record: LogRecord instance
        """
        try:
            msg = self.format(record)
            stream = self.stream
            stream.write('\r' + ' ' * 80 + '\r')
            stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


class MinimalFormatter(logging.Formatter):
    """
    Minimalist colored formatter with symbols for log levels.
    """
    GREY = "\x1b[38;5;240m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    RED = "\x1b[31m"
    RESET = "\x1b[0m"

    def format(self, record):
        """
        Format a log record with color and level symbol.

        :param record: LogRecord instance
        :return: Formatted string
        """
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
    """
    Configure root logger with custom console handler.

    :param verbose: If True, set level to DEBUG, otherwise INFO
    :return: Configured root logger
    """
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler = ConsoleOverwriterHandler(sys.stdout)
    console_handler.setFormatter(MinimalFormatter())
    root_logger.addHandler(console_handler)

    logging.getLogger("paramiko").setLevel(logging.WARNING)

    return root_logger
