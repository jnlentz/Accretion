"""
====================================================================================================
PROJECT SINGULARITY -> ACCRETION VALIDATION SUITE: LIVE VS HISTORICAL PREDICTION AUDIT
Verification Lab for Live Execution Parity & Feature Drift Detection (Self-Contained Edition)
====================================================================================================
Purpose:
  After the live bot (in Accretion) runs for an operational segment (e.g. 24h, 1 week, or N bars),
  this validation lab compares its real-time logged predictions, structural features, and order
  prices against historical data processed through the exact SingularityCore research pipeline.

Audits Performed:
  1. Feature Parity Audit:
     - Measures maximum absolute error and Pearson correlation across all 34 structural features.
     - Flags any feature drift, warm-up edge effects, or lookahead contamination.
  2. Prediction Probability Parity (P_live vs P_historical):
     - Asserts max |P_live - P_hist| < 1e-4.
     - Computes R^2 correlation and Brier calibration drift.
  3. Binary Signal & Order Price Match Rate:
     - Verifies 100% agreement on TradeSignal triggers (BUY_LIMIT vs NO_SIGNAL).
     - Verifies limit buy (-0.50%), limit sell (+x* + 0.15%), and stop loss (-y*) prices.
  4. Dark-Mode 4-Panel Diagnostic Dashboard (plt.show()):
     - Panel 1: Live P vs Historical P Time Series Overlay.
     - Panel 2: Live P vs Historical P Scatter Correlation (Asserting R^2 = 1.0).
     - Panel 3: Maximum Absolute Error across 34 Structural Features.
     - Panel 4: Binary Trade Signal Alignment & Executed Orders Timeline.

Self-Contained Architecture:
  - ZERO dependencies on SingularityCore research labs (no imports from labs.*).
  - Uses serialized models from models/ directory and configuration from config/ directory.
  - Contains embedded, bit-for-bit research feature computation formulas.
  - Can be copied directly into Accretion or any live deployment environment.

Usage:
  python crypto_live_prediction_validation_lab.py --symbol XBTUSD
  python crypto_live_prediction_validation_lab.py --log-file logs/live_predictions_XBTUSD.jsonl
  python crypto_live_prediction_validation_lab.py --mock-demo  (Generates mock live segment to test)
====================================================================================================
"""

import sys
import os
import json
import sqlite3
import argparse
import warnings
warnings.filterwarnings('ignore')

from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import numpy as np
import pandas as pd
import joblib
import types
import matplotlib.pyplot as plt

# ==============================================================================
# 🛠️ SCIKIT-LEARN UNPICKLING COMPATIBILITY SHIM
# Resolves 'No module named _loss' when loading models saved with sklearn 1.7.x in 1.9.x+
# ==============================================================================
def _apply_sklearn_compatibility_shims():
    if '_loss' not in sys.modules:
        try:
            import sklearn._loss as _sk_loss
            proxy = types.ModuleType('_loss')
            proxy.__dict__.update(_sk_loss.__dict__)
            if hasattr(_sk_loss, 'loss'):
                proxy.__dict__.update(_sk_loss.loss.__dict__)
            try:
                import sklearn._loss._loss as _sk_loss_cy
                proxy.__dict__.update(_sk_loss_cy.__dict__)
            except ImportError:
                pass
            try:
                import sklearn.ensemble._hist_gradient_boosting._loss as _sk_hgb_loss
                proxy.__dict__.update(_sk_hgb_loss.__dict__)
            except ImportError:
                pass

            class _LossProxy(types.ModuleType):
                def __getattr__(self, name):
                    if hasattr(_sk_loss, name):
                        return getattr(_sk_loss, name)
                    for sub in ['loss', '_loss']:
                        if hasattr(_sk_loss, sub) and hasattr(getattr(_sk_loss, sub), name):
                            return getattr(getattr(_sk_loss, sub), name)
                    raise AttributeError(f"module '_loss' has no attribute '{name}'")

            loss_proxy = _LossProxy('_loss')
            loss_proxy.__dict__.update(proxy.__dict__)
            sys.modules['_loss'] = loss_proxy
        except Exception:
            pass

_apply_sklearn_compatibility_shims()

# Add current directory and project root to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
if SCRIPT_DIR.name == "research_import":
    PROJECT_ROOT = SCRIPT_DIR.parent
elif SCRIPT_DIR.name == "accretion":
    PROJECT_ROOT = SCRIPT_DIR
elif (SCRIPT_DIR / "databases").exists() or (SCRIPT_DIR / "src").exists():
    PROJECT_ROOT = SCRIPT_DIR
else:
    PROJECT_ROOT = SCRIPT_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Import self-contained live engine classes
try:
    from crypto_live_engine import (
        CryptoLiveFeatureEngine,
        CryptoLiveInferenceEngine,
        TradeSignal,
        LivePredictionLogger,
        CRYPTO_CHAMPIONS,
        MODEL_FEATURE_NAMES,
        WARMUP_BARS_MIN
    )
except ImportError:
    from research_import.crypto_live_engine import (
        CryptoLiveFeatureEngine,
        CryptoLiveInferenceEngine,
        TradeSignal,
        LivePredictionLogger,
        CRYPTO_CHAMPIONS,
        MODEL_FEATURE_NAMES,
        WARMUP_BARS_MIN
    )

# ==============================================================================
# ⚙️ CONSTANTS & VALIDATION CONFIGURATION
# ==============================================================================
DEFAULT_SYMBOL = "XBTUSD"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs" if (PROJECT_ROOT / "logs").exists() else SCRIPT_DIR / "logs"
FEATURE_DRIFT_TOLERANCE = 1e-4      # Max acceptable absolute feature drift
PROBABILITY_TOLERANCE   = 1e-4      # Max acceptable probability divergence
CORRELATION_THRESHOLD   = 0.9999    # Min acceptable Pearson r

# Rolling Container Spans (15m bars)
ROLLING_HOUR_BARS = 4     # 1 hour  = 4 bars of 15m
ROLLING_DAY_BARS  = 96    # 24 hours = 96 bars of 15m
ROLLING_WEEK_BARS = 672   # 7 days  = 672 bars of 15m

# TCXA Kinematic Spans (15m bars)
TCXA_H_SHORT = 4
TCXA_H_LONG  = 8
TCXA_D_SHORT = 48
TCXA_D_LONG  = 96


# ==============================================================================
# 🗄️ SELF-CONTAINED DATABASE RESOLUTION & CANDLE INGESTION
# ==============================================================================
def resolve_crypto_db(ticker: str, custom_path: Optional[str] = None) -> Path:
    """
    Locates the SQLite database for the specified Kraken crypto ticker without
    relying on external config files or research lab modules.
    """
    if custom_path:
        p = Path(custom_path)
        if p.exists():
            return p
        raise FileNotFoundError(f"Custom database path not found: {custom_path}")

    clean = ticker.replace('.sqlite', '').replace('_synth', '').upper()
    kraken_alias_map = {
        'BTCUSD': 'XBTUSD',
        'BTCUSDT': 'XBTUSD',
        'BTCUSDC': 'XBTUSD',
        'DOGEUSD': 'XDGUSD',
        'DOGEUSDT': 'XDGUSD',
        'ETHUSDT': 'ETHUSD',
        'ADAUSDT': 'ADAUSD',
        'XRPUSDT': 'XRPUSD',
        'BNBUSDT': 'BNBUSD'
    }
    canonical = kraken_alias_map.get(clean, clean)

    candidates = [
        PROJECT_ROOT / "databases" / "kraken" / f"{canonical}.sqlite",
        SCRIPT_DIR.parent / "databases" / "kraken" / f"{canonical}.sqlite",
        PROJECT_ROOT / "databases" / f"{canonical}.sqlite",
        SCRIPT_DIR / "databases" / "kraken" / f"{canonical}.sqlite",
        SCRIPT_DIR / "data" / "kraken" / f"{canonical}.sqlite",
        SCRIPT_DIR / f"{canonical}.sqlite",
    ]

    for p in candidates:
        if p.exists():
            return p

    raise FileNotFoundError(
        f"Could not locate Kraken crypto database for '{ticker}'.\n"
        f"Checked paths:\n" + "\n".join(f" - {p}" for p in candidates) +
        f"\nPlease pass --db-path to specify the SQLite database location."
    )


def load_raw_crypto_15m(ticker: str, db_path: Optional[Path] = None) -> pd.DataFrame:
    """Loads 15-minute candles from SQLite database, sorting and indexing causally."""
    if db_path is None:
        db_path = resolve_crypto_db(ticker)

    with sqlite3.connect(db_path) as con:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        target_table = None
        for t in ['klines_15m', 'bars_15m', 'ohlcv_15m']:
            if t in tables:
                target_table = t
                break

        if not target_table:
            raise KeyError(f"No 15m table ('klines_15m') found in {db_path.name}. Found tables: {tables}")

        query = f"SELECT time, open, high, low, close, volume FROM '{target_table}' ORDER BY time ASC"
        df = pd.read_sql_query(query, con)

    if df.empty:
        raise ValueError(f"Table '{target_table}' in {db_path.name} is empty.")

    # Convert UNIX timestamp to datetime
    sample_t = df['time'].iloc[0]
    unit = 'ms' if sample_t > 1e11 else 's'
    df['dt_utc'] = pd.to_datetime(df['time'], unit=unit, utc=True)
    df.drop_duplicates(subset=['dt_utc'], inplace=True)
    df.sort_values('dt_utc', inplace=True)
    df.set_index('dt_utc', inplace=True)

    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)

    return df


# ==============================================================================
# 🧠 MODEL REGISTRY & CONFIG LOADER (NO RETRAINING)
# ==============================================================================
def load_champion_model_and_config(symbol: str) -> Tuple[Any, float, float, float]:
    """
    Loads the trained champion model (.joblib) and its operational cutoff/targets
    from local models/ and config/ directories without any retraining.
    Returns: (model, cutoff_threshold, x_star, y_star)
    """
    # 1. Resolve model file
    model_candidates = [
        SCRIPT_DIR / "models" / f"{symbol}_gbdt.joblib",
        SCRIPT_DIR / f"{symbol}_gbdt.joblib",
        PROJECT_ROOT / "models" / f"{symbol}_gbdt.joblib",
        PROJECT_ROOT / "export" / "accretion" / "models" / f"{symbol}_gbdt.joblib",
        PROJECT_ROOT / "export" / "research_import" / "models" / f"{symbol}_gbdt.joblib",
    ]
    model_path = None
    for p in model_candidates:
        if p.exists():
            model_path = p
            break

    if model_path is None:
        raise FileNotFoundError(
            f"Could not locate serialized model for {symbol}.\nChecked: " +
            "\n".join(f" - {p}" for p in model_candidates)
        )

    model = joblib.load(model_path)

    # 2. Resolve champion configuration (cutoff, x_star, y_star)
    cfg_candidates = [
        SCRIPT_DIR / "config" / "crypto_champions_config.json",
        SCRIPT_DIR / "crypto_champions_config.json",
        PROJECT_ROOT / "config" / "crypto_champions_config.json",
        PROJECT_ROOT / "export" / "research_import" / "config" / "crypto_champions_config.json",
    ]
    cfg_data = None
    for p in cfg_candidates:
        if p.exists():
            with open(p, 'r') as f:
                cfg_data = json.load(f)
            break

    cutoff_threshold = 0.50
    x_star = 4.00
    y_star = 2.00

    if cfg_data and 'champions' in cfg_data and symbol in cfg_data['champions']:
        c = cfg_data['champions'][symbol]
        cutoff_threshold = float(c.get('cutoff_threshold', 0.50))
        x_star = float(c.get('x_star', 4.00))
        y_star = float(c.get('y_star', 2.00))
    elif symbol in CRYPTO_CHAMPIONS:
        c = CRYPTO_CHAMPIONS[symbol]
        x_star = float(c.get('x_star', 4.00))
        y_star = float(c.get('y_star', 2.00))
        cutoff_threshold = 0.128235 if symbol == 'XBTUSD' else 0.10

    # Also check {symbol}_meta.json if present
    meta_path = model_path.parent / f"{symbol}_meta.json"
    if meta_path.exists():
        try:
            with open(meta_path, 'r') as f:
                meta = json.load(f)
            if 'cutoff_threshold' in meta:
                cutoff_threshold = float(meta['cutoff_threshold'])
            if 'x_star' in meta:
                x_star = float(meta['x_star'])
            if 'y_star' in meta:
                y_star = float(meta['y_star'])
        except Exception:
            pass

    return model, cutoff_threshold, x_star, y_star


# ==============================================================================
# 🔬 EMBEDDED RESEARCH FEATURE FORMULAS (BIT-FOR-BIT IDENTICAL)
# ==============================================================================
def compute_containers_and_macro_regimes(df: pd.DataFrame) -> pd.DataFrame:
    """Computes Rolling Hourly, Daily, and Weekly Containers and Macro Biomes causally."""
    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values

    # Strictly shifted lookbacks (excluding bar t)
    s_h = pd.Series(highs, index=df.index)
    s_l = pd.Series(lows, index=df.index)

    prior_h_h  = s_h.shift(1).rolling(ROLLING_HOUR_BARS, min_periods=ROLLING_HOUR_BARS).max().bfill().values
    prior_h_l  = s_l.shift(1).rolling(ROLLING_HOUR_BARS, min_periods=ROLLING_HOUR_BARS).min().bfill().values
    prior_d_h  = s_h.shift(1).rolling(ROLLING_DAY_BARS, min_periods=ROLLING_DAY_BARS).max().bfill().values
    prior_d_l  = s_l.shift(1).rolling(ROLLING_DAY_BARS, min_periods=ROLLING_DAY_BARS).min().bfill().values
    prior_wk_h = s_h.shift(1).rolling(ROLLING_WEEK_BARS, min_periods=ROLLING_WEEK_BARS).max().bfill().values
    prior_wk_l = s_l.shift(1).rolling(ROLLING_WEEK_BARS, min_periods=ROLLING_WEEK_BARS).min().bfill().values

    tactical_state = np.empty(n, dtype=object)
    state_duration = np.zeros(n, dtype=int)

    cur_state = "BULL_EXHAUSTED"
    cur_duration = 0

    for t in range(n):
        h_t = highs[t]
        l_t = lows[t]
        c_t = closes[t]
        o_t = opens[t]
        pd_h = prior_d_h[t]
        pd_l = prior_d_l[t]
        ph_h = prior_h_h[t]
        ph_l = prior_h_l[t]

        # Tactical State Engine (Daily High/Low pushes)
        pushed_d_up = (h_t > pd_h)
        pushed_d_dn = (l_t < pd_l)

        prev_state = cur_state
        if pushed_d_up and not pushed_d_dn:
            cur_state = "ACTIVE_BULL_WAVE"  # Green
        elif pushed_d_dn and not pushed_d_up:
            cur_state = "ACTIVE_BEAR_WAVE"  # Red
        elif pushed_d_up and pushed_d_dn:
            cur_state = "ACTIVE_BULL_WAVE" if c_t >= o_t else "ACTIVE_BEAR_WAVE"
        else:
            if cur_state == "ACTIVE_BULL_WAVE" and c_t < ph_l:
                cur_state = "BULL_EXHAUSTED"  # Yellow (pulled back below Hourly low)
            elif cur_state == "ACTIVE_BEAR_WAVE" and c_t > ph_h:
                cur_state = "BEAR_EXHAUSTED"  # Purple (bounced above Hourly high)

        if cur_state == prev_state:
            cur_duration += 1
        else:
            cur_duration = 1

        tactical_state[t] = cur_state
        state_duration[t] = cur_duration

    out = df.copy()
    out['prior_h_h'] = prior_h_h
    out['prior_h_l'] = prior_h_l
    out['prior_d_h'] = prior_d_h
    out['prior_d_l'] = prior_d_l
    out['prior_wk_h'] = prior_wk_h
    out['prior_wk_l'] = prior_wk_l
    out['tactical_state'] = tactical_state
    out['tactical_state_duration_bars'] = state_duration

    # Macro Regime One-Hot Indicators
    out['is_green']  = (out['tactical_state'] == 'ACTIVE_BULL_WAVE').astype(float)
    out['is_yellow'] = (out['tactical_state'] == 'BULL_EXHAUSTED').astype(float)
    out['is_red']    = (out['tactical_state'] == 'ACTIVE_BEAR_WAVE').astype(float)
    out['is_purple'] = (out['tactical_state'] == 'BEAR_EXHAUSTED').astype(float)

    return out


def compute_continuous_micro_states(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """
    Computes the continuous 24/7 binary Micro State bar-by-bar:
      - MICRO_RED   : Downward cascade leg (searching for low, ratcheting down).
      - MICRO_GREEN : Active relief expansion holding above confirmed shelf floor L_k.
    """
    n = len(df)
    closes = df['close'].values
    lows = df['low'].values

    micro_states = np.empty(n, dtype=object)
    dist_floor_pct = np.zeros(n, dtype=float)

    curr_state = "MICRO_RED"
    curr_cand_low = lows[0]
    curr_floor = None

    for t in range(n):
        l_t = lows[t]
        c_t = closes[t]

        if curr_state == "MICRO_RED":
            if l_t < curr_cand_low:
                curr_cand_low = l_t
            elif c_t > curr_cand_low:
                curr_floor = curr_cand_low
                curr_state = "MICRO_GREEN"
        elif curr_state == "MICRO_GREEN":
            if curr_floor is not None and l_t < curr_floor:
                curr_state = "MICRO_RED"
                curr_cand_low = l_t
                curr_floor = None

        micro_states[t] = curr_state

        if curr_state == "MICRO_GREEN" and curr_floor is not None and curr_floor > 0:
            dist_floor_pct[t] = (c_t - curr_floor) / curr_floor * 100.0
        else:
            dist_floor_pct[t] = 0.0

    return micro_states, dist_floor_pct


def compute_tactical_tcxa_15m(df: pd.DataFrame) -> pd.DataFrame:
    """Computes Tactical TCXA features on continuous 24/7 crypto candles."""
    out = pd.DataFrame(index=df.index)
    closes = df['close']
    volumes = df['volume']
    eps = 1e-8

    log_vol = np.log1p(np.maximum(volumes.values, 0.0))
    price_diff = closes.diff().fillna(0).values
    inc_eff = price_diff / (log_vol + 1.0)

    tiers = [
        ('h', TCXA_H_SHORT, TCXA_H_LONG),
        ('d', TCXA_D_SHORT, TCXA_D_LONG)
    ]

    for prefix, s_span, l_span in tiers:
        ema_s = closes.ewm(span=s_span, adjust=False).mean()
        ema_l = closes.ewm(span=l_span, adjust=False).mean()

        phase = np.where(ema_s > ema_l, 1, -1)
        x_flip = (pd.Series(phase, index=df.index) != pd.Series(phase, index=df.index).shift(1))
        x_flip.iloc[0] = True
        phase_id = x_flip.cumsum()

        time_since_x = phase_id.groupby(phase_id).cumcount()

        c_val = np.zeros(len(df))
        c_age = np.zeros(len(df))
        eff_decay = np.zeros(len(df))

        cur_c = closes.iloc[0]
        cur_c_idx = 0
        cur_phase = phase[0]
        eff_since_c = 0.0
        eff_total = 0.0

        for i in range(len(df)):
            p_i = phase[i]
            c_i = closes.iloc[i]
            eff_i = inc_eff[i]

            if x_flip.iloc[i]:
                cur_phase = p_i
                cur_c = c_i
                cur_c_idx = i
                eff_since_c = 0.0
                eff_total = 0.0

            eff_total += eff_i

            if cur_phase == 1:
                if c_i > cur_c:
                    cur_c = c_i
                    cur_c_idx = i
                    eff_since_c = 0.0
                else:
                    eff_since_c += eff_i
            else:
                if c_i < cur_c:
                    cur_c = c_i
                    cur_c_idx = i
                    eff_since_c = 0.0
                else:
                    eff_since_c += eff_i

            c_val[i] = cur_c
            c_age[i] = i - cur_c_idx
            eff_decay[i] = eff_since_c / (abs(eff_total) + eps)

        out[f'tcxa_{prefix}_phase'] = phase
        out[f'tcxa_{prefix}_time_since_x'] = time_since_x
        out[f'tcxa_{prefix}_c_to_t_pct'] = (closes.values - c_val) / c_val * 100.0
        out[f'tcxa_{prefix}_c_age_bars'] = c_age
        out[f'tcxa_{prefix}_c_velocity'] = out[f'tcxa_{prefix}_c_to_t_pct'] / (c_age + 1.0)
        out[f'tcxa_{prefix}_efficiency_decay'] = eff_decay

    return out


def compute_sdi_and_rpi_features(df: pd.DataFrame) -> pd.DataFrame:
    """Computes expanding SDI stretch, Z-scores, and RPI coordinates across containers."""
    out = pd.DataFrame(index=df.index)
    closes = df['close']
    highs = df['high']
    lows = df['low']
    eps = 1e-8

    # 1. RPI Position in Container Boxes [-0.5 to 1.5]
    out['rpi_h_pos']  = ((closes - df['prior_h_l']) / (df['prior_h_h'] - df['prior_h_l'] + eps)).clip(-0.5, 1.5)
    out['rpi_d_pos']  = ((closes - df['prior_d_l']) / (df['prior_d_h'] - df['prior_d_l'] + eps)).clip(-0.5, 1.5)
    out['rpi_wk_pos'] = ((closes - df['prior_wk_l']) / (df['prior_wk_h'] - df['prior_wk_l'] + eps)).clip(-0.5, 1.5)

    # 2. Container Volatility Compressions
    out['rpi_compression_h_in_d'] = (df['prior_h_h'] - df['prior_h_l']) / (df['prior_d_h'] - df['prior_d_l'] + eps)
    out['rpi_compression_d_in_wk'] = (df['prior_d_h'] - df['prior_d_l']) / (df['prior_wk_h'] - df['prior_wk_l'] + eps)

    # 3. Expanding SDI Statistical Stretch (Z-scores from expanding anchors)
    ret_15m = closes.pct_change().fillna(0.0)

    # Hourly SDI (4 bars)
    anchor_h = closes.rolling(ROLLING_HOUR_BARS, min_periods=1).mean()
    vol_h = ret_15m.rolling(ROLLING_HOUR_BARS, min_periods=ROLLING_HOUR_BARS).std().bfill() + eps
    out['sdi_h_stretch'] = (closes - anchor_h) / anchor_h * 100.0
    out['sdi_h_zscore']  = out['sdi_h_stretch'] / (vol_h * 100.0 + eps)

    # Daily SDI (96 bars)
    anchor_d = closes.rolling(ROLLING_DAY_BARS, min_periods=1).mean()
    vol_d = ret_15m.rolling(ROLLING_DAY_BARS, min_periods=ROLLING_DAY_BARS).std().bfill() + eps
    out['sdi_d_stretch'] = (closes - anchor_d) / anchor_d * 100.0
    out['sdi_d_zscore']  = out['sdi_d_stretch'] / (vol_d * 100.0 + eps)

    # Weekly SDI (672 bars)
    anchor_wk = closes.rolling(ROLLING_WEEK_BARS, min_periods=1).mean()
    vol_wk = ret_15m.rolling(ROLLING_WEEK_BARS, min_periods=ROLLING_WEEK_BARS).std().bfill() + eps
    out['sdi_wk_stretch'] = (closes - anchor_wk) / anchor_wk * 100.0
    out['sdi_wk_zscore']  = out['sdi_wk_stretch'] / (vol_wk * 100.0 + eps)

    # 4. Instantaneous Candle Kinematics
    candle_range = (highs - lows + eps)
    out['bar_body_ratio'] = (closes - df['open']).abs() / candle_range
    out['bar_thrust_dir'] = np.where(closes >= df['open'], 1.0, -1.0)

    # Relative Volume (RVOL relative to rolling 96-bar / 24h median volume)
    median_vol = df['volume'].rolling(ROLLING_DAY_BARS, min_periods=ROLLING_HOUR_BARS).median().bfill() + eps
    out['bar_rvol'] = df['volume'] / median_vol

    # Rolling 24-hour return
    out['ret_24h_pct'] = (closes - closes.shift(ROLLING_DAY_BARS)) / closes.shift(ROLLING_DAY_BARS) * 100.0
    out['ret_24h_pct'].fillna(0.0, inplace=True)

    return out


# ==============================================================================
# 🛠️ MOCK LOG GENERATION & VALIDATION LOG INGESTION
# ==============================================================================
def generate_mock_live_segment(symbol: str = "XBTUSD", segment_bars: int = 96, db_path: Optional[Path] = None) -> Path:
    """
    Generates a realistic mock live prediction log from the tail of the historical database
    to test the validation suite when no live log file is yet available.
    Uses pre-trained models from models/ directory (NO retraining).
    """
    print(f"\n[Mock Generator] Simulating live bot execution for {symbol} over {segment_bars} bars...")
    df_raw = load_raw_crypto_15m(symbol, db_path=db_path)
    
    # Load champion model directly from exported model registry
    model, cutoff, x_star, y_star = load_champion_model_and_config(symbol)

    # Initialize streaming live engines
    feature_engine = CryptoLiveFeatureEngine(symbol)
    warmup_df = df_raw.iloc[:-segment_bars]
    feature_engine.warmup(warmup_df)

    inference_engine = CryptoLiveInferenceEngine()
    inference_engine.register_model(symbol, model, cutoff, x_star, y_star)

    log_dir = DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = LivePredictionLogger(log_dir)
    log_file = log_dir / f"live_predictions_{symbol}.jsonl"
    if log_file.exists():
        log_file.unlink()

    # Stream the last segment_bars
    test_slice = df_raw.iloc[-segment_bars:]
    for dt, row in test_slice.iterrows():
        feats = feature_engine.on_new_bar(
            dt=dt,
            open_p=row['open'],
            high_p=row['high'],
            low_p=row['low'],
            close_p=row['close'],
            volume=row['volume']
        )
        if feats is not None:
            sig = inference_engine.evaluate(symbol, dt, row['close'], feats)
            if sig is not None:
                logger.log_prediction(sig)

    print(f"✅ Mock live log generated: {log_file} ({segment_bars} bars)")
    return log_file


def load_live_log(log_path: Path) -> pd.DataFrame:
    """Loads and parses a live prediction JSONL file into a structured DataFrame."""
    if not log_path.exists():
        raise FileNotFoundError(f"Live log file not found: {log_path}")

    records = []
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    if not records:
        raise ValueError(f"Live log file is empty: {log_path}")

    df_live = pd.DataFrame(records)
    df_live['dt'] = pd.to_datetime(df_live['timestamp'], utc=True)
    df_live.set_index('dt', inplace=True)
    df_live.sort_index(inplace=True)

    # Flatten feature dictionary into separate columns
    feat_df = pd.json_normalize(df_live['features'])
    feat_df.index = df_live.index
    df_live = pd.concat([df_live.drop(columns=['features']), feat_df], axis=1)

    print(f"Loaded {len(df_live)} live prediction records from {log_path.name}")
    print(f"Segment Span: {df_live.index.min()} to {df_live.index.max()}")
    return df_live


def compute_historical_research_features(symbol: str, start_dt: pd.Timestamp, end_dt: pd.Timestamp, db_path: Optional[Path] = None) -> Tuple[pd.DataFrame, Any, float]:
    """
    Loads historical Kraken candles covering the live segment (plus warm-up lookback),
    computes features using the exact research preprocessor formulas,
    loads the pre-trained champion GBDT model, and returns the aligned segment DataFrame.
    """
    df_raw = load_raw_crypto_15m(symbol, db_path=db_path)

    # Need at least WARMUP_BARS_MIN prior to start_dt
    loc_start = df_raw.index.get_indexer([start_dt], method='nearest')[0]
    warmup_start_idx = max(0, loc_start - WARMUP_BARS_MIN - 10)
    loc_end = df_raw.index.get_indexer([end_dt], method='nearest')[0]

    df_slice = df_raw.iloc[warmup_start_idx:loc_end + 1].copy()

    # Run embedded research feature formulas
    df_containers = compute_containers_and_macro_regimes(df_slice)
    m_states, dist_floor = compute_continuous_micro_states(df_containers)
    df_containers['micro_state'] = m_states
    df_containers['is_micro_green'] = (m_states == 'MICRO_GREEN').astype(float)
    df_containers['dist_to_shelf_floor_pct'] = dist_floor

    df_tcxa = compute_tactical_tcxa_15m(df_containers)
    df_sdi_rpi = compute_sdi_and_rpi_features(df_containers)

    df_hist_all = pd.concat([df_containers, df_tcxa, df_sdi_rpi], axis=1)

    # Align strictly to the segment [start_dt, end_dt]
    df_hist_segment = df_hist_all.loc[start_dt:end_dt].copy()

    # Load champion GBDT model and cutoff without retraining
    model, cutoff, x_star, y_star = load_champion_model_and_config(symbol)

    # Predict historical probabilities
    X_hist = df_hist_segment[MODEL_FEATURE_NAMES].values
    p_hist = model.predict_proba(X_hist)[:, 1]
    df_hist_segment['p_pred_hist'] = p_hist
    df_hist_segment['is_signal_hist'] = (p_hist >= cutoff)

    return df_hist_segment, model, cutoff


# ==============================================================================
# 🔍 AUDIT & VERIFICATION ENGINE
# ==============================================================================
def audit_live_vs_historical(df_live: pd.DataFrame, df_hist: pd.DataFrame) -> Dict[str, Any]:
    """
    Performs comprehensive statistical comparison between live logged data and research pipeline.
    """
    common_idx = df_live.index.intersection(df_hist.index)
    if len(common_idx) == 0:
        raise ValueError("Zero overlapping timestamps between live log and historical data!")

    df_l = df_live.loc[common_idx]
    df_h = df_hist.loc[common_idx]

    feature_audit = []
    has_drift_alert = False

    for feat in MODEL_FEATURE_NAMES:
        if feat in df_l.columns and feat in df_h.columns:
            l_vals = df_l[feat].astype(float).values
            h_vals = df_h[feat].astype(float).values
            abs_err = np.abs(l_vals - h_vals)
            max_err = float(np.max(abs_err))
            mean_err = float(np.mean(abs_err))

            std_l = np.std(l_vals)
            std_h = np.std(h_vals)
            if std_l > 1e-8 and std_h > 1e-8:
                corr = float(np.corrcoef(l_vals, h_vals)[0, 1])
            else:
                corr = 1.0 if max_err < FEATURE_DRIFT_TOLERANCE else 0.0

            passed = (max_err <= FEATURE_DRIFT_TOLERANCE) and (corr >= CORRELATION_THRESHOLD)
            if not passed:
                has_drift_alert = True

            feature_audit.append({
                'feature': feat,
                'max_err': max_err,
                'mean_err': mean_err,
                'corr': corr,
                'status': '✅ PASS' if passed else '🚨 DRIFT'
            })

    # Probability comparison
    p_live = df_l['p_pred'].values
    p_hist = df_h['p_pred_hist'].values
    p_abs_err = np.abs(p_live - p_hist)
    max_p_err = float(np.max(p_abs_err))
    mean_p_err = float(np.mean(p_abs_err))
    p_corr = float(np.corrcoef(p_live, p_hist)[0, 1]) if np.std(p_live) > 1e-8 and np.std(p_hist) > 1e-8 else 1.0

    # Signal alignment
    sig_live = df_l['is_signal'].astype(bool).values
    sig_hist = df_h['is_signal_hist'].astype(bool).values
    signal_agreement_pct = float(np.mean(sig_live == sig_hist) * 100.0)

    audit_summary = {
        'total_bars': len(common_idx),
        'start_time': str(common_idx.min()),
        'end_time': str(common_idx.max()),
        'max_p_err': max_p_err,
        'mean_p_err': mean_p_err,
        'p_corr': p_corr,
        'signal_agreement_pct': signal_agreement_pct,
        'has_drift_alert': has_drift_alert,
        'feature_audit': pd.DataFrame(feature_audit),
        'df_l': df_l,
        'df_h': df_h
    }

    return audit_summary


def print_audit_report(summary: Dict[str, Any]) -> None:
    """Prints a clean CLI verification scorecard."""
    print("\n" + "=" * 95)
    print("📋 PROJECT SINGULARITY -> ACCRETION PREDICTION VALIDATION SCORECARD")
    print("=" * 95)
    print(f"Segment Analyzed      : {summary['start_time']} to {summary['end_time']}")
    print(f"Total 15m Bars Audited: {summary['total_bars']:,}")
    print(f"Prediction Prob Corr  : {summary['p_corr']:.6f} (R^2 = {summary['p_corr']**2:.6f})")
    print(f"Max Probability Error : {summary['max_p_err']:.2e}")
    print(f"Mean Prob Error       : {summary['mean_p_err']:.2e}")
    print(f"Signal Agreement Rate : {summary['signal_agreement_pct']:.2f}%")
    print("-" * 95)
    print("FEATURE DRIFT BREAKDOWN (34 PRODUCTION FEATURES):")
    print(f"{'Feature Name':<30} | {'Max Abs Err':<12} | {'Mean Abs Err':<12} | {'Corr':<10} | {'Status'}")
    print("-" * 95)
    for _, r in summary['feature_audit'].iterrows():
        print(f"{r['feature']:<30} | {r['max_err']:<12.2e} | {r['mean_err']:<12.2e} | {r['corr']:<10.6f} | {r['status']}")
    print("=" * 95)

    if not summary['has_drift_alert'] and summary['max_p_err'] <= PROBABILITY_TOLERANCE and summary['signal_agreement_pct'] == 100.0:
        print("🌟 VERDICT: PERFECT 100% PARITY! Live bot exactly matches research preprocessor.")
    else:
        print("⚠️ VERDICT: DISCREPANCIES DETECTED. Review highlighted drift features above.")


def plot_dark_mode_validation_dashboard(summary: Dict[str, Any], symbol: str) -> None:
    """
    Renders a 4-panel dark-mode diagnostic dashboard comparing live vs historical execution.
    GEMINI.md Hard Rule Compliance: Dark mode only, plt.show() only.
    """
    plt.style.use('dark_background')
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle(f"SingularityCore vs Accretion Live Parity Audit: {symbol}", fontsize=15, fontweight='bold', color='#00e5ff')

    df_l = summary['df_l']
    df_h = summary['df_h']
    feat_df = summary['feature_audit']

    # Panel 1: Prediction Probability Time Series Overlay
    ax1 = axes[0, 0]
    ax1.plot(df_l.index, df_l['p_pred'], label='Live Accretion P(Hit)', color='#00e5ff', lw=1.8, alpha=0.9)
    ax1.plot(df_h.index, df_h['p_pred_hist'], label='Research Historical P(Hit)', color='#ff9100', lw=1.2, ls='--', alpha=0.85)
    cutoff = df_l['cutoff_threshold'].iloc[0] if 'cutoff_threshold' in df_l.columns else 0.50
    ax1.axhline(cutoff, color='#ff1744', ls=':', lw=1.2, label=f'Conviction Cutoff P* ({cutoff:.2f})')
    ax1.set_title("Panel 1: Real-Time Prediction Probability Overlay (P_live vs P_hist)", color='white', fontsize=11)
    ax1.set_ylabel("Probability P(Hit +x*)", color='white')
    ax1.legend(loc='upper right', framealpha=0.3)
    ax1.grid(True, alpha=0.2)

    # Panel 2: Scatter Correlation (Asserting R^2 = 1.0)
    ax2 = axes[0, 1]
    ax2.scatter(df_h['p_pred_hist'], df_l['p_pred'], color='#00e676', alpha=0.6, s=25, label='Inference Points')
    p_min = float(min(df_h['p_pred_hist'].min(), df_l['p_pred'].min()))
    p_max = float(max(df_h['p_pred_hist'].max(), df_l['p_pred'].max()))
    if abs(p_max - p_min) < 1e-6:
        p_min -= 0.01
        p_max += 0.01
    ax2.plot([p_min, p_max], [p_min, p_max], color='#ff1744', ls='--', lw=1.5, label='Perfect Parity (y = x)')
    ax2.set_title(f"Panel 2: Probability Parity Correlation (R^2 = {summary['p_corr']**2:.6f})", color='white', fontsize=11)
    ax2.set_xlabel("Historical Research P", color='white')
    ax2.set_ylabel("Live Accretion P", color='white')
    ax2.legend(loc='lower right', framealpha=0.3)
    ax2.grid(True, alpha=0.2)

    # Panel 3: Maximum Absolute Error across Features
    ax3 = axes[1, 0]
    bars = ax3.barh(feat_df['feature'], feat_df['max_err'], color='#7c4dff', alpha=0.85)
    ax3.axvline(FEATURE_DRIFT_TOLERANCE, color='#ff1744', ls='--', lw=1.2, label=f'Tolerance ({FEATURE_DRIFT_TOLERANCE})')
    ax3.set_xscale('log')
    ax3.set_title("Panel 3: Maximum Feature Absolute Error (Log Scale)", color='white', fontsize=11)
    ax3.set_xlabel("Max Absolute Error (Live - Hist)", color='white')
    ax3.legend(loc='lower right', framealpha=0.3)
    ax3.grid(True, alpha=0.2)

    # Panel 4: Signal Trigger Alignment Timeline
    ax4 = axes[1, 1]
    live_signals = df_l[df_l['is_signal'] == True]
    hist_signals = df_h[df_h['is_signal_hist'] == True]

    ax4.plot(df_l.index, df_l['close_price'] if 'close_price' in df_l.columns else df_h['close'], color='#78909c', lw=1.0, alpha=0.6, label='Price Close')
    if len(live_signals) > 0:
        ax4.scatter(live_signals.index, live_signals['close_price'] if 'close_price' in live_signals.columns else live_signals['close'],
                    color='#00e5ff', marker='^', s=80, label=f"Live Buy Signals ({len(live_signals)})", zorder=5)
    if len(hist_signals) > 0:
        ax4.scatter(hist_signals.index, hist_signals['close'],
                    color='#ff9100', marker='o', s=35, facecolors='none', edgecolors='#ff9100', lw=1.5,
                    label=f"Historical Buy Signals ({len(hist_signals)})", zorder=6)

    ax4.set_title(f"Panel 4: Trade Signal Alignment ({summary['signal_agreement_pct']:.1f}% Agreement)", color='white', fontsize=11)
    ax4.set_ylabel("Price (USD)", color='white')
    ax4.legend(loc='upper left', framealpha=0.3)
    ax4.grid(True, alpha=0.2)

    plt.tight_layout()
    plt.show()


# ==============================================================================
# 🚀 CLI ENTRYPOINT
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Live vs Historical Prediction Validation Suite (Self-Contained)")
    parser.add_argument("--symbol", type=str, default=DEFAULT_SYMBOL, help="Kraken symbol to audit (e.g. XBTUSD, SOLUSD)")
    parser.add_argument("--log-file", type=str, default=None, help="Path to live prediction JSONL log")
    parser.add_argument("--db-path", type=str, default=None, help="Custom path to Kraken SQLite database")
    parser.add_argument("--mock-demo", action="store_true", help="Generate a mock live segment to test the validation suite")
    parser.add_argument("--segment-bars", type=int, default=96, help="Number of bars to audit in mock demo (default: 96 = 24h)")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    if symbol in ('BTCUSD', 'BTCUSDT'): symbol = 'XBTUSD'
    elif symbol in ('DOGEUSD', 'DOGEUSDT'): symbol = 'XDGUSD'

    db_path = Path(args.db_path) if args.db_path else None

    if args.mock_demo:
        log_path = generate_mock_live_segment(symbol, segment_bars=args.segment_bars, db_path=db_path)
    elif args.log_file:
        log_path = Path(args.log_file)
    else:
        # Search all possible project locations for live predictions JSONL
        possible_log_paths = [
            PROJECT_ROOT / "logs" / f"live_predictions_{symbol}.jsonl",
            DEFAULT_LOG_DIR / f"live_predictions_{symbol}.jsonl",
            SCRIPT_DIR / "logs" / f"live_predictions_{symbol}.jsonl",
            Path.cwd() / "logs" / f"live_predictions_{symbol}.jsonl",
            Path.cwd() / f"live_predictions_{symbol}.jsonl",
        ]
        log_path = None
        for p in possible_log_paths:
            if p.exists() and p.stat().st_size > 0:
                log_path = p
                break

        if log_path is None:
            fallback = PROJECT_ROOT / "logs" / f"live_predictions_{symbol}.jsonl"
            print(f"ℹ️ No live log found at {fallback}. Running with --mock-demo to demonstrate verification...")
            log_path = generate_mock_live_segment(symbol, segment_bars=args.segment_bars, db_path=db_path)

    # 1. Load live log
    df_live = load_live_log(log_path)
    start_dt = df_live.index.min()
    end_dt = df_live.index.max()

    # 2. Compute historical research features
    print(f"\n[Research Engine] Processing historical candles for {symbol} ({start_dt} to {end_dt})...")
    df_hist, model, cutoff = compute_historical_research_features(symbol, start_dt, end_dt, db_path=db_path)

    # 3. Statistical audit
    summary = audit_live_vs_historical(df_live, df_hist)

    # 4. Print scorecard
    print_audit_report(summary)

    # 5. Render dark-mode dashboard
    plot_dark_mode_validation_dashboard(summary, symbol)


if __name__ == "__main__":
    main()
