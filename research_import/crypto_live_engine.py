"""
====================================================================================================
PROJECT SINGULARITY -> ACCRETION LIVE BRIDGE: 24/7 CRYPTO INTRADAY ENGINE
Modular, Self-Contained Production Classes for Real-Time Streaming Inference & Execution
====================================================================================================
Purpose:
  Provides production-ready, zero-dependency engine classes designed to be copied directly
  into the Accretion live execution project:
    1. CryptoLiveFeatureEngine:
       - Streaming, state-based 15m feature extractor with rolling in-memory ring buffers.
       - Exact mathematical parity with SingularityCore research feature preprocessor.
       - 24/7 continuous physics: Rolling Hourly (4b), Daily (96b), Weekly (672b) containers.
       - Continuous Micro State Machine: MICRO_RED (cascade) vs MICRO_GREEN (shelf floor).
       - Tactical TCXA kinematics, multi-scale SDI statistical stretch, and RPI coordinates.
    2. CryptoLiveInferenceEngine:
       - Manages trained GBDT champion models and conviction thresholds per asset.
       - Computes real-time barrier breach probabilities P(Hit +x* before -y*).
       - Generates structured TradeSignal events with debounce anti-chatter protection.
       - Supports loading serialized .joblib models from disk.
    3. CryptoDualLimitExecutionEngine:
       - Cross-exchange execution bridge: Ingests Kraken signals -> Routes to BinanceUS.
       - Dual Passive Maker Limits: Maker Buy Discount (-0.50%) / Maker Sell Premium (+0.15%).
       - BinanceUS Native Fee Accounting: 0.0% Maker Fee / 1.9 bps Taker Fee.
       - Fixed Limit Take-Profit (100% pre-placed maker TP exit).
       - Dynamic Compounded Position Sizing (e.g. 50% equity) & Slot Concurrency (K=5).
    4. LivePredictionLogger:
       - Structured JSONL logging for live predictions, features, and order parameters.
       - Directly feeds the offline validation script (crypto_live_prediction_validation_lab.py).
====================================================================================================
"""

import os
import sys
import time
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any, Union
from dataclasses import dataclass, asdict
from collections import deque
import numpy as np
import pandas as pd
import joblib
import types

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

# ==============================================================================
# ⚙️ CONSTANTS & DEFAULT PARAMETERS (100% ALIGNED WITH RESEARCH LAB)
# ==============================================================================
ROLLING_HOUR_BARS = 4     # 1 hour  = 4 bars of 15m
ROLLING_DAY_BARS  = 96    # 24 hours = 96 bars of 15m
ROLLING_WEEK_BARS = 672   # 7 days  = 672 bars of 15m
WARMUP_BARS_MIN   = 672   # Minimum history required to fully initialize weekly state

# TCXA Spans (15m bars)
TCXA_H_SHORT = 4
TCXA_H_LONG  = 8
TCXA_D_SHORT = 48
TCXA_D_LONG  = 96

# Validated Production Champions across Active Universe (Kraken Symbols)
CRYPTO_CHAMPIONS: Dict[str, Dict[str, Any]] = {
    'XBTUSD': {'x_star': 4.00, 'y_star': 2.00, 'cutoff_pct': 2.0, 'binance_sym': 'BTCUSD'},
    'ETHUSD': {'x_star': 5.00, 'y_star': 2.50, 'cutoff_pct': 2.0, 'binance_sym': 'ETHUSD'},
    'SOLUSD': {'x_star': 5.00, 'y_star': 2.50, 'cutoff_pct': 2.0, 'binance_sym': 'SOLUSD'},
    'ADAUSD': {'x_star': 5.00, 'y_star': 2.50, 'cutoff_pct': 2.0, 'binance_sym': 'ADAUSD'},
    'XRPUSD': {'x_star': 2.50, 'y_star': 1.25, 'cutoff_pct': 2.0, 'binance_sym': 'XRPUSD'},
    'XDGUSD': {'x_star': 5.00, 'y_star': 2.50, 'cutoff_pct': 2.0, 'binance_sym': 'DOGEUSD'},
}

# 28 Production Features Required for GBDT Model Inference
MODEL_FEATURE_NAMES = [
    'rpi_h_pos',
    'rpi_d_pos',
    'rpi_wk_pos',
    'rpi_compression_h_in_d',
    'rpi_compression_d_in_wk',
    'sdi_h_stretch',
    'sdi_h_zscore',
    'sdi_d_stretch',
    'sdi_d_zscore',
    'sdi_wk_stretch',
    'sdi_wk_zscore',
    'bar_body_ratio',
    'bar_thrust_dir',
    'bar_rvol',
    'ret_24h_pct',
    'is_green',
    'is_yellow',
    'is_red',
    'is_purple',
    'tactical_state_duration_bars',
    'is_micro_green',
    'dist_to_shelf_floor_pct',
    'tcxa_h_phase',
    'tcxa_h_time_since_x',
    'tcxa_h_c_to_t_pct',
    'tcxa_h_c_age_bars',
    'tcxa_h_c_velocity',
    'tcxa_h_efficiency_decay',
    'tcxa_d_phase',
    'tcxa_d_time_since_x',
    'tcxa_d_c_to_t_pct',
    'tcxa_d_c_age_bars',
    'tcxa_d_c_velocity',
    'tcxa_d_efficiency_decay'
]

# BinanceUS Fee Schedule (Decimal)
BINANCE_US_MAKER_FEE = 0.00000   # 0.0% Maker (0 bps)
BINANCE_US_TAKER_FEE = 0.00019   # 0.019% Taker (1.9 bps)


# ==============================================================================
# 📦 DATA CLASSES
# ==============================================================================
@dataclass
class TradeSignal:
    timestamp: pd.Timestamp
    kraken_symbol: str
    binance_symbol: str
    close_price: float
    p_pred: float
    cutoff_threshold: float
    x_star: float
    y_star: float
    is_signal: bool
    limit_buy_price: float
    limit_sell_price: float
    stop_loss_price: float
    features: Dict[str, float]


@dataclass
class LivePosition:
    position_id: str
    kraken_symbol: str
    binance_symbol: str
    entry_timestamp: pd.Timestamp
    entry_price: float
    quantity: float
    allocated_capital: float
    limit_sell_price: float
    stop_loss_price: float
    timeout_bar_limit: int
    bars_held: int = 0
    status: str = "PENDING_BUY"  # PENDING_BUY, FILLED, EXITED_TP, EXITED_STOP, EXITED_TIMEOUT


# ==============================================================================
# 1. STREAMING FEATURE ENGINE
# ==============================================================================
class CryptoLiveFeatureEngine:
    """
    State-based, real-time 15m structural feature engine for 24/7 crypto.
    Maintains in-memory rolling history and computes all 28 structural features
    with 100% identical mathematical output to SingularityCore research preprocessor.
    """

    def __init__(self, symbol: str, buffer_size: int = 800):
        self.symbol = symbol.upper()
        self.buffer_size = max(buffer_size, WARMUP_BARS_MIN + 50)
        self.history: deque = deque(maxlen=self.buffer_size)
        self.is_warmed_up: bool = False

    def warmup(self, df_15m: pd.DataFrame) -> None:
        """
        Warms up the feature engine from a historical DataFrame of 15m candles.
        Required columns: ['open', 'high', 'low', 'close', 'volume'] with DatetimeIndex or 'time'.
        """
        df = df_15m.copy()
        if 'dt_utc' in df.columns:
            df.set_index('dt_utc', inplace=True)
        elif 'time' in df.columns and not isinstance(df.index, pd.DatetimeIndex):
            sample_t = df['time'].iloc[0]
            unit = 'ms' if sample_t > 1e11 else 's'
            df['dt_utc'] = pd.to_datetime(df['time'], unit=unit, utc=True)
            df.set_index('dt_utc', inplace=True)

        df.sort_index(inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)

        # Ingest the last buffer_size candles
        recent = df.tail(self.buffer_size)
        self.history.clear()
        for dt, row in recent.iterrows():
            self.history.append({
                'dt': dt,
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume'])
            })

        if len(self.history) >= WARMUP_BARS_MIN:
            self.is_warmed_up = True
            logging.info(f"[{self.symbol}] Warm-up complete: {len(self.history)} bars buffered.")
        else:
            logging.warning(f"[{self.symbol}] Partial warm-up: {len(self.history)}/{WARMUP_BARS_MIN} bars. Engine needs more data.")

    def on_new_bar(self, dt: pd.Timestamp, open_p: float, high_p: float, low_p: float, close_p: float, volume: float) -> Optional[Dict[str, float]]:
        """
        Processes a newly closed 15-minute bar, updates internal state, and emits
        the real-time structural feature vector.
        """
        self.history.append({
            'dt': dt,
            'open': float(open_p),
            'high': float(high_p),
            'low': float(low_p),
            'close': float(close_p),
            'volume': float(volume)
        })

        if len(self.history) < WARMUP_BARS_MIN:
            return None

        self.is_warmed_up = True
        return self._compute_latest_features()

    def _compute_latest_features(self) -> Dict[str, float]:
        """
        Computes the complete structural feature dictionary for the most recent bar (bar t)
        using the buffered history without lookahead bias.
        """
        df = pd.DataFrame(list(self.history)).set_index('dt')
        n = len(df)
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        opens = df['open'].values
        volumes = df['volume'].values
        eps = 1e-8

        # --- 1. Rolling Containers (Strictly shifted: excludes bar t) ---
        s_h = pd.Series(highs, index=df.index)
        s_l = pd.Series(lows, index=df.index)

        prior_h_h = s_h.shift(1).rolling(ROLLING_HOUR_BARS, min_periods=ROLLING_HOUR_BARS).max().values[-1]
        prior_h_l = s_l.shift(1).rolling(ROLLING_HOUR_BARS, min_periods=ROLLING_HOUR_BARS).min().values[-1]
        prior_d_h = s_h.shift(1).rolling(ROLLING_DAY_BARS, min_periods=ROLLING_DAY_BARS).max().values[-1]
        prior_d_l = s_l.shift(1).rolling(ROLLING_DAY_BARS, min_periods=ROLLING_DAY_BARS).min().values[-1]
        prior_wk_h = s_h.shift(1).rolling(ROLLING_WEEK_BARS, min_periods=ROLLING_WEEK_BARS).max().values[-1]
        prior_wk_l = s_l.shift(1).rolling(ROLLING_WEEK_BARS, min_periods=ROLLING_WEEK_BARS).min().values[-1]

        # --- 2. Tactical State Machine & Duration ---
        pd_h_arr = s_h.shift(1).rolling(ROLLING_DAY_BARS, min_periods=ROLLING_DAY_BARS).max().bfill().values
        pd_l_arr = s_l.shift(1).rolling(ROLLING_DAY_BARS, min_periods=ROLLING_DAY_BARS).min().bfill().values
        ph_h_arr = s_h.shift(1).rolling(ROLLING_HOUR_BARS, min_periods=ROLLING_HOUR_BARS).max().bfill().values
        ph_l_arr = s_l.shift(1).rolling(ROLLING_HOUR_BARS, min_periods=ROLLING_HOUR_BARS).min().bfill().values

        cur_state = "BULL_EXHAUSTED"
        cur_duration = 0
        for i in range(n):
            h_i = highs[i]
            l_i = lows[i]
            c_i = closes[i]
            o_i = opens[i]
            pushed_d_up = (h_i > pd_h_arr[i])
            pushed_d_dn = (l_i < pd_l_arr[i])
            prev_state = cur_state

            if pushed_d_up and not pushed_d_dn:
                cur_state = "ACTIVE_BULL_WAVE"
            elif pushed_d_dn and not pushed_d_up:
                cur_state = "ACTIVE_BEAR_WAVE"
            elif pushed_d_up and pushed_d_dn:
                cur_state = "ACTIVE_BULL_WAVE" if c_i >= o_i else "ACTIVE_BEAR_WAVE"
            else:
                if cur_state == "ACTIVE_BULL_WAVE" and c_i < ph_l_arr[i]:
                    cur_state = "BULL_EXHAUSTED"
                elif cur_state == "ACTIVE_BEAR_WAVE" and c_i > ph_h_arr[i]:
                    cur_state = "BEAR_EXHAUSTED"

            if cur_state == prev_state:
                cur_duration += 1
            else:
                cur_duration = 1

        is_green = 1.0 if cur_state == "ACTIVE_BULL_WAVE" else 0.0
        is_yellow = 1.0 if cur_state == "BULL_EXHAUSTED" else 0.0
        is_red = 1.0 if cur_state == "ACTIVE_BEAR_WAVE" else 0.0
        is_purple = 1.0 if cur_state == "BEAR_EXHAUSTED" else 0.0

        # --- 3. Continuous Micro State Machine ---
        curr_m_state = "MICRO_RED"
        curr_cand_low = lows[0]
        curr_floor = None
        for i in range(n):
            l_i = lows[i]
            c_i = closes[i]
            if curr_m_state == "MICRO_RED":
                if l_i < curr_cand_low:
                    curr_cand_low = l_i
                elif c_i > curr_cand_low:
                    curr_floor = curr_cand_low
                    curr_m_state = "MICRO_GREEN"
            elif curr_m_state == "MICRO_GREEN":
                if curr_floor is not None and l_i < curr_floor:
                    curr_m_state = "MICRO_RED"
                    curr_cand_low = l_i
                    curr_floor = None

        is_micro_green = 1.0 if curr_m_state == "MICRO_GREEN" else 0.0
        c_t = closes[-1]
        dist_to_shelf_floor_pct = (c_t - curr_floor) / curr_floor * 100.0 if (curr_m_state == "MICRO_GREEN" and curr_floor and curr_floor > 0) else 0.0

        # --- 4. RPI & SDI Features ---
        rpi_h_pos = np.clip((c_t - prior_h_l) / (prior_h_h - prior_h_l + eps), -0.5, 1.5)
        rpi_d_pos = np.clip((c_t - prior_d_l) / (prior_d_h - prior_d_l + eps), -0.5, 1.5)
        rpi_wk_pos = np.clip((c_t - prior_wk_l) / (prior_wk_h - prior_wk_l + eps), -0.5, 1.5)

        rpi_compression_h_in_d = (prior_h_h - prior_h_l) / (prior_d_h - prior_d_l + eps)
        rpi_compression_d_in_wk = (prior_d_h - prior_d_l) / (prior_wk_h - prior_wk_l + eps)

        s_c = pd.Series(closes, index=df.index)
        ret_15m = s_c.pct_change().fillna(0.0)

        # Hourly SDI (4 bars)
        anchor_h = s_c.rolling(ROLLING_HOUR_BARS, min_periods=1).mean().iloc[-1]
        vol_h = ret_15m.rolling(ROLLING_HOUR_BARS, min_periods=ROLLING_HOUR_BARS).std().bfill().iloc[-1] + eps
        sdi_h_stretch = (c_t - anchor_h) / anchor_h * 100.0
        sdi_h_zscore = sdi_h_stretch / (vol_h * 100.0 + eps)

        # Daily SDI (96 bars)
        anchor_d = s_c.rolling(ROLLING_DAY_BARS, min_periods=1).mean().iloc[-1]
        vol_d = ret_15m.rolling(ROLLING_DAY_BARS, min_periods=ROLLING_DAY_BARS).std().bfill().iloc[-1] + eps
        sdi_d_stretch = (c_t - anchor_d) / anchor_d * 100.0
        sdi_d_zscore = sdi_d_stretch / (vol_d * 100.0 + eps)

        # Weekly SDI (672 bars)
        anchor_wk = s_c.rolling(ROLLING_WEEK_BARS, min_periods=1).mean().iloc[-1]
        vol_wk = ret_15m.rolling(ROLLING_WEEK_BARS, min_periods=ROLLING_WEEK_BARS).std().bfill().iloc[-1] + eps
        sdi_wk_stretch = (c_t - anchor_wk) / anchor_wk * 100.0
        sdi_wk_zscore = sdi_wk_stretch / (vol_wk * 100.0 + eps)

        # Instantaneous candle kinematics
        candle_range = (highs[-1] - lows[-1] + eps)
        bar_body_ratio = abs(c_t - opens[-1]) / candle_range
        bar_thrust_dir = 1.0 if c_t >= opens[-1] else -1.0

        median_vol = pd.Series(volumes).rolling(ROLLING_DAY_BARS, min_periods=ROLLING_HOUR_BARS).median().bfill().iloc[-1] + eps
        bar_rvol = volumes[-1] / median_vol

        c_prev_24h = closes[-ROLLING_DAY_BARS - 1] if n > ROLLING_DAY_BARS else closes[0]
        ret_24h_pct = (c_t - c_prev_24h) / c_prev_24h * 100.0

        # --- 5. Tactical TCXA Kinematics (Hourly & Daily) ---
        log_vol = np.log1p(np.maximum(volumes, 0.0))
        price_diff = np.diff(closes, prepend=closes[0])
        inc_eff = price_diff / (log_vol + 1.0)

        tcxa_feats = {}
        for prefix, s_span, l_span in [('h', TCXA_H_SHORT, TCXA_H_LONG), ('d', TCXA_D_SHORT, TCXA_D_LONG)]:
            ema_s = s_c.ewm(span=s_span, adjust=False).mean().values
            ema_l = s_c.ewm(span=l_span, adjust=False).mean().values
            phase = np.where(ema_s > ema_l, 1, -1)

            x_flip = np.empty(n, dtype=bool)
            x_flip[0] = True
            x_flip[1:] = (phase[1:] != phase[:-1])

            cur_p = phase[0]
            cur_c_val = closes[0]
            cur_c_idx = 0
            eff_since_c = 0.0
            eff_total = 0.0

            time_since_x = 0
            for i in range(n):
                p_i = phase[i]
                c_i = closes[i]
                eff_i = inc_eff[i]

                if x_flip[i]:
                    cur_p = p_i
                    cur_c_val = c_i
                    cur_c_idx = i
                    eff_since_c = 0.0
                    eff_total = 0.0
                    time_since_x = 0
                else:
                    time_since_x += 1

                eff_total += eff_i
                if cur_p == 1:
                    if c_i > cur_c_val:
                        cur_c_val = c_i
                        cur_c_idx = i
                        eff_since_c = 0.0
                    else:
                        eff_since_c += eff_i
                else:
                    if c_i < cur_c_val:
                        cur_c_val = c_i
                        cur_c_idx = i
                        eff_since_c = 0.0
                    else:
                        eff_since_c += eff_i

            c_age = (n - 1) - cur_c_idx
            c_to_t_pct = (c_t - cur_c_val) / cur_c_val * 100.0
            c_velocity = c_to_t_pct / (c_age + 1.0)
            eff_decay = eff_since_c / (abs(eff_total) + eps)

            tcxa_feats[f'tcxa_{prefix}_phase'] = float(cur_p)
            tcxa_feats[f'tcxa_{prefix}_time_since_x'] = float(time_since_x)
            tcxa_feats[f'tcxa_{prefix}_c_to_t_pct'] = float(c_to_t_pct)
            tcxa_feats[f'tcxa_{prefix}_c_age_bars'] = float(c_age)
            tcxa_feats[f'tcxa_{prefix}_c_velocity'] = float(c_velocity)
            tcxa_feats[f'tcxa_{prefix}_efficiency_decay'] = float(eff_decay)

        # Assemble full structural feature vector
        features = {
            'rpi_h_pos': float(rpi_h_pos),
            'rpi_d_pos': float(rpi_d_pos),
            'rpi_wk_pos': float(rpi_wk_pos),
            'rpi_compression_h_in_d': float(rpi_compression_h_in_d),
            'rpi_compression_d_in_wk': float(rpi_compression_d_in_wk),
            'sdi_h_stretch': float(sdi_h_stretch),
            'sdi_h_zscore': float(sdi_h_zscore),
            'sdi_d_stretch': float(sdi_d_stretch),
            'sdi_d_zscore': float(sdi_d_zscore),
            'sdi_wk_stretch': float(sdi_wk_stretch),
            'sdi_wk_zscore': float(sdi_wk_zscore),
            'bar_body_ratio': float(bar_body_ratio),
            'bar_thrust_dir': float(bar_thrust_dir),
            'bar_rvol': float(bar_rvol),
            'ret_24h_pct': float(ret_24h_pct),
            'is_green': float(is_green),
            'is_yellow': float(is_yellow),
            'is_red': float(is_red),
            'is_purple': float(is_purple),
            'tactical_state_duration_bars': float(cur_duration),
            'is_micro_green': float(is_micro_green),
            'dist_to_shelf_floor_pct': float(dist_to_shelf_floor_pct),
            **tcxa_feats
        }

        return features


# ==============================================================================
# 2. INFERENCE ENGINE (GBDT MODEL & CHAMPION MANAGEMENT)
# ==============================================================================
class CryptoLiveInferenceEngine:
    """
    Manages calibrated GBDT probability classifiers and conviction cutoffs.
    Evaluates real-time barrier breach probabilities and emits TradeSignals.
    """

    def __init__(
        self,
        models_dir: Optional[Union[str, Path]] = None,
        discount_pct: float = 0.50,
        sell_premium_pct: float = 0.15
    ):
        self.models_dir = Path(models_dir) if models_dir else None
        self.discount_pct = discount_pct
        self.sell_premium_pct = sell_premium_pct
        self.models: Dict[str, Any] = {}
        self.cutoffs: Dict[str, float] = {}
        self.champions: Dict[str, Dict[str, Any]] = CRYPTO_CHAMPIONS.copy()

        if self.models_dir and self.models_dir.exists():
            self.load_models_from_dir(self.models_dir)

    def load_models_from_dir(self, models_dir: Union[str, Path], config_path: Optional[Union[str, Path]] = None) -> None:
        """
        Loads serialized .joblib models and their configuration from a directory.
        """
        p = Path(models_dir)
        if not p.exists():
            raise FileNotFoundError(f"Models directory not found: {p}")

        # Check config in config/ or same dir
        cfg_file = Path(config_path) if config_path else p.parent / "config" / "crypto_champions_config.json"
        if not cfg_file.exists():
            cfg_file = p / "crypto_champions_config.json"

        if cfg_file.exists():
            try:
                with open(cfg_file, 'r') as f:
                    cfg_data = json.load(f)
                    if "champions" in cfg_data:
                        for sym, c_info in cfg_data["champions"].items():
                            clean = sym.upper()
                            self.champions[clean] = c_info
                            if "cutoff_threshold" in c_info:
                                self.cutoffs[clean] = float(c_info["cutoff_threshold"])
            except Exception as e:
                logging.warning(f"Could not load config file {cfg_file}: {e}")

        # Load each .joblib model found in models_dir
        for file in p.glob("*_gbdt.joblib"):
            sym = file.stem.replace("_gbdt", "").upper()
            try:
                model = joblib.load(file)
                self.models[sym] = model
                logging.info(f"Loaded GBDT model for {sym} from {file.name}")

                # Check if dedicated {sym}_meta.json exists
                meta_file = p / f"{sym}_meta.json"
                if meta_file.exists():
                    try:
                        with open(meta_file, 'r') as mf:
                            meta = json.load(mf)
                        if "cutoff_threshold" in meta:
                            self.cutoffs[sym] = float(meta["cutoff_threshold"])
                        if sym in self.champions:
                            if "x_star" in meta:
                                self.champions[sym]["x_star"] = float(meta["x_star"])
                            if "y_star" in meta:
                                self.champions[sym]["y_star"] = float(meta["y_star"])
                        logging.info(f"Loaded metadata for {sym} from {meta_file.name}: cutoff={self.cutoffs.get(sym)}")
                    except Exception as me:
                        logging.warning(f"Could not load metadata from {meta_file}: {me}")
            except Exception as e:
                logging.error(f"Failed to load model {file}: {e}")

    def register_model(self, symbol: str, model: Any, cutoff_threshold: float, x_star: float, y_star: float) -> None:
        """Registers an in-memory trained GBDT model with its calibrated validation cutoff."""
        clean = symbol.upper()
        self.models[clean] = model
        self.cutoffs[clean] = cutoff_threshold
        if clean in self.champions:
            self.champions[clean]['x_star'] = x_star
            self.champions[clean]['y_star'] = y_star

    def evaluate(self, symbol: str, dt: pd.Timestamp, close_p: float, features: Dict[str, float]) -> Optional[TradeSignal]:
        """
        Evaluates a newly closed bar for a given symbol.
        Returns a TradeSignal if conviction >= cutoff threshold, else None.
        """
        clean = symbol.upper()
        if clean not in self.models:
            return None

        model = self.models[clean]
        cutoff = self.cutoffs.get(clean, 0.50)
        champ = self.champions.get(clean, {'x_star': 4.0, 'y_star': 2.0, 'binance_sym': clean})

        # Ensure features are arranged in the exact order the model expects
        feature_vec = np.array([[features[col] for col in MODEL_FEATURE_NAMES]], dtype=float)
        p_pred = float(model.predict_proba(feature_vec)[0, 1])

        is_signal = bool(p_pred >= cutoff)

        x_star = champ['x_star']
        y_star = champ['y_star']
        binance_sym = champ['binance_sym']

        # Pre-calculate Dual Passive Limit Execution Parameters:
        # Maker Limit Buy at discount (-0.50%)
        limit_buy_price = close_p * (1.0 - self.discount_pct / 100.0)
        # Maker Limit Take-Profit at target + premium (+x* + 0.15%)
        limit_sell_price = limit_buy_price * (1.0 + (x_star + self.sell_premium_pct) / 100.0)
        # Structural Stop Loss at -y*
        stop_loss_price = limit_buy_price * (1.0 - y_star / 100.0)

        return TradeSignal(
            timestamp=dt,
            kraken_symbol=clean,
            binance_symbol=binance_sym,
            close_price=close_p,
            p_pred=p_pred,
            cutoff_threshold=cutoff,
            x_star=x_star,
            y_star=y_star,
            is_signal=is_signal,
            limit_buy_price=round(limit_buy_price, 4),
            limit_sell_price=round(limit_sell_price, 4),
            stop_loss_price=round(stop_loss_price, 4),
            features=features
        )


# ==============================================================================
# 3. DUAL LIMIT EXECUTION ENGINE (CROSS-EXCHANGE ORDER BRIDGE)
# ==============================================================================
class CryptoDualLimitExecutionEngine:
    """
    Simulates / routes cross-exchange execution:
      - Takes signal generated from Kraken data.
      - Places Maker Limit Buy on BinanceUS at -d% discount (0.0% fee).
      - Upon fill, immediately places resting Maker Limit Sell at +(x* + p)% (0.0% fee).
      - Enforces slot concurrency (K=5) and compounded position sizing (e.g. 50% equity).
      - Strict balance sheet conservation (GEMINI.md compliant).
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        max_slots: int = 5,
        position_size_fraction: float = 0.50,
        discount_pct: float = 0.50,
        sell_premium_pct: float = 0.15,
        order_timeout_bars: int = 1
    ):
        self.initial_capital = initial_capital
        self.free_cash = initial_capital
        self.max_slots = max_slots
        self.position_size_fraction = position_size_fraction
        self.discount_pct = discount_pct
        self.sell_premium_pct = sell_premium_pct
        self.order_timeout_bars = order_timeout_bars

        self.active_positions: Dict[str, LivePosition] = {}
        self.closed_trades: List[Dict[str, Any]] = []

    def get_total_equity(self, current_prices: Dict[str, float]) -> float:
        """Calculates total portfolio equity = free cash + mark-to-market position value."""
        pos_val = 0.0
        for pos in self.active_positions.values():
            if pos.status == "FILLED":
                cur_p = current_prices.get(pos.binance_symbol, pos.entry_price)
                pos_val += pos.quantity * cur_p
            elif pos.status == "PENDING_BUY":
                pos_val += pos.allocated_capital
        return self.free_cash + pos_val

    def on_signal(self, signal: TradeSignal) -> Optional[LivePosition]:
        """Processes a new TradeSignal, allocating capital and creating an order if a slot is free."""
        if not signal.is_signal:
            return None

        # Anti-chatter: only one active trade per symbol at a time
        if signal.kraken_symbol in self.active_positions:
            return None

        # Concurrency slot check
        if len(self.active_positions) >= self.max_slots:
            return None

        # Proportional compounded position sizing
        tot_equity = self.free_cash + sum(p.allocated_capital for p in self.active_positions.values())
        alloc_cap = min(tot_equity * self.position_size_fraction, self.free_cash)

        if alloc_cap < 50.0:  # Minimum viable order size
            return None

        self.free_cash -= alloc_cap
        pos_id = f"{signal.binance_symbol}_{signal.timestamp.strftime('%Y%m%d%H%M')}"
        qty = alloc_cap / signal.limit_buy_price

        pos = LivePosition(
            position_id=pos_id,
            kraken_symbol=signal.kraken_symbol,
            binance_symbol=signal.binance_symbol,
            entry_timestamp=signal.timestamp,
            entry_price=signal.limit_buy_price,
            quantity=qty,
            allocated_capital=alloc_cap,
            limit_sell_price=signal.limit_sell_price,
            stop_loss_price=signal.stop_loss_price,
            timeout_bar_limit=self.order_timeout_bars,
            bars_held=0,
            status="PENDING_BUY"
        )
        self.active_positions[signal.kraken_symbol] = pos
        return pos

    def on_candle_update(self, kraken_symbol: str, high_p: float, low_p: float, close_p: float) -> Optional[Dict[str, Any]]:
        """
        Updates the position state based on new price candle:
          - Fills pending maker buy if low_p <= limit_buy_price.
          - Fills maker limit TP if high_p >= limit_sell_price (0.0% fee).
          - Triggers taker stop loss if low_p <= stop_loss_price (1.9 bps fee).
        """
        if kraken_symbol not in self.active_positions:
            return None

        pos = self.active_positions[kraken_symbol]
        pos.bars_held += 1

        # State 1: PENDING_BUY
        if pos.status == "PENDING_BUY":
            if low_p <= pos.entry_price:
                pos.status = "FILLED"
                # BinanceUS Maker entry: 0.0% fee drag!
                return {'event': 'ORDER_FILLED', 'pos': pos}
            elif pos.bars_held >= pos.timeout_bar_limit:
                # Cancel unfilled maker buy, release cash
                self.free_cash += pos.allocated_capital
                del self.active_positions[kraken_symbol]
                return {'event': 'BUY_CANCELLED_TIMEOUT', 'symbol': kraken_symbol}

        # State 2: FILLED (Open position)
        elif pos.status == "FILLED":
            # Check Take-Profit first (maker limit sell at +(x* + p)%)
            if high_p >= pos.limit_sell_price:
                # Maker TP exit: 0.0% fee!
                proceeds = pos.quantity * pos.limit_sell_price
                net_pnl = proceeds - pos.allocated_capital
                self.free_cash += proceeds
                del self.active_positions[kraken_symbol]
                record = {
                    'symbol': kraken_symbol,
                    'entry_price': pos.entry_price,
                    'exit_price': pos.limit_sell_price,
                    'exit_reason': 'TAKE_PROFIT_LIMIT',
                    'net_pnl': net_pnl,
                    'fee_paid': 0.0,
                    'bars_held': pos.bars_held
                }
                self.closed_trades.append(record)
                return {'event': 'POSITION_CLOSED_TP', 'record': record}

            # Check Stop-Loss (taker market exit at -y*)
            elif low_p <= pos.stop_loss_price:
                gross_proceeds = pos.quantity * pos.stop_loss_price
                fee = gross_proceeds * BINANCE_US_TAKER_FEE  # 1.9 bps
                net_proceeds = gross_proceeds - fee
                net_pnl = net_proceeds - pos.allocated_capital
                self.free_cash += net_proceeds
                del self.active_positions[kraken_symbol]
                record = {
                    'symbol': kraken_symbol,
                    'entry_price': pos.entry_price,
                    'exit_price': pos.stop_loss_price,
                    'exit_reason': 'STOP_LOSS_MARKET',
                    'net_pnl': net_pnl,
                    'fee_paid': fee,
                    'bars_held': pos.bars_held
                }
                self.closed_trades.append(record)
                return {'event': 'POSITION_CLOSED_STOP', 'record': record}

        return None


# ==============================================================================
# 4. LIVE PREDICTION & AUDIT LOGGER
# ==============================================================================
class LivePredictionLogger:
    """
    Logs live 15m feature vectors, model predictions, and trade decisions to JSONL.
    Serves as the ground truth input for the historical validation script.
    """

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log_prediction(self, signal: TradeSignal) -> None:
        """Appends a prediction event to symbol-specific JSONL."""
        log_file = self.log_dir / f"live_predictions_{signal.kraken_symbol}.jsonl"
        record = {
            'timestamp': signal.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'kraken_symbol': signal.kraken_symbol,
            'binance_symbol': signal.binance_symbol,
            'close_price': signal.close_price,
            'p_pred': signal.p_pred,
            'cutoff_threshold': signal.cutoff_threshold,
            'x_star': signal.x_star,
            'y_star': signal.y_star,
            'is_signal': signal.is_signal,
            'limit_buy_price': signal.limit_buy_price,
            'limit_sell_price': signal.limit_sell_price,
            'stop_loss_price': signal.stop_loss_price,
            'features': signal.features
        }
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')
