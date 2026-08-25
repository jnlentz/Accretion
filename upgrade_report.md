# Accretion v1 — Repository Upgrade & Migration Report

**Date:** August 24, 2026  
**Target System:** Accretion Live Runtime (v1)  
**Reference Document:** [ARCHITECTURE.md](file:///E:/Projects/Accretion/ARCHITECTURE.md), [GEMINI.md](file:///E:/Projects/Accretion/GEMINI.md)

---

## 1. Executive Summary & Response to Feedback

This report outlines the technical steps to transition the **Accretion** repository from legacy salvage code (Dash dashboard, old single-position 4-state machine, and isolated indicator/backtest scripts) to the **Accretion v1 Live Runtime**.

### Response to User Corrections & Design Clarifications

1. **Multi-Writer Database Architecture & External Command Channel:**
   - *Clarification:* SQLite will not be single-writer at the file level; the bot owns and writes to its runtime and bookkeeping tables, but external applications (such as an Android app, remote controller, or CLI) will write command intents into an incoming command queue table (`commands`).
   - *Architecture:* The ledger database operates in SQLite WAL mode (`journal_mode=WAL`). The bot continuously polls the `commands` table, executes requested actions (e.g., pause, resume, force-sell, update parameters), and updates the command execution status.

2. **Market Data Architecture & `BinanceData` Schema Compatibility:**
   - *Clarification:* Market data is decoupled into dedicated per-symbol, per-exchange database files matching the existing ecosystem format (e.g., `databases/binance_us/BTCUSDT.sqlite`), stored in tables named `klines_{timeframe}` (`1m`, `3m`, `15m`, `1h`, `4h`, `1d`).
   - *Integration:* The recently imported [`src/adapters/BinanceData.py`](file:///E:/Projects/Accretion/src/adapters/BinanceData.py) and [`historical_downloader_binance.py`](file:///E:/Projects/Accretion/historical_downloader_binance.py) define the authoritative schema and download mechanism. Import pathing in [`historical_downloader_binance.py`](file:///E:/Projects/Accretion/historical_downloader_binance.py) is updated to `from src.adapters.BinanceData import BinanceData`. Existing historical databases from the research project can be imported directly into `databases/` with zero schema friction.

3. **Preserving Full Configuration Structure (`config.py`):**
   - *Clarification:* The full dictionary schema in [`config.py`](file:///E:/Projects/Accretion/config.py) (including `MarketDataConfig`, `MarketDatabases`, `PolygonDatabases`, Kraken, Alpaca, and Polygon credentials) is retained intact.
   - *Alignment:* Accretion runtime modules will align with and consume this established config format rather than reshaping it, ensuring forward compatibility as the system expands across assets and venues.

4. **High-Density Simultaneous Limit Order Management:**
   - *Clarification:* Accretion's live policy will manage dozens of concurrent resting limit buy and sell orders (inventory lots).
   - *Design:* The exchange adapter and reconciliation engine are designed from the ground up for multi-order tracking, non-blocking batch order status checks, and resilient order mapping.

---

## 2. Current Codebase Audit & Gap Analysis

```
Accretion/
├── ARCHITECTURE.md                 # Architectural constraints and design intent
├── GEMINI.md                       # Project rules and scope boundaries
├── README.md                       # Documentation
├── config.py                       # Master configuration (credentials, paths, mappings)
├── config.example.py               # Master configuration template
├── historical_downloader_binance.py# Historical kline downloader CLI
├── main_backtest.py                # Legacy backtesting entrypoint
├── main_live.py                    # Legacy live runner running the 4-state machine
├── requirements.txt                # Python dependencies
├── src/
│   ├── adapters/
│   │   ├── BinanceData.py          # Authoritative Binance async historical kline updater
│   │   └── binance_client.py       # BinanceSpotAdapter (spot execution & account adapter)
│   ├── backtest/
│   │   ├── engine.py               # Legacy limit order backtester
│   │   └── optimizer.py            # Legacy grid search optimizer
│   ├── data/
│   │   ├── pipeline.py             # Legacy pipeline script
│   │   └── storage.py              # Generic SQLAlchemy DataFrame storage
│   ├── execution/
│   │   ├── runner.py               # DualLoopRunner (basic timer loop)
│   │   └── state_machine.py        # Legacy 4-state machine
│   ├── features/                   # Legacy indicators (RSI, EMA stacks, LRA)
│   │   ├── momentum.py
│   │   ├── regime.py
│   │   ├── regression.py
│   │   └── volume.py
│   ├── ui/
│   │   └── dashboard.py            # Legacy Plotly Dash dashboard
│   └── utils/
│       └── precision.py            # Float truncation utility
└── tests/
    ├── test_features.py            # Legacy tests
    └── test_state_machine.py       # Legacy tests
```

### Subsystem Audit & Actions

| Subsystem | Current State in Repo | Target State for Accretion v1 | Action Required |
|---|---|---|---|
| **Market Downloader & Ingest** (`historical_downloader_binance.py`, `src/adapters/BinanceData.py`) | Recently imported `BinanceData` class and downloader script with outdated import paths (`from classes.BinanceData import ...`). | Standardized market ingest module using `klines_{tf}` tables in `databases/<exchange>/<symbol>.sqlite`. | **Update import paths**, integrate with runtime market loop for incremental bar ingestion, and import existing market DBs. |
| **Exchange Adapter** (`src/adapters/binance_client.py`) | Mixed adapter with single-position sizing (`get_balance` all-in logic) and single trade queries. | Stateless, robust, multi-symbol adapter managing concurrent limit orders, bulk balances, order queries, and fills. | **Refactor.** Strip strategy sizing; add bulk query primitives, precision formatting, and rate-limit backoff. |
| **Persistence / Ledger** (`src/ledger/`) | Currently only generic DataFrame dumping in `src/data/storage.py`. | Dedicated SQLite Ledger (`state/accretion_ledger.sqlite`) with tables for balances, orders, fills, reconciliation, snapshots, events, and external commands. | **Implement.** Create `database.py` and `schema.py` with WAL mode and atomic transaction management. |
| **Reconciliation Engine** (`src/ledger/reconciliation.py`) | Embedded inside legacy `state_machine.py` with hardcoded single-order assumptions. | Dedicated reconciliation module synchronizing exchange state vs local ledger every 1m poll and on startup. | **Implement.** Build multi-order mismatch detection, fill discovery, and balance drift reconciliation. |
| **Runtime Loops** (`src/runtime/`, `main_live.py`) | Basic timer loop in `src/execution/runner.py` tied to legacy state machine. | Dual-interval engine: Market Loop (bar close @ 3m) + Account Loop (~1m bookkeeping, reconciliation, command processing). | **Refactor.** Build clean async/threaded runtime coordinator with graceful OS signal handling. |
| **Legacy UI & Features** (`src/ui/`, `src/features/`, `src/backtest/`, `main_backtest.py`) | Dash app, indicator calculations, backtester, parameter sweepers. | Research features reside in Singularity; live runtime remains focused on execution and state truth. | **Decommission & Remove.** Remove `src/ui/`, `src/features/`, `src/backtest/`, `main_backtest.py`. |
| **Dependencies** (`requirements.txt`) | Contains UI and research packages (`dash`, `plotly`, `scikit-learn`, `pymysql`, `matplotlib`). | Minimal production runtime dependencies. | **Prune.** Retain `python-binance`, `pandas`, `numpy`, `sqlalchemy`, `tqdm`, `pytest`. |

---

## 3. Database Architecture & Schema Specifications

The storage architecture is organized into two distinct layers:
1. **Market Data Databases:** Per-symbol databases (`databases/<exchange>/<symbol>.sqlite`) containing historical and incremental klines.
2. **Account & Runtime Ledger Database:** Local operational ledger (`state/accretion_ledger.sqlite`) tracking balances, orders, fills, events, and external commands.

```
Accretion/
├── databases/
│   ├── binance_us/
│   │   ├── BTCUSDT.sqlite        # Contains klines_1m, klines_3m, klines_15m, klines_1h, etc.
│   │   └── ETHUSDT.sqlite
│   └── binance_com/
│       └── BTCUSDT_com.sqlite
└── state/
    └── accretion_ledger.sqlite   # Balances, Orders, Fills, Reconciliation, Commands, Snapshots
```

### Layer A: Market Data Schema (`databases/<exchange>/<symbol>.sqlite`)

Matches the exact schema defined in [`src/adapters/BinanceData.py`](file:///E:/Projects/Accretion/src/adapters/BinanceData.py):

```sql
-- Generated per timeframe: klines_1m, klines_3m, klines_15m, klines_1h, klines_4h, klines_1d
CREATE TABLE IF NOT EXISTS klines_3m (
    time INTEGER PRIMARY KEY,        -- Unix epoch timestamp in seconds
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    trade_count REAL NOT NULL        -- Number of trades executed during the bar
);
CREATE INDEX IF NOT EXISTS idx_klines_3m_time ON klines_3m(time);
```

### Layer B: Account & Runtime Ledger Schema (`state/accretion_ledger.sqlite`)

```sql
-- 1. Asset Balances
CREATE TABLE IF NOT EXISTS balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL UNIQUE,
    free REAL NOT NULL,
    locked REAL NOT NULL,
    total REAL NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 2. Orders (Tracking multiple simultaneous limit orders)
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
    client_tag TEXT,                 -- Lot identifier / policy tag (for future lot queue)
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_symbol_status ON orders(symbol, status);
CREATE INDEX IF NOT EXISTS idx_orders_exchange_id ON orders(exchange_order_id);

-- 3. Fills (Execution trade records)
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
    payload_json TEXT,               -- Arbitrary JSON parameters for the command
    status TEXT NOT NULL,            -- 'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'REJECTED'
    result_json TEXT,                -- Output or error details populated by the bot
    created_at INTEGER NOT NULL,
    processed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status);

-- 5. Reconciliation Events
CREATE TABLE IF NOT EXISTS reconciliation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    symbol TEXT,
    mismatch_type TEXT NOT NULL,      -- 'UNTRACKED_OPEN_ORDER', 'STATUS_DESYNC', 'BALANCE_DRIFT', 'MISSING_FILL'
    local_state TEXT,
    exchange_state TEXT,
    resolution TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

-- 6. Account Snapshots (Periodic full checkpoint)
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    total_equity_usd REAL,
    balances_json TEXT NOT NULL,
    open_orders_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

-- 7. System Events & Structured Logs
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    level TEXT NOT NULL,             -- 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    component TEXT NOT NULL,         -- 'ADAPTER', 'LEDGER', 'MARKET_LOOP', 'ACCOUNT_LOOP', 'COMMAND_PROCESSOR'
    message TEXT NOT NULL,
    metadata_json TEXT
);
```

---

## 4. Target Project Layout

```
Accretion/
├── ARCHITECTURE.md
├── GEMINI.md
├── README.md
├── .gitignore
├── config.py                       # Master configuration
├── config.example.py               # Master configuration template
├── historical_downloader_binance.py# CLI for batch/historical data downloads
├── main_live.py                    # Accretion v1 live entrypoint
├── requirements.txt                # Cleaned runtime dependencies
├── databases/                      # Market data databases (per-exchange / per-symbol)
│   ├── binance_us/
│   │   └── BTCUSDT.sqlite
│   └── binance_com/
├── state/                          # Ledger & runtime state databases
│   └── accretion_ledger.sqlite
├── src/
│   ├── __init__.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── BinanceData.py          # Kline synchronization engine
│   │   └── binance_client.py       # REST spot order & account client
│   ├── ledger/
│   │   ├── __init__.py
│   │   ├── database.py             # SQLite WAL connection manager & transaction helpers
│   │   ├── schema.py               # Ledger schema initialization & migrations
│   │   └── reconciliation.py       # Exchange-to-ledger state reconciliation engine
│   ├── runtime/
│   │   ├── __init__.py
│   │   ├── loop_runner.py          # Asynchronous / threaded dual-loop coordinator
│   │   ├── market_poller.py        # Bar-close market data poller (3m)
│   │   ├── account_poller.py       # 1m account poller & reconciliation runner
│   │   └── command_processor.py    # Multi-writer command queue processor
│   └── utils/
│       ├── __init__.py
│       ├── logger.py               # Structured logger (console, file, SQLite)
│       └── precision.py            # Precision truncation & lot/price rounding
└── tests/
    ├── __init__.py
    ├── test_adapter.py             # Unit tests for exchange adapter
    ├── test_ledger.py              # Unit tests for SQLite ledger CRUD & WAL concurrency
    ├── test_reconciliation.py      # Unit tests for reconciliation & state healing
    └── test_commands.py            # Unit tests for external command handling
```

---

## 5. Step-by-Step Implementation Roadmap

### Phase 1: Environment Cleanup & Downloader Path Alignment
1. **Fix Downloader Imports:**
   - In [`historical_downloader_binance.py`](file:///E:/Projects/Accretion/historical_downloader_binance.py), update import line:
     ```python
     from src.adapters.BinanceData import BinanceData
     ```
2. **Decommission Legacy Research & UI:**
   - Remove `src/ui/` (`dashboard.py`).
   - Remove `src/features/` (`momentum.py`, `regime.py`, `regression.py`, `volume.py`).
   - Remove `src/backtest/` (`engine.py`, `optimizer.py`) and `main_backtest.py`.
   - Remove legacy test scripts (`tests/test_features.py`, `tests/test_state_machine.py`).
3. **Clean Dependencies & Security:**
   - Update [`requirements.txt`](file:///E:/Projects/Accretion/requirements.txt):
     ```text
     python-binance>=1.0.15
     pandas>=1.3.0
     numpy>=1.21.0
     tqdm>=4.64.0
     pytest>=7.0.0
     ```
   - Establish `.gitignore` to safeguard API keys, databases (`*.sqlite`, `*.db`), and cache files.

### Phase 2: Configuration Alignment
1. Preserve the full schema of [`config.py`](file:///E:/Projects/Accretion/config.py) (`MarketDataConfig`, `MarketDatabases`, `PolygonDatabases`, exchange keys).
2. Ensure runtime defaults in `config.py` point to:
   - Primary live symbol: `BTCUSDT` (via `binance_us`).
   - Ledger database path: `os.path.join(STATE_DIR, 'accretion_ledger.sqlite')`.
   - Polling intervals: Market Loop = 180s (3m), Account Loop = 60s (1m).
3. Update [`config.example.py`](file:///E:/Projects/Accretion/config.example.py) to mirror the full master configuration layout with placeholder keys.

### Phase 3: Exchange Adapter Refactoring (`src/adapters/binance_client.py`)
1. **Remove Single-Position Strategy Logic:**
   - Remove `calculate_buy_qty`, discount factor calculations, and whole-balance buy/sell methods.
2. **Implement Core Execution & Query Primitives:**
   - `get_account_balances() -> Dict[str, Dict[str, float]]`: returns `{asset: {'free': float, 'locked': float, 'total': float}}`.
   - `get_open_orders(symbol: Optional[str] = None) -> List[Dict[str, Any]]`: fetches all open orders across configured symbols.
   - `get_order_status(symbol: str, order_id: str) -> Dict[str, Any]`: queries status for a specific exchange order.
   - `get_recent_fills(symbol: str, limit: int = 100, from_id: Optional[int] = None) -> List[Dict[str, Any]]`: fetches trade executions.
   - `place_limit_order(symbol: str, side: str, price: float, quantity: float, client_order_id: Optional[str] = None) -> Dict[str, Any]`: places a GTC limit order with strict symbol precision truncation.
   - `cancel_order(symbol: str, order_id: str) -> bool`: cancels an order by ID.
   - `get_symbol_filters(symbol: str) -> Dict[str, Any]`: returns tick size, step size, min notional, and precision rules.
3. **Rate Limit & Network Resilience:**
   - Implement exponential backoff on HTTP 429/5xx responses and connection timeouts.

### Phase 4: SQLite Ledger Implementation (`src/ledger/`)
1. **Schema Definition (`src/ledger/schema.py`):**
   - Implement DDL for all ledger tables (`balances`, `orders`, `fills`, `commands`, `reconciliation_events`, `snapshots`, `events`).
2. **Database Access Layer (`src/ledger/database.py`):**
   - Initialize SQLite connection with WAL mode (`PRAGMA journal_mode=WAL;`) and busy timeout (`PRAGMA busy_timeout=5000;`) to support concurrent reads and external command writes.
   - Transactional persistence methods:
     - `upsert_balances(balances: List[Dict[str, Any]])`
     - `record_order(order_data: Dict[str, Any])`
     - `update_order(local_or_exchange_id: str, updates: Dict[str, Any])`
     - `record_fill(fill_data: Dict[str, Any])`
     - `record_reconciliation_event(event_data: Dict[str, Any])`
     - `record_snapshot(snapshot_data: Dict[str, Any])`
     - `get_pending_commands() -> List[Dict[str, Any]]`
     - `update_command_status(command_id: str, status: str, result: Optional[Dict] = None)`
     - `log_event(level: str, component: str, message: str, metadata: Optional[Dict] = None)`

### Phase 5: Reconciliation Engine (`src/ledger/reconciliation.py`)
1. **Reconciliation Cycle Logic (~1m interval & startup):**
   - **Open Order Verification:**
     - Query local DB for orders marked `NEW` or `PARTIALLY_FILLED`.
     - Query Binance API for active open orders.
     - Detect orders filled, canceled, or expired out-of-band; update local ledger.
   - **Trade / Fill Discovery:**
     - Fetch trade fills since the last recorded fill timestamp.
     - Match fills to known local orders or record untracked fills; update cumulative executed quantities.
   - **Balance Drift Check:**
     - Compare exchange free/locked balances against local records; log drift events and synchronize balances.
   - **Startup Healing:**
     - Perform a comprehensive reconciliation run before the bot enters its main execution loop.

### Phase 6: Dual Runtime Engine & Command Processor (`src/runtime/` & `main_live.py`)
1. **Market Poller (`src/runtime/market_poller.py`):**
   - Triggers on bar close (e.g. 3m).
   - Ingests latest closed candle for configured symbols into `databases/<exchange>/<symbol>.sqlite` using `BinanceData` routines.
   - Zero trading decisions in v1.
2. **Account Poller (`src/runtime/account_poller.py`):**
   - Triggers every ~60 seconds.
   - Calls adapter for balances, open orders, and recent fills; invokes `ReconciliationEngine`.
   - Generates periodic account snapshots in `snapshots` table.
3. **Command Processor (`src/runtime/command_processor.py`):**
   - Evaluates incoming commands from the `commands` table (e.g., `PAUSE`, `RESUME`, `CANCEL_ORDER`, `FORCE_SELL`).
   - Executes authorized operational tasks and updates command state to `COMPLETED` or `FAILED`.
4. **Runtime Coordinator (`src/runtime/loop_runner.py`):**
   - Coordinates the two polling loops and command processor.
   - Handles OS termination signals (`SIGINT`, `SIGTERM`) for graceful teardown without database corruption.
5. **Entrypoint (`main_live.py`):**
   - Initializes configuration, initializes market and ledger databases, connects adapter, runs startup reconciliation, and begins monitoring loops.

### Phase 7: Test Suite & Dry-Run Protocol
1. **Automated Unit Tests (`tests/`):**
   - `test_adapter.py`: Mocked Binance API order placement, cancellations, balance queries, and error handling.
   - `test_ledger.py`: Multi-threaded WAL database writes, balance updates, order updates, and command polling.
   - `test_reconciliation.py`: Simulating out-of-band fills, external order cancellations, and balance drift reconciliation.
   - `test_commands.py`: External command queue ingestion and execution.
   - `test_precision.py`: Precision truncation against Binance `LOT_SIZE` and `PRICE_FILTER` rules.
2. **Dry-Run Monitoring Protocol:**
   - Execute the bot in live read-only monitoring mode for 48–72 hours.
   - Verify continuous 3m candle ingestion into `databases/binance_us/BTCUSDT.sqlite`.
   - Verify periodic 1m reconciliation cycles and snapshot creation in `state/accretion_ledger.sqlite` with zero database lock errors.

---

## 6. Execution Checklist

- [x] **Step 1:** Update import path in [`historical_downloader_binance.py`](file:///E:/Projects/Accretion/historical_downloader_binance.py).
- [x] **Step 2:** Decommission legacy research modules (`src/ui/`, `src/features/`, `src/backtest/`, `main_backtest.py`).
- [x] **Step 3:** Prune [`requirements.txt`](file:///E:/Projects/Accretion/requirements.txt) and establish `.gitignore`.
- [x] **Step 4:** Synchronize [`config.example.py`](file:///E:/Projects/Accretion/config.example.py) with master [`config.py`](file:///E:/Projects/Accretion/config.py).
- [x] **Step 5:** Refactor [`src/adapters/binance_client.py`](file:///E:/Projects/Accretion/src/adapters/binance_client.py) for multi-order spot execution and querying.
- [x] **Step 6:** Implement SQLite Ledger in `src/ledger/` (`schema.py`, `database.py`).
- [x] **Step 7:** Implement `src/ledger/reconciliation.py`.
- [x] **Step 8:** Implement `src/runtime/` (`market_poller.py`, `account_poller.py`, `command_processor.py`, `loop_runner.py`) and wire [`main_live.py`](file:///E:/Projects/Accretion/main_live.py).
- [x] **Step 9:** Implement unit test suite in `tests/` and verify with pytest / unittest.
