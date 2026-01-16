"""
Core orchestrator base class for test modules.
Provides common initialization, cleanup, and emergency handling.
"""

import logging
from abc import ABC, abstractmethod
from core.router_manager import RouterManager
from core.config import ReportPaths

logger = logging.getLogger(__name__)


class CoreOrchestrator(ABC):
    """
    Abstract base class for test orchestrators.
    Provides common infrastructure for router management, cleanup, and reporting.
    """

    def __init__(self, report_generator=None):
        """
        Initialize orchestrator with router manager and optional report generator.

        :param report_generator: Optional CoreReportGenerator instance for unified reporting
        """
        self.router = RouterManager()
        self.report = report_generator

        # Ensure local reports directory exists
        ReportPaths.LOCAL_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def run_full_suite(self) -> dict:
        """
        Execute complete test suite sequentially.
        Must be implemented by subclasses.

        :return: Dictionary of test results
        """
        pass

    @abstractmethod
    def run_parallel_suite(self, max_workers=None) -> dict:
        """
        Execute test suite in parallel.
        Must be implemented by subclasses.

        :param max_workers: Maximum number of parallel workers
        :return: Dictionary of test results
        """
        pass

    def _cleanup(self):
        """
        Perform global cleanup: reset router to default settings.
        Can be overridden by subclasses for module-specific cleanup.
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
                executor.run_plugin_command('wifi', 'allow_sleep')
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
