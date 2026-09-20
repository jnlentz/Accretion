# Accretion Live Strategy Specification: 24/7 Crypto Intraday Engine

**Project:** SingularityCore $\to$ Accretion Live Bridge  
**Author:** Jesse Lentz & Singularity Core Lab  
**Date:** September 2026  
**Status:** Production Deployment Blueprint for Autonomous Agents & Live Execution  
**Target Environment:** Accretion Live Trading System  

---

## 1. Executive Summary & Strategic Architecture

This document serves as the **authoritative reference manual** for the autonomous agents, software engineers, and execution pipelines in the **Accretion** live project. 

The strategy deployed here is the direct production realization of our 24/7 continuous crypto research in SingularityCore. It ports our structural breakthroughs from equity markets to continuous cryptocurrency markets, unlocking superior market physics:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                         CROSS-EXCHANGE HYBRID ARCHITECTURE                                       │
├─────────────────────────────────────┬────────────────────────────────────────────────────────────┤
│     DATA INGESTION (KRAKEN)         │               ORDER EXECUTION (BINANCE.US)                 │
├─────────────────────────────────────┼────────────────────────────────────────────────────────────┤
│ • Canonical trade-level data feed   │ • Native 0.0% Maker Fee tier ($0.00 fee drag)              │
│ • Unmanipulated institutional volume│ • Ultra-low 1.9 bps (0.019%) taker market fee              │
│ • Deep continuous history back to   │ • High-speed REST & WebSocket order routing                │
│   2013 (BTC) & 2015 (ETH)           │ • Passive resting maker limit buy discounts (-0.50%)       │
│ • Identical distribution to models  │ • Passive resting maker limit take-profit (+x* + 0.15%)    │
└─────────────────────────────────────┴────────────────────────────────────────────────────────────┘
```

### The Three Core Structural Breakthroughs:
1. **Zero Forced Session Exits (Pure 24/7 Physics):** Unlike equities where positions must be dumped before 16:00 ET to avoid catastrophic overnight gaps, crypto trades continuously. Forward Triple Barrier targets are strictly bounded $N$ hours forward ($H = 16$ bars = $4.0$ hours) from the entry bar $P_t$, executing organically without artificial liquidation penalties.
2. **Dual Passive Limit Execution (Capturing the Full Maker Spread):**
   - **Maker Limit Buy at a Discount ($-0.50\%$):** Limit bids placed $-0.50\%$ below signal close $P_t$, eliminating adverse excursion and achieving superior fill prices.
   - **Maker Limit Take-Profit at a Premium ($+0.15\%$):** Resting maker offers placed at $+x^* + 0.15\%$ above entry, capturing the full kinetic expansion.
   - **Fee Drag Elimination:** Because both entry and take-profit are passive maker orders, **trading fees are $0.00$ on BinanceUS**.
3. **High-Conviction GBDT Barrier Classification:**
   A `HistGradientBoostingClassifier` trained on 28 first-principles structural features predicts the probability $P(\text{Hit } +x^* \text{ before } -y^*)$. Filtering for the **Top 2.0% conviction cutoff ($P^*$)** produces an empirical win rate of $43\%\text{--}52\%$ against a random walk base rate of $0.5\%\text{--}3.0\%$ (a **$30\times$ to $100\times$ statistical edge**).

---

## 2. Universe Scope & Cross-Exchange Symbol Translation

The strategy operates across the **6 primary liquid crypto assets**. Live data is ingested from Kraken and executed on BinanceUS using the following canonical symbol mapping:

| Kraken Ingest Symbol | BinanceUS Execution Pair | Champion Target ($+x^*$) | Stop Loss ($-y^*$) | Conviction Cutoff ($P^*$) | Primary Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`XBTUSD`** | `BTCUSD` / `BTCUSDT` | **$+4.00\%$** | **$-2.00\%$** | Top 2.0% ($P \ge P^*$) | Macro Anchor / Deepest Liquidity |
| **`ETHUSD`** | `ETHUSD` / `ETHUSDT` | **$+5.00\%$** | **$-2.50\%$** | Top 2.0% ($P \ge P^*$) | Large-Cap Smart Contract Beta |
| **`SOLUSD`** | `SOLUSD` / `SOLUSDT` | **$+5.00\%$** | **$-2.50\%$** | Top 2.0% ($P \ge P^*$) | High-Beta Kinetic Momentum |
| **`ADAUSD`** | `ADAUSD` / `ADAUSDT` | **$+5.00\%$** | **$-2.50\%$** | Top 2.0% ($P \ge P^*$) | Liquid Major Altcoin |
| **`XRPUSD`** | `XRPUSD` / `XRPUSDT` | **$+2.50\%$** | **$-1.25\%$** | Top 2.0% ($P \ge P^*$) | Rapid Mean-Reversion Altcoin |
| **`XDGUSD`** | `DOGEUSD` / `DOGEUSDT` | **$+5.00\%$** | **$-2.50\%$** | Top 2.0% ($P \ge P^*$) | High Kinetic Volatility Retail Anchor |

---

## 3. Structural Feature Space (28 Production Features)

Every 15-minute bar, the `CryptoLiveFeatureEngine` updates rolling in-memory buffers and calculates the exact 28 features required by the trained GBDT models:

### 3.1 Rolling Containers & Relative Position Index (RPI)
- **Hourly Container ($H$):** 4 bars ($1.0$ hour). Prior high (`prior_h_h`) and low (`prior_h_l`) shifted by 1 bar.
- **Daily Container ($D$):** 96 bars ($24.0$ hours). Prior high (`prior_d_h`) and low (`prior_d_l`) shifted by 1 bar.
- **Weekly Container ($W$):** 672 bars ($7.0$ days / $168.0$ hours). Prior high (`prior_wk_h`) and low (`prior_wk_l`) shifted by 1 bar.
- **Features:**
  - `rpi_h_pos`: Position of $P_t$ in Hourly container $[0.0, 1.0]$ (clipped to $[-0.5, 1.5]$).
  - `rpi_d_pos`: Position of $P_t$ in Daily container $[0.0, 1.0]$ (clipped to $[-0.5, 1.5]$).
  - `rpi_wk_pos`: Position of $P_t$ in Weekly container $[0.0, 1.0]$ (clipped to $[-0.5, 1.5]$).
  - `rpi_compression_h_in_d`: Ratio of Hourly range to Daily range.
  - `rpi_compression_d_in_wk`: Ratio of Daily range to Weekly range.

### 3.2 Expanding SDI Statistical Stretch (Anchor Displacements)
Measures the percentage stretch and volatility-normalized Z-score of price relative to expanding moving average anchors:
- `sdi_h_stretch` & `sdi_h_zscore`: 4-bar rolling mean anchor.
- `sdi_d_stretch` & `sdi_d_zscore`: 96-bar rolling mean anchor.
- `sdi_wk_stretch` & `sdi_wk_zscore`: 672-bar rolling mean anchor.

### 3.3 Continuous Micro State Machine (`MICRO_RED` $\leftrightarrow$ `MICRO_GREEN`)
Derived from our foundational micro-structure research (`MICRO_substrategies.md`):
- **`MICRO_RED` (Cascade Leg):** Price is making lower lows. The candidate low $L_{\text{cand}}$ ratchets downward on every new low ($L_t < L_{\text{cand}}$).
- **`MICRO_GREEN` (Confirmed Shelf Floor):** The first bar where price closes above candidate low ($C_t > L_{\text{cand}}$). The floor is locked at $L_k = L_{\text{cand}}$.
- **Floor Breakdown:** If $L_t < L_k$, the shelf is invalidated and state immediately flips back to `MICRO_RED`.
- **Features:**
  - `is_micro_green`: $1.0$ if holding confirmed shelf floor, $0.0$ if cascading in `MICRO_RED`.
  - `dist_to_shelf_floor_pct`: Percentage distance $(C_t - L_k) / L_k \times 100.0$.

### 3.4 Tactical State Machine (Macro Biomes)
Tracks the relationship between current price and rolling 24-hour extremes:
- `is_green` (`ACTIVE_BULL_WAVE`): Pushed above prior 24h high.
- `is_yellow` (`BULL_EXHAUSTED`): Pulled back below prior 1h low during bull wave.
- `is_red` (`ACTIVE_BEAR_WAVE`): Pushed below prior 24h low.
- `is_purple` (`BEAR_EXHAUSTED`): Bounced above prior 1h high during bear wave.
- `tactical_state_duration_bars`: Number of consecutive 15m bars in current biome.

### 3.5 Tactical TCXA Kinematics
- Evaluates 1-hour (4/8 EMAs) and 24-hour (48/96 EMAs) crossovers:
  - `tcxa_h_phase` / `tcxa_d_phase`: $+1$ (Bullish) or $-1$ (Bearish).
  - `tcxa_h_time_since_x` / `tcxa_d_time_since_x`: Bars since EMA crossover.
  - `tcxa_h_c_to_t_pct` / `tcxa_d_c_to_t_pct`: Distance from candidate extreme to current close.
  - `tcxa_h_c_age_bars` / `tcxa_d_c_age_bars`: Bars elapsed since candidate extreme.
  - `tcxa_h_c_velocity` / `tcxa_d_c_velocity`: Velocity of move away from extreme.
  - `tcxa_h_efficiency_decay` / `tcxa_d_efficiency_decay`: Price change relative to log volume accumulation.

### 3.6 Instantaneous Candle Kinematics
- `bar_body_ratio`: $|C_t - O_t| / (H_t - L_t + \epsilon)$.
- `bar_thrust_dir`: $+1.0$ if $C_t \ge O_t$ else $-1.0$.
- `bar_rvol`: 15m volume relative to rolling 96-bar (24h) median volume.
- `ret_24h_pct`: 24-hour return $(C_t - C_{t-96}) / C_{t-96} \times 100.0$.

---

## 4. Execution Logic & Order Lifecycle (Dual Limit Engine)

When `CryptoLiveInferenceEngine` generates a `TradeSignal` (`is_signal == True`):

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                             DUAL LIMIT ORDER LIFECYCLE                                           │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                      │
                         [15m Candle Closes on Kraken]
                                      │
                         [Compute 28 Features & Predict P]
                                      │
                           Is P >= P* (Top 2.0%)?
                                      ├── No ──> [Do Nothing / Wait for Next Bar]
                                     Yes
                                      │
                       Are Concurrency Slots < 5?
                                      ├── No ──> [Reject Signal / Slots Saturated]
                                     Yes
                                      │
               [Place Maker Limit Buy on BinanceUS at -0.50% Discount]
                                      │
                     Does Low_t <= Limit Buy within 1 Bar?
                                      ├── No ──> [Cancel Order / Release Capital]
                                     Yes
                                      │
             ┌────────────────────────┴────────────────────────┐
             │                                                 │
 [Pre-Place Resting Maker Limit Sell]             [Register Stop Loss Trigger]
  Price = Entry * (1 + x* + 0.15%)                 Price = Entry * (1 - y*)
  Fee = 0.0% (Maker Limit)                         Fee = 0.019% (Taker Market)
             │                                                 │
    High_t >= Limit Sell?                             Low_t <= Stop Loss?
             ├── Yes ──> [BANK +x* PROFIT]                     ├── Yes ──> [EXIT AT STOP]
             │           (0.0% Fee Drag)                       │           (1.9 bps Fee)
```

### Exact Parameter Formulas:
1. **Maker Limit Buy Price:**
   $$P_{\text{limit\_buy}} = P_{\text{close}} \times (1.0 - 0.0050)$$
2. **Maker Limit Take-Profit Price:**
   $$P_{\text{limit\_sell}} = P_{\text{limit\_buy}} \times \left(1.0 + \frac{x^* + 0.15}{100.0}\right)$$
3. **Stop-Loss Price:**
   $$P_{\text{stop\_loss}} = P_{\text{limit\_buy}} \times \left(1.0 - \frac{y^*}{100.0}\right)$$
4. **Order Timeout:** Unfilled limit buy orders are automatically cancelled after **1 bar (15 minutes)** to prevent stale execution.

---

## 5. Portfolio Capital Allocation & Slot Management

- **Starting Capital ($USD):** Configurable (e.g. $\$10,000.00$).
- **Maximum Concurrent Slots ($K$):** **$5$ simultaneous open positions**.
- **Position Sizing Fraction:** **$50\%$ of total portfolio equity** per slot.
- **Capital Allocation Rule:**
  $$\text{Slot Capital} = \min\left(\text{Total Equity}(t) \times 0.50, \text{Free Cash}(t)\right)$$
- **Proceeds Recycling:** 100% of realized capital + net profit from closed positions immediately returns to free cash to fund subsequent entries.
- **Balance Sheet Conservation:**
  $$\text{Total Equity}(t) = \text{Free Cash}(t) + \sum_{i \in \text{Active Positions}} \text{Position Value}_i(t)$$

---

## 6. Live Operational Invariants & Failsafes

1. **Warm-Up Invariant:** The `CryptoLiveFeatureEngine` **MUST NOT** generate signals until at least **672 bars (7 days)** of continuous 15m Kraken history are buffered in memory. If the bot restarts, it must execute `warmup(df_historical)` using Kraken SQLite or REST API before trading.
2. **Symbol Mapping Invariant:** Ensure orders sent to BinanceUS use the exact exchange symbol (`BTCUSD` vs `BTCUSDT`). If USD order book liquidity is thin, route to `USDT` with appropriate balance checks.
3. **Structured Prediction Logging:** Accretion must call `LivePredictionLogger.log_prediction(signal)` on **every 15-minute bar** (both signals and non-signals). This log file is required by `crypto_live_prediction_validation_lab.py` to audit live execution against research historical data.
4. **Debounce Invariant:** Exactly **one active position per symbol** is permitted at any given time. Duplicate triggers on adjacent bars during the same wave are blocked.
