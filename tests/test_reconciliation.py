"""
Unit tests for ReconciliationEngine
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock
from src.ledger.database import SQLiteLedger
from src.ledger.reconciliation import ReconciliationEngine

class TestReconciliationEngine(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_reconcile_ledger.sqlite")
        self.ledger = SQLiteLedger(self.db_path)
        self.adapter = MagicMock()
        self.reconciliation_engine = ReconciliationEngine(
            adapter=self.adapter,
            ledger=self.ledger,
            target_symbols=['BTCUSDT']
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_balance_reconciliation(self):
        # Local starts empty
        self.adapter.get_account_balances.return_value = {
            'BTC': {'free': 1.0, 'locked': 0.0, 'total': 1.0},
            'USDT': {'free': 5000.0, 'locked': 1000.0, 'total': 6000.0}
        }
        res = self.reconciliation_engine.reconcile_balances()
        self.assertEqual(res['balances_updated'], 2)
        self.assertEqual(res['mismatches'], 2)

        # Second run: already synchronized
        res2 = self.reconciliation_engine.reconcile_balances()
        self.assertEqual(res2['mismatches'], 0)

    def test_order_reconciliation_untracked_and_closed(self):
        # 1. Exchange has an open order not known locally
        self.adapter.get_open_orders.return_value = [
            {
                'orderId': 99901,
                'clientOrderId': 'cid_99901',
                'symbol': 'BTCUSDT',
                'side': 'BUY',
                'type': 'LIMIT',
                'price': '45000.00',
                'origQty': '0.500000',
                'executedQty': '0.000000',
                'cummulativeQuoteQty': '0.00',
                'status': 'NEW',
                'timeInForce': 'GTC',
                'time': 1700000000000
            }
        ]
        res = self.reconciliation_engine.reconcile_orders_for_symbol('BTCUSDT')
        self.assertEqual(res['orders_healed'], 1)

        # Check that order was imported to ledger
        imported_order = self.ledger.get_order(exchange_order_id='99901')
        self.assertIsNotNone(imported_order)
        self.assertEqual(imported_order['status'], 'NEW')
        self.assertEqual(imported_order['price'], 45000.0)

        # 2. Next cycle: Order was filled on exchange (now absent from get_open_orders)
        self.adapter.get_open_orders.return_value = []
        self.adapter.get_order_status.return_value = {
            'orderId': 99901,
            'symbol': 'BTCUSDT',
            'status': 'FILLED',
            'executedQty': '0.500000',
            'cummulativeQuoteQty': '22500.00'
        }

        res2 = self.reconciliation_engine.reconcile_orders_for_symbol('BTCUSDT')
        self.assertEqual(res2['orders_healed'], 1)

        # Verify status healed to FILLED
        healed_order = self.ledger.get_order(exchange_order_id='99901')
        self.assertEqual(healed_order['status'], 'FILLED')
        self.assertEqual(healed_order['executed_qty'], 0.5)

    def test_sync_fills(self):
        self.adapter.get_recent_fills.return_value = [
            {
                'id': 8801,
                'orderId': 99901,
                'symbol': 'BTCUSDT',
                'isBuyer': True,
                'price': '45000.00',
                'qty': '0.500000',
                'quoteQty': '22500.00',
                'commission': '0.000000',
                'commissionAsset': 'USDT',
                'time': 1700000050000
            }
        ]
        res = self.reconciliation_engine.sync_fills_for_symbol('BTCUSDT')
        self.assertEqual(res['new_fills'], 1)

        fills = self.ledger.get_recent_fills('BTCUSDT')
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]['exchange_trade_id'], '8801')

if __name__ == '__main__':
    unittest.main()
