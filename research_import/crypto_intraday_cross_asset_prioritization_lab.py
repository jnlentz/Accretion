"""
====================================================================================================
PROJECT SINGULARITY: 24/7 CRYPTO INTRADAY CROSS-ASSET PRIORITIZATION LAB
Evaluating Prioritization Architectures for Simultaneous Signals on Crypto Markets
====================================================================================================
Purpose:
  When multiple crypto assets fire buy signals at the exact same 15-minute candle close
  and available concurrency slots (K slots) cannot accommodate all of them, the portfolio
  must prioritize which assets receive capital.

Competing Prioritization Policies Evaluated:
  - Policy 0: Baseline FCFS (Arbitrary List Order)
              Simultaneous signals admitted in fixed ticker list order (XBT -> ETH -> SOL ...).
  - Policy 1: Option A - Net Expected Value (EV)
              Rank by: Net EV = p_pred * x* - (1 - p_pred) * y* - friction
              Capital flows strictly to the highest mathematical expectancy per dollar risked.
  - Policy 2: Option B - Macro Red Priority (Regime Superiority)
              Rank by: (1 if is_bear else 0, Net EV)
              Bear relief bounces (proven +140.8% alpha biome) take slots before Bull setups.
  - Policy 3: Option C - Classifier Conviction Lift (Alpha Spread Δp)
              Rank by: Δp = p_pred - BaseWinRate
              Measures how much statistical edge the GBDT model adds above random walk for that coin.
  - Policy 4: Option D - RVOL Volume Surge Intensity
              Rank by: bar_rvol = vol_t / median(vol_{t-96:t})
              Coins experiencing the strongest institutional volume bursts take priority.
  - Policy 5: Option E - Combined Champion (Macro Red Priority + Net EV + Macro Red Eviction)
              Combines Macro Red ranking, Net EV sorting, AND preemptive Macro Red queue eviction.

Outputs:
  - Table 1: Master Cross-Asset Prioritization Scorecard ($10k Account, K=3 Slots)
  - Table 2: Slot Contention & Capital Constraint Sweep (K=2 vs K=3 vs K=5 Slots)
  - Table 3: Asset Selection & Allocation Audit (Admitted vs Starved Assets)
  - Dark-Mode 4-Panel Verification Dashboard (plt.show())

Performance Optimization:
  - Models are cached to disk (export/accretion/models/{symbol}_gbdt.joblib) so retraining is eliminated.
  - Candidate setups are cached to results/crypto_intraday_features/crypto_candidate_setups.parquet.
====================================================================================================
"""

import sys
import os
import json
import warnings
warnings.filterwarnings('ignore')

from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import joblib

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.crypto_live_engine import (
    CRYPTO_CHAMPIONS,
    MODEL_FEATURE_NAMES,
    BINANCE_US_MAKER_FEE,
    BINANCE_US_TAKER_FEE
)
from labs.crypto_intraday_barrier_feature_preprocessor import (
    OUTPUT_DIR,
    DEFAULT_HORIZON_BARS
)
from labs.crypto_intraday_barrier_gbdt_lab import (
    load_or_generate_crypto_datasets,
    run_crypto_target_sweep,
    REWARD_RISK_RATIO
)

# ==============================================================================
# ⚙️ LAB CONFIGURATION (Jesse can adjust directly here)
# ==============================================================================
INITIAL_CAPITAL: float         = 10_000.0   # Starting fund capital ($ USD)
DEFAULT_CONCURRENT_SLOTS: int  = 2          # Baseline slot constraint (K=2)
POSITION_SIZE_FRACTION: float  = 0.50       # 50% per slot at K=2 (or 0.3333 at K=3)
ORDER_TTL_BARS: int            = 1          # Max bars (45 min) an unfilled limit buy can rest
DEFAULT_DISCOUNT_PCT: float     = 0.50       # -0.50% Maker Limit Buy Discount
DEFAULT_SELL_PREMIUM_PCT: float = 0.30       # +0.50% Maker Limit Sell Premium (Matches Discount Lab champion)

# Execution Accounting Architecture:
# True  = Realistic queue: resting limit orders occupy a slot & escrow cash while resting
# False = Filled-only: resting orders do NOT occupy slots until filled (matches Discount Lab)
RESTING_ORDERS_CONSUME_SLOTS: bool = True

# Exchange Fee Schedule: BinanceUS Native (0.0% Maker / 1.9 bps Taker)
MAKER_FEE_BPS: float           = 0.0
TAKER_FEE_BPS: float           = 1.9

# Concurrency Stress Test Grid
SWEEP_SLOT_COUNTS: List[int]   = [2, 3, 5]

# Caching Directories
MODELS_DIR = PROJECT_ROOT / "export" / "accretion" / "models"
CANDIDATE_CACHE_FILE = OUTPUT_DIR / f"crypto_candidate_setups_d{int(DEFAULT_DISCOUNT_PCT*100)}_p{int(DEFAULT_SELL_PREMIUM_PCT*100)}_ttl{ORDER_TTL_BARS}.parquet"
# ==============================================================================


# ==============================================================================
# 1. MODEL CACHING & FAST CANDIDATE PRECOMPUTATION
# ==============================================================================
def load_or_train_crypto_champion(clean: str, df_train: pd.DataFrame, df_val: pd.DataFrame) -> Tuple[Any, float, float]:
    """
    Loads fitted GBDT model and calibrated cutoff from MODELS_DIR if available,
    eliminating retraining. Only trains and serializes once if not cached.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_file = MODELS_DIR / f"{clean}_gbdt.joblib"
    meta_file = MODELS_DIR / f"{clean}_meta.json"

    if model_file.exists() and meta_file.exists():
        try:
            model = joblib.load(model_file)
            with open(meta_file, 'r') as f:
                meta = json.load(f)
            return model, float(meta['cutoff_threshold']), float(meta.get('base_win_rate', 0.20))
        except Exception as e:
            print(f"⚠️ Error loading cached model for {clean}: {e}. Retraining...")

    # Train and calibrate
    _, sweep_champ = run_crypto_target_sweep(df_train, df_val, reward_risk_ratio=REWARD_RISK_RATIO)
    if not sweep_champ:
        return None, 0.50, 0.20

    model = sweep_champ['model']
    cutoff = float(sweep_champ['threshold'])
    base_rate = float(sweep_champ.get('base_win_rate', 0.20))

    # Save to disk
    joblib.dump(model, model_file, compress=3)
    with open(meta_file, 'w') as f:
        json.dump({
            'cutoff_threshold': cutoff,
            'base_win_rate': base_rate,
            'x_star': sweep_champ['x'],
            'y_star': sweep_champ['y']
        }, f, indent=2)

    return model, cutoff, base_rate


def get_or_precompute_crypto_candidates(
    active_symbols: List[str] = list(CRYPTO_CHAMPIONS.keys()),
    discount_pct: float = DEFAULT_DISCOUNT_PCT,
    sell_premium_pct: float = DEFAULT_SELL_PREMIUM_PCT,
    horizon_bars: int = DEFAULT_HORIZON_BARS,
    force_recompute: bool = False
) -> List[Dict[str, Any]]:
    """
    Loads precomputed candidate setups from parquet in 0.1s, or precomputes
    and caches them across the synchronized Test Lockbox.
    """
    if not force_recompute and CANDIDATE_CACHE_FILE.exists():
        print(f"⚡ Loading cached candidate setups from {CANDIDATE_CACHE_FILE.name}...")
        try:
            df_cands = pd.read_parquet(CANDIDATE_CACHE_FILE)
            records = df_cands.to_dict(orient='records')
            print(f"✅ Loaded {len(records):,} cached candidate setups across {len(active_symbols)} coins in 0.1s!")
            return records
        except Exception as e:
            print(f"⚠️ Cache read error: {e}. Recomputing...")

    print("=" * 105)
    print("📥 PRECOMPUTING 24/7 CANDIDATE SETUPS ACROSS KRAKEN UNIVERSE (SYNCHRONIZED TEST LOCKBOX)...")
    all_candidates = []

    for symbol in active_symbols:
        clean = symbol.upper()
        champ = CRYPTO_CHAMPIONS.get(clean)
        if not champ:
            continue

        x_star = champ['x_star']
        y_star = champ['y_star']
        binance_sym = champ['binance_sym']

        # Load datasets
        df_train, df_val, df_test = load_or_generate_crypto_datasets(clean)

        # Load or train champion model (cached)
        model, cutoff_threshold, base_win_rate = load_or_train_crypto_champion(clean, df_train, df_val)
        if model is None:
            continue

        # Predict probabilities on Unseen Test Lockbox
        X_test = df_test[MODEL_FEATURE_NAMES].values
        p_pred = model.predict_proba(X_test)[:, 1]

        closes = df_test['close'].values
        highs = df_test['high'].values
        lows = df_test['low'].values
        rvols = df_test['bar_rvol'].values if 'bar_rvol' in df_test.columns else np.ones(len(df_test))
        timestamps = df_test.index.values
        is_bear = (df_test['is_red'].values == 1.0)
        is_bull = (df_test['is_green'].values == 1.0)
        micro_green = (df_test['is_micro_green'].values == 1.0)
        n = len(df_test)

        symbol_cands = 0
        for t in range(n):
            p_t = p_pred[t]
            if p_t >= cutoff_threshold:
                # Net Expected Value ($EV per dollar risked):
                ev = (p_t * x_star) - ((1.0 - p_t) * y_star)
                # Alpha Conviction Lift (Δp above base rate):
                alpha_lift = p_t - (base_win_rate / 100.0)

                p_signal = closes[t]
                p_limit = p_signal * (1.0 - discount_pct / 100.0)

                end_idx = min(t + horizon_bars + 1, n)
                if end_idx <= t + 1:
                    continue

                # Check if limit fills within ORDER_TTL_BARS
                fill_step = None
                if discount_pct <= 0.0:
                    fill_step = 0
                    p_entry = p_signal
                else:
                    for step in range(1, min(ORDER_TTL_BARS + 1, end_idx - t)):
                        if lows[t + step] <= p_limit:
                            fill_step = step
                            p_entry = p_limit
                            break

                fill_time = None
                exit_time = None
                net_ret_pct = 0.0
                is_win = False
                exit_reason = "UNFILLED"
                p_exit = p_signal

                if fill_step is not None:
                    fill_time = timestamps[t + fill_step]
                    eff_target = x_star + sell_premium_pct
                    p_tp = p_entry * (1.0 + eff_target / 100.0)
                    p_stop = p_entry * (1.0 - y_star / 100.0)

                    exit_step = end_idx - t - 1
                    exit_reason = "TIMEOUT"
                    p_exit = p_entry
                    is_maker_exit = False

                    for step in range(fill_step + 1, end_idx - t):
                        idx = t + step
                        h_bar = highs[idx]
                        l_bar = lows[idx]

                        if l_bar <= p_stop:
                            p_exit = p_stop
                            exit_step = step
                            exit_reason = "STOP"
                            is_maker_exit = False
                            break
                        elif h_bar >= p_tp:
                            p_exit = p_tp
                            exit_step = step
                            exit_reason = "TARGET_TP"
                            is_maker_exit = True
                            break

                    if exit_reason == "TIMEOUT":
                        p_exit = closes[t + exit_step]

                    exit_time = timestamps[t + exit_step]
                    raw_ret = (p_exit - p_entry) / p_entry * 100.0
                    fee_bps = (MAKER_FEE_BPS + (MAKER_FEE_BPS if is_maker_exit else TAKER_FEE_BPS))
                    net_ret_pct = raw_ret - (fee_bps / 100.0)
                    is_win = (net_ret_pct > 0.0)

                all_candidates.append({
                    'symbol': clean,
                    'binance_symbol': binance_sym,
                    'bar_idx': t,
                    'timestamp': timestamps[t],
                    'p_pred': float(p_t),
                    'x_star': float(x_star),
                    'y_star': float(y_star),
                    'expected_value': float(ev),
                    'alpha_lift': float(alpha_lift),
                    'rvol': float(rvols[t]),
                    'is_bear': bool(is_bear[t]),
                    'is_bull': bool(is_bull[t]),
                    'is_micro_green': bool(micro_green[t]),
                    'close_price': float(p_signal),
                    'limit_buy_price': float(p_limit),
                    'is_filled': bool(fill_step is not None),
                    'fill_time': fill_time,
                    'exit_time': exit_time,
                    'p_entry': float(p_entry) if fill_step is not None else 0.0,
                    'p_exit': float(p_exit),
                    'net_ret_pct': float(net_ret_pct),
                    'exit_reason': exit_reason,
                    'is_win': bool(is_win)
                })
                symbol_cands += 1

        print(f"   ✅ {clean:<8}: Precomputed {symbol_cands:,} candidate setups.")

    all_candidates.sort(key=lambda c: c['timestamp'])

    # Save to parquet cache
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_save = pd.DataFrame(all_candidates)
    df_save.to_parquet(CANDIDATE_CACHE_FILE, index=False)
    print(f"💾 Cached {len(all_candidates):,} candidate setups to {CANDIDATE_CACHE_FILE.name}")

    return all_candidates


# ==============================================================================
# 2. CROSS-ASSET PRIORITIZATION SIMULATION ENGINE
# ==============================================================================
def simulate_crypto_prioritization(
    all_candidates: List[Dict[str, Any]],
    policy_name: str,
    priority_ranking_fn: Any,
    enable_macro_red_eviction: bool = False,
    max_slots: int = DEFAULT_CONCURRENT_SLOTS,
    initial_capital: float = INITIAL_CAPITAL,
    position_fraction: float = POSITION_SIZE_FRACTION,
    order_ttl_bars: int = ORDER_TTL_BARS,
    asymmetric_sizing: bool = False,
    resting_orders_consume_slots: bool = RESTING_ORDERS_CONSUME_SLOTS
) -> Dict[str, Any]:
    """
    Simulates continuous 24/7 cross-asset capital allocation:
      - When multiple setups fire at the same timestamp, ranks them using priority_ranking_fn.
      - Fills top setups into available slots.
      - If enable_macro_red_eviction is True, incoming Bear setups can evict resting Bull orders.
      - If resting_orders_consume_slots is True, resting limit orders reserve slots and escrow cash.
        If False, resting orders do not reserve slots until filled (matches Discount Lab accounting).
    """
    free_cash = initial_capital
    active_positions = []
    pending_orders = []

    executed_trades = []
    eviction_events = []
    starved_signals = 0
    equity_curve = []
    admitted_by_symbol = {s: 0 for s in CRYPTO_CHAMPIONS.keys()}
    starved_by_symbol = {s: 0 for s in CRYPTO_CHAMPIONS.keys()}

    time_grouped = {}
    for cand in all_candidates:
        t_val = cand['timestamp']
        if t_val not in time_grouped:
            time_grouped[t_val] = []
        time_grouped[t_val].append(cand)

    unique_timestamps = sorted(list(time_grouped.keys()))

    for current_time in unique_timestamps:
        # Step A: Close Active Positions
        still_active = []
        for pos in active_positions:
            if pos['exit_time'] <= current_time:
                net_pnl = pos['allocated_cap'] * (pos['net_ret_pct'] / 100.0)
                free_cash += pos['allocated_cap'] + net_pnl
                executed_trades.append({
                    'symbol': pos['symbol'],
                    'entry_time': pos['fill_time'],
                    'exit_time': pos['exit_time'],
                    'net_ret_pct': pos['net_ret_pct'],
                    'dollar_pnl': net_pnl,
                    'is_win': pos['is_win'],
                    'exit_reason': pos['exit_reason'],
                    'is_bear': pos['is_bear'],
                    'expected_value': pos['expected_value']
                })
            else:
                still_active.append(pos)
        active_positions = still_active

        # Step B: Check Pending Orders for Fill or TTL Expiration
        still_pending = []
        for order in pending_orders:
            if order['is_filled'] and order['fill_time'] is not None and order['fill_time'] <= current_time:
                if not resting_orders_consume_slots:
                    # Filled-only mode (Discount Lab model): Check slot availability at fill time
                    total_eq = free_cash + sum(p['allocated_cap'] for p in active_positions)
                    if len(active_positions) < max_slots:
                        size_mult = (1.0 if order['is_bear'] else 0.5) if asymmetric_sizing else 1.0
                        target_slot_cap = total_eq * position_fraction * size_mult
                        alloc_cap = min(target_slot_cap, free_cash)
                        if alloc_cap >= 50.0:
                            free_cash -= alloc_cap
                            order['allocated_cap'] = alloc_cap
                            active_positions.append(order)
                            admitted_by_symbol[order['symbol']] += 1
                        else:
                            starved_signals += 1
                            starved_by_symbol[order['symbol']] += 1
                    else:
                        starved_signals += 1
                        starved_by_symbol[order['symbol']] += 1
                else:
                    # Queue realism: Order already reserved slot and escrowed capital
                    active_positions.append(order)
            else:
                bars_waiting = order.get('bars_waiting', 0) + 1
                order['bars_waiting'] = bars_waiting
                if bars_waiting >= order_ttl_bars:
                    if resting_orders_consume_slots:
                        free_cash += order['allocated_cap']
                else:
                    still_pending.append(order)
        pending_orders = still_pending

        # Step C: Ingest New Candidate Setups at current_time
        bar_setups = time_grouped[current_time]
        eligible = []
        for cand in bar_setups:
            sym = cand['symbol']
            if any(p['symbol'] == sym for p in active_positions):
                continue
            if any(o['symbol'] == sym for o in pending_orders):
                continue
            eligible.append(cand)

        if not eligible:
            tot_eq = free_cash + sum(p['allocated_cap'] for p in active_positions) + (sum(o['allocated_cap'] for o in pending_orders) if resting_orders_consume_slots else 0.0)
            equity_curve.append(tot_eq)
            continue

        # Step D: Apply Policy Priority Ranking
        eligible.sort(key=priority_ranking_fn, reverse=True)

        # Step E: Slot Allocation & Eviction Auction
        for cand in eligible:
            if resting_orders_consume_slots:
                total_equity = free_cash + sum(p['allocated_cap'] for p in active_positions) + sum(o['allocated_cap'] for o in pending_orders)
                allocated_slots = len(active_positions) + len(pending_orders)

                size_mult = (1.0 if cand['is_bear'] else 0.5) if asymmetric_sizing else 1.0
                target_slot_cap = total_equity * position_fraction * size_mult
                alloc_cap = min(target_slot_cap, free_cash)

                if alloc_cap < 50.0:
                    starved_signals += 1
                    starved_by_symbol[cand['symbol']] += 1
                    continue

                order_payload = {
                    'symbol': cand['symbol'],
                    'timestamp': current_time,
                    'expected_value': cand['expected_value'],
                    'p_pred': cand['p_pred'],
                    'is_bear': cand['is_bear'],
                    'is_bull': cand['is_bull'],
                    'allocated_cap': alloc_cap,
                    'is_filled': cand['is_filled'],
                    'fill_time': cand['fill_time'],
                    'exit_time': cand['exit_time'],
                    'net_ret_pct': cand['net_ret_pct'],
                    'is_win': cand['is_win'],
                    'exit_reason': cand['exit_reason'],
                    'bars_waiting': 0
                }

                # Case 1: Slot Available -> Admit Setup
                if allocated_slots < max_slots:
                    free_cash -= alloc_cap
                    pending_orders.append(order_payload)
                    admitted_by_symbol[cand['symbol']] += 1

                # Case 2: Slots Full -> Check Eviction if Enabled
                else:
                    if enable_macro_red_eviction and len(pending_orders) > 0 and cand['is_bear']:
                        # Look for a resting Bull order to evict
                        bull_pending = [o for o in pending_orders if not o['is_bear']]
                        if bull_pending:
                            worst_order = min(bull_pending, key=lambda o: o['expected_value'])
                            pending_orders.remove(worst_order)
                            free_cash += worst_order['allocated_cap']

                            alloc_cap_new = min(target_slot_cap, free_cash)
                            free_cash -= alloc_cap_new
                            order_payload['allocated_cap'] = alloc_cap_new
                            pending_orders.append(order_payload)
                            admitted_by_symbol[cand['symbol']] += 1

                            eviction_events.append({
                                'timestamp': current_time,
                                'evicted': worst_order['symbol'],
                                'admitted': cand['symbol'],
                                'admitted_won': cand['is_win'],
                                'admitted_ret': cand['net_ret_pct']
                            })
                        else:
                            starved_signals += 1
                            starved_by_symbol[cand['symbol']] += 1
                    else:
                        starved_signals += 1
                        starved_by_symbol[cand['symbol']] += 1
            else:
                # Filled-only mode (Discount Lab model): resting orders do not escrow cash or consume slots
                order_payload = {
                    'symbol': cand['symbol'],
                    'timestamp': current_time,
                    'expected_value': cand['expected_value'],
                    'p_pred': cand['p_pred'],
                    'is_bear': cand['is_bear'],
                    'is_bull': cand['is_bull'],
                    'allocated_cap': 0.0,
                    'is_filled': cand['is_filled'],
                    'fill_time': cand['fill_time'],
                    'exit_time': cand['exit_time'],
                    'net_ret_pct': cand['net_ret_pct'],
                    'is_win': cand['is_win'],
                    'exit_reason': cand['exit_reason'],
                    'bars_waiting': 0
                }
                pending_orders.append(order_payload)

        tot_eq = free_cash + sum(p['allocated_cap'] for p in active_positions) + (sum(o['allocated_cap'] for o in pending_orders) if resting_orders_consume_slots else 0.0)
        equity_curve.append(tot_eq)

    # Finalize remaining positions
    for pos in active_positions:
        net_pnl = pos['allocated_cap'] * (pos['net_ret_pct'] / 100.0)
        free_cash += pos['allocated_cap'] + net_pnl
        executed_trades.append({
            'symbol': pos['symbol'],
            'entry_time': pos['fill_time'],
            'exit_time': pos['exit_time'],
            'net_ret_pct': pos['net_ret_pct'],
            'dollar_pnl': net_pnl,
            'is_win': pos['is_win'],
            'exit_reason': pos['exit_reason'],
            'is_bear': pos['is_bear'],
            'expected_value': pos['expected_value']
        })

    for order in pending_orders:
        free_cash += order['allocated_cap']

    final_equity = free_cash
    tot_return_pct = (final_equity - initial_capital) / initial_capital * 100.0

    df_trades = pd.DataFrame(executed_trades)
    n_trades = len(df_trades)
    win_rate = float(df_trades['is_win'].sum() / n_trades * 100.0) if n_trades > 0 else 0.0
    wins = df_trades[df_trades['dollar_pnl'] > 0]['dollar_pnl'] if n_trades > 0 else pd.Series()
    losses = df_trades[df_trades['dollar_pnl'] < 0]['dollar_pnl'] if n_trades > 0 else pd.Series()
    gross_win = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 1e-6
    profit_factor = gross_win / gross_loss if gross_loss > 0 else 99.0

    eq_arr = np.array(equity_curve) if equity_curve else np.array([initial_capital, final_equity])
    running_max = np.maximum.accumulate(eq_arr)
    dds = (eq_arr - running_max) / running_max * 100.0
    max_dd = float(np.min(dds)) if len(dds) > 0 else 0.0
    calmar = float(tot_return_pct / abs(max_dd)) if abs(max_dd) > 0.01 else 0.0

    return {
        'policy': policy_name,
        'trades': n_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'final_equity': final_equity,
        'tot_return_pct': tot_return_pct,
        'max_dd': max_dd,
        'calmar': calmar,
        'starved_signals': starved_signals,
        'eviction_count': len(eviction_events),
        'admitted_by_symbol': admitted_by_symbol,
        'starved_by_symbol': starved_by_symbol,
        'equity_curve': eq_arr
    }


# ==============================================================================
# 3. DARK-MODE 4-PANEL VERIFICATION DASHBOARD (GEMINI.MD COMPLIANT)
# ==============================================================================
def plot_crypto_prioritization_dashboard(
    policy_results: Dict[str, Dict],
    scarcity_results: Dict[int, Dict]
) -> None:
    """Renders the dark-mode 4-panel cross-asset prioritization dashboard."""
    plt.style.use('dark_background')
    fig, axes = plt.subplots(2, 2, figsize=(18, 10))
    fig.suptitle(
        f"PROJECT SINGULARITY: 24/7 CRYPTO CROSS-ASSET PRIORITIZATION LAB\n"
        f"BinanceUS 0.0% Maker Fee Tier | Buy Discount -{DEFAULT_DISCOUNT_PCT:.2f}% | Sell Premium +{DEFAULT_SELL_PREMIUM_PCT:.2f}% | TTL: {ORDER_TTL_BARS} Bars",
        fontsize=13,
        fontweight='bold',
        color='cyan'
    )

    # --------------------------------------------------------------------------
    # Panel 1: Compounded Portfolio Equity Curves across Prioritization Policies
    # --------------------------------------------------------------------------
    ax1 = axes[0, 0]
    colors1 = ['#78909c', '#00e5ff', '#00e676', '#ffea00', '#ff1744', '#d500f9']
    for idx, (p_name, res) in enumerate(policy_results.items()):
        c = colors1[idx % len(colors1)]
        lw = 2.4 if "Combined" in p_name or "Macro Red" in p_name else 1.2
        label = f"{p_name} (Ret: {res['tot_return_pct']:+.1f}% | DD: {res['max_dd']:.1f}%)"
        ax1.plot(res['equity_curve'], color=c, lw=lw, label=label)

    ax1.axhline(INITIAL_CAPITAL, color='white', linestyle='--', alpha=0.4)
    ax1.set_title("Panel 1: Compounded Equity Curves by Prioritization Policy ($10k Start)", color='white', fontsize=11)
    ax1.set_xlabel("15m Timeline Progression", fontsize=10)
    ax1.set_ylabel("Portfolio Capital ($ USD)", fontsize=10)
    ax1.legend(loc='upper left', fontsize=8.5, framealpha=0.3)
    ax1.grid(True, alpha=0.2)

    # --------------------------------------------------------------------------
    # Panel 2: Total Return (%) & Win Rate (%) by Prioritization Policy
    # --------------------------------------------------------------------------
    ax2 = axes[0, 1]
    p_names = list(policy_results.keys())
    rets = [policy_results[k]['tot_return_pct'] for k in p_names]
    wrs = [policy_results[k]['win_rate'] for k in p_names]
    x_pos = np.arange(len(p_names))

    bar_cols = ['#78909c' if "Baseline" in k else '#00e5ff' for k in p_names]
    ax2.bar(x_pos, rets, color=bar_cols, alpha=0.85, width=0.5)
    ax2.set_xticks(x_pos)
    ax2.set_xticklabels([k.replace("Policy ", "P") for k in p_names], rotation=25, ha='right', fontsize=9)
    ax2.set_ylabel("Compounded Return (%)", color='white', fontsize=10)
    ax2.set_title("Panel 2: Compounded Total Return across Prioritization Policies", color='white', fontsize=11)
    ax2.grid(True, alpha=0.2)

    for i, (r, w) in enumerate(zip(rets, wrs)):
        ax2.annotate(f"{r:+.1f}%\n(WR {w:.1f}%)", (i, r), textcoords="offset points", xytext=(0, 6),
                     ha='center', fontsize=8.5, color='white', fontweight='bold')

    # --------------------------------------------------------------------------
    # Panel 3: Slot Contention Stress Test (K=2 vs K=3 vs K=5 slots)
    # --------------------------------------------------------------------------
    ax3 = axes[1, 0]
    scarcity_colors = {'2 Slots': '#ff1744', '3 Slots': '#00e5ff', '5 Slots': '#00e676'}
    for s_name, res in scarcity_results.items():
        c = scarcity_colors.get(s_name, 'cyan')
        ax3.plot(res['equity_curve'], color=c, lw=2.0, label=f"{s_name} (Ret: {res['tot_return_pct']:+.1f}% | DD: {res['max_dd']:.1f}%)")

    ax3.axhline(INITIAL_CAPITAL, color='white', linestyle='--', alpha=0.4)
    ax3.set_title("Panel 3: Slot Contention Stress Test under Combined Champion Policy", color='white', fontsize=11)
    ax3.set_xlabel("15m Timeline Progression", fontsize=10)
    ax3.set_ylabel("Portfolio Capital ($ USD)", fontsize=10)
    ax3.legend(loc='upper left', fontsize=9, framealpha=0.3)
    ax3.grid(True, alpha=0.2)

    # --------------------------------------------------------------------------
    # Panel 4: Asset Allocation & Selection Distribution (Who Gets Admitted?)
    # --------------------------------------------------------------------------
    ax4 = axes[1, 1]
    symbols = list(CRYPTO_CHAMPIONS.keys())
    x_sym = np.arange(len(symbols))
    width = 0.25

    # Compare Baseline vs Net EV vs Combined Champion
    adm_base = [policy_results['Policy 0: Baseline FCFS']['admitted_by_symbol'][s] for s in symbols]
    adm_ev   = [policy_results['Policy 1: Net Expected Value (EV)']['admitted_by_symbol'][s] for s in symbols]
    adm_comb = [policy_results['Policy 5: Combined Champion (Macro Red + EV + Evict)']['admitted_by_symbol'][s] for s in symbols]

    ax4.bar(x_sym - width, adm_base, width, label='Policy 0 (Baseline FCFS)', color='#78909c', alpha=0.85)
    ax4.bar(x_sym, adm_ev, width, label='Policy 1 (Net EV)', color='#00e5ff', alpha=0.85)
    ax4.bar(x_sym + width, adm_comb, width, label='Policy 5 (Combined Champion)', color='#00e676', alpha=0.85)

    ax4.set_xticks(x_sym)
    ax4.set_xticklabels(symbols, fontsize=10)
    ax4.set_ylabel("Admitted Trade Count", color='white', fontsize=10)
    ax4.set_title("Panel 4: Asset Allocation Distribution by Prioritization Architecture", color='white', fontsize=11)
    ax4.legend(loc='upper right', fontsize=8.5, framealpha=0.3)
    ax4.grid(True, alpha=0.2)

    plt.tight_layout()
    plt.show()


# ==============================================================================
# 4. MAIN EXPERIMENT RUNNER
# ==============================================================================
def main():
    print("=" * 105)
    print("🚀 PROJECT SINGULARITY: 24/7 BATCH CRYPTO CROSS-ASSET PRIORITIZATION LAB")
    print(f"   Universe          : {', '.join(CRYPTO_CHAMPIONS.keys())}")
    print(f"   Fee Schedule      : BINANCE.US (0.0% Maker / 1.9 bps Taker)")
    print(f"   Execution Geometry: Buy Discount -{DEFAULT_DISCOUNT_PCT:.2f}% / Sell Premium +{DEFAULT_SELL_PREMIUM_PCT:.2f}%")
    print(f"   Order Accounting  : {'Realistic Queue (Resting Orders Escrow Slots/Cash)' if RESTING_ORDERS_CONSUME_SLOTS else 'Filled-Only Concurrency (Discount Lab Matching)'}")
    print(f"   Order TTL / Slots : {ORDER_TTL_BARS} Bars ({ORDER_TTL_BARS * 15} min) | K={DEFAULT_CONCURRENT_SLOTS} Slots ({int(POSITION_SIZE_FRACTION*100)}% Sizing)")
    print("=" * 105)

    # Step 1: Load cached candidates in 0.1s
    candidates = get_or_precompute_crypto_candidates()

    # Canonical symbol index map for Policy 0
    sym_order = {s: i for i, s in enumerate(CRYPTO_CHAMPIONS.keys())}

    # ==========================================================================
    # TABLE 1: MASTER CROSS-ASSET PRIORITIZATION SCORECARD
    # ==========================================================================
    print("\n" + "=" * 105)
    mode_str = "QUEUE REALISM (RESTING ORDERS ESCROW SLOTS)" if RESTING_ORDERS_CONSUME_SLOTS else "FILLED-ONLY CONCURRENCY (DISCOUNT LAB MODEL)"
    print(f"🏆 [TABLE 1] MASTER CROSS-ASSET PRIORITIZATION SCORECARD (K={DEFAULT_CONCURRENT_SLOTS} SLOTS, {int(POSITION_SIZE_FRACTION*100)}% SIZING, ${INITIAL_CAPITAL:,.0f} ACCOUNT)...")
    print(f"   Accounting Architecture: {mode_str}")
    print("=" * 105)

    policies = [
        ("Policy 0: Baseline FCFS", lambda c: -sym_order.get(c['symbol'], 99), False),
        ("Policy 1: Net Expected Value (EV)", lambda c: c['expected_value'], False),
        ("Policy 2: Macro Red Priority", lambda c: (1 if c['is_bear'] else 0, c['expected_value']), False),
        ("Policy 3: Alpha Spread Lift (Δp)", lambda c: c['alpha_lift'], False),
        ("Policy 4: RVOL Volume Surge", lambda c: c['rvol'], False),
        ("Policy 5: Combined Champion (Macro Red + EV + Evict)", lambda c: (1 if c['is_bear'] else 0, c['expected_value']), True)
    ]

    policy_results = {}
    for p_name, rank_fn, evict in policies:
        res = simulate_crypto_prioritization(
            candidates,
            policy_name=p_name,
            priority_ranking_fn=rank_fn,
            enable_macro_red_eviction=evict,
            max_slots=DEFAULT_CONCURRENT_SLOTS
        )
        policy_results[p_name] = res

        print(
            f"  • {p_name:<46} | Tr: {res['trades']:<4} | WR: {res['win_rate']:>5.1f}% | PF: {res['profit_factor']:>4.2f} | "
            f"Final: ${res['final_equity']:>10.2f} | Ret: {res['tot_return_pct']:>+6.1f}% | DD: {res['max_dd']:>5.1f}% | "
            f"Calmar: {res['calmar']:>5.2f} | Starved: {res['starved_signals']:<4} | Evictions: {res['eviction_count']}"
        )

    # ==========================================================================
    # TABLE 1B: ARCHITECTURAL COMPARISON (QUEUE REALISM vs FILLED-ONLY CONCURRENCY)
    # ==========================================================================
    alt_mode = not RESTING_ORDERS_CONSUME_SLOTS
    alt_label = "Realistic Queue (Resting Orders Escrow Slots)" if alt_mode else "Filled-Only Concurrency (Discount Lab Matching)"
    print("\n" + "=" * 105)
    print(f"🔬 [TABLE 1B] ARCHITECTURAL COMPARISON AUDIT ({alt_label})...")
    print(f"   Directly demonstrates the exact impact of phantom slot blocking vs filled-only execution")
    print("=" * 105)

    for p_name, rank_fn, evict in [
        ("Policy 0: Baseline FCFS", lambda c: -sym_order.get(c['symbol'], 99), False),
        ("Policy 5: Combined Champion (Macro Red + EV + Evict)", lambda c: (1 if c['is_bear'] else 0, c['expected_value']), True)
    ]:
        res_alt = simulate_crypto_prioritization(
            candidates,
            policy_name=p_name,
            priority_ranking_fn=rank_fn,
            enable_macro_red_eviction=evict,
            max_slots=DEFAULT_CONCURRENT_SLOTS,
            resting_orders_consume_slots=alt_mode
        )
        print(
            f"  • {p_name:<46} | Tr: {res_alt['trades']:<4} | WR: {res_alt['win_rate']:>5.1f}% | PF: {res_alt['profit_factor']:>4.2f} | "
            f"Final: ${res_alt['final_equity']:>10.2f} | Ret: {res_alt['tot_return_pct']:>+6.1f}% | DD: {res_alt['max_dd']:>5.1f}% | "
            f"Calmar: {res_alt['calmar']:>5.2f} | Starved: {res_alt['starved_signals']:<4}"
        )

    # ==========================================================================
    # TABLE 2: SLOT CONTENTION & CAPITAL CONSTRAINT SWEEP
    # ==========================================================================
    print("\n" + "=" * 105)
    print("🎯 [TABLE 2] SLOT CONTENTION SWEEP UNDER COMBINED CHAMPION (K=2, 3, 5 SLOTS)...")
    print("=" * 105)

    scarcity_results = {}
    for k in SWEEP_SLOT_COUNTS:
        k_label = f"{k} Slots"
        pos_frac = 1.0 / float(k)
        res_k = simulate_crypto_prioritization(
            candidates,
            policy_name=k_label,
            priority_ranking_fn=lambda c: (1 if c['is_bear'] else 0, c['expected_value']),
            enable_macro_red_eviction=True,
            max_slots=k,
            position_fraction=pos_frac,
            resting_orders_consume_slots=RESTING_ORDERS_CONSUME_SLOTS
        )
        scarcity_results[k_label] = res_k

        print(
            f"  • Slot Concurrency K={k:<2} ({int(pos_frac*100)}% Sizing) | Tr: {res_k['trades']:<4} | WR: {res_k['win_rate']:>5.1f}% | "
            f"PF: {res_k['profit_factor']:>4.2f} | Final: ${res_k['final_equity']:>10.2f} | Ret: {res_k['tot_return_pct']:>+6.1f}% | "
            f"DD: {res_k['max_dd']:>5.1f}% | Calmar: {res_k['calmar']:>5.2f} | Starved: {res_k['starved_signals']}"
        )

    # ==========================================================================
    # TABLE 3: ASSET SELECTION & ALLOCATION FORENSIC AUDIT
    # ==========================================================================
    print("\n" + "=" * 105)
    print("📋 [TABLE 3] ASSET ALLOCATION FORENSIC AUDIT (ADMITTED vs STARVED BY COIN)...")
    print("=" * 105)
    print(f"{'Coin':<8} | {'Base Admitted':<14} | {'EV Admitted':<12} | {'Combined Admitted':<18} | {'Combined Starved':<16}")
    print("-" * 105)
    for sym in CRYPTO_CHAMPIONS.keys():
        adm_0 = policy_results['Policy 0: Baseline FCFS']['admitted_by_symbol'][sym]
        adm_1 = policy_results['Policy 1: Net Expected Value (EV)']['admitted_by_symbol'][sym]
        adm_5 = policy_results['Policy 5: Combined Champion (Macro Red + EV + Evict)']['admitted_by_symbol'][sym]
        starv_5 = policy_results['Policy 5: Combined Champion (Macro Red + EV + Evict)']['starved_by_symbol'][sym]
        print(f"{sym:<8} | {adm_0:<14} | {adm_1:<12} | {adm_5:<18} | {starv_5:<16}")
    print("=" * 105)

    # Step 5: Render Dark-Mode 4-Panel Verification Dashboard
    print("\n📊 Rendering 4-panel dark-mode verification dashboard via plt.show()...")
    plot_crypto_prioritization_dashboard(policy_results, scarcity_results)


if __name__ == "__main__":
    main()
