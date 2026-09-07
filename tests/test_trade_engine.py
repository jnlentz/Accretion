"""
Unit tests for TradeEngine structural state machine, rolling containers,
causal buffers, and decaying profit target ladder.
"""
import unittest
import numpy as np
from src.strategy.trade_engine import TradeEngine, TradeAction, ActionType


class TestTradeEngine(unittest.TestCase):

    def setUp(self):
        self.engine = TradeEngine(
            initial_target=0.12,
            purple_discount=0.02,
            floor_target=0.005,
            day_hours=24,
            week_hours=168,
            micro_floor_hours=6
        )

    def _feed_neutral_bars(self, count: int = 168, base_price: float = 50000.0):
        """Helper to feed flat/neutral bars to warm up circular buffers."""
        for i in range(count):
            self.engine.on_candle(
                timestamp=1000 + i * 3600,
                open_p=base_price,
                high_p=base_price + 100.0,
                low_p=base_price - 100.0,
                close_p=base_price,
                volume=10.0
            )

    def test_buffer_warming(self):
        """Actions should not be emitted before week_hours bars are ingested."""
        # Ingest 167 bars
        for i in range(167):
            actions = self.engine.on_candle(1000 + i * 3600, 50000, 50100, 49900, 50000, 10)
            self.assertEqual(len(actions), 0)
        self.assertEqual(self.engine.bar_count, 167)
        self.assertEqual(self.engine.macro_state, "NEUTRAL")

    def test_causal_lookback_excludes_current_bar(self):
        """Prior weekly/daily extremes must strictly exclude the forming/current bar t."""
        self._feed_neutral_bars(168, base_price=50000.0)
        # Bar 169 has an extreme spike in high
        # Prior weekly high should still reflect the previous 168 bars (50100), not this new bar's 60000
        prior_wk_h = self.engine._get_max(self.engine.high_buf, 168, offset=1)
        self.assertEqual(prior_wk_h, 50100.0)

    def test_green_breakout_transition(self):
        """Breaching prior weekly high should transition to ACTIVE_BULL_WAVE and emit monitored buy."""
        self._feed_neutral_bars(168, base_price=50000.0)
        
        # Next bar pushes higher than 50100.0
        actions = self.engine.on_candle(
            timestamp=200000,
            open_p=50000.0,
            high_p=51000.0,  # Breaks prior weekly high
            low_p=49950.0,
            close_p=50800.0,
            volume=50.0
        )
        self.assertEqual(self.engine.macro_state, "ACTIVE_BULL_WAVE")
        self.assertTrue(self.engine.in_position)
        self.assertEqual(self.engine.entry_price, 50800.0)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, ActionType.SUBMIT_MONITORED_LIMIT_BUY)
        self.assertEqual(actions[0].reason, "STANDARD_MACRO_GREEN_ENTRY")

    def test_bull_exhaustion_and_yellow_reload(self):
        """Dropping below 24h floor transitions to BULL_EXHAUSTED; micro rebound triggers reload."""
        self._feed_neutral_bars(168, base_price=50000.0)
        # Breakout to Green
        self.engine.on_candle(200000, 50000, 52000, 50000, 51500, 50)
        self.assertEqual(self.engine.macro_state, "ACTIVE_BULL_WAVE")

        # Prior daily low was 49900. Drop close below it
        self.engine.on_candle(203600, 50000, 50100, 49000, 49500, 50)
        self.assertEqual(self.engine.macro_state, "BULL_EXHAUSTED")

        # Transition micro to MICRO_BEAR_EXHAUSTED to trigger reload
        self.engine.micro_state = "MICRO_ACTIVE_BEAR"
        prior_mf_h = self.engine._get_max(self.engine.high_buf, 6, offset=1)
        
        # Next bar closes above prior 6h ceiling
        actions = self.engine.on_candle(
            timestamp=207200,
            open_p=49600,
            high_p=prior_mf_h + 200,
            low_p=49500,
            close_p=prior_mf_h + 100,
            volume=30
        )
        self.assertEqual(self.engine.micro_state, "MICRO_BEAR_EXHAUSTED")
        # Reload executed
        self.assertEqual(self.engine.reload_count, 2)
        # Decayed target: T0 / 2^1 = 12% / 2 = 6%
        self.assertAlmostEqual(self.engine.active_target_pct, 0.06, places=4)
        reload_actions = [a for a in actions if a.action_type == ActionType.SUBMIT_MONITORED_LIMIT_BUY]
        self.assertEqual(len(reload_actions), 1)
        self.assertIn("YELLOW_PULLBACK_RELOAD", reload_actions[0].reason)

    def test_purple_accumulation_sequence(self):
        """In BEAR_EXHAUSTED, micro sequence BEAR -> BEAR_EXHAUST -> BULL triggers resting limit bid."""
        self._feed_neutral_bars(168, base_price=50000.0)
        # Push into ACTIVE_BEAR_WAVE
        self.engine.on_candle(200000, 50000, 50000, 48000, 48500, 50)
        self.assertEqual(self.engine.macro_state, "ACTIVE_BEAR_WAVE")

        # Break 24h ceiling to enter BEAR_EXHAUSTED
        prior_d_h = self.engine._get_max(self.engine.high_buf, 24, offset=1)
        self.engine.on_candle(203600, 49000, prior_d_h + 100, 48500, prior_d_h + 50, 40)
        self.assertEqual(self.engine.macro_state, "BEAR_EXHAUSTED")

        # Micro step 1: MICRO_ACTIVE_BEAR
        self.engine.micro_state = "MICRO_ACTIVE_BEAR"
        self.engine.seen_micro_red = True

        # Micro step 2: MICRO_BEAR_EXHAUSTED
        self.engine.micro_state = "MICRO_BEAR_EXHAUSTED"
        self.engine.seen_micro_purple_after_red = True

        # Micro step 3: push new 24h high -> MICRO_ACTIVE_BULL
        prior_d_h = self.engine._get_max(self.engine.high_buf, 24, offset=1)
        actions = self.engine.on_candle(207200, 49000, prior_d_h + 200, 48900, prior_d_h + 100, 50)

        self.assertEqual(self.engine.micro_state, "MICRO_ACTIVE_BULL")
        self.assertTrue(self.engine.resting_limit_active)
        # 2% discount below close
        expected_bid = (prior_d_h + 100) * 0.98
        self.assertAlmostEqual(self.engine.resting_limit_price, expected_bid, places=2)
        resting_actions = [a for a in actions if a.action_type == ActionType.PLACE_RESTING_LIMIT_BUY]
        self.assertEqual(len(resting_actions), 1)

    def test_invalidation_stop_on_macro_red(self):
        """Holding a position through yellow and encountering ACTIVE_BEAR_WAVE exits immediately."""
        self._feed_neutral_bars(168, base_price=50000.0)
        # Enter position
        self.engine.in_position = True
        self.engine.entry_price = 50000.0
        self.engine.macro_state = "BULL_EXHAUSTED"

        # Crash through weekly low (49900)
        actions = self.engine.on_candle(200000, 49500, 49600, 47000, 47200, 100)
        self.assertEqual(self.engine.macro_state, "ACTIVE_BEAR_WAVE")
        self.assertFalse(self.engine.in_position)
        sell_actions = [a for a in actions if a.action_type == ActionType.SUBMIT_MONITORED_LIMIT_SELL]
        self.assertEqual(len(sell_actions), 1)
        self.assertEqual(sell_actions[0].reason, "POLICY4_STRUCTURAL_INVALIDATION_STOP")

    def test_state_serialization(self):
        """Engine state should serialize and deserialize losslessly."""
        self._feed_neutral_bars(168, base_price=50000.0)
        self.engine.macro_state = "BULL_EXHAUSTED"
        self.engine.micro_state = "MICRO_BEAR_EXHAUSTED"
        self.engine.in_position = True
        self.engine.entry_price = 52100.0
        self.engine.reload_count = 3

        saved_dict = self.engine.to_dict()
        
        new_engine = TradeEngine()
        new_engine.from_dict(saved_dict)

        self.assertEqual(new_engine.bar_count, 168)
        self.assertEqual(new_engine.macro_state, "BULL_EXHAUSTED")
        self.assertEqual(new_engine.micro_state, "MICRO_BEAR_EXHAUSTED")
        self.assertTrue(new_engine.in_position)
        self.assertEqual(new_engine.entry_price, 52100.0)
        self.assertEqual(new_engine.reload_count, 3)
        self.assertEqual(len(new_engine.high_buf), len(self.engine.high_buf))


if __name__ == '__main__':
    unittest.main()
