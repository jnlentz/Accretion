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
        self.timeframes = timeframes or ["1h"]
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

    def get_warmup_candles(self, symbol: str, timeframe: str = "1h", limit: int = 168) -> List[Dict[str, Any]]:
        """
        Retrieves up to `limit` closed bars from local SQLite database.
        If local storage has fewer than `limit` bars and an adapter is present,
        fetches the required history from the exchange and persists it first.
        """
        symbol = symbol.upper()
        db_path = self._db_paths.get(symbol)
        table_name = f"klines_{timeframe}"

        # 1. First check local SQLite database
        local_bars: List[Dict[str, Any]] = []
        if db_path and os.path.exists(db_path):
            try:
                with sqlite3.connect(db_path, timeout=10.0) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute(f"""
                        SELECT time, open, high, low, close, volume, trade_count
                        FROM {table_name}
                        ORDER BY time DESC
                        LIMIT ?
                    """, (limit,))
                    rows = cursor.fetchall()
                    local_bars = [dict(r) for r in reversed(rows)]
            except Exception as e:
                logger.warning(f"Error querying local klines for {symbol}: {e}")

        if len(local_bars) >= limit:
            logger.info(f"Loaded {len(local_bars)} warmup bars for {symbol} ({timeframe}) from local DB.")
            return local_bars

        # 2. If insufficient, fetch directly from exchange adapter
        if self.adapter:
            logger.info(f"Local bars ({len(local_bars)}) < {limit}. Fetching {limit + 10} bars from exchange for {symbol}...")
            try:
                raw_klines = self.adapter.get_klines(symbol=symbol, interval=timeframe, limit=limit + 10)
                # Discard forming bar (the very last one)
                closed_klines = raw_klines[:-1] if len(raw_klines) > 1 else []
                if closed_klines and db_path:
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
                        for k in closed_klines:
                            cursor.execute(f"""
                                INSERT OR REPLACE INTO {table_name} (time, open, high, low, close, volume, trade_count)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (
                                k['open_time'], k['open'], k['high'], k['low'], k['close'], k['volume'], float(k['trade_count'])
                            ))
                        conn.commit()

                    # Re-query the latest limit closed bars
                    formatted_bars = [
                        {
                            'time': k['open_time'],
                            'open': k['open'],
                            'high': k['high'],
                            'low': k['low'],
                            'close': k['close'],
                            'volume': k['volume'],
                            'trade_count': float(k['trade_count'])
                        }
                        for k in closed_klines[-limit:]
                    ]
                    logger.info(f"Warmed {len(formatted_bars)} bars for {symbol} ({timeframe}) from exchange.")
                    return formatted_bars

            except Exception as e:
                logger.error(f"Failed to fetch warmup klines from exchange for {symbol}: {e}")

        return local_bars

    def get_latest_closed_bar(self, symbol: str, timeframe: str = "1h") -> Optional[Dict[str, Any]]:
        """Returns the most recent closed candle recorded in SQLite."""
        db_path = self._db_paths.get(symbol.upper())
        if not db_path or not os.path.exists(db_path):
            return None

        table_name = f"klines_{timeframe}"
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(f"SELECT * FROM {table_name} ORDER BY time DESC LIMIT 1")
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error fetching latest closed bar for {symbol}: {e}")
            return None

