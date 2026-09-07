"""
Unit tests for OrderExecutionManager: resting purple bids, monitored immediate limits,
order chasing logic, and drift safety guards.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock
from src.strategy.trade_engine import TradeAction, ActionType
from src.runtime.order_manager import OrderExecutionManager, MonitoredOrder
from src.ledger.database import SQLiteLedger


class TestOrderExecutionManager(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_ledger.sqlite")
        self.ledger = SQLiteLedger(self.db_path)

        # Mock adapter
        self.adapter = MagicMock()
        self.adapter.format_price.side_effect = lambda sym, p: round(float(p), 2)
        self.adapter.format_quantity.side_effect = lambda sym, q: round(float(q), 6)
        self.adapter.get_ticker_price.return_value = 50000.0
        self.adapter.get_asset_balance.return_value = {'free': 0.1, 'locked': 0.0, 'total': 0.1}
        self.adapter.get_symbol_filters.return_value = {
            'step_size': 0.000001,
            'min_qty': 0.00001,
            'tick_size': 0.01,
            'min_notional': 5.0,
            'base_asset': 'BTC',
            'quote_asset': 'USDT'
        }
        self.adapter.place_limit_order.return_value = {
            'orderId': 98765,
            'status': 'NEW',
            'symbol': 'BTCUSDT'
        }
        self.adapter.cancel_order.return_value = {
            'orderId': 98765,
            'status': 'CANCELED'
        }

        self.manager = OrderExecutionManager(
            adapter=self.adapter,
            ledger=self.ledger,
            default_quote_allocation_usd=100.0,
            default_timeout_sec=10.0,
            default_max_drift_pct=0.005
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_resting_purple_bid_lifecycle(self):
        """Placing and cancelling a passive resting limit bid."""
        action = TradeAction(
            action_type=ActionType.PLACE_RESTING_LIMIT_BUY,
            price=49000.0,
            reason="PURPLE_DISCOUNT_RESTING_BID"
        )
        res = self.manager.handle_action(action, symbol="BTCUSDT")
        self.assertIsNotNone(res)
        self.assertIn("BTCUSDT", self.manager.resting_orders)

        # Verify recorded in ledger
        orders = self.ledger.get_active_orders("BTCUSDT")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]['price'], 49000.0)

        # Cancel resting bid
        cancel_act = TradeAction(
            action_type=ActionType.CANCEL_RESTING_LIMIT_BUY,
            price=49000.0,
            reason="CANCEL_PURPLE_ON_GREEN_BREAKOUT"
        )
        c_res = self.manager.handle_action(cancel_act, symbol="BTCUSDT")
        self.assertIsNotNone(c_res)
        self.assertNotIn("BTCUSDT", self.manager.resting_orders)

    def test_monitored_limit_buy_fill(self):
        """Monitored limit buy submits, gets monitored, and handles fill event."""
        action = TradeAction(
            action_type=ActionType.SUBMIT_MONITORED_LIMIT_BUY,
            price=50000.0,
            reason="STANDARD_MACRO_GREEN_ENTRY"
        )
        self.manager.handle_action(action, symbol="BTCUSDT")
        self.assertEqual(len(self.manager.monitored_orders), 1)

        loc_id = list(self.manager.monitored_orders.keys())[0]

        # Simulate exchange returning FILLED on tick
        self.adapter.get_order_status.return_value = {
            'status': 'FILLED',
            'executedQty': 0.002,
            'cummulativeQuoteQty': 100.0
        }

        events = self.manager.tick_monitored_orders()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event'], 'ORDER_FILLED')
        # Order should be removed from active monitored map
        self.assertEqual(len(self.manager.monitored_orders), 0)

        # Ledger status updated to FILLED
        order = self.ledger.get_order(local_order_id=loc_id)
        self.assertEqual(order['status'], 'FILLED')

    def test_monitored_limit_chase_on_timeout(self):
        """When an order stays unfilled past the timeout window, it cancels and re-quotes at current ticker."""
        action = TradeAction(
            action_type=ActionType.SUBMIT_MONITORED_LIMIT_BUY,
            price=50000.0,
            reason="YELLOW_PULLBACK_RELOAD"
        )
        self.manager.handle_action(action, symbol="BTCUSDT")
        loc_id = list(self.manager.monitored_orders.keys())[0]
        monitored = self.manager.monitored_orders[loc_id]

        # Simulate time passing beyond timeout window
        monitored.quote_time -= 15.0

        # Simulate order still NEW on exchange
        self.adapter.get_order_status.return_value = {
            'status': 'NEW',
            'executedQty': 0.0,
            'cummulativeQuoteQty': 0.0
        }
        # Ticker moved slightly (within drift limit)
        self.adapter.get_ticker_price.return_value = 50100.0
        self.adapter.place_limit_order.return_value = {
            'orderId': 112233,
            'status': 'NEW',
            'symbol': 'BTCUSDT'
        }

        self.manager.tick_monitored_orders()

        # Previous order cancelled, new replacement order placed
        self.adapter.cancel_order.assert_called()
        self.assertEqual(len(self.manager.monitored_orders), 1)
        new_loc_id = list(self.manager.monitored_orders.keys())[0]
        new_monitored = self.manager.monitored_orders[new_loc_id]
        self.assertEqual(new_monitored.replace_count, 1)
        self.assertEqual(new_monitored.last_quote_price, 50100.0)

    def test_max_drift_protection_guard(self):
        """When market price drifts beyond max_drift_pct on a buy, chase halts."""
        action = TradeAction(
            action_type=ActionType.SUBMIT_MONITORED_LIMIT_BUY,
            price=50000.0,
            reason="GREEN_ENTRY"
        )
        self.manager.handle_action(action, symbol="BTCUSDT")
        loc_id = list(self.manager.monitored_orders.keys())[0]
        monitored = self.manager.monitored_orders[loc_id]

        # Force timeout
        monitored.quote_time -= 15.0
        self.adapter.get_order_status.return_value = {
            'status': 'NEW',
            'executedQty': 0.0,
            'cummulativeQuoteQty': 0.0
        }
        # Price surged by 2.0% (51000 vs 50000), exceeding 0.5% max drift
        self.adapter.get_ticker_price.return_value = 51000.0

        self.manager.tick_monitored_orders()

        # Chase should halt and cancel
        self.adapter.cancel_order.assert_called()
        self.assertEqual(len(self.manager.monitored_orders), 0)
        order = self.ledger.get_order(local_order_id=loc_id)
        self.assertEqual(order['status'], 'CANCELED')


if __name__ == '__main__':
    unittest.main()
