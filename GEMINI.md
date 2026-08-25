# Accretion — Context & Rules

## Project Overview

Accretion is a live inventory trading bot. It is **not** the research lab.

- Research (MarketMapper, SDI, structural levels, strategy experiments) lives in Singularity / related research folders.
- Accretion is the deployed runtime: poll market data, keep exchange account state honest, persist every action, and (later) execute the lot-based DCA + limit-sell policy.

**Current phase (v1):** data + account + ledger + order primitives.  
Do **not** add MarketMapper, TradeEngine, strategy logic, contribution schedules, or a UI.

---

## Current Scope (strict)

**In scope now**
- Poll market data on the bar interval (3m for BTC first).
- Poll account state on a faster interval (~1m): balances, open orders, fills.
- Reconcile exchange state with a local SQLite ledger.
- Order methods: place, cancel, query status (already exist — review, do not rewrite blindly).
- Multi-symbol ready: do not hardcode a single coin into core types. Config already owns exchange/symbol lists.
- Logging and crash-safe persistence.

**Out of scope now**
- MarketMapper, SDI, parent/child levels, RPI.
- Buy-phase / no-buy / lot-queue strategy.
- Contribution schedule ($X/day etc.).
- Dash or any other UI.
- Android / remote control (later consumers of the same SQLite DB).

When research is ready, mapper + policy will be added as separate layers. Do not anticipate them in v1 code.

---

## Hard Rules

### 1. Architecture
- Replace any leftover Dash / old-strategy runner. Do not extend them.
- Keep layers separate:
  1. Exchange adapter
  2. Ledger (SQLite is source of truth locally)
  3. Runtime loops (market poll vs account poll)
  4. Policy (does not exist yet — do not invent it)
- Account loop is bookkeeping only. No trading decisions on the 1m poll.
- Market loop runs on bar close. No strategy in v1 beyond “store the bar.”

### 2. Persistence
- SQLite for everything that must survive a restart.
- Record: bars (or references), balances, order placements, cancels, fills, rejects, reconciliation mismatches.
- The bot is the only writer. Future UIs only read (and later submit commands).

### 3. Code discipline
- Follow comments and the user’s explicit request. Do not expand scope.
- Do not invent features, indicators, or strategy rules.
- Prefer reviewing and wiring existing order methods over rewriting them.
- Multi-asset: symbol-specific state lives behind a per-symbol book/config, not scattered `if symbol == 'BTC'` checks.

### 4. Visualization
- No Dash. If a debug plot is requested, dark mode + `plt.show()` only, and only when asked.

### 5. Execution
- Write-only advisor unless the user explicitly asks to run something.
- Do not touch git history or install packages unless asked.

---

## Technical Context

- Language: Python
- Exchange: already configured (BinanceUS / symbols in existing config)
- First live symbol: BTC pair from config
- Intervals: market ~3m, account ~1m
- DB: SQLite
- Limit orders are the intended live order type later (maker / low or zero fee on BinanceUS). v1 only needs the methods in place.

---

## Design Intent (do not implement yet — remember it)

Later Accretion will run a long-biased lot inventory system:
- Split free cash into lots, buy on a schedule with min wait
- Place a limit sell above each lot’s cost (never sell at a loss)
- Recycle fill proceeds into remaining queued lots
- Use research features (structure / levels / RPI) as no-buy and “especially low” gates

v1 exists so that pipeline has a reliable live backbone before any of that is attached.