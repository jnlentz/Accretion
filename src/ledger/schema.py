"""
Relational Schema DDL & Table Initializer for Accretion SQLite Ledger
"""

LEDGER_SCHEMA_SQL = """
-- 1. Asset Balances
CREATE TABLE IF NOT EXISTS balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL UNIQUE,
    free REAL NOT NULL,
    locked REAL NOT NULL,
    total REAL NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_balances_asset ON balances(asset);

-- 2. Orders (Multi-order limit inventory tracking)
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_order_id TEXT UNIQUE NOT NULL,
    exchange_order_id TEXT UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,              -- 'BUY', 'SELL'
    order_type TEXT NOT NULL,        -- 'LIMIT', 'MARKET'
    price REAL NOT NULL,
    orig_qty REAL NOT NULL,
    executed_qty REAL NOT NULL DEFAULT 0.0,
    cummulative_quote_qty REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL,            -- 'NEW', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'REJECTED', 'EXPIRED'
    time_in_force TEXT NOT NULL,     -- 'GTC', 'IOC', 'FOK'
    client_tag TEXT,                 -- Lot identifier / policy tag
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_symbol_status ON orders(symbol, status);
CREATE INDEX IF NOT EXISTS idx_orders_exchange_id ON orders(exchange_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_local_id ON orders(local_order_id);

-- 3. Fills (Trade execution records)
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_trade_id TEXT UNIQUE NOT NULL,
    exchange_order_id TEXT,
    local_order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    quote_qty REAL NOT NULL,
    commission REAL NOT NULL DEFAULT 0.0,
    commission_asset TEXT,
    trade_time INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fills_symbol_time ON fills(symbol, trade_time);
CREATE INDEX IF NOT EXISTS idx_fills_order_id ON fills(exchange_order_id);

-- 4. External Commands & Control Queue (Multi-Writer Channel)
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT UNIQUE NOT NULL,
    sender TEXT NOT NULL,            -- 'CLI', 'MOBILE_APP', 'WEB_PANEL', 'MANUAL'
    command_type TEXT NOT NULL,      -- 'PAUSE', 'RESUME', 'CANCEL_ORDER', 'FORCE_SELL', 'SET_PARAM'
    payload_json TEXT,               -- Arbitrary JSON parameters
    status TEXT NOT NULL,            -- 'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'REJECTED'
    result_json TEXT,                -- Output or error details
    created_at INTEGER NOT NULL,
    processed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status);

-- 5. Reconciliation Events (State mismatches & resolution)
CREATE TABLE IF NOT EXISTS reconciliation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    symbol TEXT,
    mismatch_type TEXT NOT NULL,      -- 'UNTRACKED_OPEN_ORDER', 'STATUS_DESYNC', 'BALANCE_DRIFT', 'MISSING_FILL'
    local_state TEXT,                -- JSON snapshot of local expectation
    exchange_state TEXT,             -- JSON snapshot of exchange response
    resolution TEXT NOT NULL,        -- Action taken to heal state
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reconciliation_timestamp ON reconciliation_events(timestamp);

-- 6. Account Snapshots (Periodic checkpoint)
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    total_equity_usd REAL,
    balances_json TEXT NOT NULL,
    open_orders_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp);

-- 7. System Events & Structured Logs
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    level TEXT NOT NULL,             -- 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    component TEXT NOT NULL,         -- 'ADAPTER', 'LEDGER', 'MARKET_LOOP', 'ACCOUNT_LOOP', 'COMMANDS'
    message TEXT NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_timestamp_level ON events(timestamp, level);
"""

def init_ledger_schema(conn) -> None:
    """
    Executes the SQLite DDL statements to ensure all ledger tables exist.
    """
    cursor = conn.cursor()
    cursor.executescript(LEDGER_SCHEMA_SQL)
    conn.commit()
