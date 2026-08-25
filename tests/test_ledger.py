"""
Unit tests for SQLiteLedger database operations
"""
import os
import shutil
import tempfile
import unittest
from src.ledger.database import SQLiteLedger

class TestSQLiteLedger(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_ledger.sqlite")
        self.ledger = SQLiteLedger(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_balances_upsert_and_query(self):
        initial_balances = {
            'BTC': {'free': 0.5, 'locked': 0.1, 'total': 0.6},
            'USDT': {'free': 1000.0, 'locked': 500.0, 'total': 1500.0}
        }
        self.ledger.upsert_balances(initial_balances)
        
        stored = self.ledger.get_balances()
        self.assertEqual(stored['BTC']['free'], 0.5)
        self.assertEqual(stored['BTC']['locked'], 0.1)
        self.assertEqual(stored['BTC']['total'], 0.6)
        self.assertEqual(stored['USDT']['total'], 1500.0)

        # Update balances
        updated_balances = {
            'BTC': {'free': 0.4, 'locked': 0.2, 'total': 0.6},
            'ETH': {'free': 2.0, 'locked': 0.0, 'total': 2.0}
        }
        self.ledger.upsert_balances(updated_balances)
        stored_after = self.ledger.get_balances()
        self.assertEqual(stored_after['BTC']['free'], 0.4)
        self.assertEqual(stored_after['ETH']['total'], 2.0)

    def test_order_lifecycle(self):
        order_data = {
            'local_order_id': 'loc_order_001',
            'exchange_order_id': '123456',
            'symbol': 'BTCUSDT',
            'side': 'BUY',
            'order_type': 'LIMIT',
            'price': 50000.0,
            'orig_qty': 0.1,
            'status': 'NEW',
            'time_in_force': 'GTC',
            'client_tag': 'lot_1'
        }
        loc_id = self.ledger.record_order(order_data)
        self.assertEqual(loc_id, 'loc_order_001')

        # Retrieve order
        order = self.ledger.get_order(exchange_order_id='123456')
        self.assertIsNotNone(order)
        self.assertEqual(order['symbol'], 'BTCUSDT')
        self.assertEqual(order['status'], 'NEW')

        # Check active orders
        active = self.ledger.get_active_orders('BTCUSDT')
        self.assertEqual(len(active), 1)

        # Update order to filled
        updated = self.ledger.update_order_status(
            status='FILLED',
            exchange_order_id='123456',
            executed_qty=0.1,
            cummulative_quote_qty=5000.0
        )
        self.assertTrue(updated)

        active_after = self.ledger.get_active_orders('BTCUSDT')
        self.assertEqual(len(active_after), 0)

        filled_order = self.ledger.get_order(exchange_order_id='123456')
        self.assertEqual(filled_order['status'], 'FILLED')
        self.assertEqual(filled_order['executed_qty'], 0.1)

    def test_fills_recording(self):
        fill_1 = {
            'exchange_trade_id': 'trade_1001',
            'exchange_order_id': '123456',
            'symbol': 'BTCUSDT',
            'side': 'BUY',
            'price': 50000.0,
            'qty': 0.05,
            'quote_qty': 2500.0,
            'commission': 0.0,
            'commission_asset': 'USDT',
            'trade_time': 1700000000
        }
        # First insert
        self.assertTrue(self.ledger.record_fill(fill_1))
        # Duplicate insert (idempotency check)
        self.assertFalse(self.ledger.record_fill(fill_1))

        fills = self.ledger.get_recent_fills('BTCUSDT')
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]['exchange_trade_id'], 'trade_1001')

    def test_command_queue(self):
        cmd_id = self.ledger.submit_command(
            sender='TEST_CLI',
            command_type='PAUSE',
            payload={'reason': 'testing'}
        )
        pending = self.ledger.get_pending_commands()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['command_id'], cmd_id)
        self.assertEqual(pending[0]['command_type'], 'PAUSE')

        self.ledger.update_command_status(cmd_id, 'COMPLETED', result={'ok': True})
        pending_after = self.ledger.get_pending_commands()
        self.assertEqual(len(pending_after), 0)

if __name__ == '__main__':
    unittest.main()
