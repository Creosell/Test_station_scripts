"""
Global Test Suite Orchestrator.
Coordinates multiple test modules and generates unified reports.
"""

import argparse
import socket
import sys

from core.config import setup_logging, DUTConfig, RouterConfig
from core.core_report import CoreReportGenerator
from modules.wifi.wifi_orchestrator import WiFiTestOrchestrator

logger = setup_logging()


class TestSuiteOrchestrator:
    """
    Global test suite coordinator.
    Manages multiple test modules with shared report.
    """

    def __init__(self):
        # Unified report for all modules
        self.report = CoreReportGenerator()

        # Register modules
        self.modules = {
            'wifi': WiFiTestOrchestrator(report_generator=self.report),
            # Future: 'bluetooth': BluetoothTestOrchestrator(...)
        }

    def run(self, modules=['wifi'], parallel=False, max_workers=None):
        """
        Execute specified test modules.

        :param modules: List of module names
        :param parallel: Enable parallel execution
        :param max_workers: Max workers for parallel mode
        :return: Aggregated results
        """
        all_results = {}

        for module_name in modules:
            if module_name not in self.modules:
                logger.error(f"Unknown module: {module_name}")
                continue

            logger.info(f"\n=== Starting {module_name.upper()} Module ===")
            orchestrator = self.modules[module_name]

            try:
                if parallel:
                    results = orchestrator.run_parallel_suite(max_workers)
                else:
                    results = orchestrator.run_full_suite()

                all_results[module_name] = results

            except KeyboardInterrupt:
                logger.warning(f"{module_name} interrupted")
                raise
            except Exception as e:
                logger.error(f"{module_name} failed: {e}")
                all_results[module_name] = {'error': str(e)}

        # Generate unified report (if needed)
        # Note: WiFi module handles its own reports currently

        return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unified Test Suite")
    parser.add_argument('--modules', default='wifi',
                        help='Comma-separated modules (e.g., wifi,bluetooth)')
    parser.add_argument('--parallel', action='store_true',
                        help='Run tests in parallel across all devices')
    parser.add_argument('--workers', type=int, default=None,
                        help='Maximum number of parallel workers')
    parser.add_argument('--auto-discover', action='store_true',
                        help='Automatically discover DUT devices in network')
    parser.add_argument('--discovery-user', default='slave',
                        help='Default SSH username for device discovery')
    parser.add_argument('--discovery-password', default='66668888',
                        help='Default SSH password for device discovery')
    parser.add_argument('--master-ip', default=None,
                        help='Master station IP to exclude from discovery')
    args = parser.parse_args()

    # Auto-discovery
    if args.auto_discover:
        from core.device_discovery import DeviceDiscovery, save_discovered_config

        logger.info("Auto-discovery mode enabled")

        router_config = {
            'ip': RouterConfig.IP,
            'user': RouterConfig.USER,
            'password': RouterConfig.PASSWORD
        }

        master_ip = args.master_ip
        if not master_ip:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect((router_config['ip'], 80))
                master_ip = s.getsockname()[0]
                s.close()
                logger.info(f"Auto-detected master IP: {master_ip}")
            except:
                logger.error("Failed to auto-detect master IP. Use --master-ip argument")
                sys.exit(1)

        discovery = DeviceDiscovery(router_config, master_ip)
        devices = discovery.discover_devices(args.discovery_user, args.discovery_password)

        if not devices:
            logger.error("No devices discovered")
            sys.exit(1)

        save_discovered_config(devices, "modules/wifi/discovered_devices.py")
        DUTConfig.DEVICES = devices
        logger.info(f"✓ Discovered {len(devices)} devices")

    # Parse modules
    modules_list = [m.strip() for m in args.modules.split(',')]

    # Run suite
    suite = TestSuiteOrchestrator()
    try:
        results = suite.run(modules_list, parallel=args.parallel, max_workers=args.workers)
        print(f"\n✓ Test suite completed")
    except KeyboardInterrupt:
        logger.warning("\n⚠ Suite interrupted")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
