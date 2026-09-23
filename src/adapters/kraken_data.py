"""
Kraken Public Market Data Adapter
Ingests completed 15m kline bars into SQLite (databases/kraken/<symbol>.sqlite).
Supports gap-bridging between historical SQLite data and the current moment,
and real-time streaming polling of closed 15m candles.
"""

import os
import sys
import time
import json
import sqlite3
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import pandas as pd

logger = logging.getLogger("accretion.adapter.kraken")

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

# Canonical Kraken Symbol Mapping for Public REST API
KRAKEN_PAIR_MAP = {
    "XBTUSD": "XXBTZUSD",
    "BTCUSD": "XXBTZUSD",
    "ETHUSD": "XETHZUSD",
    "SOLUSD": "SOLUSD",
    "ADAUSD": "ADAUSD",
    "XRPUSD": "XXRPZUSD",
    "XDGUSD": "XXDGZUSD",
    "DOGEUSD": "XXDGZUSD",
}

INTERVAL_MINUTES_15M = 15
INTERVAL_SECONDS_15M = 15 * 60  # 900 seconds


class KrakenData:
    """
    Adapter for Kraken public market data.
    Provides historical gap bridging and live 15m candle ingestion into SQLite.
    """

    def __init__(self, db_dir: Optional[str] = None):
        if db_dir:
            self.db_dir = Path(db_dir)
        else:
            # Default to <project_root>/databases/kraken
            project_root = Path(__file__).resolve().parent.parent.parent
            self.db_dir = project_root / "databases" / "kraken"

        self.db_dir.mkdir(parents=True, exist_ok=True)

    def resolve_db_path(self, symbol: str) -> Path:
        """Returns the absolute path to the SQLite database for a symbol."""
        clean = symbol.upper()
        # Canonicalize aliases
        if clean in ("BTCUSD", "BTCUSDT"):
            clean = "XBTUSD"
        elif clean in ("DOGEUSD", "DOGEUSDT"):
            clean = "XDGUSD"
        elif clean.endswith("USDT"):
            clean = clean.replace("USDT", "USD")

        return self.db_dir / f"{clean}.sqlite"

    def fetch_ohlc_raw(self, symbol: str, interval: int = 15, since: Optional[int] = None) -> Tuple[List[list], int]:
        """
        Queries Kraken's public OHLC endpoint.
        Returns: (candles_list, last_timestamp)
        Each candle: [time, open, high, low, close, vwap, volume, count]
        """
        clean = symbol.upper()
        pair = KRAKEN_PAIR_MAP.get(clean, clean)

        url = f"{KRAKEN_OHLC_URL}?pair={pair}&interval={interval}"
        if since is not None:
            url += f"&since={since}"

        headers = {
            "User-Agent": "Accretion-Live-Engine/1.0 (Windows; Python)"
        }
        req = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to fetch Kraken OHLC for {symbol}: {e}")
            return [], 0

        if data.get("error"):
            logger.error(f"Kraken API error for {symbol}: {data['error']}")
            return [], 0

        result = data.get("result", {})
        last_ts = int(result.get("last", 0))

        # Find the pair data key (first key that is not 'last')
        pair_key = None
        for k in result.keys():
            if k != "last":
                pair_key = k
                break

        if not pair_key or not isinstance(result[pair_key], list):
            logger.warning(f"No OHLC candle array found in Kraken response for {symbol}")
            return [], last_ts

        return result[pair_key], last_ts

    def get_latest_db_timestamp(self, db_path: Path, table_name: str = "klines_15m") -> Optional[int]:
        """
        Queries the latest timestamp in the SQLite database.
        Returns timestamp in seconds, or None if empty / table does not exist.
        """
        if not db_path.exists():
            return None

        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                cursor = conn.cursor()
                # Check table existence
                tables = [r[0] for r in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
                ).fetchall()]
                if not tables:
                    # Check fallback tables
                    for fb in ["bars_15m", "ohlcv_15m"]:
                        tables = [r[0] for r in cursor.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (fb,)
                        ).fetchall()]
                        if tables:
                            table_name = fb
                            break

                if not tables:
                    return None

                cursor.execute(f"SELECT MAX(time) FROM '{table_name}'")
                row = cursor.fetchone()
                if row and row[0] is not None:
                    raw_t = int(row[0])
                    # If ms, convert to seconds
                    return raw_t // 1000 if raw_t > 1e11 else raw_t
        except Exception as e:
            logger.error(f"Error reading latest timestamp from {db_path.name}: {e}")

        return None

    def store_candles(self, db_path: Path, candles: List[list], table_name: str = "klines_15m") -> int:
        """
        Inserts raw Kraken candles into SQLite.
        Filters out forming candles and duplicates.
        """
        if not candles:
            return 0

        now_sec = int(time.time())
        inserted = 0

        with sqlite3.connect(db_path, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS '{table_name}' (
                    time INTEGER PRIMARY KEY,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    trade_count REAL DEFAULT 0
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_time ON '{table_name}'(time)")

            for c in candles:
                c_time = int(c[0])
                # Causal check: candle MUST be closed (c_time + 900s <= now)
                if c_time + INTERVAL_SECONDS_15M > now_sec:
                    continue  # Discard forming bar

                o = float(c[1])
                h = float(c[2])
                l = float(c[3])
                cl = float(c[4])
                vol = float(c[6])
                count = float(c[7]) if len(c) > 7 else 0.0

                cursor.execute(f"""
                    INSERT OR REPLACE INTO '{table_name}' (time, open, high, low, close, volume, trade_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (c_time, o, h, l, cl, vol, count))
                inserted += 1

            conn.commit()

        return inserted

    def bridge_gap(self, symbol: str) -> int:
        """
        Bridges the gap between the end of the SQLite database and the current moment.
        Fetches all missing closed 15m candles from Kraken public API and commits them to SQLite.
        Returns total number of new bars inserted.
        """
        db_path = self.resolve_db_path(symbol)
        latest_ts = self.get_latest_db_timestamp(db_path)
        now_sec = int(time.time())

        if latest_ts is None:
            logger.info(f"No existing data found for {symbol} in {db_path.name}. Fetching recent 720 bars...")
            candles, _ = self.fetch_ohlc_raw(symbol, interval=INTERVAL_MINUTES_15M)
            return self.store_candles(db_path, candles)

        gap_seconds = now_sec - (latest_ts + INTERVAL_SECONDS_15M)
        if gap_seconds < INTERVAL_SECONDS_15M:
            logger.info(f"Database {db_path.name} is already current. Gap: {gap_seconds}s (< 15m).")
            return 0

        gap_bars = gap_seconds // INTERVAL_SECONDS_15M
        logger.info(f"Bridging gap for {symbol}: {gap_bars} missing 15m bars ({gap_seconds / 3600:.1f} hours).")

        total_inserted = 0
        since = latest_ts

        # Paginate through Kraken OHLC until we catch up to current time
        max_iterations = 20  # Safety ceiling (20 * 720 = 14,400 bars = 150 days)
        for _ in range(max_iterations):
            candles, last_ts = self.fetch_ohlc_raw(symbol, interval=INTERVAL_MINUTES_15M, since=since)
            if not candles:
                break

            # Check if all returned candles are already <= since
            valid_candles = [c for c in candles if int(c[0]) > latest_ts]
            if not valid_candles:
                break

            inserted = self.store_candles(db_path, valid_candles)
            total_inserted += inserted

            new_latest = int(valid_candles[-1][0])
            if new_latest <= since or (now_sec - new_latest) < INTERVAL_SECONDS_15M * 2:
                # Caught up to present
                break

            since = new_latest
            time.sleep(0.5)  # Respect public rate limits

        logger.info(f"Successfully bridged {total_inserted} 15m bar(s) for {symbol}. DB is now current.")
        return total_inserted

    def get_warmup_candles(self, symbol: str, limit: int = 672) -> pd.DataFrame:
        """
        Loads the trailing `limit` closed bars from SQLite into a DataFrame.
        Indexed by UTC datetime, columns: ['open', 'high', 'low', 'close', 'volume'].
        """
        db_path = self.resolve_db_path(symbol)
        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

        with sqlite3.connect(db_path) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            target_table = None
            for t in ["klines_15m", "bars_15m", "ohlcv_15m"]:
                if t in tables:
                    target_table = t
                    break

            if not target_table:
                raise KeyError(f"No 15m table found in {db_path.name}")

            query = f"""
                SELECT time, open, high, low, close, volume
                FROM '{target_table}'
                ORDER BY time DESC
                LIMIT ?
            """
            df = pd.read_sql_query(query, conn, params=(limit + 10,))

        if df.empty:
            raise ValueError(f"Table '{target_table}' in {db_path.name} is empty.")

        # Reverse to chronological order
        df = df.iloc[::-1].reset_index(drop=True)

        sample_t = df['time'].iloc[0]
        unit = 'ms' if sample_t > 1e11 else 's'
        df['dt_utc'] = pd.to_datetime(df['time'], unit=unit, utc=True)
        df.drop_duplicates(subset=['dt_utc'], inplace=True)
        df.set_index('dt_utc', inplace=True)

        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)

        return df

    def poll_latest_closed_bar(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Polls the single latest completed 15m bar from Kraken and stores it.
        Returns candle dictionary or None if no new bar closed.
        """
        db_path = self.resolve_db_path(symbol)
        latest_ts = self.get_latest_db_timestamp(db_path) or 0

        candles, _ = self.fetch_ohlc_raw(symbol, interval=INTERVAL_MINUTES_15M, since=latest_ts)
        if not candles:
            return None

        now_sec = int(time.time())
        # Filter for completed bars that are strictly newer than latest_ts
        closed_new = [
            c for c in candles
            if int(c[0]) > latest_ts and (int(c[0]) + INTERVAL_SECONDS_15M) <= now_sec
        ]

        if not closed_new:
            return None

        self.store_candles(db_path, closed_new)
        latest_c = closed_new[-1]
        c_time = int(latest_c[0])

        return {
            "time": c_time,
            "dt": pd.to_datetime(c_time, unit="s", utc=True),
            "open": float(latest_c[1]),
            "high": float(latest_c[2]),
            "low": float(latest_c[3]),
            "close": float(latest_c[4]),
            "volume": float(latest_c[6]),
        }

    def bridge_gap_all(self, symbols: List[str]) -> Dict[str, int]:
        """Bridges gaps across multiple symbols sequentially."""
        results = {}
        for sym in symbols:
            try:
                inserted = self.bridge_gap(sym)
                results[sym] = inserted
            except Exception as e:
                logger.error(f"Error bridging gap for {sym}: {e}")
                results[sym] = 0
            time.sleep(0.3)
        return results

    def poll_latest_closed_bars_all(self, symbols: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """Polls the latest closed bar across all symbols."""
        results = {}
        for sym in symbols:
            try:
                bar = self.poll_latest_closed_bar(sym)
                results[sym] = bar
            except Exception as e:
                logger.error(f"Error polling closed bar for {sym}: {e}")
                results[sym] = None
            time.sleep(0.1)
        return results

