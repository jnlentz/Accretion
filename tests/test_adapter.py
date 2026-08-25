"""
Unit tests for BinanceSpotAdapter
"""
import unittest
from unittest.mock import MagicMock, patch
from src.adapters.binance_client import BinanceSpotAdapter

class TestBinanceSpotAdapter(unittest.TestCase):

    def setUp(self):
        self.adapter = BinanceSpotAdapter(
            api_key="test_key",
            api_secret="test_secret",
            tld="us"
        )
        self.adapter.client = MagicMock()

    def test_get_symbol_filters(self):
        self.adapter.client.get_symbol_info.return_value = {
            'symbol': 'BTCUSDT',
            'status': 'TRADING',
            'baseAsset': 'BTC',
            'quoteAsset': 'USDT',
            'filters': [
                {'filterType': 'LOT_SIZE', 'stepSize': '0.00001000', 'minQty': '0.00001000', 'maxQty': '1000.0'},
                {'filterType': 'PRICE_FILTER', 'tickSize': '0.01000000'},
                {'filterType': 'MIN_NOTIONAL', 'minNotional': '5.00000000'}
            ]
        }

        filters = self.adapter.get_symbol_filters('BTCUSDT')
        self.assertEqual(filters['step_size'], 0.00001)
        self.assertEqual(filters['tick_size'], 0.01)
        self.assertEqual(filters['min_notional'], 5.0)

    def test_format_price_and_quantity(self):
        self.adapter._symbol_filters_cache['BTCUSDT'] = {
            'symbol': 'BTCUSDT',
            'step_size': 0.00001,
            'tick_size': 0.01,
            'min_qty': 0.00001,
            'min_notional': 5.0
        }

        formatted_p = self.adapter.format_price('BTCUSDT', 51234.5678)
        self.assertEqual(formatted_p, 51234.57)

        formatted_q = self.adapter.format_quantity('BTCUSDT', 0.1234567)
        self.assertEqual(formatted_q, 0.12345)

    def test_get_account_balances(self):
        self.adapter.client.get_account.return_value = {
            'balances': [
                {'asset': 'BTC', 'free': '1.25000000', 'locked': '0.00000000'},
                {'asset': 'USDT', 'free': '500.00000000', 'locked': '250.00000000'},
                {'asset': 'ETH', 'free': '0.00000000', 'locked': '0.00000000'}
            ]
        }

        balances = self.adapter.get_account_balances()
        self.assertIn('BTC', balances)
        self.assertEqual(balances['BTC']['free'], 1.25)
        self.assertIn('USDT', balances)
        self.assertEqual(balances['USDT']['total'], 750.0)
        self.assertNotIn('ETH', balances)  # Zero balances excluded

    def test_place_limit_order(self):
        self.adapter._symbol_filters_cache['BTCUSDT'] = {
            'symbol': 'BTCUSDT',
            'step_size': 0.00001,
            'tick_size': 0.01,
            'min_qty': 0.00001,
            'min_notional': 5.0
        }
        self.adapter.client.create_order.return_value = {
            'symbol': 'BTCUSDT',
            'orderId': 100200,
            'clientOrderId': 'cid_test_01',
            'status': 'NEW'
        }

        order = self.adapter.place_limit_order(
            symbol='BTCUSDT',
            side='BUY',
            price=45000.567,
            quantity=0.100008,
            client_order_id='cid_test_01'
        )
        self.assertEqual(order['orderId'], 100200)
        self.adapter.client.create_order.assert_called_once_with(
            symbol='BTCUSDT',
            side='BUY',
            type='LIMIT',
            timeInForce='GTC',
            quantity='0.1',
            price='45000.57',
            newClientOrderId='cid_test_01'
        )

    def test_place_limit_order_min_notional_validation(self):
        self.adapter._symbol_filters_cache['BTCUSDT'] = {
            'symbol': 'BTCUSDT',
            'step_size': 0.00001,
            'tick_size': 0.01,
            'min_qty': 0.00001,
            'min_notional': 10.0
        }
        # 0.0001 @ 50000 = $5.00 < $10.00
        with self.assertRaises(ValueError):
            self.adapter.place_limit_order(symbol='BTCUSDT', side='BUY', price=50000.0, quantity=0.0001)

if __name__ == '__main__':
    unittest.main()
