"""
====================================================================================================
PROJECT ACCRETION: ACTIVITY & TELEMETRY LEDGER (SQLITE WAL MODE)
====================================================================================================
Purpose:
  Provides a high-concurrency, indexed SQLite activity database designed for real-time live trading
  telemetry and future Android mobile app backend integration:
    1. predictions_and_features : Historical log of all 15m feature vectors and GBDT inferences.
    2. order_activity_log       : Event log for every order submit, cancel, fill, timeout, and stop.
    3. portfolio_snapshots      : Real-time and periodic snapshots of equity, cash, and slot usage.
    4. completed_trades         : Historical round-trip performance ledger (entry, exit, PnL, duration).

Concurrency Architecture:
  - Configured with PRAGMA journal_mode=WAL and PRAGMA busy_timeout=5000.
  - Allows external Android backend API processes (e.g. FastAPI) to read concurrently with zero lock contention.
====================================================================================================
"""

import os
import sys
import json
import time
import sqlite3
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timezone
from dataclasses import asdict, is_dataclass

logger = logging.getLogger("accretion.ledger.activity")


class ActivityLedger:
    """
    High-performance telemetry and activity ledger for Accretion.
    Stores all operational events, feature states, and portfolio snapshots in SQLite.
    """

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path:
            self.db_path = Path(db_path)
        else:
            project_root = Path(__file__).resolve().parent.parent.parent
            self.db_path = project_root / "state" / "accretion_live_activity.sqlite"

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        """Initializes tables and indexes for activity telemetry."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Predictions & Structural Features
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS predictions_and_features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    timestamp_unix INTEGER NOT NULL,
                    kraken_symbol TEXT NOT NULL,
                    binance_symbol TEXT NOT NULL,
                    close_price REAL NOT NULL,
                    p_pred REAL NOT NULL,
                    cutoff_threshold REAL NOT NULL,
                    is_signal INTEGER NOT NULL,
                    bar_rvol REAL NOT NULL,
                    limit_buy_price REAL NOT NULL,
                    limit_sell_price REAL NOT NULL,
                    stop_loss_price REAL NOT NULL,
                    x_star REAL NOT NULL,
                    y_star REAL NOT NULL,
                    features_json TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pred_time ON predictions_and_features(timestamp_unix);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pred_sym ON predictions_and_features(kraken_symbol);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pred_signal ON predictions_and_features(is_signal);")

            # 2. Order Activity Log
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS order_activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    timestamp_unix INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    binance_symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    allocated_capital REAL NOT NULL,
                    order_id TEXT,
                    exchange_order_id TEXT,
                    reason TEXT,
                    status TEXT NOT NULL,
                    details_json TEXT
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_time ON order_activity_log(timestamp_unix);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_sym ON order_activity_log(symbol);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_event ON order_activity_log(event_type);")

            # 3. Portfolio Snapshots (Real-time Android State Feed)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    timestamp_unix INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    total_equity REAL NOT NULL,
                    free_cash REAL NOT NULL,
                    slots_occupied INTEGER NOT NULL,
                    max_slots INTEGER NOT NULL,
                    active_positions_count INTEGER NOT NULL,
                    resting_orders_count INTEGER NOT NULL,
                    total_realized_pnl REAL NOT NULL,
                    active_positions_json TEXT NOT NULL,
                    resting_orders_json TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_snap_time ON portfolio_snapshots(timestamp_unix);")

            # 4. Completed Trades Ledger
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS completed_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    binance_symbol TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    allocated_capital REAL NOT NULL,
                    dollar_pnl REAL NOT NULL,
                    net_ret_pct REAL NOT NULL,
                    exit_reason TEXT NOT NULL,
                    bars_held INTEGER NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_exit ON completed_trades(exit_time);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_sym ON completed_trades(symbol);")

            conn.commit()

    # ==========================================================================
    # ✍️ WRITE METHODS (Called by Live Strategy Runner)
    # ==========================================================================
    def log_prediction(self, sig: Any) -> None:
        """Logs a single 15m prediction and feature vector."""
        now_ts = datetime.now(timezone.utc)
        dt_str = sig.timestamp.strftime("%Y-%m-%d %H:%M:%S") if hasattr(sig.timestamp, 'strftime') else str(sig.timestamp)
        unix_ts = int(sig.timestamp.timestamp()) if hasattr(sig.timestamp, 'timestamp') else int(now_ts.timestamp())
        rvol = float(sig.features.get('bar_rvol', 1.0)) if hasattr(sig, 'features') else 1.0

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO predictions_and_features (
                    timestamp, timestamp_unix, kraken_symbol, binance_symbol,
                    close_price, p_pred, cutoff_threshold, is_signal, bar_rvol,
                    limit_buy_price, limit_sell_price, stop_loss_price,
                    x_star, y_star, features_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dt_str,
                unix_ts,
                sig.kraken_symbol,
                sig.binance_symbol,
                float(sig.close_price),
                float(sig.p_pred),
                float(sig.cutoff_threshold),
                1 if sig.is_signal else 0,
                rvol,
                float(sig.limit_buy_price),
                float(sig.limit_sell_price),
                float(sig.stop_loss_price),
                float(sig.x_star),
                float(sig.y_star),
                json.dumps(sig.features)
            ))
            conn.commit()

    def log_order_event(
        self,
        event_type: str,
        symbol: str,
        binance_symbol: str,
        side: str,
        order_type: str,
        price: float,
        quantity: float,
        allocated_capital: float,
        order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
        reason: str = "",
        status: str = "CONFIRMED",
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Logs an order lifecycle event (placement, cancellation, fill, stop, etc.)."""
        now = datetime.now(timezone.utc)
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO order_activity_log (
                    timestamp, timestamp_unix, event_type, symbol, binance_symbol,
                    side, order_type, price, quantity, allocated_capital,
                    order_id, exchange_order_id, reason, status, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now.strftime("%Y-%m-%d %H:%M:%S"),
                int(now.timestamp()),
                event_type,
                symbol,
                binance_symbol,
                side.upper(),
                order_type.upper(),
                float(price),
                float(quantity),
                float(allocated_capital),
                order_id,
                exchange_order_id,
                reason,
                status,
                json.dumps(details or {})
            ))
            conn.commit()

    def log_snapshot(
        self,
        mode: str,
        total_equity: float,
        free_cash: float,
        slots_occupied: int,
        max_slots: int,
        active_positions: Dict[str, Any],
        resting_orders: Dict[str, Any],
        total_realized_pnl: float = 0.0
    ) -> None:
        """Logs a real-time portfolio snapshot for Android app monitoring."""
        now = datetime.now(timezone.utc)

        def _serialize_item(item: Any) -> Any:
            if hasattr(item, 'to_dict') and callable(getattr(item, 'to_dict')):
                return item.to_dict()
            if is_dataclass(item):
                return asdict(item)
            if hasattr(item, '__dataclass_fields__'):
                return asdict(item)
            if isinstance(item, dict):
                return item
            return str(item)

        pos_json = json.dumps({s: _serialize_item(p) for s, p in active_positions.items()})
        orders_json = json.dumps({s: _serialize_item(o) for s, o in resting_orders.items()})

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO portfolio_snapshots (
                    timestamp, timestamp_unix, mode, total_equity, free_cash,
                    slots_occupied, max_slots, active_positions_count, resting_orders_count,
                    total_realized_pnl, active_positions_json, resting_orders_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now.strftime("%Y-%m-%d %H:%M:%S"),
                int(now.timestamp()),
                mode.upper(),
                float(total_equity),
                float(free_cash),
                int(slots_occupied),
                int(max_slots),
                len(active_positions),
                len(resting_orders),
                float(total_realized_pnl),
                pos_json,
                orders_json
            ))
            conn.commit()

    def log_completed_trade(self, trade: Dict[str, Any]) -> None:
        """Logs a completed round-trip trade into the permanent performance ledger."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO completed_trades (
                    symbol, binance_symbol, entry_time, exit_time,
                    entry_price, exit_price, quantity, allocated_capital,
                    dollar_pnl, net_ret_pct, exit_reason, bars_held
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade['symbol'],
                trade['binance_symbol'],
                str(trade['entry_time']),
                str(trade['exit_time']),
                float(trade['entry_price']),
                float(trade['exit_price']),
                float(trade['quantity']),
                float(trade['allocated_capital']),
                float(trade['dollar_pnl']),
                float(trade['net_ret_pct']),
                str(trade['exit_reason']),
                int(trade.get('bars_held', 0))
            ))
            conn.commit()

    # ==========================================================================
    # 📱 READ API METHODS (For future Android Mobile App Backend)
    # ==========================================================================
    def get_latest_snapshot(self) -> Optional[Dict[str, Any]]:
        """Returns the most recent portfolio snapshot for mobile dashboard rendering."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d['active_positions'] = json.loads(d['active_positions_json'])
                d['resting_orders'] = json.loads(d['resting_orders_json'])
                return d
        return None

    def get_recent_activity(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns the latest N order events for activity feed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM order_activity_log ORDER BY id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_recent_predictions(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns recent predictions and feature vectors."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if symbol:
                cursor.execute("""
                    SELECT * FROM predictions_and_features
                    WHERE kraken_symbol = ?
                    ORDER BY id DESC LIMIT ?
                """, (symbol.upper(), limit))
            else:
                cursor.execute("SELECT * FROM predictions_and_features ORDER BY id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d['features'] = json.loads(d['features_json'])
                out.append(d)
            return out

    def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns recent completed trades."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM completed_trades ORDER BY id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
