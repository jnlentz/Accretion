"""
structural_rapid_profit_eval.py
===============================
Champion Structural Rapid-Profit Scalper Lab & Independent Parameter Sweeps (BTC, ETH, SOL, Alts | 1H | 2020-2026)
- Core Purpose:
    * Focused entirely on the champion Unfiltered Rapid-Profit Scalper Strategy:
        1. Resting Purple Limit Buy Orders: Place limit buy at N% discount below trigger upon Purple bottom confirmation.
        2. Fallback Breakout Buying: Buy on Macro Green breakout if limit was unfilled.
        3. Cancellation on Macro Red: Cancel resting order if bottom attempt collapses into Red.
        4. Decaying Halving Targets: T_k = T_0 / 2^(k-1) on reloads inside Macro Green.
        5. Policy 4 Invalidation Stop: Hold through Yellow; exit ONLY on 7d Weekly Low breakdown (Macro Red).
        6. Strict Intra-Bar Causality: Position exits strictly evaluate starting on bar t+1 (entry_idx < t).
    * Independent Parameter Sweeps:
        - Sweep 1 (Initial Target Sweep): Sweeps initial profit targets (+8% to +22%) with Purple Discount fixed at DEFAULT_PURPLE_DISCOUNT_PCT (-2.0%).
        - Sweep 2 (Purple Discount Sweep): Sweeps purple discounts (0.0% to -4.0%) with Initial Target fixed at DEFAULT_INITIAL_TARGET_PCT (+12.0%).
        - Benchmarking against Pure Macro Runner Baseline (Infinite Target Baseline).
        - Fee Audit: 0.000% Maker Limit Fee vs. 0.038% Standard Taker Fee.
- 4-Panel Dark Mode Dashboard:
    * Panel 1: Compounded Portfolio Growth ($10k Log Scale): Optimal Scalper vs. Pure Macro Runner vs. Asset Benchmark
    * Panel 2: Initial Target Profit Parameter Sweep Bar Chart (Win Rate % & Profit Factor)
    * Panel 3: Purple Limit Discount Parameter Sweep Bar Chart (Win Rate % & Compounded Final Equity)
    * Panel 4: Annual Realized Alpha Matrix for Optimal Scalper Configuration (2020-2026)
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import sqlite3
import os
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ==============================================================================
# 1. ASSET SELECTION & DYNAMIC PARAMETER SWEEP CONFIGURATION
# ==============================================================================
# Target asset pair (e.g. "XBTUSD", "BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD", "XRPUSD")
ASSET_PAIR = "XBTUSD"

START_DATE = "2020-01-01"
END_DATE   = "2027-01-01"   # Extended to capture all 2026 data up to present day

# Structural Wave Lookbacks (1H Resolution)
ROLLING_DAY_HOURS   = 24    # 24 bars = 1 Day Daily Channel
ROLLING_WEEK_HOURS  = 168   # 168 bars = 7 Days Weekly Channel
MICRO_FLOOR_HOURS   = 6     # 6 bars = 6 Hour trailing floor

# Default Reference Parameters
DEFAULT_INITIAL_TARGET_PCT  = 0.120  # +12.0% Default Initial Target
DEFAULT_PURPLE_DISCOUNT_PCT = 0.020  # -2.0% Default Purple Limit Discount

# Dynamic Target Profit Sweep Parameters (T_0)
MIN_INITIAL_TARGET_PCT  = 0.08   # Starting min initial target (e.g. 8.0% for BTC, 12.0%+ for alts)
TARGET_STEP_PCT         = 0.02   # Ascending step size (+2.0%)
NUM_TARGET_STEPS        = 8      # Steps: [8%, 10%, 12%, 14%, 16%, 18%, 20%, 22%]

# Dynamic Purple Limit Discount Sweep Parameters (N%)
MIN_PURPLE_DISCOUNT_PCT = 0.010  # Starting min discount below trigger (-1.0%)
DISCOUNT_STEP_PCT       = 0.005  # Ascending step size (-0.5%)
NUM_DISCOUNT_STEPS      = 7      # Steps: [0.0%, 1.0%, 1.5%, 2.0%, 2.5%, 3.0%, 3.5%, 4.0%]

TARGET_FLOOR_PCT        = 0.005  # Minimum target floor on reloads (+0.50%)
FEE_RATE_PER_SIDE_STD   = 0.00019 # 0.019% per side (0.038% round-trip)
FEE_RATE_ZERO           = 0.00000 # 0.000% maker limit order
STARTING_CAPITAL        = 10000.0

# Visual Color Palette (Dark Mode)
C_OPTIMAL_EQ  = '#00E5FF'  # Cyan (Optimal Scalper)
C_RUNNER_EQ   = '#70E000'  # Lime Green (Pure Macro Runner)
C_BENCHMARK   = '#707070'  # Gray (Buy & Hold Benchmark)

C_WIN         = '#00FF66'  # Neon Green
C_LOSS        = '#FF3366'  # Neon Red
# ==============================================================================


def load_hourly_data(asset_pair: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Dynamically searches SQLite databases across directories for the requested asset pair,
    prioritizing official Kraken updated databases.
    """
    search_dirs = [
        PROJECT_ROOT / "databases" / "kraken",
        PROJECT_ROOT / "databases",
        PROJECT_ROOT / "databases" / "historical_data",
        PROJECT_ROOT / "databases" / "binance_us",
        PROJECT_ROOT / "databases" / "binance_com"
    ]

    pair_clean = asset_pair.upper().replace("/", "").replace("-", "")
    variants = [
        f"{pair_clean}.sqlite",
        f"{pair_clean}_us.sqlite",
        f"{pair_clean}_com.sqlite",
        f"{pair_clean}_historical_klines.sqlite"
    ]
    if pair_clean in ["BTCUSD", "XBTUSD"]:
        variants = ["XBTUSD.sqlite", "xbtusd_kraken_klines.sqlite", "BTCUSD.sqlite", "BTCUSDT.sqlite", "BTCUSDC.sqlite", "btcusd_historical_klines.sqlite"]
    elif pair_clean in ["ETHUSD", "ETHUSDT"]:
        variants = ["ETHUSD.sqlite", "ETHUSDT.sqlite", "ETHUSDT_com.sqlite"]
    elif pair_clean in ["SOLUSD", "SOLUSDT"]:
        variants = ["SOLUSD.sqlite", "SOLUSDT.sqlite"]

    found_dbs = []
    for s_dir in search_dirs:
        if s_dir.exists():
            for v in variants:
                candidate = s_dir / v
                if candidate.exists() and candidate not in found_dbs:
                    found_dbs.append(candidate)

    print(f"Searching database candidates for {asset_pair} ({len(found_dbs)} found)...")
    for db_path in found_dbs:
        try:
            with sqlite3.connect(db_path) as con:
                tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if 'klines_1h' in tables:
                    df = pd.read_sql_query("SELECT time, open, high, low, close, volume FROM 'klines_1h' ORDER BY time ASC", con)
                    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
                    df.set_index('time', inplace=True)
                    if df.index.tz is not None:
                        df.index = df.index.tz_localize(None)
                    view = df.loc[start_date:end_date].copy() if not df.empty else df
                    if len(view) > 500:
                        print(f"SUCCESS: Loaded {len(view):,} 1h bars from {db_path.parent.name}/{db_path.name} (klines_1h).")
                        print(f"Date Range: {view.index.min().strftime('%Y-%m-%d %H:%M')} to {view.index.max().strftime('%Y-%m-%d %H:%M')}")
                        return view.sort_index()
                elif 'klines_3m' in tables:
                    print(f"Resampling klines_3m for {asset_pair} from {db_path.parent.name}/{db_path.name} to 1h...")
                    df = pd.read_sql_query("SELECT time, open, high, low, close, volume FROM 'klines_3m' ORDER BY time ASC", con)
                    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
                    df.set_index('time', inplace=True)
                    if df.index.tz is not None:
                        df.index = df.index.tz_localize(None)
                    df_1h = df.resample('1h').agg({
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last',
                        'volume': 'sum'
                    }).dropna()
                    view = df_1h.loc[start_date:end_date].copy() if not df_1h.empty else df_1h
                    if len(view) > 500:
                        print(f"SUCCESS: Loaded {len(view):,} resampled 1h bars from {db_path.parent.name}/{db_path.name}.")
                        print(f"Date Range: {view.index.min().strftime('%Y-%m-%d %H:%M')} to {view.index.max().strftime('%Y-%m-%d %H:%M')}")
                        return view.sort_index()
        except Exception as e:
            continue

    # Fallback to parquet
    parquet_path = PROJECT_ROOT / "results" / "market_maps" / "XBTUSD_3m_landmark_map.parquet"
    if parquet_path.exists():
        print(f"Loading 1h candles from parquet fallback: {parquet_path.name}...")
        df_3m = pd.read_parquet(parquet_path)
        if df_3m.index.tz is not None:
            df_3m.index = df_3m.index.tz_localize(None)
        df_1h = df_3m.resample('1h').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        view = df_1h.loc[start_date:end_date].copy()
        if not view.empty:
            return view.sort_index()

    raise FileNotFoundError(f"Could not find 1H data for {asset_pair} in databases/.")


def compute_nested_structural_waves(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes both Macro Tier (1D in 7D Weekly) and Micro Tier (1H in 24H Daily) waves.
    """
    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values

    d_h = np.zeros(n)
    d_l = np.zeros(n)
    wk_h = np.zeros(n)
    wk_l = np.zeros(n)
    micro_flr_h = np.zeros(n)
    micro_flr_l = np.zeros(n)

    macro_state = np.zeros(n, dtype=object)
    micro_state = np.zeros(n, dtype=object)

    cur_macro_state = "NEUTRAL_EQUILIBRIUM"
    cur_micro_state = "MICRO_NEUTRAL"

    s_h = pd.Series(highs)
    s_l = pd.Series(lows)

    # Shifted prior rolling extremes (strictly causal t-1)
    prior_wk_high = s_h.rolling(window=ROLLING_WEEK_HOURS, min_periods=1).max().shift(1).bfill().values
    prior_wk_low  = s_l.rolling(window=ROLLING_WEEK_HOURS, min_periods=1).min().shift(1).bfill().values

    prior_d_high = s_h.rolling(window=ROLLING_DAY_HOURS, min_periods=1).max().shift(1).bfill().values
    prior_d_low  = s_l.rolling(window=ROLLING_DAY_HOURS, min_periods=1).min().shift(1).bfill().values

    prior_mf_high = s_h.rolling(window=MICRO_FLOOR_HOURS, min_periods=1).max().shift(1).bfill().values
    prior_mf_low  = s_l.rolling(window=MICRO_FLOOR_HOURS, min_periods=1).min().shift(1).bfill().values

    cur_wk_high = s_h.rolling(window=ROLLING_WEEK_HOURS, min_periods=1).max().values
    cur_wk_low  = s_l.rolling(window=ROLLING_WEEK_HOURS, min_periods=1).min().values

    cur_d_high = s_h.rolling(window=ROLLING_DAY_HOURS, min_periods=1).max().values
    cur_d_low  = s_l.rolling(window=ROLLING_DAY_HOURS, min_periods=1).min().values

    for t in range(n):
        h_t = highs[t]
        l_t = lows[t]
        c_t = closes[t]
        o_t = opens[t]

        pw_h = prior_wk_high[t]
        pw_l = prior_wk_low[t]
        pd_h = prior_d_high[t]
        pd_l = prior_d_low[t]
        pmf_h = prior_mf_high[t]
        pmf_l = prior_mf_low[t]

        d_h[t] = cur_d_high[t]
        d_l[t] = cur_d_low[t]
        wk_h[t] = cur_wk_high[t]
        wk_l[t] = cur_wk_low[t]
        micro_flr_h[t] = pmf_h
        micro_flr_l[t] = pmf_l

        # Macro Tier (1D in 7D Weekly)
        pushed_wk_up = (h_t > pw_h)
        pushed_wk_dn = (l_t < pw_l)

        if pushed_wk_up and not pushed_wk_dn:
            cur_macro_state = "ACTIVE_BULL_WAVE"
        elif pushed_wk_dn and not pushed_wk_up:
            cur_macro_state = "ACTIVE_BEAR_WAVE"
        elif pushed_wk_up and pushed_wk_dn:
            cur_macro_state = "ACTIVE_BULL_WAVE" if c_t >= o_t else "ACTIVE_BEAR_WAVE"
        else:
            if cur_macro_state == "ACTIVE_BULL_WAVE" and c_t < pd_l:
                cur_macro_state = "BULL_EXHAUSTED"
            elif cur_macro_state == "ACTIVE_BEAR_WAVE" and c_t > pd_h:
                cur_macro_state = "BEAR_EXHAUSTED"

        # Micro Tier (1H in 24H Daily)
        pushed_d_up = (h_t > pd_h)
        pushed_d_dn = (l_t < pd_l)

        if pushed_d_up and not pushed_d_dn:
            cur_micro_state = "MICRO_ACTIVE_BULL"
        elif pushed_d_dn and not pushed_d_up:
            cur_micro_state = "MICRO_ACTIVE_BEAR"
        elif pushed_d_up and pushed_d_dn:
            cur_micro_state = "MICRO_ACTIVE_BULL" if c_t >= o_t else "MICRO_ACTIVE_BEAR"
        else:
            if cur_micro_state == "MICRO_ACTIVE_BULL" and c_t < pmf_l:
                cur_micro_state = "MICRO_BULL_EXHAUSTED"
            elif cur_micro_state == "MICRO_ACTIVE_BEAR" and c_t > pmf_h:
                cur_micro_state = "MICRO_BEAR_EXHAUSTED"

        macro_state[t] = cur_macro_state
        micro_state[t] = cur_micro_state

    df_out = df.copy()
    df_out['d_h'] = d_h
    df_out['d_l'] = d_l
    df_out['wk_h'] = wk_h
    df_out['wk_l'] = wk_l
    df_out['macro_state'] = macro_state
    df_out['micro_state'] = micro_state

    return df_out


def simulate_rapid_profit_scalper(df: pd.DataFrame, 
                                  initial_target_pct: float,
                                  purple_discount_pct: float,
                                  enable_halving: bool = True,
                                  fee_rate: float = FEE_RATE_PER_SIDE_STD) -> tuple[pd.DataFrame, dict]:
    """
    Simulates Unfiltered Champion Rapid Profit Scalper:
    - Resting Purple Limit Buy at N% discount.
    - Breakout buy on Macro Green fallback.
    - Cancellation on Macro Red breakdown.
    - Halving profit targets on reloads (T_k = T_0 / 2^(k-1)).
    - Policy 4 Weekly Low Stop (Macro Red).
    - Trades entered on bar t strictly evaluate exits starting on bar t+1 (entry_idx < t).
    """
    trades = []
    in_pos = False
    entry_idx = None
    entry_time = None
    entry_price = np.nan
    target_price = np.nan
    entry_reason = None
    carried_into_green = False
    cur_trade_target_pct = np.nan

    seen_micro_red_in_cur_purple = False
    seen_micro_purple_after_red_in_cur_purple = False

    has_resting_purple_bid = False
    resting_purple_price = np.nan

    purple_fills = 0
    green_breakout_buys = 0
    purple_cancels = 0

    green_reload_count = 0

    times = df.index
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    macro_states = df['macro_state'].values
    micro_states = df['micro_state'].values
    n = len(df)

    for t in range(n):
        m_st = macro_states[t]
        u_st = micro_states[t]
        h_p = highs[t]
        l_p = lows[t]
        c_p = closes[t]
        t_time = times[t]

        prev_u_st = micro_states[t - 1] if t > 0 else "NONE"
        prev_m_st = macro_states[t - 1] if t > 0 else "NONE"

        # Track fresh Green wave transitions
        if m_st == "ACTIVE_BULL_WAVE" and prev_m_st != "ACTIVE_BULL_WAVE":
            green_reload_count = 0

        # Sequence tracking inside Macro Purple
        if m_st == "BEAR_EXHAUSTED":
            if prev_m_st != "BEAR_EXHAUSTED":
                seen_micro_red_in_cur_purple = False
                seen_micro_purple_after_red_in_cur_purple = False

            if u_st == "MICRO_ACTIVE_BEAR":
                seen_micro_red_in_cur_purple = True
            elif u_st == "MICRO_BEAR_EXHAUSTED" and seen_micro_red_in_cur_purple:
                seen_micro_purple_after_red_in_cur_purple = True
        else:
            seen_micro_red_in_cur_purple = False
            seen_micro_purple_after_red_in_cur_purple = False

        # ----------------------------------------------------------------------
        # 1. Manage Open Position Exits (Only for positions open BEFORE bar t)
        # ----------------------------------------------------------------------
        if in_pos and entry_idx < t:
            if m_st == "ACTIVE_BULL_WAVE":
                carried_into_green = True

            # Limit Target Execution Check
            if initial_target_pct is not None and h_p >= target_price:
                exit_idx = t
                exit_time = t_time
                exit_price = target_price
                duration = max(1, exit_idx - entry_idx)
                raw_ret = ((exit_price - entry_price) / entry_price) * 100.0
                net_ret = ((exit_price / entry_price) * (1.0 - fee_rate)**2 - 1.0) * 100.0

                trades.append({
                    'trade_id': len(trades) + 1,
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'duration_hours': duration,
                    'entry_reason': entry_reason,
                    'exit_reason': 'TARGET_LIMIT_FILL',
                    'target_pct': cur_trade_target_pct * 100.0,
                    'raw_ret_pct': raw_ret,
                    'net_ret_pct': net_ret,
                    'is_win': net_ret > 0.0,
                    'year': entry_time.year
                })
                in_pos = False
                carried_into_green = False

                if m_st == "ACTIVE_BULL_WAVE":
                    green_reload_count += 1

            else:
                # Target NOT hit: Policy 4 Stop (Weekly Breakdown)
                if m_st == "ACTIVE_BEAR_WAVE":
                    exit_idx = t
                    exit_time = t_time
                    exit_price = c_p
                    duration = max(1, exit_idx - entry_idx)
                    raw_ret = ((exit_price - entry_price) / entry_price) * 100.0
                    net_ret = ((exit_price / entry_price) * (1.0 - fee_rate)**2 - 1.0) * 100.0

                    trades.append({
                        'trade_id': len(trades) + 1,
                        'entry_time': entry_time,
                        'exit_time': exit_time,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'duration_hours': duration,
                        'entry_reason': entry_reason,
                        'exit_reason': 'WIDE_MACRO_RED_STOP',
                        'target_pct': cur_trade_target_pct * 100.0,
                        'raw_ret_pct': raw_ret,
                        'net_ret_pct': net_ret,
                        'is_win': net_ret > 0.0,
                        'year': entry_time.year
                    })
                    in_pos = False
                    carried_into_green = False
                    green_reload_count = 0

        # ----------------------------------------------------------------------
        # 2. Manage Resting Purple Limit Buy Order
        # ----------------------------------------------------------------------
        if not in_pos and has_resting_purple_bid:
            if l_p <= resting_purple_price and m_st in ["BEAR_EXHAUSTED", "NEUTRAL_EQUILIBRIUM", "BULL_EXHAUSTED"]:
                in_pos = True
                has_resting_purple_bid = False
                entry_idx = t
                entry_time = t_time
                entry_price = resting_purple_price
                eff_target = initial_target_pct if initial_target_pct is not None else np.inf
                target_price = entry_price * (1.0 + eff_target) if initial_target_pct is not None else np.inf
                cur_trade_target_pct = eff_target
                entry_reason = "PURPLE_DISCOUNT_LIMIT_FILL"
                carried_into_green = False
                purple_fills += 1

            elif m_st == "ACTIVE_BULL_WAVE":
                in_pos = True
                has_resting_purple_bid = False
                entry_idx = t
                entry_time = t_time
                entry_price = c_p
                eff_target = initial_target_pct if initial_target_pct is not None else np.inf
                target_price = entry_price * (1.0 + eff_target) if initial_target_pct is not None else np.inf
                cur_trade_target_pct = eff_target
                entry_reason = "GREEN_BREAKOUT_FALLBACK"
                carried_into_green = True
                green_breakout_buys += 1

            elif m_st == "ACTIVE_BEAR_WAVE":
                has_resting_purple_bid = False
                purple_cancels += 1

        # ----------------------------------------------------------------------
        # 3. Evaluate New Entries / Resting Order Placements when Flat
        # ----------------------------------------------------------------------
        if not in_pos and not has_resting_purple_bid:
            if m_st == "ACTIVE_BULL_WAVE":
                in_pos = True
                entry_idx = t
                entry_time = t_time
                entry_price = c_p
                if initial_target_pct is not None:
                    if enable_halving and green_reload_count > 0:
                        eff_target = max(TARGET_FLOOR_PCT, initial_target_pct / (2.0 ** green_reload_count))
                    else:
                        eff_target = initial_target_pct
                else:
                    eff_target = np.inf
                target_price = entry_price * (1.0 + eff_target) if initial_target_pct is not None else np.inf
                cur_trade_target_pct = eff_target
                entry_reason = "MACRO_GREEN_ENTRY"
                carried_into_green = True

            elif m_st == "BULL_EXHAUSTED" and u_st == "MICRO_BEAR_EXHAUSTED" and prev_u_st != "MICRO_BEAR_EXHAUSTED":
                in_pos = True
                entry_idx = t
                entry_time = t_time
                entry_price = c_p
                eff_target = initial_target_pct if initial_target_pct is not None else np.inf
                target_price = entry_price * (1.0 + eff_target) if initial_target_pct is not None else np.inf
                cur_trade_target_pct = eff_target
                entry_reason = "YELLOW_DISCOUNT_ENTRY"
                carried_into_green = False

            elif m_st == "BEAR_EXHAUSTED" and u_st == "MICRO_ACTIVE_BULL" and prev_u_st != "MICRO_ACTIVE_BULL":
                if seen_micro_purple_after_red_in_cur_purple:
                    if purple_discount_pct > 0.0:
                        has_resting_purple_bid = True
                        resting_purple_price = c_p * (1.0 - purple_discount_pct)
                    else:
                        in_pos = True
                        entry_idx = t
                        entry_time = t_time
                        entry_price = c_p
                        eff_target = initial_target_pct if initial_target_pct is not None else np.inf
                        target_price = entry_price * (1.0 + eff_target) if initial_target_pct is not None else np.inf
                        cur_trade_target_pct = eff_target
                        entry_reason = "PURPLE_MARKET_BUY"
                        carried_into_green = False

    diag = {
        'purple_fills': purple_fills,
        'green_breakout_buys': green_breakout_buys,
        'purple_cancels': purple_cancels
    }
    return pd.DataFrame(trades), diag


def plot_master_cross_asset_dashboard(df: pd.DataFrame, 
                                       df_opt: pd.DataFrame,
                                       df_runner: pd.DataFrame,
                                       sweep_targets: pd.DataFrame,
                                       sweep_disc: pd.DataFrame,
                                       best_target: float,
                                       best_disc: float,
                                       asset_pair: str):
    """
    Renders 4-panel dark mode dashboard for dynamic cross-asset evaluation.
    """
    plt.style.use('dark_background')
    fig = plt.figure(figsize=(18, 14))
    gs = fig.add_gridspec(3, 2, height_ratios=[2.2, 1.3, 1.3], hspace=0.38, wspace=0.22)

    ax1 = fig.add_subplot(gs[0, :])       # Top: Compounded Portfolio Growth Curves (Log Scale)
    ax2 = fig.add_subplot(gs[1, 0])       # Middle Left: Initial Target Parameter Sweep
    ax3 = fig.add_subplot(gs[1, 1])       # Middle Right: Purple Limit Discount Parameter Sweep
    ax4 = fig.add_subplot(gs[2, :])       # Bottom: Annual Realized Returns Matrix

    fig.suptitle(f"Structural Rapid-Profit Scalper Lab & Independent Sweeps | {asset_pair} 1H (2020-2026)", 
                 fontsize=14, fontweight='bold', y=0.985)

    # --------------------------------------------------------------------------
    # 1. Panel 1: Compounded Growth Curves (Log Scale)
    # --------------------------------------------------------------------------
    bnh_equity = STARTING_CAPITAL * (df['close'] / df['close'].iloc[0])
    ax1.plot(df.index, bnh_equity, color=C_BENCHMARK, lw=1.0, linestyle=':', alpha=0.6, label=f'Buy & Hold {asset_pair} Benchmark')

    def get_curve(df_t):
        if len(df_t) == 0:
            return df.index, np.full(len(df), STARTING_CAPITAL)
        df_sorted = df_t.sort_values('exit_time').copy()
        df_sorted['cum_ret'] = (1.0 + df_sorted['net_ret_pct'] / 100.0).cumprod()
        return df_sorted['exit_time'], STARTING_CAPITAL * df_sorted['cum_ret']

    t_opt, eq_opt = get_curve(df_opt)
    t_run, eq_run = get_curve(df_runner)

    ax1.step(t_run, eq_run, where='post', color=C_RUNNER_EQ, lw=1.8, 
             label=f"Pure Macro Runner (No Target Cap) [${eq_run.iloc[-1]:,.0f}] ({len(df_runner):,} Trades)")
    ax1.step(t_opt, eq_opt, where='post', color=C_OPTIMAL_EQ, lw=2.2, 
             label=f"Champion Scalper (+{best_target*100:.1f}% Target | -{best_disc*100:.1f}% Purple Disc) [${eq_opt.iloc[-1]:,.0f}] ({len(df_opt):,} Trades)")

    ax1.set_yscale('log')
    ax1.set_title(f"Compounded Growth ($ Log Scale): Champion {asset_pair} Scalper vs. Pure Macro Runner vs. Benchmark", fontsize=11, fontweight='bold')
    ax1.set_ylabel("Portfolio Value ($)")
    ax1.grid(True, linestyle=':', alpha=0.25, color='gray')
    ax1.legend(loc='upper left', framealpha=0.4, fontsize=8.5)

    locator = mdates.AutoDateLocator(minticks=5, maxticks=12)
    formatter = mdates.ConciseDateFormatter(locator)
    ax1.xaxis.set_major_locator(locator)
    ax1.xaxis.set_major_formatter(formatter)

    # --------------------------------------------------------------------------
    # 2. Panel 2: Initial Target Profit Parameter Sweep
    # --------------------------------------------------------------------------
    labels_t = [f"+{r:.0f}%" for r in sweep_targets['target_pct']]
    x_t = np.arange(len(labels_t))
    width = 0.35

    rects1 = ax2.bar(x_t - width/2, sweep_targets['win_rate'], width, label='Win Rate (%)', color=C_OPTIMAL_EQ, alpha=0.85)
    rects2 = ax2.bar(x_t + width/2, sweep_targets['pf'] * 20.0, width, label='Profit Factor (x20 scale)', color=C_RUNNER_EQ, alpha=0.8)

    for rect, wr in zip(rects1, sweep_targets['win_rate']):
        h = rect.get_height()
        ax2.annotate(f'{wr:.0f}%', xy=(rect.get_x() + rect.get_width() / 2, h),
                    xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=6.5, fontweight='bold')

    for rect, pf in zip(rects2, sweep_targets['pf']):
        h = rect.get_height()
        ax2.annotate(f'{pf:.2f}', xy=(rect.get_x() + rect.get_width() / 2, h),
                    xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=6.5, fontweight='bold')

    ax2.set_xticks(x_t)
    ax2.set_xticklabels(labels_t, fontsize=7.5)
    ax2.set_title(f"1. Initial Target Profit Sweep (Fixed -{DEFAULT_PURPLE_DISCOUNT_PCT*100:.1f}% Purple Disc) | {asset_pair}", fontsize=10.0, fontweight='bold')
    ax2.grid(True, linestyle=':', alpha=0.25, color='gray')
    ax2.legend(loc='upper right', framealpha=0.35, fontsize=8)

    # --------------------------------------------------------------------------
    # 3. Panel 3: Purple Limit Discount Parameter Sweep
    # --------------------------------------------------------------------------
    labels_d = [f"-{r:.1f}%" for r in sweep_disc['discount_pct']]
    x_d = np.arange(len(labels_d))

    bars_d = ax3.bar(x_d, sweep_disc['final_equity_std'], width=0.45, color=C_OPTIMAL_EQ, alpha=0.85, label='Standard 0.038% Fee')
    ax3.axhline(eq_run.iloc[-1], color=C_RUNNER_EQ, linestyle='--', lw=1.2, label=f'Pure Runner [${eq_run.iloc[-1]:,.0f}]')

    for b, eq, wr in zip(bars_d, sweep_disc['final_equity_std'], sweep_disc['win_rate']):
        ax3.text(b.get_x() + b.get_width()/2.0, b.get_height() + 2000, f"${eq/1000:,.0f}k\n({wr:.0f}% WR)", 
                 ha='center', va='bottom', fontsize=6.5, fontweight='bold')

    ax3.set_xticks(x_d)
    ax3.set_xticklabels(labels_d, fontsize=7.5)
    ax3.set_title(f"2. Purple Limit Discount Sweep (Fixed +{DEFAULT_INITIAL_TARGET_PCT*100:.1f}% Target) | {asset_pair}", fontsize=10.0, fontweight='bold')
    ax3.set_ylabel("Final Equity ($)")
    ax3.grid(True, linestyle=':', alpha=0.25, color='gray')
    ax3.legend(loc='upper left', framealpha=0.35, fontsize=7.5)

    # --------------------------------------------------------------------------
    # 4. Panel 4: Annual Realized Returns Matrix
    # --------------------------------------------------------------------------
    yr_grp = df_opt.groupby('year').agg(
        trades=('net_ret_pct', 'count'),
        wins=('is_win', 'sum'),
        mean_ret=('net_ret_pct', 'mean'),
        total_ret=('net_ret_pct', 'sum')
    )
    yr_grp['win_rate'] = (yr_grp['wins'] / yr_grp['trades']) * 100.0

    years = yr_grp.index.astype(str)
    x_y = np.arange(len(years))

    bars_y = ax4.bar(x_y, yr_grp['total_ret'], width=0.55, color=[C_WIN if r > 0 else C_LOSS for r in yr_grp['total_ret']], alpha=0.85)
    ax4.axhline(0, color='#FFFFFF', linestyle='-', lw=0.8, alpha=0.5)

    for b, tot, wr, n_t in zip(bars_y, yr_grp['total_ret'], yr_grp['win_rate'], yr_grp['trades']):
        pos = b.get_height() + (5 if tot >= 0 else -15)
        ax4.text(b.get_x() + b.get_width()/2.0, pos, f"{tot:+.1f}%\n({n_t} trds | {wr:.0f}% WR)", 
                 ha='center', va='bottom' if tot >= 0 else 'top', fontsize=7.5, fontweight='bold')

    ax4.set_xticks(x_y)
    ax4.set_xticklabels(years, fontsize=9)
    ax4.set_title(f"Annual Realized Returns: Champion {asset_pair} Scalper (+{best_target*100:.1f}% Target | -{best_disc*100:.1f}% Purple Disc)", fontsize=11, fontweight='bold')
    ax4.set_ylabel("Total Return (%)")
    ax4.grid(True, linestyle=':', alpha=0.25, color='gray')

    plt.subplots_adjust(top=0.94, bottom=0.06, left=0.06, right=0.96, hspace=0.38, wspace=0.22)
    plt.show()


def run_cross_asset_evaluation():
    print("=" * 120)
    print(f"STARTING CHAMPION RAPID-PROFIT SCALPER EVALUATION: {ASSET_PAIR} 1H (2020 - 2026)")
    print("=" * 120)

    df = load_hourly_data(ASSET_PAIR, START_DATE, END_DATE)
    print(f"Computing nested structural waves on {len(df):,} hourly bars for {ASSET_PAIR}...")
    df_nested = compute_nested_structural_waves(df)

    # 1. Pure Macro Runner Baseline
    print(f"\nSimulating Pure Macro Runner Baseline for {ASSET_PAIR}...")
    df_runner, _ = simulate_rapid_profit_scalper(df_nested, initial_target_pct=None, purple_discount_pct=0.0, enable_halving=False, fee_rate=FEE_RATE_PER_SIDE_STD)

    # Generate Dynamic Parameter Sweep Arrays
    target_sweep_arr = [MIN_INITIAL_TARGET_PCT + i * TARGET_STEP_PCT for i in range(NUM_TARGET_STEPS)]
    discount_sweep_arr = [0.0] + [MIN_PURPLE_DISCOUNT_PCT + i * DISCOUNT_STEP_PCT for i in range(NUM_DISCOUNT_STEPS)]

    # --------------------------------------------------------------------------
    # SWEEP 1: Initial Target Profit Parameter Sweep (Fixed at DEFAULT_PURPLE_DISCOUNT_PCT)
    # --------------------------------------------------------------------------
    target_records = []
    print(f"\nRunning Sweep 1: Initial Target Profit Sweep (+{target_sweep_arr[0]*100:.1f}% to +{target_sweep_arr[-1]*100:.1f}%) with Default -{DEFAULT_PURPLE_DISCOUNT_PCT*100:.1f}% Purple Discount...")
    for tg in target_sweep_arr:
        df_t_std, _ = simulate_rapid_profit_scalper(df_nested, initial_target_pct=tg, purple_discount_pct=DEFAULT_PURPLE_DISCOUNT_PCT, enable_halving=True, fee_rate=FEE_RATE_PER_SIDE_STD)
        df_t_zero, _ = simulate_rapid_profit_scalper(df_nested, initial_target_pct=tg, purple_discount_pct=DEFAULT_PURPLE_DISCOUNT_PCT, enable_halving=True, fee_rate=FEE_RATE_ZERO)

        w = df_t_std[df_t_std['net_ret_pct'] > 0]['net_ret_pct']
        l = df_t_std[df_t_std['net_ret_pct'] <= 0]['net_ret_pct']
        n_t = len(df_t_std)
        wr = (len(w) / n_t) * 100.0 if n_t > 0 else 0.0
        w_avg = w.mean() if len(w) > 0 else 0.0
        l_avg = l.mean() if len(l) > 0 else 0.0
        po = abs(w_avg / l_avg) if l_avg != 0 else np.nan
        pf = (w.sum() / abs(l.sum())) if len(l) > 0 and l.sum() != 0 else np.nan
        cum_std = (1.0 + df_t_std['net_ret_pct'] / 100.0).cumprod().iloc[-1] if n_t > 0 else 1.0
        cum_zero = (1.0 + df_t_zero['net_ret_pct'] / 100.0).cumprod().iloc[-1] if n_t > 0 else 1.0

        target_records.append({
            'target_pct': tg * 100.0, 'trades': n_t, 'win_rate': wr,
            'avg_win': w_avg, 'avg_loss': l_avg, 'payoff': po, 'pf': pf,
            'final_equity_std': STARTING_CAPITAL * cum_std, 'final_equity_zero': STARTING_CAPITAL * cum_zero
        })

    sweep_targets = pd.DataFrame(target_records)

    # --------------------------------------------------------------------------
    # SWEEP 2: Purple Limit Discount Parameter Sweep (Fixed at DEFAULT_INITIAL_TARGET_PCT)
    # --------------------------------------------------------------------------
    disc_records = []
    print(f"\nRunning Sweep 2: Purple Limit Discount Sweep (0.0% to -{discount_sweep_arr[-1]*100:.1f}%) with Default Target +{DEFAULT_INITIAL_TARGET_PCT*100:.1f}%...")
    for disc in discount_sweep_arr:
        df_d_std, _ = simulate_rapid_profit_scalper(df_nested, initial_target_pct=DEFAULT_INITIAL_TARGET_PCT, purple_discount_pct=disc, enable_halving=True, fee_rate=FEE_RATE_PER_SIDE_STD)
        df_d_zero, _ = simulate_rapid_profit_scalper(df_nested, initial_target_pct=DEFAULT_INITIAL_TARGET_PCT, purple_discount_pct=disc, enable_halving=True, fee_rate=FEE_RATE_ZERO)

        w = df_d_std[df_d_std['net_ret_pct'] > 0]['net_ret_pct']
        l = df_d_std[df_d_std['net_ret_pct'] <= 0]['net_ret_pct']
        n_t = len(df_d_std)
        wr = (len(w) / n_t) * 100.0 if n_t > 0 else 0.0
        w_avg = w.mean() if len(w) > 0 else 0.0
        l_avg = l.mean() if len(l) > 0 else 0.0
        po = abs(w_avg / l_avg) if l_avg != 0 else np.nan
        pf = (w.sum() / abs(l.sum())) if len(l) > 0 and l.sum() != 0 else np.nan
        cum_std = (1.0 + df_d_std['net_ret_pct'] / 100.0).cumprod().iloc[-1] if n_t > 0 else 1.0
        cum_zero = (1.0 + df_d_zero['net_ret_pct'] / 100.0).cumprod().iloc[-1] if n_t > 0 else 1.0

        disc_records.append({
            'discount_pct': disc * 100.0, 'trades': n_t, 'win_rate': wr,
            'avg_win': w_avg, 'avg_loss': l_avg, 'payoff': po, 'pf': pf,
            'final_equity_std': STARTING_CAPITAL * cum_std, 'final_equity_zero': STARTING_CAPITAL * cum_zero
        })

    sweep_disc = pd.DataFrame(disc_records)

    # --------------------------------------------------------------------------
    # GLOBAL OPTIMAL SIMULATION (Using Default or Peak Equity Pair)
    # --------------------------------------------------------------------------
    best_target_row = sweep_targets.loc[sweep_targets['final_equity_std'].idxmax()]
    best_target_val = best_target_row['target_pct'] / 100.0

    best_disc_row = sweep_disc.loc[sweep_disc['final_equity_std'].idxmax()]
    best_disc_val = best_disc_row['discount_pct'] / 100.0

    df_opt, opt_diag = simulate_rapid_profit_scalper(df_nested, initial_target_pct=best_target_val, purple_discount_pct=best_disc_val, enable_halving=True, fee_rate=FEE_RATE_PER_SIDE_STD)

    # Print Terminal Tables
    print("\n" + "=" * 120)
    print(f"AUDIT 1: INITIAL TARGET PROFIT SWEEP (+X% HALVING) | {ASSET_PAIR} 1H (Fixed -{DEFAULT_PURPLE_DISCOUNT_PCT*100:.1f}% Purple Disc)")
    print("=" * 120)
    print(f"  {'Initial Target':<16} | {'Trades':<7} | {'Win Rate':<10} | {'Avg Win':<10} | {'Avg Loss':<10} | {'Payoff':<8} | {'PF':<6} | {'Equity (0.038%)':<17} | {'Equity (0.000%)'}")
    print("  " + "-" * 118)
    for _, row in sweep_targets.iterrows():
        print(f"  + {row['target_pct']:>4.1f}%          | {int(row['trades']):<7,} | {row['win_rate']:>7.1f}%  | {row['avg_win']:>+7.2f}%   | {row['avg_loss']:>+7.2f}%   | {row['payoff']:>6.2f}x  | {row['pf']:>4.2f} | ${row['final_equity_std']:>15,.0f} | ${row['final_equity_zero']:>15,.0f}")

    print("\n" + "=" * 120)
    print(f"AUDIT 2: PURPLE LIMIT DISCOUNT SWEEP (N% BELOW TRIGGER) | {ASSET_PAIR} 1H (Fixed +{DEFAULT_INITIAL_TARGET_PCT*100:.1f}% Target)")
    print("=" * 120)
    print(f"  {'Purple Discount':<16} | {'Trades':<7} | {'Win Rate':<10} | {'Avg Win':<10} | {'Avg Loss':<10} | {'Payoff':<8} | {'PF':<6} | {'Equity (0.038%)':<17} | {'Equity (0.000%)'}")
    print("  " + "-" * 118)
    for _, row in sweep_disc.iterrows():
        print(f"  - {row['discount_pct']:>4.1f}%          | {int(row['trades']):<7,} | {row['win_rate']:>7.1f}%  | {row['avg_win']:>+7.2f}%   | {row['avg_loss']:>+7.2f}%   | {row['payoff']:>6.2f}x  | {row['pf']:>4.2f} | ${row['final_equity_std']:>15,.0f} | ${row['final_equity_zero']:>15,.0f}")

    w_run = df_runner[df_runner['net_ret_pct'] > 0]['net_ret_pct']
    l_run = df_runner[df_runner['net_ret_pct'] <= 0]['net_ret_pct']
    pf_run = (w_run.sum() / abs(l_run.sum())) if len(l_run) > 0 and l_run.sum() != 0 else np.nan
    eq_run = STARTING_CAPITAL * (1.0 + df_runner['net_ret_pct'] / 100.0).cumprod().iloc[-1]
    print("  " + "-" * 118)
    print(f"  {'Pure Macro Runner':<16} | {len(df_runner):<7,} | {len(w_run)/len(df_runner)*100:>7.1f}%  | {w_run.mean():>+7.2f}%   | {l_run.mean():>+7.2f}%   | {abs(w_run.mean()/l_run.mean()):>6.2f}x  | {pf_run:>4.2f} | ${eq_run:>15,.0f} | ${eq_run:>15,.0f}")
    print("=" * 120 + "\n")

    plot_master_cross_asset_dashboard(df_nested, df_opt, df_runner, sweep_targets, sweep_disc, best_target_val, best_disc_val, ASSET_PAIR)


if __name__ == "__main__":
    run_cross_asset_evaluation()
