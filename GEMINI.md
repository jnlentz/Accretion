# Project Accretion: Context & Rules

## Project Overview

Accretion is the **live** execution environment. Singularity is research. Do not mix them.

This bot describes present market state from hierarchical rolling ranges and acts on that state. It does not forecast price. It does not use RSI, MACD, or other off-the-shelf indicators.

**Current job:** Build and harden the live system around `TradeEngine` — ingest completed candles, maintain wave state, emit order actions, persist everything, route to the exchange.

---

## Hard Rules (Strict Adherence Required)

### 1. Visualization & Plotting
* **Dark Mode Only:** `plt.style.use('dark_background')`.
* **Display Method:** `plt.show()` by default. Never `savefig()` unless explicitly requested.

### 2. Code Execution & Git
* **Write-Only Mode:** The AI writes code and answers questions. It does not execute locally, install packages, or touch git history.
* Stick to the requested file and scope.

### 3. Workflow Constraints
* **Comment-Driven Development:** Follow instructions in code comments exactly.
* **No Hallucinated Features:** Do not import libraries or add logic that was not asked for.
* **Causal data only:** Rolling windows must exclude the forming bar. No look-ahead.
* **Descriptive, not predictive:** Features and logs answer “what object are we inside?” — not “what happens next?”

### 4. Live-system constraints
* Do not put Pandas in the hot path. TradeEngine uses Python + NumPy (circular buffers).
* Do not place orders from inside feature math. Engine emits `TradeAction`; an adapter talks to the exchange.
* Do not silently reset state on restart. Persist and restore.
* Do not add MarketMapper / Singularity research scripts into this repo unless asked.

---

## Technical Context

* **Language:** Python
* **Live stack:** NumPy in the engine; SQLite for ledger/state; exchange adapter (Binance.US first; multi-symbol ready)
* **Research stack (if plotting/sim):** pandas, matplotlib dark mode
* **Clocks:** market bars on the strategy timeframe (1h); account/order reconciliation more often (e.g. 1m)
* **Orders:** prefer maker/limit where the policy says limit; map actions 1:1 to exchange calls
* **Quote:** BTC/USD preferred on Binance.US unless told otherwise (cleaner tax lots than USDT)

---

## Strategy (implement only what is asked)

Native bar: **1h**. Rolling windows exclude the current bar.

**Containers**
* Daily: trailing 24h high/low
* Weekly: trailing 168h high/low
* Micro-floor: trailing 6h high/low (immediate invalidation aid)

**Macro states**
* `ACTIVE_BULL` — daily pushing new weekly high
* `BULL_EXHAUSTED` — 24h floor broken; up-wave stalled
* `ACTIVE_BEAR` — daily pushing new weekly low
* `BEAR_EXHAUSTED` — 24h ceiling broken; down-wave stalled

**Micro states:** same pattern of push/stall of 1h vs 24h.

**Intended action shape (do not invent extra modes)**
* Resting limit bid in `BEAR_EXHAUSTED` after a micro bottom sequence; cancel and market-buy if macro goes `ACTIVE_BULL` unfilled
* Market buy on direct macro transition to `ACTIVE_BULL`
* Conditional reload in `BULL_EXHAUSTED` only when micro prints bear-exhaustion bounce
* Harvest: decaying/halving profit targets on reloads in the same wave, down to a floor
* Structural stop: hold through bull-exhausted pullbacks; flatten if macro becomes `ACTIVE_BEAR`
* Never buy `ACTIVE_BEAR`. Default no-buy in `BULL_EXHAUSTED` except the explicit reload rule.

If a prompt conflicts with this shape, follow the prompt and do not silently “improve” the policy.

---

## TradeEngine

Self-contained event-driven class.

* `on_candle(ohlcv)` — called on **closed** 1h bars only
* Updates buffers → evaluates state → checks fills / policy → returns `TradeAction`s
* Action types stay explicit: `PLACE_RESTING_LIMIT_BUY`, `CANCEL_RESTING_LIMIT_BUY`, `EXECUTE_MARKET_BUY`, `EXECUTE_MARKET_SELL` (extend only when asked)
* Startup: warm with 168 closed 1h candles before any live action
* Persist engine dict + open orders + fills to SQLite (or JSON if that is what the repo already uses)

---

## Persistence & Safety

* Bot is the only writer of the ledger
* Record: every action intent, exchange ack, fill, cancel, balance snapshot, state snapshot
* Idempotent order placement (no duplicate live orders after restart)
* Fail closed: if state/buffers are incomplete, do not trade

---

## Design Intent (background)

Accretion vs Singularity stay separate. Research proved the range-push / physical-invalidation objects. Live code must reproduce those objects causally, then execute inventory policy against them. Complexity belongs in state + policy, not in extra indicators.