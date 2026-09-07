"""
Dual-Interval Runtime Loop Runner & Signal Handler
Coordinates the Market Poller (1H/3m), Account Poller (1m), Command Processor (5s),
Active Order Execution Manager, and the TradeEngine.
"""
import time
import signal
import logging
from typing import Optional, Callable, Dict, Any, List
from src.runtime.market_poller import MarketPoller
from src.runtime.account_poller import AccountPoller
from src.runtime.command_processor import CommandProcessor
from src.runtime.order_manager import OrderExecutionManager
from src.strategy.trade_engine import TradeEngine, TradeAction
from src.ledger.database import SQLiteLedger

logger = logging.getLogger("accretion.runtime.runner")


class LoopRunner:
    """
    Main runtime loop engine coordinating periodic tasks, order chasing,
    stateful TradeEngine evaluations, and OS signal handling.
    """
    def __init__(
        self,
        market_poller: MarketPoller,
        account_poller: AccountPoller,
        command_processor: CommandProcessor,
        ledger: SQLiteLedger,
        order_manager: Optional[OrderExecutionManager] = None,
        trade_engine: Optional[TradeEngine] = None,
        target_symbol: str = "BTCUSDT",
        strategy_timeframe: str = "1h",
        market_interval_sec: int = 60,
        account_interval_sec: int = 60,
        command_interval_sec: int = 5
    ):
        self.market_poller = market_poller
        self.account_poller = account_poller
        self.command_processor = command_processor
        self.ledger = ledger
        self.order_manager = order_manager
        self.trade_engine = trade_engine
        self.target_symbol = target_symbol.upper()
        self.strategy_timeframe = strategy_timeframe

        self.market_interval = market_interval_sec
        self.account_interval = account_interval_sec
        self.command_interval = command_interval_sec

        self.is_running = False
        self._last_market_poll = 0.0
        self._last_account_poll = 0.0
        self._last_command_poll = 0.0
        self._last_processed_bar_time: int = 0

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

    def initialize_strategy(self) -> None:
        """
        Initializes or restores TradeEngine state.
        Checks SQLite ledger for saved state; if missing, performs cold-start warm-up.
        """
        if not self.trade_engine:
            return

        logger.info(f"Initializing TradeEngine for {self.target_symbol} ({self.strategy_timeframe})...")
        saved_state = self.ledger.load_engine_state(self.target_symbol)

        if saved_state:
            self.trade_engine.from_dict(saved_state)
            logger.info(
                f"Restored TradeEngine state from SQLite: Macro={self.trade_engine.macro_state}, "
                f"Micro={self.trade_engine.micro_state}, InPosition={self.trade_engine.in_position}, "
                f"BarCount={self.trade_engine.bar_count}"
            )
        else:
            logger.info(f"No saved state found. Cold starting: warming buffers with {self.trade_engine.week_hours} bars...")
            warmup_bars = self.market_poller.get_warmup_candles(
                symbol=self.target_symbol,
                timeframe=self.strategy_timeframe,
                limit=self.trade_engine.week_hours
            )
            ingested = self.trade_engine.warm_up(warmup_bars)
            logger.info(
                f"Warmed TradeEngine with {ingested} bars. Initial Macro={self.trade_engine.macro_state}, "
                f"Micro={self.trade_engine.micro_state}"
            )
            self.ledger.save_engine_state(self.target_symbol, self.trade_engine.to_dict())

        # Seed last processed bar timestamp from local DB
        latest_bar = self.market_poller.get_latest_closed_bar(self.target_symbol, self.strategy_timeframe)
        if latest_bar:
            self._last_processed_bar_time = int(latest_bar['time'])
            logger.info(f"Last recorded closed bar timestamp: {self._last_processed_bar_time}")

    def start(self, max_cycles: Optional[int] = None) -> None:
        """
        Starts the main execution loop.
        Runs startup reconciliation, market ingestion, and state initialization,
        then enters interval polling.
        """
        self.is_running = True
        logger.info("==================================================")
        logger.info("       ACCRETION LIVE RUNTIME STARTING            ")
        logger.info("==================================================")
        logger.info(
            f"Strategy TF: {self.strategy_timeframe} | Symbol: {self.target_symbol} | "
            f"Market Check: {self.market_interval}s | Account: {self.account_interval}s | Command/Chase: {self.command_interval}s"
        )
        self.ledger.log_event("INFO", "SYSTEM", "Accretion live runtime started.")

        # 1. Startup Account Reconciliation
        try:
            logger.info("Running initial startup account reconciliation...")
            self.account_poller.poll_and_reconcile()
            self._last_account_poll = time.time()
        except Exception as e:
            logger.error(f"Startup account reconciliation error: {e}")

        # 2. Startup Market Data Ingest
        try:
            logger.info("Running initial market data poll...")
            self.market_poller.poll_and_store_all()
            self._last_market_poll = time.time()
        except Exception as e:
            logger.error(f"Startup market poller error: {e}")

        # 3. Initialize Strategy State & Buffers
        try:
            self.initialize_strategy()
        except Exception as e:
            logger.error(f"Strategy initialization error: {e}")

        cycles = 0

        try:
            while self.is_running:
                now = time.time()

                # 1. Command Processor & Active Order Chase Loop (Fast ~5s)
                if now - self._last_command_poll >= self.command_interval:
                    # A. Process incoming commands
                    try:
                        self.command_processor.process_pending_commands()
                    except Exception as e:
                        logger.error(f"Error in CommandProcessor loop: {e}")

                    # B. High-frequency order chasing & fill detection
                    if self.order_manager:
                        try:
                            completed_events = self.order_manager.tick_monitored_orders()
                            for ev in completed_events:
                                if ev.get('event') == 'ORDER_FILLED' and self.trade_engine:
                                    m_order = ev['order']
                                    if m_order.side == 'BUY':
                                        self.trade_engine.sync_position(
                                            in_position=True,
                                            entry_price=ev.get('avg_price', m_order.last_quote_price),
                                            reload_count=self.trade_engine.reload_count
                                        )
                                    elif m_order.side == 'SELL':
                                        self.trade_engine.sync_position(
                                            in_position=False,
                                            entry_price=0.0,
                                            reload_count=self.trade_engine.reload_count
                                        )
                                    # Persist updated position truth
                                    self.ledger.save_engine_state(self.target_symbol, self.trade_engine.to_dict())
                        except Exception as e:
                            logger.error(f"Error ticking OrderExecutionManager: {e}")

                    self._last_command_poll = now

                # 2. Account Reconciliation Loop (~60s)
                if now - self._last_account_poll >= self.account_interval:
                    try:
                        self.account_poller.poll_and_reconcile()
                    except Exception as e:
                        logger.error(f"Error in AccountPoller loop: {e}")
                    self._last_account_poll = now
                    cycles += 1

                # 3. Market Ingest & Strategy Evaluation Loop (~60s)
                if now - self._last_market_poll >= self.market_interval:
                    try:
                        self.market_poller.poll_and_store_all()
                    except Exception as e:
                        logger.error(f"Error in MarketPoller loop: {e}")

                    # Check for completed strategy candle
                    if self.trade_engine:
                        try:
                            latest_closed = self.market_poller.get_latest_closed_bar(
                                symbol=self.target_symbol,
                                timeframe=self.strategy_timeframe
                            )
                            if latest_closed and int(latest_closed['time']) > self._last_processed_bar_time:
                                bar_time = int(latest_closed['time'])
                                logger.info(
                                    f"New completed {self.strategy_timeframe} bar detected: Close=${latest_closed['close']:,.2f} @ {bar_time}. "
                                    f"Feeding into TradeEngine..."
                                )
                                actions = self.trade_engine.on_candle(
                                    timestamp=bar_time,
                                    open_p=float(latest_closed['open']),
                                    high_p=float(latest_closed['high']),
                                    low_p=float(latest_closed['low']),
                                    close_p=float(latest_closed['close']),
                                    volume=float(latest_closed['volume'])
                                )
                                self._last_processed_bar_time = bar_time

                                # Persist updated engine state immediately
                                self.ledger.save_engine_state(self.target_symbol, self.trade_engine.to_dict())

                                logger.info(
                                    f"TradeEngine evaluated: Macro={self.trade_engine.macro_state}, "
                                    f"Micro={self.trade_engine.micro_state} | Emitted {len(actions)} action(s)"
                                )

                                # Route actions to OrderExecutionManager
                                if self.order_manager:
                                    for act in actions:
                                        logger.info(f"Routing action: {act.action_type} ({act.reason}) @ ${act.price:,.2f}")
                                        self.order_manager.handle_action(
                                            action=act,
                                            symbol=self.target_symbol
                                        )

                        except Exception as e:
                            logger.error(f"Error during strategy candle evaluation: {e}")

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
            if self.trade_engine:
                try:
                    self.ledger.save_engine_state(self.target_symbol, self.trade_engine.to_dict())
                    logger.info(f"Persisted TradeEngine state before shutdown for {self.target_symbol}.")
                except Exception as e:
                    logger.error(f"Failed to save engine state on shutdown: {e}")

            logger.info("Accretion live runtime stopped cleanly.")
            self.ledger.log_event("INFO", "SYSTEM", "Accretion live runtime stopped cleanly.")
