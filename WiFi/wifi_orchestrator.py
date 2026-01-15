# wifi_orchestrator.py
import time
from pathlib import Path

from config import (setup_logging, NETWORKS, WIFI_STANDARDS_2G, WIFI_STANDARDS_5G,
                    Timings, Limits, DUTConfig, ReportPaths, NetworkConfig)
from remote_executor import RemoteDeviceExecutor
from router_manager import RouterManager

# Initialize logging
logger = setup_logging()


class WiFiTestOrchestrator:
    """
    Orchestrates Wi-Fi performance tests across multiple devices with automatic HTML report generation.
    Supports incremental reporting, parallel execution, and checkpoint/resume capability.
    """

    def __init__(self, enable_checkpoints=False):
        """
        Initialize orchestrator with router manager and ensure report directories exist.

        :param enable_checkpoints: Enable checkpoint/resume functionality
        """
        self.router = RouterManager()
        self.enable_checkpoints = enable_checkpoints
        self.checkpoint_file = Path("test_checkpoint.json") if enable_checkpoints else None

        # Dynamically assign unique iperf ports to configured devices
        self._assign_iperf_ports()

        # Ensure local reports directory exists
        ReportPaths.LOCAL_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def _assign_iperf_ports(self):
        """
        Assigns a unique iperf port from the pool to each device in DUTConfig.
        """
        available_ports = NetworkConfig.IPERF_PORTS
        devices = DUTConfig.DEVICES

        if len(devices) > len(available_ports):
            logger.warning("Not enough iperf ports for all devices! Some may conflict.")

        for idx, device in enumerate(devices):
            # Use modulo to cycle ports if devices > ports (though ideally shouldn't happen)
            port_idx = idx % len(available_ports)
            assigned_port = available_ports[port_idx]
            device['iperf_port'] = assigned_port
            logger.info(f"Assigned iperf port {assigned_port} to device: {device['name']}")

    def run_full_suite(self):
        """
        Execute complete test suite for all configured devices.
        Generates and collects HTML reports automatically.
        """
        logger.info("=== Starting Multi-Device Orchestrated WiFi Test ===")

        for device_conf in DUTConfig.DEVICES:
            device_name = device_conf["name"]
            logger.info(f"\n>>> TARGETING DEVICE: {device_name} ({device_conf['ip']}) <<<\n")

            executor = None
            try:
                # Initialize Remote Executor for this device
                executor = RemoteDeviceExecutor(device_conf)
                executor.connect()

                # Prevent sleep on DUT during testing
                try:
                    executor.run_agent_command("prevent_sleep")
                    logger.info("Sleep prevention enabled on DUT")
                except Exception as e:
                    logger.warning(f"Could not prevent sleep: {e}")

                # Run tests with report generation
                self.run_device_tests(executor, device_conf)

            except KeyboardInterrupt:
                logger.warning("\n⚠ Interrupted by user (Ctrl+C)")
                self._emergency_cleanup(executor)
                raise

            except Exception as e:
                logger.error(f"Failed to test device {device_name}: {e}")

            finally:
                # Re-enable sleep on DUT
                if executor:
                    try:
                        executor.run_agent_command("allow_sleep")
                        logger.info("Sleep prevention disabled on DUT")
                    except:
                        pass

                    try:
                        executor.close()
                    except:
                        pass

        self._cleanup()

    def run_device_tests(self, device_executor, device_conf):
        """
        Execute all tests for a single device using incremental HTML reporting on DUT.

        :param device_executor: RemoteDeviceExecutor instance for the target device
        :param device_conf: Device configuration dictionary
        """
        device_name = device_conf['name']
        device_ip = device_conf['ip']
        system_product_name = device_conf.get('system_product')
        remote_report_path = None

        try:
            logger.info(f"Device identified: {system_product_name}")

            # Initialize report on DUT
            remote_report_path = device_executor.init_remote_report(system_product_name, device_ip)

            if not remote_report_path:
                logger.error("Failed to initialize remote report, tests will run without reporting")

            # Run band tests
            self.test_band(device_executor, "2G", NETWORKS["2G"], remote_report_path)
            time.sleep(Timings.BAND_TEST_DELAY)
            self.test_band(device_executor, "5G", NETWORKS["5G"], remote_report_path)

            # Download final report with new structure
            if remote_report_path:
                # Create structured path: reports/{system_product}/{date}/
                from datetime import datetime
                testing_day = datetime.now().strftime('%Y-%m-%d')

                report_subdir = ReportPaths.LOCAL_REPORTS_DIR / system_product_name / testing_day
                report_subdir.mkdir(parents=True, exist_ok=True)

                local_path = device_executor.download_report(remote_report_path, str(report_subdir))
                if local_path:
                    logger.info(f"✓ Report saved: {Path(local_path).relative_to(ReportPaths.LOCAL_REPORTS_DIR)}")
                else:
                    logger.warning("Failed to download report from DUT")

        except Exception as e:
            logger.error(f"Device test sequence aborted: {e}")

            if remote_report_path:
                try:
                    from datetime import datetime
                    import re

                    testing_day = datetime.now().strftime('%Y-%m-%d')
                    safe_product = re.sub(r'[^\w\-]', '_', device_conf.get('system_product', 'Unknown'))

                    report_subdir = ReportPaths.LOCAL_REPORTS_DIR / safe_product / testing_day
                    report_subdir.mkdir(parents=True, exist_ok=True)

                    local_path = device_executor.download_report(remote_report_path, str(report_subdir))
                    if local_path:
                        logger.warning(
                            f"Partial report downloaded: {Path(local_path).relative_to(ReportPaths.LOCAL_REPORTS_DIR)}")
                except:
                    pass

    def _cleanup(self):
        """
        Perform global cleanup: reset router to default settings.
        """
        logger.info("Global Cleanup...")
        try:
            self.router.set_channel_auto()
            self.router.set_standard_auto()
            self.router.close()
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

        # Clear checkpoint on successful completion
        if self.enable_checkpoints:
            self._clear_checkpoint()

        logger.info("=== All Tests Finished ===")

    def _save_checkpoint(self, device_name, band, standard, channel):
        """
        Save test progress checkpoint to JSON file.

        :param device_name: Device name being tested
        :param band: Current band (2G/5G)
        :param standard: Current Wi-Fi standard
        :param channel: Current channel
        """
        if not self.enable_checkpoints or not self.checkpoint_file:
            return

        import json
        checkpoint_data = {
            'timestamp': time.time(),
            'device': device_name,
            'band': band,
            'standard': standard,
            'channel': channel
        }

        try:
            with open(self.checkpoint_file, 'w') as f:
                json.dump(checkpoint_data, f, indent=2)
            logger.debug(f"Checkpoint saved: {band}/{standard}/Ch{channel}")
        except Exception as e:
            logger.warning(f"Failed to save checkpoint: {e}")

    def _load_checkpoint(self):
        """
        Load test progress from checkpoint file.

        :return: Checkpoint data dict or None if no checkpoint exists
        """
        if not self.enable_checkpoints or not self.checkpoint_file:
            return None

        if not self.checkpoint_file.exists():
            return None

        try:
            import json
            with open(self.checkpoint_file, 'r') as f:
                data = json.load(f)
            logger.info(f"Checkpoint loaded: {data['device']} @ {data['band']}/{data['standard']}/Ch{data['channel']}")
            return data
        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}")
            return None

    def _clear_checkpoint(self):
        """
        Remove checkpoint file on successful test completion.
        """
        if self.checkpoint_file and self.checkpoint_file.exists():
            try:
                self.checkpoint_file.unlink()
                logger.info("Checkpoint cleared")
            except Exception as e:
                logger.warning(f"Failed to clear checkpoint: {e}")

    def run_parallel_suite(self, max_workers=None):
        """
        Execute test suite with synchronized parallel device testing.

        CRITICAL: All devices test the SAME channel before router switches to next channel.
        This prevents cascading failures from router channel changes during active tests.

        Devices with persistent failures (3+ consecutive) are excluded from further testing.

        :param max_workers: Maximum number of parallel threads (default: number of devices)
        """

        logger.info("=== Starting Synchronized Parallel WiFi Test ===")

        max_workers = max_workers or len(DUTConfig.DEVICES)

        # Initialize all devices and get executors
        device_executors = {}
        remote_report_paths = {}
        device_failure_counts = {}  # Track consecutive failures
        results = {}

        logger.info("Initializing all devices...")
        for device_conf in DUTConfig.DEVICES:
            device_name = device_conf["name"]
            try:
                executor = RemoteDeviceExecutor(device_conf)
                executor.connect()

                # Prevent sleep
                try:
                    executor.run_agent_command("prevent_sleep")
                    logger.info(f"[{device_name}] Sleep prevention enabled")
                except Exception as e:
                    logger.warning(f"[{device_name}] Could not prevent sleep: {e}")

                # Get system info and initialize remote report
                system_product_name = device_conf["system_product"]

                remote_report_path = executor.init_remote_report(system_product_name, device_conf['ip'])

                device_executors[device_name] = executor
                remote_report_paths[device_name] = remote_report_path
                device_failure_counts[device_name] = 0

                logger.info(f"✓ [{device_name}] Initialized ({system_product_name})")

            except Exception as e:
                logger.error(f"✖ [{device_name}] Initialization failed: {e}")

        if not device_executors:
            logger.error("No devices initialized successfully")
            return {}

        # Flag to track interruption
        interrupted = False

        # Test both bands with synchronized channel switching
        try:
            for band_key in ["2G", "5G"]:
                if interrupted: break

                net_config = NETWORKS[band_key]
                band_display = "2.4 GHz" if band_key == "2G" else "5 GHz"
                standards = WIFI_STANDARDS_2G if band_key == "2G" else WIFI_STANDARDS_5G

                logger.info(f"\n=== Starting {band_display} Band Tests ===")

                for standard in standards:
                    if interrupted: break

                    logger.info(f"Testing Standard: {standard}")

                    try:
                        self.router.change_standard(net_config["device"], standard)
                    except Exception as e:
                        logger.error(f"Failed to set standard {standard}: {e}")
                        continue

                    for channel in net_config["channels"]:
                        if interrupted: break

                        logger.info(f"\n>>> Testing Channel {channel} ({standard}) on ALL devices <<<")

                        # Switch router to this channel (ONE TIME for all devices)
                        if not self._safe_switch_router(
                                lambda: self.router.change_channel(net_config["device"], channel),
                                net_config["device"], "channel", str(channel)
                        ):
                            logger.error(f"Failed to set channel {channel}, skipping")
                            continue

                        # Filter out devices with too many failures
                        active_devices = {
                            name: executor for name, executor in device_executors.items()
                            if device_failure_counts[name] < 3
                        }

                        if not active_devices:
                            logger.error("All devices have failed persistently, aborting")
                            interrupted = True  # Abort loops
                            break

                        excluded_count = len(device_executors) - len(active_devices)
                        if excluded_count > 0:
                            logger.warning(f"⚠ {excluded_count} device(s) excluded due to persistent failures")

                        # Test this channel on ALL active devices in parallel
                        failures = self._test_channel_on_all_devices(
                            active_devices,
                            remote_report_paths,
                            net_config["ssid"],
                            net_config["password"],
                            band_display,
                            standard,
                            channel,
                            max_workers
                        )

                        # Update failure counts
                        for device_name in active_devices:
                            if device_name in failures and failures[device_name]:
                                device_failure_counts[device_name] += 1
                                logger.debug(f"[{device_name}] Failure count: {device_failure_counts[device_name]}")
                            else:
                                device_failure_counts[device_name] = 0  # Reset on success

                if not interrupted:
                    time.sleep(Timings.BAND_TEST_DELAY)

        except KeyboardInterrupt:
            logger.warning("\n⚠ Parallel testing interrupted (saving partial results...)")
            interrupted = True

        finally:
            # Download reports and cleanup all devices
            for device_name, executor in device_executors.items():
                try:
                    remote_path = remote_report_paths.get(device_name)
                    if remote_path:
                        # Find device config to get system_product
                        device_conf = next((d for d in DUTConfig.DEVICES if d['name'] == device_name), None)

                        if device_conf:
                            from datetime import datetime
                            import re

                            testing_day = datetime.now().strftime('%Y-%m-%d')

                            report_subdir = ReportPaths.LOCAL_REPORTS_DIR / device_conf["system_product"] / testing_day
                            report_subdir.mkdir(parents=True, exist_ok=True)

                            local_path = executor.download_report(remote_path, str(report_subdir))
                        else:
                            local_path = executor.download_report(remote_path, str(ReportPaths.LOCAL_REPORTS_DIR))

                        if local_path:
                            logger.info(f"✓ [{device_name}] Report downloaded")
                            results[device_name] = {'success': True, 'error': None}
                        else:
                            results[device_name] = {'success': False, 'error': 'Report download failed'}

                    # Re-enable sleep
                    # try:
                    #     executor.run_agent_command("allow_sleep")
                    # except:
                    #     pass

                    executor.close()

                except Exception as e:
                    logger.error(f"✖ [{device_name}] Cleanup failed: {e}")
                    results[device_name] = {'success': False, 'error': str(e)}

            # Global cleanup
            self._cleanup()

            # Print summary
            logger.info("\n=== Test Summary ===")
            for device, result in results.items():
                status = "✓ PASS" if result['success'] else "✖ FAIL"
                failures = device_failure_counts.get(device, 0)
                logger.info(f"{status} - {device} (failures: {failures})")

            if interrupted:
                logger.warning("⚠ Tests completed partially due to interruption")

        return results

    def _test_channel_on_all_devices(self, device_executors, remote_report_paths,
                                     ssid, password, band_display, standard, channel, max_workers):
        """
        Test a single channel on all devices in parallel (synchronized).
        Uses timeout to prevent one frozen device from blocking all others.

        :param device_executors: Dict of device_name -> RemoteDeviceExecutor
        :param remote_report_paths: Dict of device_name -> remote_report_path
        :param ssid: Network SSID
        :param password: Network password
        :param band_display: Band name for display (e.g., "2.4 GHz")
        :param standard: Wi-Fi standard (e.g., "11n")
        :param channel: Channel number
        :param max_workers: Maximum parallel workers
        :return: Dict of device_name -> bool (True if failed)
        """
        from concurrent.futures import ThreadPoolExecutor, wait

        failures = {}

        def test_device_on_channel(device_name, executor):
            """Test single device on current channel."""
            try:
                # Connect to Wi-Fi
                if not executor.connect_wifi(ssid, password):
                    logger.warning(f"[{device_name}] Failed to connect to {ssid}")
                    return False

                # Run iperf
                iperf_output = executor.run_iperf()

                if not iperf_output:
                    logger.warning(f"[{device_name}] Iperf failed")
                    return False

                # Save checkpoint if enabled
                if self.enable_checkpoints:
                    self._save_checkpoint(device_name, band_display, standard, channel)

                # Add to remote report
                remote_path = remote_report_paths.get(device_name)
                if remote_path:
                    standard_formatted = f"802.{standard}"
                    executor.add_remote_test_result(
                        remote_path, band_display, ssid, standard_formatted, channel, iperf_output
                    )

                logger.info(f"✓ [{device_name}] Ch{channel} completed")
                return True

            except Exception as e:
                logger.error(f"✖ [{device_name}] Ch{channel} failed: {e}")
                return False

        # Execute tests in parallel with timeout
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(test_device_on_channel, name, executor): name
                for name, executor in device_executors.items()
            }

            # Wait with timeout (max: WIFI_CONNECTION_TIMEOUT + IPERF_TIMEOUT + 30s buffer)
            max_wait_time = Timings.WIFI_CONNECTION_TIMEOUT + Timings.IPERF_TIMEOUT + 30

            try:
                # Block until ALL devices finish OR timeout
                done, not_done = wait(futures, timeout=max_wait_time)

                # Handle timed-out devices
                if not_done:
                    logger.warning(f"⚠ {len(not_done)} device(s) timed out on Ch{channel}")
                    for future in not_done:
                        device_name = futures[future]
                        logger.error(f"✖ [{device_name}] Timeout - cancelling")
                        future.cancel()
                        failures[device_name] = True

                # Check results from completed devices
                for future in done:
                    device_name = futures[future]
                    try:
                        success = future.result()
                        if not success:
                            logger.warning(f"[{device_name}] Test on Ch{channel} had issues")
                            failures[device_name] = True
                    except Exception as e:
                        logger.error(f"[{device_name}] Unexpected error: {e}")
                        failures[device_name] = True

            except Exception as e:
                logger.error(f"Critical error during parallel testing: {e}")
                # Cancel all pending futures
                for future in futures:
                    future.cancel()
                # Mark all as failed
                for device_name in device_executors.keys():
                    failures[device_name] = True

        return failures

    def _emergency_cleanup(self, executor=None):
        """
        Emergency cleanup on interrupt (Ctrl+C).

        :param executor: RemoteDeviceExecutor instance (optional)
        """
        logger.info("Performing emergency cleanup...")

        # Try to restore sleep settings on DUT
        if executor:
            try:
                executor.run_agent_command("allow_sleep")
            except:
                pass

            try:
                executor.close()
            except:
                pass

        # Reset router
        try:
            self.router.set_channel_auto()
            self.router.set_standard_auto()
            self.router.close()
        except:
            pass

        logger.info("Emergency cleanup completed")

    def test_band(self, device_executor, band_name, net_config, remote_report_path=None):
        """
        Test a specific frequency band (2.4 GHz or 5 GHz) across all standards and channels.
        Results are incrementally saved to report on DUT after each test.

        :param device_executor: RemoteDeviceExecutor instance
        :param band_name: Band identifier ("2G" or "5G")
        :param net_config: Network configuration dictionary
        :param remote_report_path: Path to report file on DUT (optional)
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

            try:
                self.router.change_standard(net_config["device"], standard)
            except Exception as e:
                logger.error(f"Failed to set standard {standard}: {e}")
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

                # Save checkpoint after successful test
                if self.enable_checkpoints:
                    self._save_checkpoint(
                        device_executor.ip,
                        band_name,
                        standard,
                        channel
                    )

                # Add result to remote report incrementally (fault-tolerant)
                if remote_report_path and iperf_output:
                    try:
                        # Convert standard format (11n -> 802.11n)
                        standard_formatted = f"802.{standard}"

                        device_executor.add_remote_test_result(
                            remote_report_path,
                            band_display,
                            ssid,
                            standard_formatted,
                            channel,
                            iperf_output
                        )
                    except Exception as e:
                        logger.debug(f"Failed to add test to remote report: {e}")

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
    import argparse
    import sys

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="WiFi Test Orchestrator with parallel execution and checkpoint support")
    parser.add_argument('--parallel', action='store_true',
                        help='Run tests in parallel across all devices')
    parser.add_argument('--resume', action='store_true',
                        help='Enable checkpoint/resume capability (saves progress)')
    parser.add_argument('--workers', type=int, default=None,
                        help='Maximum number of parallel workers (default: number of devices)')
    parser.add_argument('--auto-discover', action='store_true',
                        help='Automatically discover DUT devices in network')
    parser.add_argument('--discovery-user', default='slave',
                        help='Default SSH username for device discovery (default: slave)')
    parser.add_argument('--discovery-password', default='66668888',
                        help='Default SSH password for device discovery')
    parser.add_argument('--master-ip', default=None,
                        help='Master station IP to exclude from discovery (default: auto-detect)')
    args = parser.parse_args()

    # Auto-discover devices if requested
    if args.auto_discover:
        from device_discovery import DeviceDiscovery, save_discovered_config
        import socket

        logger.info("Auto-discovery mode enabled")

        # Get router config from existing config
        try:
            from config import RouterConfig

            router_config = {
                'ip': RouterConfig.IP,
                'user': RouterConfig.USER,
                'password': RouterConfig.PASSWORD
            }
        except:
            logger.error("RouterConfig not found in config.py")
            sys.exit(1)

        # Auto-detect master IP if not provided
        master_ip = args.master_ip
        if not master_ip:
            try:
                # Get local IP by connecting to router
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect((router_config['ip'], 80))
                master_ip = s.getsockname()[0]
                s.close()
                logger.info(f"Auto-detected master station IP: {master_ip}")
            except:
                logger.error("Failed to auto-detect master IP. Use --master-ip argument")
                sys.exit(1)

        # Run discovery
        discovery = DeviceDiscovery(router_config, master_ip)
        devices = discovery.discover_devices(
            args.discovery_user,
            args.discovery_password
        )

        if not devices:
            logger.error("No devices discovered. Exiting.")
            sys.exit(1)

        # Save and update config
        save_discovered_config(devices, "discovered_devices.py")
        DUTConfig.DEVICES = devices

        logger.info(f"✓ Discovered {len(devices)} devices, proceeding with tests...")

    orchestrator = WiFiTestOrchestrator(enable_checkpoints=args.resume)

    try:
        if args.parallel:
            logger.info(f"Parallel mode enabled (max workers: {args.workers or len(DUTConfig.DEVICES)})")
            orchestrator.run_parallel_suite(max_workers=args.workers)
        else:
            orchestrator.run_full_suite()

    except KeyboardInterrupt:
        logger.warning("\n⚠ Test suite interrupted by user")
        sys.exit(130)  # Standard exit code for Ctrl+C

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)