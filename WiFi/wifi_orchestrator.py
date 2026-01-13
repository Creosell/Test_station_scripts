# wifi_orchestrator.py
import time
import logging
from pathlib import Path
from config import (setup_logging, NETWORKS, WIFI_STANDARDS_2G, WIFI_STANDARDS_5G, 
                    Timings, Limits, DUTConfig, ReportPaths)
from router_manager import RouterManager
from remote_executor import RemoteDeviceExecutor

# Initialize logging
logger = setup_logging()


class WiFiTestOrchestrator:
    """
    Orchestrates WiFi performance tests across multiple devices with automatic HTML report generation.
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
        remote_report_path = None
        
        try:
            # Get system product name from DUT
            stdout, stderr = device_executor.run_agent_command("sysinfo")
            system_product_name = "Unknown"
            for line in stdout.split('\n'):
                if line.startswith("SYSTEM_PRODUCT:"):
                    system_product_name = line.split(":", 1)[1].strip()
                    break
            
            logger.info(f"Device identified: {system_product_name}")
            
            # Initialize report on DUT (incremental approach)
            remote_report_path = device_executor.init_remote_report(system_product_name, device_ip)
            
            if not remote_report_path:
                logger.error("Failed to initialize remote report, tests will run without reporting")
            
            # Run band tests with incremental reporting
            self.test_band(device_executor, "2G", NETWORKS["2G"], remote_report_path)
            time.sleep(Timings.BAND_TEST_DELAY)
            self.test_band(device_executor, "5G", NETWORKS["5G"], remote_report_path)
            
            # Download final report from DUT
            if remote_report_path:
                local_path = device_executor.download_report(remote_report_path, str(ReportPaths.LOCAL_REPORTS_DIR))
                if local_path:
                    logger.info(f"✓ Report saved locally: {Path(local_path).name}")
                else:
                    logger.warning("Failed to download report from DUT")
            
        except Exception as e:
            logger.error(f"Device test sequence aborted: {e}")
            
            # Still try to download partial report
            if remote_report_path:
                try:
                    local_path = device_executor.download_report(remote_report_path, str(ReportPaths.LOCAL_REPORTS_DIR))
                    if local_path:
                        logger.warning(f"Partial report downloaded: {Path(local_path).name}")
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
        :param standard: Current WiFi standard
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
        Execute test suite with parallel device testing using ThreadPoolExecutor.
        
        :param max_workers: Maximum number of parallel threads (default: number of devices)
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        logger.info("=== Starting Parallel Multi-Device WiFi Test ===")
        
        max_workers = max_workers or len(DUTConfig.DEVICES)
        results = {}
        
        def test_device_wrapper(device_conf):
            """Wrapper for thread-safe device testing."""
            device_name = device_conf["name"]
            try:
                logger.info(f"\n>>> TARGETING DEVICE: {device_name} ({device_conf['ip']}) <<<\n")
                
                executor = RemoteDeviceExecutor(device_conf)
                executor.connect()
                
                # Prevent sleep on DUT
                try:
                    executor.run_agent_command("prevent_sleep")
                    logger.info(f"[{device_name}] Sleep prevention enabled")
                except Exception as e:
                    logger.warning(f"[{device_name}] Could not prevent sleep: {e}")
                
                # Run tests
                self.run_device_tests(executor, device_conf)
                
                return (device_name, True, None)
            
            except KeyboardInterrupt:
                logger.warning(f"\n⚠ [{device_name}] Interrupted by user")
                raise
            
            except Exception as e:
                logger.error(f"[{device_name}] Test failed: {e}")
                return (device_name, False, str(e))
            
            finally:
                if 'executor' in locals():
                    try:
                        executor.run_agent_command("allow_sleep")
                        logger.info(f"[{device_name}] Sleep prevention disabled")
                    except:
                        pass
                    
                    try:
                        executor.close()
                    except:
                        pass
        
        # Execute tests in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(test_device_wrapper, dev): dev['name'] 
                      for dev in DUTConfig.DEVICES}
            
            try:
                for future in as_completed(futures):
                    device_name = futures[future]
                    try:
                        name, success, error = future.result()
                        results[name] = {'success': success, 'error': error}
                        
                        if success:
                            logger.info(f"✓ [{name}] Testing completed successfully")
                        else:
                            logger.error(f"✖ [{name}] Testing failed: {error}")
                    
                    except Exception as e:
                        logger.error(f"✖ [{device_name}] Unexpected error: {e}")
                        results[device_name] = {'success': False, 'error': str(e)}
            
            except KeyboardInterrupt:
                logger.warning("\n⚠ Parallel testing interrupted, waiting for threads...")
                pool.shutdown(wait=True, cancel_futures=True)
                raise
        
        # Global cleanup
        self._cleanup()
        
        # Print summary
        logger.info("\n=== Test Summary ===")
        for device, result in results.items():
            status = "✓ PASS" if result['success'] else "✖ FAIL"
            logger.info(f"{status} - {device}")
        
        return results

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
        logger.info("=== All Tests Finished ===")

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
    parser = argparse.ArgumentParser(description="WiFi Test Orchestrator with parallel execution and checkpoint support")
    parser.add_argument('--parallel', action='store_true', 
                       help='Run tests in parallel across all devices')
    parser.add_argument('--resume', action='store_true',
                       help='Enable checkpoint/resume capability (saves progress)')
    parser.add_argument('--workers', type=int, default=None,
                       help='Maximum number of parallel workers (default: number of devices)')
    args = parser.parse_args()
    
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
