"""
Accretion Live Runtime Entrypoint (v1)
Dry-run live bookkeeping, market data ingestion, and account reconciliation runner.
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
    
    # Active symbol list for v1
    target_symbols = ['BTCUSDT']
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

    # 5. Initialize Reconciliation & Runtime Engines
    reconciliation_engine = ReconciliationEngine(
        adapter=adapter,
        ledger=ledger,
        target_symbols=target_symbols
    )

    market_poller = MarketPoller(
        symbols=target_symbols,
        exchange=exchange_name,
        timeframes=['3m'],
        adapter=adapter
    )

    account_poller = AccountPoller(
        reconciliation_engine=reconciliation_engine
    )

    command_processor = CommandProcessor(
        ledger=ledger,
        adapter=adapter
    )

    # 6. Initialize Dual-Interval Loop Runner
    runner = LoopRunner(
        market_poller=market_poller,
        account_poller=account_poller,
        command_processor=command_processor,
        ledger=ledger,
        market_interval_sec=180,  # 3 minutes for market bar closes
        account_interval_sec=60,  # 1 minute for account reconciliation
        command_interval_sec=5    # 5 seconds for external command checks
    )

    # 7. Start Engine
    runner.start()

if __name__ == '__main__':
    main()
