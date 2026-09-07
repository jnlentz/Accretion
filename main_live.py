"""
Accretion Live Runtime Entrypoint (v1)
Autonomous live structural wave trading engine, market data ingestion,
zero-market-order monitored execution, and account reconciliation runner.
"""
import os
import sys
import logging
from pathlib import Path

# --- Path Setup ---
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

import config
from src.utils.logger import setup_logger
from src.adapters.binance_client import BinanceSpotAdapter
from src.ledger.database import SQLiteLedger
from src.ledger.reconciliation import ReconciliationEngine
from src.strategy.trade_engine import TradeEngine
from src.runtime.order_manager import OrderExecutionManager
from src.runtime.market_poller import MarketPoller
from src.runtime.account_poller import AccountPoller
from src.runtime.command_processor import CommandProcessor
from src.runtime.loop_runner import LoopRunner


def main():
    # 1. Setup Structured Logging
    log_dir = getattr(config, 'LOG_DIR', os.path.join(config.BASE_DIR, 'logs'))
    logger = setup_logger(name="accretion", log_dir=log_dir, level=logging.INFO)
    
    logger.info("Initializing Accretion v1 Live Runtime...")

    # 2. Configuration & Paths
    state_dir = getattr(config, 'STATE_DIR', os.path.join(config.BASE_DIR, 'state'))
    os.makedirs(state_dir, exist_ok=True)
    ledger_db_path = os.path.join(state_dir, 'accretion_ledger.sqlite')
    
    # Active symbol and timeframe
    primary_symbol = 'BTCUSDT'
    target_symbols = [primary_symbol]
    strategy_timeframe = '1h'
    exchange_name = 'binance_us'
    tld = 'us'

    # 3. Initialize SQLite Ledger
    ledger = SQLiteLedger(db_path=ledger_db_path)

    # 4. Initialize Exchange Adapter
    api_key = getattr(config, 'BINANCE_API_KEY', '')
    api_secret = getattr(config, 'BINANCE_API_SECRET', '')
    adapter = BinanceSpotAdapter(
        api_key=api_key,
        api_secret=api_secret,
        tld=tld
    )

    # 5. Initialize Structural Strategy Engine
    trade_engine = TradeEngine(
        initial_target=0.12,        # 12% initial leg target
        purple_discount=0.02,       # 2% discount below close for resting purple bid
        floor_target=0.005,         # 0.5% harvest floor
        day_hours=24,               # 24h trailing daily container
        week_hours=168,             # 168h trailing weekly container
        micro_floor_hours=6         # 6h trailing micro-floor
    )

    # 6. Initialize Order Execution Manager (Zero-Market-Order Engine)
    order_manager = OrderExecutionManager(
        adapter=adapter,
        ledger=ledger,
        default_quote_allocation_usd=100.0,  # Configurable sizing
        default_timeout_sec=20.0,            # 20s chase window
        default_max_drift_pct=0.005          # 0.5% drift ceiling
    )

    # 7. Initialize Poller & Reconciliation Engines
    reconciliation_engine = ReconciliationEngine(
        adapter=adapter,
        ledger=ledger,
        target_symbols=target_symbols
    )

    market_poller = MarketPoller(
        symbols=target_symbols,
        exchange=exchange_name,
        timeframes=[strategy_timeframe],
        adapter=adapter
    )

    account_poller = AccountPoller(
        reconciliation_engine=reconciliation_engine
    )

    command_processor = CommandProcessor(
        ledger=ledger,
        adapter=adapter
    )

    # 8. Initialize Runtime Loop Runner
    runner = LoopRunner(
        market_poller=market_poller,
        account_poller=account_poller,
        command_processor=command_processor,
        ledger=ledger,
        order_manager=order_manager,
        trade_engine=trade_engine,
        target_symbol=primary_symbol,
        strategy_timeframe=strategy_timeframe,
        market_interval_sec=60,   # Check for 1h candle closes every 60s
        account_interval_sec=60,  # Account reconciliation every 60s
        command_interval_sec=5    # Command processing & active order chasing every 5s
    )

    # 9. Start Engine
    runner.start()


if __name__ == '__main__':
    main()
