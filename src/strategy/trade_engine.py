"""
TradeEngine - Core Event-Driven Structural Wave & Inventory State Machine
Strictly causal, descriptive physical container state evaluation on 1H closed bars.
Operates with NumPy circular buffers without Pandas in the hot path.
"""
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Dict, Any
import numpy as np


class ActionType(Enum):
    PLACE_RESTING_LIMIT_BUY = "PLACE_RESTING_LIMIT_BUY"
    CANCEL_RESTING_LIMIT_BUY = "CANCEL_RESTING_LIMIT_BUY"
    SUBMIT_MONITORED_LIMIT_BUY = "SUBMIT_MONITORED_LIMIT_BUY"
    SUBMIT_MONITORED_LIMIT_SELL = "SUBMIT_MONITORED_LIMIT_SELL"
    # Research spec backward compatibility aliases
    EXECUTE_MARKET_BUY = "SUBMIT_MONITORED_LIMIT_BUY"
    EXECUTE_MARKET_SELL = "SUBMIT_MONITORED_LIMIT_SELL"
    UPDATE_PROFIT_TARGET = "UPDATE_PROFIT_TARGET"
    NO_ACTION = "NO_ACTION"


@dataclass
class TradeAction:
    action_type: ActionType
    price: float
    reason: str
    target_price: Optional[float] = None
    reload_index: int = 0


class TradeEngine:
    """
    Self-contained, stateful trading engine for live deployment in Accretion.
    Maintains internal circular buffers for rolling lookbacks and computes all 
    structural features on incoming raw OHLCV candles without external dependencies.
    """
    def __init__(
        self,
        initial_target: float = 0.12,
        purple_discount: float = 0.02,
        floor_target: float = 0.005,
        day_hours: int = 24,
        week_hours: int = 168,
        micro_floor_hours: int = 6
    ):
        self.t0 = float(initial_target)
        self.p_disc = float(purple_discount)
        self.t_floor = float(floor_target)
        self.day_hours = int(day_hours)
        self.week_hours = int(week_hours)
        self.mf_hours = int(micro_floor_hours)

        # Circular Rolling Buffers (FIFO arrays of length week_hours + 10)
        self.buf_size = self.week_hours + 10
        self.high_buf = np.zeros(self.buf_size, dtype=np.float64)
        self.low_buf = np.zeros(self.buf_size, dtype=np.float64)
        self.close_buf = np.zeros(self.buf_size, dtype=np.float64)
        self.bar_count = 0

        # State Machine Registers
        self.macro_state = "NEUTRAL"
        self.micro_state = "NEUTRAL"
        self.seen_micro_red = False
        self.seen_micro_purple_after_red = False

        # Position & Order State
        self.in_position = False
        self.entry_price = 0.0
        self.active_target_pct = self.t0
        self.reload_count = 0
        self.resting_limit_active = False
        self.resting_limit_price = 0.0

    def _get_max(self, buf: np.ndarray, window: int, offset: int = 1) -> float:
        """
        Extracts maximum over lookback window strictly excluding the current bar.
        offset=1 means lookback starts from bar t-1.
        """
        curr_idx = (self.bar_count - 1) % self.buf_size
        indices = (curr_idx - offset - np.arange(window)) % self.buf_size
        return float(np.max(buf[indices]))

    def _get_min(self, buf: np.ndarray, window: int, offset: int = 1) -> float:
        """
        Extracts minimum over lookback window strictly excluding the current bar.
        offset=1 means lookback starts from bar t-1.
        """
        curr_idx = (self.bar_count - 1) % self.buf_size
        indices = (curr_idx - offset - np.arange(window)) % self.buf_size
        return float(np.min(buf[indices]))

    def warm_up(self, candles: List[Dict[str, Any]]) -> int:
        """
        Warms circular buffers and updates state registers with historical candles.
        Emits no trade actions during warming.
        Returns the number of candles ingested.
        """
        count = 0
        for c in candles:
            # Handle dictionary formats
            t = int(c.get('time') or c.get('open_time', 0))
            o = float(c['open'])
            h = float(c['high'])
            l = float(c['low'])
            cl = float(c['close'])
            v = float(c.get('volume', 0.0))
            self.on_candle(t, o, h, l, cl, v, is_warming=True)
            count += 1
        return count

    def on_candle(
        self,
        timestamp: int,
        open_p: float,
        high_p: float,
        low_p: float,
        close_p: float,
        volume: float,
        is_warming: bool = False
    ) -> List[TradeAction]:
        """
        Streaming execution entrypoint called exactly once at the close of every 1-hour candle.
        Returns a list of actionable order directives (TradeAction) for the execution gateway.
        """
        actions: List[TradeAction] = []

        # 1. Update circular buffers
        idx = self.bar_count % self.buf_size
        self.high_buf[idx] = float(high_p)
        self.low_buf[idx] = float(low_p)
        self.close_buf[idx] = float(close_p)
        self.bar_count += 1

        if self.bar_count < self.week_hours:
            return actions  # Buffer warming period

        # 2. Extract prior extremes (strictly excluding current bar t)
        prior_d_h = self._get_max(self.high_buf, self.day_hours, offset=1)
        prior_d_l = self._get_min(self.low_buf, self.day_hours, offset=1)
        prior_wk_h = self._get_max(self.high_buf, self.week_hours, offset=1)
        prior_wk_l = self._get_min(self.low_buf, self.week_hours, offset=1)
        prior_mf_h = self._get_max(self.high_buf, self.mf_hours, offset=1)
        prior_mf_l = self._get_min(self.low_buf, self.mf_hours, offset=1)

        prev_macro = self.macro_state
        prev_micro = self.micro_state

        # 3. Macro State Machine Transition (1D in 7D)
        pushed_wk_up = (high_p > prior_wk_h)
        pushed_wk_dn = (low_p < prior_wk_l)

        if pushed_wk_up and not pushed_wk_dn:
            self.macro_state = "ACTIVE_BULL_WAVE"
        elif pushed_wk_dn and not pushed_wk_up:
            self.macro_state = "ACTIVE_BEAR_WAVE"
        elif pushed_wk_up and pushed_wk_dn:
            self.macro_state = "ACTIVE_BULL_WAVE" if close_p >= open_p else "ACTIVE_BEAR_WAVE"
        else:
            if self.macro_state == "ACTIVE_BULL_WAVE" and close_p < prior_d_l:
                self.macro_state = "BULL_EXHAUSTED"
            elif self.macro_state == "ACTIVE_BEAR_WAVE" and close_p > prior_d_h:
                self.macro_state = "BEAR_EXHAUSTED"

        # 4. Micro State Machine Transition (1H in 24H)
        pushed_d_up = (high_p > prior_d_h)
        pushed_d_dn = (low_p < prior_d_l)

        if pushed_d_up and not pushed_d_dn:
            self.micro_state = "MICRO_ACTIVE_BULL"
        elif pushed_d_dn and not pushed_d_up:
            self.micro_state = "MICRO_ACTIVE_BEAR"
        elif pushed_d_up and pushed_d_dn:
            self.micro_state = "MICRO_ACTIVE_BULL" if close_p >= open_p else "MICRO_ACTIVE_BEAR"
        else:
            if self.micro_state == "MICRO_ACTIVE_BULL" and close_p < prior_mf_l:
                self.micro_state = "MICRO_BULL_EXHAUSTED"
            elif self.micro_state == "MICRO_ACTIVE_BEAR" and close_p > prior_mf_h:
                self.micro_state = "MICRO_BEAR_EXHAUSTED"

        # 5. Purple Bottoming Sequence Tracking (BEAR -> BEAR_EXHAUST -> BULL)
        if self.macro_state == "BEAR_EXHAUSTED":
            if prev_macro != "BEAR_EXHAUSTED":
                self.seen_micro_red = False
                self.seen_micro_purple_after_red = False
            if self.micro_state == "MICRO_ACTIVE_BEAR":
                self.seen_micro_red = True
            elif self.micro_state == "MICRO_BEAR_EXHAUSTED" and self.seen_micro_red:
                self.seen_micro_purple_after_red = True
        else:
            self.seen_micro_red = False
            self.seen_micro_purple_after_red = False

        if is_warming:
            return actions

        # 6. Execution Directives & Order Logic
        # A. Position Management (Target Exits & Policy 4 Structural Invalidation Stop)
        if self.in_position:
            target_price = self.entry_price * (1.0 + self.active_target_pct)
            if high_p >= target_price:
                actions.append(TradeAction(
                    action_type=ActionType.SUBMIT_MONITORED_LIMIT_SELL,
                    price=target_price,
                    reason=f"PROFIT_TARGET_HIT (+{self.active_target_pct * 100:.2f}%)"
                ))
                self.in_position = False
            elif self.macro_state == "ACTIVE_BEAR_WAVE":
                actions.append(TradeAction(
                    action_type=ActionType.SUBMIT_MONITORED_LIMIT_SELL,
                    price=close_p,
                    reason="POLICY4_STRUCTURAL_INVALIDATION_STOP"
                ))
                self.in_position = False

        # B. Resting Purple Limit Fill Check & Transitions
        if not self.in_position and self.resting_limit_active:
            if low_p <= self.resting_limit_price:
                # Filled as Maker on bar low dip
                self.in_position = True
                self.entry_price = self.resting_limit_price
                self.active_target_pct = self.t0
                self.reload_count = 1
                self.resting_limit_active = False
            elif self.macro_state == "ACTIVE_BULL_WAVE":
                # Rocketed up before fill: cancel resting bid and market/urgent limit buy
                actions.append(TradeAction(
                    action_type=ActionType.CANCEL_RESTING_LIMIT_BUY,
                    price=self.resting_limit_price,
                    reason="CANCEL_PURPLE_ON_GREEN_BREAKOUT"
                ))
                actions.append(TradeAction(
                    action_type=ActionType.SUBMIT_MONITORED_LIMIT_BUY,
                    price=close_p,
                    reason="GREEN_BREAKOUT_FALLBACK_BUY",
                    target_price=close_p * (1.0 + self.t0),
                    reload_index=1
                ))
                self.in_position = True
                self.entry_price = close_p
                self.active_target_pct = self.t0
                self.reload_count = 1
                self.resting_limit_active = False
            elif self.macro_state == "ACTIVE_BEAR_WAVE":
                # Breakdown into Red: cancel resting bid immediately
                actions.append(TradeAction(
                    action_type=ActionType.CANCEL_RESTING_LIMIT_BUY,
                    price=self.resting_limit_price,
                    reason="CANCEL_PURPLE_ON_RED_BREAKDOWN"
                ))
                self.resting_limit_active = False

        # C. New Entries (When Flat and No Resting Limit Active)
        if not self.in_position and not self.resting_limit_active:
            if self.macro_state == "ACTIVE_BULL_WAVE" and prev_macro != "ACTIVE_BULL_WAVE":
                # Direct Macro Green Breakout
                actions.append(TradeAction(
                    action_type=ActionType.SUBMIT_MONITORED_LIMIT_BUY,
                    price=close_p,
                    reason="STANDARD_MACRO_GREEN_ENTRY",
                    target_price=close_p * (1.0 + self.t0),
                    reload_index=1
                ))
                self.in_position = True
                self.entry_price = close_p
                self.active_target_pct = self.t0
                self.reload_count = 1

            elif (
                self.macro_state == "BEAR_EXHAUSTED"
                and self.micro_state == "MICRO_ACTIVE_BULL"
                and prev_micro != "MICRO_ACTIVE_BULL"
                and self.seen_micro_purple_after_red
            ):
                # Purple Accumulation: 3-step bottom sequence completed
                limit_bid_p = close_p * (1.0 - self.p_disc)
                actions.append(TradeAction(
                    action_type=ActionType.PLACE_RESTING_LIMIT_BUY,
                    price=limit_bid_p,
                    reason="PURPLE_DISCOUNT_RESTING_BID"
                ))
                self.resting_limit_active = True
                self.resting_limit_price = limit_bid_p

            elif (
                self.macro_state == "BULL_EXHAUSTED"
                and self.micro_state == "MICRO_BEAR_EXHAUSTED"
                and prev_micro != "MICRO_BEAR_EXHAUSTED"
            ):
                # Yellow Pullback Reload Entry with decaying target
                self.reload_count += 1
                decayed_target = max(self.t_floor, self.t0 / (2.0 ** (self.reload_count - 1)))
                actions.append(TradeAction(
                    action_type=ActionType.SUBMIT_MONITORED_LIMIT_BUY,
                    price=close_p,
                    reason=f"YELLOW_PULLBACK_RELOAD_{self.reload_count}",
                    target_price=close_p * (1.0 + decayed_target),
                    reload_index=self.reload_count
                ))
                self.in_position = True
                self.entry_price = close_p
                self.active_target_pct = decayed_target

        return actions

    def sync_position(self, in_position: bool, entry_price: float = 0.0, reload_count: int = 0) -> None:
        """
        Synchronizes engine position state with exchange reconciliation.
        """
        self.in_position = in_position
        self.entry_price = float(entry_price)
        self.reload_count = int(reload_count)
        if in_position and reload_count > 0:
            self.active_target_pct = max(self.t_floor, self.t0 / (2.0 ** (self.reload_count - 1)))

    def to_dict(self) -> Dict[str, Any]:
        """
        Serializes the complete engine state for SQLite persistence.
        """
        return {
            't0': self.t0,
            'p_disc': self.p_disc,
            't_floor': self.t_floor,
            'day_hours': self.day_hours,
            'week_hours': self.week_hours,
            'mf_hours': self.mf_hours,
            'buf_size': self.buf_size,
            'bar_count': self.bar_count,
            'high_buf': self.high_buf.tolist(),
            'low_buf': self.low_buf.tolist(),
            'close_buf': self.close_buf.tolist(),
            'macro_state': self.macro_state,
            'micro_state': self.micro_state,
            'seen_micro_red': self.seen_micro_red,
            'seen_micro_purple_after_red': self.seen_micro_purple_after_red,
            'in_position': self.in_position,
            'entry_price': self.entry_price,
            'active_target_pct': self.active_target_pct,
            'reload_count': self.reload_count,
            'resting_limit_active': self.resting_limit_active,
            'resting_limit_price': self.resting_limit_price
        }

    def from_dict(self, d: Dict[str, Any]) -> None:
        """
        Restores engine state from a serialized dictionary.
        """
        self.t0 = float(d.get('t0', self.t0))
        self.p_disc = float(d.get('p_disc', self.p_disc))
        self.t_floor = float(d.get('t_floor', self.t_floor))
        self.day_hours = int(d.get('day_hours', self.day_hours))
        self.week_hours = int(d.get('week_hours', self.week_hours))
        self.mf_hours = int(d.get('mf_hours', self.mf_hours))
        self.buf_size = int(d.get('buf_size', self.buf_size))
        self.bar_count = int(d.get('bar_count', 0))

        if 'high_buf' in d:
            self.high_buf = np.array(d['high_buf'], dtype=np.float64)
        if 'low_buf' in d:
            self.low_buf = np.array(d['low_buf'], dtype=np.float64)
        if 'close_buf' in d:
            self.close_buf = np.array(d['close_buf'], dtype=np.float64)

        self.macro_state = d.get('macro_state', 'NEUTRAL')
        self.micro_state = d.get('micro_state', 'NEUTRAL')
        self.seen_micro_red = bool(d.get('seen_micro_red', False))
        self.seen_micro_purple_after_red = bool(d.get('seen_micro_purple_after_red', False))
        self.in_position = bool(d.get('in_position', False))
        self.entry_price = float(d.get('entry_price', 0.0))
        self.active_target_pct = float(d.get('active_target_pct', self.t0))
        self.reload_count = int(d.get('reload_count', 0))
        self.resting_limit_active = bool(d.get('resting_limit_active', False))
        self.resting_limit_price = float(d.get('resting_limit_price', 0.0))
