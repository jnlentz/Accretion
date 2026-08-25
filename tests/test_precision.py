"""
Unit tests for precision float formatting and truncation utilities
"""
import unittest
from src.utils.precision import truncate_float, round_step_size, round_tick_size, get_precision_from_step

class TestPrecision(unittest.TestCase):

    def test_truncate_float(self):
        self.assertEqual(truncate_float(12.3456789, 2), 12.34)
        self.assertEqual(truncate_float(12.3456789, 4), 12.3456)
        self.assertEqual(truncate_float(12.3456789, 0), 12.0)
        self.assertEqual(truncate_float(0.0, 2), 0.0)
        self.assertEqual(truncate_float(None, 2), 0.0)

    def test_round_step_size(self):
        # BTC step size 0.00001
        self.assertEqual(round_step_size(0.1234567, 0.00001), 0.12345)
        # ETH step size 0.001
        self.assertEqual(round_step_size(1.56789, 0.001), 1.567)
        # Quantity smaller than step
        self.assertEqual(round_step_size(0.0005, 0.001), 0.0)

    def test_round_tick_size(self):
        # Price tick size 0.01
        self.assertEqual(round_tick_size(45234.5678, 0.01), 45234.57)
        # Price tick size 0.1
        self.assertEqual(round_tick_size(45234.5678, 0.1), 45234.6)
        # Price tick size 1.0
        self.assertEqual(round_tick_size(45234.5678, 1.0), 45235.0)

    def test_get_precision_from_step(self):
        self.assertEqual(get_precision_from_step(0.001), 3)
        self.assertEqual(get_precision_from_step(0.00001), 5)
        self.assertEqual(get_precision_from_step(1.0), 0)
        self.assertEqual(get_precision_from_step("0.01"), 2)

if __name__ == '__main__':
    unittest.main()
