"""
Device Discovery Module for WiFi Test Suite

Automatically discovers test devices (DUTs) in the network by:
1. Querying router for DHCP clients
2. Attempting SSH connections to each host
3. Detecting OS type (Windows/Linux)
4. Auto-populating device configuration
"""
from sys import platform

import paramiko
import logging
import socket
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger("DeviceDiscovery")


class DeviceDiscovery:
    """
    Discovers and identifies test devices in the network automatically.
    """

    def __init__(self, router_ssh_config: Dict, master_station_ip: str):
        """
        Initialize device discovery.

        :param router_ssh_config: Dict with 'ip', 'user', 'password' for router SSH
        :param master_station_ip: IP address of master test station to exclude
        """
        self.router_config = router_ssh_config
        self.master_ip = master_station_ip
        self.ssh = None

    def discover_devices(self, default_user: str = "slave",
                         default_password: str = "66668888",
                         ssh_timeout: int = 5) -> List[Dict]:
        """
        Perform full device discovery workflow.

        :param default_user: Default SSH username to try
        :param default_password: Default SSH password to try
        :param ssh_timeout: SSH connection timeout in seconds
        :return: List of discovered device configurations
        """
        logger.info("=== Starting Device Discovery ===")

        # Step 1: Get DHCP clients from router
        clients = self._get_dhcp_clients()

        if not clients:
            logger.warning("No DHCP clients found")
            return []

        # Step 2: Filter out master station
        candidates = [c for c in clients if c['ip'] != self.master_ip]
        logger.info(f"Found {len(candidates)} potential DUT candidates (excluding master)")

        # Step 3: Probe each candidate via SSH
        discovered_devices = []

        for idx, client in enumerate(candidates, 1):
            logger.info(f"\n[{idx}/{len(candidates)}] Probing {client['ip']} ({client['hostname']})...")

            device_config = self._probe_device(
                client['ip'],
                client['hostname'],
                default_user,
                default_password,
                ssh_timeout
            )

            if device_config:
                discovered_devices.append(device_config)
                logger.info(f"✓ Device discovered: {device_config['name']} ({device_config['os']})")
            else:
                logger.info(f"✖ Not a valid DUT (SSH failed or incompatible)")

        logger.info(f"\n=== Discovery Complete: {len(discovered_devices)} devices found ===")
        return discovered_devices

    def _get_dhcp_clients(self) -> List[Dict]:
        """
        Query router for DHCP client list.

        :return: List of dicts with 'ip', 'mac', 'hostname' keys
        """
        try:
            # Connect to router
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                self.router_config['ip'],
                username=self.router_config['user'],
                password=self.router_config['password'],
                timeout=10
            )

            logger.info(f"Connected to router at {self.router_config['ip']}")

            # Read DHCP leases (OpenWRT)
            stdin, stdout, stderr = self.ssh.exec_command("cat /tmp/dhcp.leases")
            exit_status = stdout.channel.recv_exit_status()

            if exit_status != 0:
                logger.error("Failed to read DHCP leases")
                return []

            leases_output = stdout.read().decode().strip()
            clients = []

            # Parse DHCP leases format:
            # <lease_time> <mac> <ip> <hostname> <client_id>
            for line in leases_output.split('\n'):
                if not line.strip():
                    continue

                parts = line.split()
                if len(parts) >= 4:
                    clients.append({
                        'mac': parts[1],
                        'ip': parts[2],
                        'hostname': parts[3] if parts[3] != '*' else f"host-{parts[2].split('.')[-1]}"
                    })

            self.ssh.close()
            logger.info(f"Retrieved {len(clients)} DHCP clients from router")
            return clients

        except Exception as e:
            logger.error(f"Failed to query router: {e}")
            if self.ssh:
                self.ssh.close()
            return []

    def _probe_device(self, ip: str, hostname: str, user: str, password: str,
                      timeout: int) -> Optional[Dict]:
        """
        Attempt SSH connection to device and detect OS type.

        :param ip: Device IP address
        :param hostname: Device hostname
        :param user: SSH username
        :param password: SSH password
        :param timeout: Connection timeout
        :return: Device config dict or None if probe failed
        """
        ssh = None
        try:
            logger.debug(f"Trying SSH: {user}@{ip}")
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Try to connect
            ssh.connect(ip, username=user, password=password, timeout=timeout)

            # Detect OS type
            os_type = self._detect_os_type(ssh)

            if not os_type:
                ssh.close()
                return None

            # Detect Python path
            python_path = self._detect_python_path(ssh, os_type)
            system_product = self._detect_system_product(ssh, os_type)

            ssh.close()

            # Generate device config
            return {
                'name': hostname,
                'ip': ip,
                'user': user,
                'password': password,
                'os': os_type,
                'python_path': python_path,
                'system_product': system_product
            }

        except (paramiko.AuthenticationException, paramiko.SSHException, socket.timeout) as e:
            logger.debug(f"SSH probe failed for {ip}: {e}")
            if ssh:
                ssh.close()
            return None

        except Exception as e:
            logger.warning(f"Unexpected error probing {ip}: {e}")
            if ssh:
                ssh.close()
            return None

    def _detect_system_product(self, ssh: paramiko.SSHClient, os_type: str) -> str:
        """
        Detect system product name on remote device.

        :param ssh: Active SSH connection
        :param os_type: 'Windows' or 'Linux'
        :return: System product name
        """
        try:
            if os_type == "Windows":
                cmd = "powershell -Command \"Get-CimInstance -ClassName Win32_ComputerSystemProduct | Select-Object -ExpandProperty Name\""
                stdin, stdout, stderr = ssh.exec_command(cmd, timeout=5)
                exit_status = stdout.channel.recv_exit_status()

                if exit_status == 0 and stdout.read().decode().strip():
                    return stdout.read().decode().strip()

            elif os_type == "Linux":
                try:
                    stdin, stdout, stderr = ssh.exec_command("cat /sys/devices/virtual/dmi/id/product_name", timeout=5)
                    exit_status = stdout.channel.recv_exit_status()

                    if exit_status == 0:
                        product = stdout.read().decode().strip()
                        if product:
                            return product
                except:
                    pass

            return platform.node() if hasattr(platform, 'node') else "Unknown"

        except Exception as e:
            logger.debug(f"System product detection failed: {e}")
            return "Unknown"

    def _detect_os_type(self, ssh: paramiko.SSHClient) -> Optional[str]:
        """
        Detect OS type via SSH command execution.

        :param ssh: Active SSH connection
        :return: 'Windows' or 'Linux' or None if detection failed
        """
        try:
            # Try Windows first
            stdin, stdout, stderr = ssh.exec_command("ver", timeout=3)
            exit_status = stdout.channel.recv_exit_status()

            if exit_status == 0:
                output = stdout.read().decode().lower()
                if "windows" in output or "microsoft" in output:
                    return "Windows"

            # Try Linux
            stdin, stdout, stderr = ssh.exec_command("uname -s", timeout=3)
            exit_status = stdout.channel.recv_exit_status()

            if exit_status == 0:
                output = stdout.read().decode().strip().lower()
                if "linux" in output:
                    return "Linux"

            return None

        except Exception as e:
            logger.debug(f"OS detection failed: {e}")
            return None

    def _detect_python_path(self, ssh: paramiko.SSHClient, os_type: str) -> str:
        """
        Detect Python executable path on remote device.

        :param ssh: Active SSH connection
        :param os_type: 'Windows' or 'Linux'
        :return: Python executable path
        """
        try:
            # Try common Python commands
            commands = ["python", "python3", "py"] if os_type == "Windows" else ["python3", "python"]

            for cmd in commands:
                test_cmd = f"{cmd} --version"
                stdin, stdout, stderr = ssh.exec_command(test_cmd, timeout=3)
                exit_status = stdout.channel.recv_exit_status()

                if exit_status == 0:
                    version = stdout.read().decode().strip()
                    logger.debug(f"Found Python: {cmd} -> {version}")
                    return cmd

            # Default fallback
            return "python" if os_type == "Windows" else "python3"

        except Exception as e:
            logger.debug(f"Python detection failed: {e}")
            return "python" if os_type == "Windows" else "python3"


def generate_config_code(devices: List[Dict]) -> str:
    """
    Generate Python code for DUTConfig.DEVICES from discovered devices.

    :param devices: List of device configuration dicts
    :return: Python code string
    """
    if not devices:
        return "# No devices discovered\nDEVICES = []"

    code_lines = ["# Auto-discovered devices", "DEVICES = ["]

    for device in devices:
        code_lines.append("    {")
        code_lines.append(f"        'name': '{device['name']}',")
        code_lines.append(f"        'ip': '{device['ip']}',")
        code_lines.append(f"        'user': '{device['user']}',")
        code_lines.append(f"        'password': '{device['password']}',")
        code_lines.append(f"        'os': '{device['os']}',")
        code_lines.append(f"        'python_path': '{device['python_path']}',")
        code_lines.append(f"        'system_product': '{device.get('system_product', 'Unknown')}'")  # ADD THIS
        code_lines.append("    },")

    code_lines.append("]")
    return '\n'.join(code_lines)


def save_discovered_config(devices: List[Dict], output_file: str = "discovered_devices.py"):
    """
    Save discovered devices to Python config file.

    :param devices: List of device configurations
    :param output_file: Output file path
    """
    config_code = generate_config_code(devices)

    with open(output_file, 'w') as f:
        f.write(config_code)

    logger.info(f"Device configuration saved to: {output_file}")


if __name__ == "__main__":
    """
    Standalone device discovery utility.

    Usage:
        python device_discovery.py
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s • [%(name)s] %(message)s',
        datefmt='%H:%M:%S'
    )

    # Configuration (update these values)
    ROUTER_CONFIG = {
        'ip': '192.168.50.1',
        'user': 'root',
        'password': '66668888'
    }

    MASTER_STATION_IP = '192.168.50.100'  # IP to exclude
    DEFAULT_SSH_USER = 'slave'
    DEFAULT_SSH_PASSWORD = '66668888'

    # Run discovery
    discovery = DeviceDiscovery(ROUTER_CONFIG, MASTER_STATION_IP)
    devices = discovery.discover_devices(DEFAULT_SSH_USER, DEFAULT_SSH_PASSWORD)

    if devices:
        print("\n" + "=" * 60)
        print("DISCOVERED DEVICES:")
        print("=" * 60)
        for device in devices:
            print(f"{device['name']:20} {device['ip']:15} {device['os']:10}")
        print("=" * 60)

        # Generate and save config
        save_discovered_config(devices, "discovered_devices.py")
        print(f"\n✓ Configuration saved to: discovered_devices.py")
        print("\nYou can now import these devices in config.py:")
        print("  from discovered_devices import DEVICES")
        print("  DUTConfig.DEVICES = DEVICES")
    else:
        print("\n✖ No devices discovered")