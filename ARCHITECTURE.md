# Accretion — Context Transfer

This document captures decisions and intent from the research conversation so the live project does not drift back into an old Dash strategy app or a predictive bot.

## Relationship to research

Two codebases, two jobs:

| Project | Role |
|---|---|
| Singularity / research folders | World model and strategy experiments (MarketMapper, SDI, parent-range levels, path analysis, DCA simulations) |
| **Accretion** | Live runtime: exchange I/O, account truth, persistence, later execution of an agreed policy |

MarketMapper is **not** in Accretion for now. It is still changing. Accretion v1 only polls data and keeps account state current. Mapper + policy attach later as a separate layer.

## What was learned that affects the live bot (later)

These are constraints on *future* policy, not v1 features:

1. Features are **descriptive state**, not forecasts. The bot should answer “where are we in the structure?” not “what happens next?”
2. Full position exits on “overbought” structural levels **reduced** profits vs holding / vanilla DCA on the 2022–2025 BTC window. Do not build a regime-switch dump-the-book engine.
3. Intended live policy (when research is ready):
   - Ongoing accumulation in **small lots** at many prices
   - **Limit sells only at a profit** above each lot’s cost
   - Never sell a lot at a loss; inventory can sit
   - Free cash split into a buy queue when a buy phase starts
   - Min wait between buys (e.g. ~1 hour), with possible exception when price is “especially low”
   - If a sell fills while buys are still queued, new cash is divided into remaining queued lots
   - No-buy zones (structure / RPI / levels) can veto new buys without touching resting sells
4. Limit / maker orders on BinanceUS are treated as effectively free. Still persist fee fields.
5. Contribution schedule ($/day etc.) is **undecided**. Do not hardcode one.

## v1 scope

**Build / keep**
- Config-driven exchange + symbol list (already exists)
- Market data poll on the working timeframe (3m first)
- Account poll (~1m): balances, open orders, recent fills
- Reconcile exchange vs local SQLite ledger
- Existing order methods: place, cancel, status — review and wire, don’t rewrite from scratch
- Process architecture that can host multiple symbols later (one “book” per symbol)

**Do not build in v1**
- MarketMapper / SDI / levels / RPI
- Lot queue, buy-phase, no-buy rules, contribution clock
- Dash UI or any control panel
- Android app (will read the same DB later)

## Runtime

Two clocks in one process (threads or equivalent):

- **Market loop** — on bar close: fetch/store klines. No trading decisions in v1.
- **Account loop** — ~1m: balances, orders, fills; write ledger; flag desync.

Account loop is bookkeeping only.

## Persistence

SQLite is the local source of truth the bot writes and that future UIs will read.

Minimum tables (names can vary, intent cannot):

- `bars` or equivalent market snapshots
- `balances`
- `orders` (place / cancel / status / exchange id)
- `fills`
- `reconciliation_events` (mismatches)
- `snapshots` (periodic full state)
- `log` / `events` (bot lifecycle)

Later (not v1): `lots`, `cash_events`, `decisions`, `commands`.

Bot = only writer. Frontends later send intents (pause, change config) through a command channel, not by sharing memory with Dash.

## Salvage policy

This repo already contains useful code recovered from a pre-LLM research folder plus an old strategy runner on Dash.

- Keep: config, exchange adapters, order helpers, data plumbing that is correct.
- Remove or isolate: Dash app, old strategy loop, anything that assumes the previous test strategy.
- New shape: clean bot runtime, not a dashboard with a strategy bolted on.

## Multi-asset

v1 runs one symbol (BTC pair from config) but types should be `symbol`-keyed. Adding ETH later should be config + a new book, not a rewrite.

## Safety (even in v1)

- Persist before assuming an order exists.
- Reconcile; do not treat the last local guess as truth if the exchange disagrees.
- No market dumps. Order primitives should favor limits.
- Kill/pause path: stop placing new orders; do not invent flatten-everything logic.

## Suggested next implementation order

1. Confirm exchange methods (place / cancel / fetch open orders / fetch balances / fetch fills) against current BinanceUS behavior.
2. SQLite schema + write path.
3. Account loop + reconciliation.
4. Market loop storing 3m bars.
5. Dry-run / paper logging for a few days with **no** strategy.
6. Only then attach policy + mapper from research.