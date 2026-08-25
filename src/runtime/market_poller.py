"""
Market Data Poller
Ingests completed kline bars on timeframe closes into SQLite databases (databases/<exchange>/<symbol>.sqlite).
"""
import os
import time
import sqlite3
import logging
import pandas as pd
from typing import List, Dict, Optional
from src.adapters.binance_client import BinanceSpotAdapter
from src.adapters.BinanceData import BinanceData
import config

logger = logging.getLogger("accretion.runtime.market")

class MarketPoller:
    """
    Polls completed klines on bar closes and persists them into per-symbol SQLite databases.
    Compatible with BinanceData schema (klines_{timeframe}).
    """
    def __init__(
        self,
        symbols: List[str],
        exchange: str = "binance_us",
        timeframes: Optional[List[str]] = None,
        adapter: Optional[BinanceSpotAdapter] = None
    ):
        self.symbols = symbols
        self.exchange = exchange
        self.timeframes = timeframes or ["3m"]
        self.adapter = adapter
        self._db_paths: Dict[str, str] = {}

        # Resolve DB path for each symbol from config.MarketDataConfig
        for sym in self.symbols:
            try:
                db_path = config.MarketDataConfig[self.exchange][sym]
            except (AttributeError, KeyError):
                fallback_dir = os.path.join(config.DATABASES_DIR, self.exchange)
                os.makedirs(fallback_dir, exist_ok=True)
                db_path = os.path.join(fallback_dir, f"{sym}.sqlite")
            
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            self._db_paths[sym] = db_path

    def poll_and_store_all(self) -> Dict[str, int]:
        """
        Polls completed klines for all configured symbols and timeframes and updates databases.
        """
        results: Dict[str, int] = {}
        for symbol in self.symbols:
            for tf in self.timeframes:
                key = f"{symbol}_{tf}"
                try:
                    count = self.poll_symbol_timeframe(symbol, tf)
                    results[key] = count
                except Exception as e:
                    logger.error(f"Failed to poll market data for {symbol} ({tf}): {e}")
                    results[key] = 0
        return results

    def poll_symbol_timeframe(self, symbol: str, timeframe: str) -> int:
        """
        Fetches latest closed klines from adapter and inserts into klines_{timeframe} table.
        """
        if not self.adapter:
            # Fallback to BinanceData sync if no REST adapter provided
            b_data = BinanceData(
                symbol=symbol,
                timeframes=[timeframe],
                exchange=self.exchange,
                update_on_init=False
            )
            b_data.update()
            return 1

        db_path = self._db_paths.get(symbol)
        if not db_path:
            return 0

        table_name = f"klines_{timeframe}"
        klines = self.adapter.get_klines(symbol=symbol, interval=timeframe, limit=5)
        if not klines:
            return 0

        # Discard the currently forming bar (the very last one in list)
        closed_klines = klines[:-1] if len(klines) > 1 else []
        if not closed_klines:
            return 0

        inserted = 0
        with sqlite3.connect(db_path, timeout=10.0) as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    time INTEGER PRIMARY KEY,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    trade_count REAL NOT NULL
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_time ON {table_name}(time)")

            for k in closed_klines:
                t_sec = k['open_time']
                cursor.execute(f"""
                    INSERT OR REPLACE INTO {table_name} (time, open, high, low, close, volume, trade_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    t_sec,
                    k['open'],
                    k['high'],
                    k['low'],
                    k['close'],
                    k['volume'],
                    float(k['trade_count'])
                ))
                inserted += 1
            conn.commit()

        if inserted > 0:
            latest = closed_klines[-1]
            logger.info(f"Stored {inserted} bar(s) for {symbol} ({timeframe}) | Latest Close: ${latest['close']:,.2f} @ {latest['open_time']}")

        return inserted
