"""
Dual-Interval Runtime Loop Runner & Signal Handler
Coordinates the Market Poller (3m), Account Poller (1m), and Command Processor (5s).
"""
import time
import signal
import logging
from typing import Optional, Callable
from src.runtime.market_poller import MarketPoller
from src.runtime.account_poller import AccountPoller
from src.runtime.command_processor import CommandProcessor
from src.ledger.database import SQLiteLedger

logger = logging.getLogger("accretion.runtime.runner")

class LoopRunner:
    """
    Main runtime loop engine coordinating periodic tasks and OS signal handling.
    """
    def __init__(
        self,
        market_poller: MarketPoller,
        account_poller: AccountPoller,
        command_processor: CommandProcessor,
        ledger: SQLiteLedger,
        market_interval_sec: int = 180,
        account_interval_sec: int = 60,
        command_interval_sec: int = 5
    ):
        self.market_poller = market_poller
        self.account_poller = account_poller
        self.command_processor = command_processor
        self.ledger = ledger
        self.market_interval = market_interval_sec
        self.account_interval = account_interval_sec
        self.command_interval = command_interval_sec

        self.is_running = False
        self._last_market_poll = 0.0
        self._last_account_poll = 0.0
        self._last_command_poll = 0.0

        self._setup_signals()

    def _setup_signals(self) -> None:
        """Installs OS signal handlers for graceful shutdown."""
        try:
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        except Exception:
            # When running in non-main threads, signal binding is skipped
            pass

    def _signal_handler(self, signum, frame):
        logger.info(f"Received termination signal ({signum}). Initiating graceful shutdown...")
        self.stop()

    def start(self, max_cycles: Optional[int] = None) -> None:
        """
        Starts the main execution loop.
        Runs startup reconciliation and market ingestion immediately, then enters interval polling.
        """
        self.is_running = True
        logger.info("==================================================")
        logger.info("       ACCRETION LIVE RUNTIME STARTING            ")
        logger.info("==================================================")
        logger.info(f"Market Interval: {self.market_interval}s | Account Interval: {self.account_interval}s | Command Interval: {self.command_interval}s")
        self.ledger.log_event("INFO", "SYSTEM", "Accretion live runtime started.")

        # Immediate startup cycle
        try:
            logger.info("Running initial startup account reconciliation...")
            self.account_poller.poll_and_reconcile()
            self._last_account_poll = time.time()
        except Exception as e:
            logger.error(f"Startup account reconciliation error: {e}")

        try:
            logger.info("Running initial market data poll...")
            self.market_poller.poll_and_store_all()
            self._last_market_poll = time.time()
        except Exception as e:
            logger.error(f"Startup market poller error: {e}")

        cycles = 0

        try:
            while self.is_running:
                now = time.time()

                # 1. Command Processor Loop (Fast ~5s)
                if now - self._last_command_poll >= self.command_interval:
                    try:
                        self.command_processor.process_pending_commands()
                    except Exception as e:
                        logger.error(f"Error in CommandProcessor loop: {e}")
                    self._last_command_poll = now

                # 2. Account Reconciliation Loop (~60s)
                if now - self._last_account_poll >= self.account_interval:
                    try:
                        self.account_poller.poll_and_reconcile()
                    except Exception as e:
                        logger.error(f"Error in AccountPoller loop: {e}")
                    self._last_account_poll = now
                    cycles += 1

                # 3. Market Ingest Loop (~180s / 3m)
                if now - self._last_market_poll >= self.market_interval:
                    try:
                        self.market_poller.poll_and_store_all()
                    except Exception as e:
                        logger.error(f"Error in MarketPoller loop: {e}")
                    self._last_market_poll = now

                if max_cycles and cycles >= max_cycles:
                    logger.info(f"Reached maximum requested cycles ({max_cycles}). Stopping...")
                    break

                time.sleep(0.5)

        except KeyboardInterrupt:
            logger.info("Loop interrupted by user.")
        finally:
            self.stop()

    def stop(self) -> None:
        """Stops the loop runner and logs shutdown."""
        if self.is_running:
            self.is_running = False
            logger.info("Accretion live runtime stopped cleanly.")
            self.ledger.log_event("INFO", "SYSTEM", "Accretion live runtime stopped cleanly.")
