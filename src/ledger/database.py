"""
SQLite Ledger Implementation for Accretion
High-reliability transactional storage for balances, orders, fills, reconciliation, and commands.
"""
import os
import json
import time
import uuid
import sqlite3
import logging
from typing import Optional, Dict, Any, List, Union
from src.ledger.schema import init_ledger_schema

logger = logging.getLogger("accretion.ledger")

class SQLiteLedger:
    """
    Thread-safe SQLite ledger manager operating in WAL mode.
    Acts as the local source of truth for all account records, order statuses, and external commands.
    """
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Creates a connection with row factory and WAL pragmas enabled."""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _init_db(self) -> None:
        """Initializes database schema."""
        with self._get_connection() as conn:
            init_ledger_schema(conn)
            logger.info(f"Initialized SQLite Ledger at {self.db_path}")

    # ==========================================
    # Balances
    # ==========================================

    def upsert_balances(self, balances: Dict[str, Dict[str, float]]) -> None:
        """
        Updates asset balances in local ledger.
        balances format: { 'BTC': {'free': 0.5, 'locked': 0.1, 'total': 0.6}, ... }
        """
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for asset, bal in balances.items():
                free = float(bal.get('free', 0.0))
                locked = float(bal.get('locked', 0.0))
                total = float(bal.get('total', free + locked))
                cursor.execute("""
                    INSERT INTO balances (asset, free, locked, total, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(asset) DO UPDATE SET
                        free = excluded.free,
                        locked = excluded.locked,
                        total = excluded.total,
                        updated_at = excluded.updated_at
                """, (asset.upper(), free, locked, total, now))
            conn.commit()

    def get_balances(self) -> Dict[str, Dict[str, float]]:
        """Returns all non-zero asset balances."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT asset, free, locked, total, updated_at FROM balances")
            rows = cursor.fetchall()
            return {
                r['asset']: {
                    'free': r['free'],
                    'locked': r['locked'],
                    'total': r['total'],
                    'updated_at': r['updated_at']
                }
                for r in rows
            }

    # ==========================================
    # Orders
    # ==========================================

    def record_order(self, order_data: Dict[str, Any]) -> str:
        """
        Inserts or updates an order in the orders table.
        """
        now = int(time.time())
        local_id = order_data.get('local_order_id') or f"loc_{uuid.uuid4().hex[:12]}"
        exchange_id = str(order_data['exchange_order_id']) if order_data.get('exchange_order_id') else None
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO orders (
                    local_order_id, exchange_order_id, symbol, side, order_type,
                    price, orig_qty, executed_qty, cummulative_quote_qty, status,
                    time_in_force, client_tag, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_order_id) DO UPDATE SET
                    exchange_order_id = COALESCE(excluded.exchange_order_id, orders.exchange_order_id),
                    price = excluded.price,
                    orig_qty = excluded.orig_qty,
                    executed_qty = excluded.executed_qty,
                    cummulative_quote_qty = excluded.cummulative_quote_qty,
                    status = excluded.status,
                    updated_at = excluded.updated_at
            """, (
                local_id,
                exchange_id,
                order_data.get('symbol', '').upper(),
                order_data.get('side', '').upper(),
                order_data.get('order_type', 'LIMIT').upper(),
                float(order_data.get('price', 0.0)),
                float(order_data.get('orig_qty', 0.0)),
                float(order_data.get('executed_qty', 0.0)),
                float(order_data.get('cummulative_quote_qty', 0.0)),
                order_data.get('status', 'NEW').upper(),
                order_data.get('time_in_force', 'GTC').upper(),
                order_data.get('client_tag', ''),
                int(order_data.get('created_at', now)),
                now
            ))
            conn.commit()
        return local_id

    def update_order_status(
        self,
        status: str,
        exchange_order_id: Optional[str] = None,
        local_order_id: Optional[str] = None,
        executed_qty: Optional[float] = None,
        cummulative_quote_qty: Optional[float] = None
    ) -> bool:
        """Updates status and executed volume of an existing order."""
        now = int(time.time())
        query = "UPDATE orders SET status = ?, updated_at = ?"
        params: List[Any] = [status.upper(), now]

        if executed_qty is not None:
            query += ", executed_qty = ?"
            params.append(float(executed_qty))
        if cummulative_quote_qty is not None:
            query += ", cummulative_quote_qty = ?"
            params.append(float(cummulative_quote_qty))

        if exchange_order_id is not None:
            query += " WHERE exchange_order_id = ?"
            params.append(str(exchange_order_id))
        elif local_order_id is not None:
            query += " WHERE local_order_id = ?"
            params.append(str(local_order_id))
        else:
            raise ValueError("Must provide exchange_order_id or local_order_id")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount > 0

    def get_order(
        self,
        exchange_order_id: Optional[str] = None,
        local_order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Retrieves a single order by exchange ID or local ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if exchange_order_id is not None:
                cursor.execute("SELECT * FROM orders WHERE exchange_order_id = ?", (str(exchange_order_id),))
            elif local_order_id is not None:
                cursor.execute("SELECT * FROM orders WHERE local_order_id = ?", (str(local_order_id),))
            else:
                return None
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all currently active orders ('NEW' or 'PARTIALLY_FILLED')."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if symbol:
                cursor.execute(
                    "SELECT * FROM orders WHERE symbol = ? AND status IN ('NEW', 'PARTIALLY_FILLED') ORDER BY created_at ASC",
                    (symbol.upper(),)
                )
            else:
                cursor.execute(
                    "SELECT * FROM orders WHERE status IN ('NEW', 'PARTIALLY_FILLED') ORDER BY created_at ASC"
                )
            return [dict(r) for r in cursor.fetchall()]

    # ==========================================
    # Fills
    # ==========================================

    def record_fill(self, fill_data: Dict[str, Any]) -> bool:
        """
        Idempotently inserts a trade execution into the fills table.
        """
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO fills (
                    exchange_trade_id, exchange_order_id, local_order_id,
                    symbol, side, price, qty, quote_qty, commission,
                    commission_asset, trade_time, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(fill_data['exchange_trade_id']),
                str(fill_data.get('exchange_order_id', '')),
                str(fill_data.get('local_order_id', '')),
                fill_data.get('symbol', '').upper(),
                fill_data.get('side', '').upper(),
                float(fill_data.get('price', 0.0)),
                float(fill_data.get('qty', 0.0)),
                float(fill_data.get('quote_qty', 0.0)),
                float(fill_data.get('commission', 0.0)),
                str(fill_data.get('commission_asset', '')).upper(),
                int(fill_data.get('trade_time', now)),
                now
            ))
            conn.commit()
            return cursor.rowcount > 0

    def get_recent_fills(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns recent fills ordered by trade_time DESC."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if symbol:
                cursor.execute(
                    "SELECT * FROM fills WHERE symbol = ? ORDER BY trade_time DESC LIMIT ?",
                    (symbol.upper(), limit)
                )
            else:
                cursor.execute(
                    "SELECT * FROM fills ORDER BY trade_time DESC LIMIT ?",
                    (limit,)
                )
            return [dict(r) for r in cursor.fetchall()]

    def get_latest_trade_time(self, symbol: str) -> Optional[int]:
        """Returns the most recent trade_time recorded for a symbol."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(trade_time) as max_time FROM fills WHERE symbol = ?", (symbol.upper(),))
            row = cursor.fetchone()
            return row['max_time'] if row and row['max_time'] is not None else None

    # ==========================================
    # External Commands (Multi-Writer Queue)
    # ==========================================

    def submit_command(
        self,
        sender: str,
        command_type: str,
        payload: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Submits a command into the commands queue (called by external apps, CLI, or UI).
        """
        now = int(time.time())
        cmd_id = f"cmd_{uuid.uuid4().hex[:12]}"
        payload_str = json.dumps(payload or {})
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO commands (
                    command_id, sender, command_type, payload_json, status, created_at
                )
                VALUES (?, ?, ?, ?, 'PENDING', ?)
            """, (cmd_id, sender, command_type.upper(), payload_str, now))
            conn.commit()
        return cmd_id

    def get_pending_commands(self) -> List[Dict[str, Any]]:
        """Returns all unhandled commands in PENDING state."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM commands WHERE status = 'PENDING' ORDER BY created_at ASC")
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if d.get('payload_json'):
                    try:
                        d['payload'] = json.loads(d['payload_json'])
                    except Exception:
                        d['payload'] = {}
                results.append(d)
            return results

    def update_command_status(
        self,
        command_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None
    ) -> None:
        """Updates the status and result of a command."""
        now = int(time.time())
        result_str = json.dumps(result) if result is not None else None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE commands
                SET status = ?, result_json = ?, processed_at = ?
                WHERE command_id = ?
            """, (status.upper(), result_str, now, command_id))
            conn.commit()

    # ==========================================
    # Reconciliation Events & Snapshots
    # ==========================================

    def record_reconciliation_event(
        self,
        mismatch_type: str,
        resolution: str,
        symbol: Optional[str] = None,
        local_state: Optional[Dict[str, Any]] = None,
        exchange_state: Optional[Dict[str, Any]] = None
    ) -> None:
        """Records a reconciliation anomaly and healing action."""
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO reconciliation_events (
                    timestamp, symbol, mismatch_type, local_state,
                    exchange_state, resolution, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                now,
                symbol.upper() if symbol else None,
                mismatch_type,
                json.dumps(local_state) if local_state else None,
                json.dumps(exchange_state) if exchange_state else None,
                resolution,
                now
            ))
            conn.commit()

    def record_snapshot(
        self,
        total_equity_usd: float,
        balances: Dict[str, Any],
        open_orders: List[Dict[str, Any]]
    ) -> None:
        """Writes a periodic full account checkpoint."""
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO snapshots (
                    timestamp, total_equity_usd, balances_json,
                    open_orders_json, created_at
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                now,
                float(total_equity_usd),
                json.dumps(balances),
                json.dumps(open_orders),
                now
            ))
            conn.commit()

    # ==========================================
    # Structured Event Logging
    # ==========================================

    def log_event(
        self,
        level: str,
        component: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Records a system lifecycle or operational event in the events table."""
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO events (
                    timestamp, level, component, message, metadata_json
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                now,
                level.upper(),
                component.upper(),
                message,
                json.dumps(metadata) if metadata else None
            ))
            conn.commit()
