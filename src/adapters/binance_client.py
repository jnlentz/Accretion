"""
Binance Spot Exchange Adapter
Multi-symbol REST API wrapper for account polling, order execution, and fills tracking.
"""
import time
import logging
from typing import Optional, Dict, Any, List, Union
from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_LIMIT, TIME_IN_FORCE_GTC
from binance.exceptions import BinanceAPIException, BinanceRequestException
from src.utils.precision import round_step_size, round_tick_size, truncate_float

logger = logging.getLogger("accretion.adapter.binance")

class BinanceSpotAdapter:
    """
    Stateless REST adapter for Binance.US and Binance Global spot accounts.
    Provides multi-symbol order execution, balance tracking, fill history, and filter formatting.
    """
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        tld: str = 'us',
        testnet: bool = False,
        requests_params: Optional[Dict[str, Any]] = None
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.tld = tld
        self.testnet = testnet
        self.requests_params = requests_params or {'timeout': 30}
        
        self.client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            tld=self.tld,
            testnet=self.testnet,
            requests_params=self.requests_params
        )
        self._symbol_filters_cache: Dict[str, Dict[str, Any]] = {}

    def get_symbol_filters(self, symbol: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fetches and caches symbol trading rules (tick size, step size, min notional, min quantity).
        """
        symbol = symbol.upper()
        if not force_refresh and symbol in self._symbol_filters_cache:
            return self._symbol_filters_cache[symbol]

        try:
            info = self.client.get_symbol_info(symbol=symbol)
            if not info:
                logger.warning(f"Symbol info not found for {symbol}")
                return {}

            filters = {
                'symbol': symbol,
                'status': info.get('status', 'TRADING'),
                'base_asset': info.get('baseAsset', ''),
                'quote_asset': info.get('quoteAsset', ''),
                'step_size': 0.000001,
                'min_qty': 0.000001,
                'max_qty': 1000000.0,
                'tick_size': 0.01,
                'min_notional': 5.0,
            }

            for f in info.get('filters', []):
                f_type = f.get('filterType')
                if f_type == 'LOT_SIZE':
                    filters['step_size'] = float(f.get('stepSize', 0.000001))
                    filters['min_qty'] = float(f.get('minQty', 0.000001))
                    filters['max_qty'] = float(f.get('maxQty', 1000000.0))
                elif f_type == 'PRICE_FILTER':
                    filters['tick_size'] = float(f.get('tickSize', 0.01))
                elif f_type in ('MIN_NOTIONAL', 'NOTIONAL'):
                    filters['min_notional'] = float(f.get('minNotional', f.get('notional', 5.0)))

            self._symbol_filters_cache[symbol] = filters
            return filters
        except Exception as e:
            logger.error(f"Error fetching symbol info for {symbol}: {e}")
            return {}

    def format_price(self, symbol: str, price: float) -> float:
        """Rounds price to valid tick size for symbol."""
        filters = self.get_symbol_filters(symbol)
        tick_size = filters.get('tick_size', 0.01)
        return round_tick_size(price, tick_size)

    def format_quantity(self, symbol: str, quantity: float) -> float:
        """Rounds quantity down to valid step size for symbol."""
        filters = self.get_symbol_filters(symbol)
        step_size = filters.get('step_size', 0.000001)
        return round_step_size(quantity, step_size)

    def get_account_balances(self) -> Dict[str, Dict[str, float]]:
        """
        Fetches all asset balances from exchange.
        Returns: {asset: {'free': float, 'locked': float, 'total': float}}
        """
        try:
            account = self.client.get_account()
            balances: Dict[str, Dict[str, float]] = {}
            for item in account.get('balances', []):
                asset = item.get('asset', '').upper()
                free = float(item.get('free', 0.0))
                locked = float(item.get('locked', 0.0))
                total = free + locked
                if total > 0.0:
                    balances[asset] = {
                        'free': free,
                        'locked': locked,
                        'total': total
                    }
            return balances
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Binance API error fetching balances: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching balances: {e}")
            raise

    def get_asset_balance(self, asset: str) -> Dict[str, float]:
        """Returns free, locked, and total balance for a specific asset."""
        balances = self.get_account_balances()
        return balances.get(asset.upper(), {'free': 0.0, 'locked': 0.0, 'total': 0.0})

    def get_ticker_price(self, symbol: str) -> float:
        """Returns the latest ticker price for symbol."""
        try:
            res = self.client.get_symbol_ticker(symbol=symbol.upper())
            return float(res['price'])
        except Exception as e:
            logger.error(f"Error fetching ticker price for {symbol}: {e}")
            return 0.0

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetches open orders from exchange.
        If symbol is None, fetches all open orders.
        """
        try:
            if symbol:
                return self.client.get_open_orders(symbol=symbol.upper())
            return self.client.get_open_orders()
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"API error fetching open orders: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching open orders: {e}")
            raise

    def get_order_status(
        self,
        symbol: str,
        order_id: Optional[Union[str, int]] = None,
        client_order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Queries exchange status for a specific order by exchange orderId or origClientOrderId.
        """
        try:
            kwargs: Dict[str, Any] = {'symbol': symbol.upper()}
            if order_id is not None:
                kwargs['orderId'] = int(order_id)
            elif client_order_id is not None:
                kwargs['origClientOrderId'] = client_order_id
            else:
                raise ValueError("Must provide either order_id or client_order_id")

            return self.client.get_order(**kwargs)
        except BinanceAPIException as e:
            if e.code == -2013:  # Order does not exist
                logger.warning(f"Order not found on exchange: {symbol} (id={order_id}, client_id={client_order_id})")
                return None
            logger.error(f"Binance API error querying order: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error querying order: {e}")
            raise

    def get_recent_fills(
        self,
        symbol: str,
        limit: int = 100,
        from_id: Optional[int] = None,
        start_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetches recent account trades (fills) for a symbol.
        """
        try:
            kwargs: Dict[str, Any] = {
                'symbol': symbol.upper(),
                'limit': min(limit, 1000)
            }
            if from_id is not None:
                kwargs['fromId'] = from_id
            if start_time is not None:
                kwargs['startTime'] = start_time

            return self.client.get_my_trades(**kwargs)
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"API error fetching recent fills for {symbol}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching recent fills for {symbol}: {e}")
            raise

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        client_order_id: Optional[str] = None,
        time_in_force: str = TIME_IN_FORCE_GTC
    ) -> Dict[str, Any]:
        """
        Places a limit order on Binance Spot with precision formatting.
        """
        symbol = symbol.upper()
        side = side.upper()
        if side not in (SIDE_BUY, SIDE_SELL):
            raise ValueError(f"Invalid order side: {side}. Must be 'BUY' or 'SELL'.")

        formatted_price = self.format_price(symbol, price)
        formatted_qty = self.format_quantity(symbol, quantity)

        filters = self.get_symbol_filters(symbol)
        min_qty = filters.get('min_qty', 0.0)
        min_notional = filters.get('min_notional', 0.0)

        if formatted_qty < min_qty:
            raise ValueError(f"Quantity {formatted_qty} is below min quantity {min_qty} for {symbol}")

        notional = formatted_price * formatted_qty
        if notional < min_notional:
            raise ValueError(f"Notional value ${notional:.2f} is below min notional ${min_notional:.2f} for {symbol}")

        order_params: Dict[str, Any] = {
            'symbol': symbol,
            'side': side,
            'type': ORDER_TYPE_LIMIT,
            'timeInForce': time_in_force,
            'quantity': f"{formatted_qty:.8f}".rstrip('0').rstrip('.'),
            'price': f"{formatted_price:.8f}".rstrip('0').rstrip('.')
        }
        if client_order_id:
            order_params['newClientOrderId'] = client_order_id

        logger.info(f"Submitting {side} LIMIT order: {symbol} {formatted_qty} @ {formatted_price} (CID={client_order_id})")
        try:
            return self.client.create_order(**order_params)
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Failed to place limit order on {symbol}: {e}")
            raise

    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[Union[str, int]] = None,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cancels an open order by exchange orderId or origClientOrderId.
        """
        try:
            kwargs: Dict[str, Any] = {'symbol': symbol.upper()}
            if order_id is not None:
                kwargs['orderId'] = int(order_id)
            elif client_order_id is not None:
                kwargs['origClientOrderId'] = client_order_id
            else:
                raise ValueError("Must provide either order_id or client_order_id")

            logger.info(f"Cancelling order for {symbol}: id={order_id}, cid={client_order_id}")
            return self.client.cancel_order(**kwargs)
        except BinanceAPIException as e:
            if e.code == -2011:  # Unknown order sent (already filled or canceled)
                logger.warning(f"Order already canceled or filled on exchange: {symbol} (id={order_id})")
                return {'status': 'ALREADY_CLOSED', 'orderId': order_id}
            logger.error(f"Error cancelling order on {symbol}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error cancelling order on {symbol}: {e}")
            raise

    def get_klines(self, symbol: str, interval: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Fetches the latest klines (OHLCV candles) for symbol and timeframe.
        """
        try:
            raw = self.client.get_klines(symbol=symbol.upper(), interval=interval, limit=limit)
            results: List[Dict[str, Any]] = []
            for k in raw:
                results.append({
                    'open_time_ms': int(k[0]),
                    'open_time': int(k[0]) // 1000,
                    'open': float(k[1]),
                    'high': float(k[2]),
                    'low': float(k[3]),
                    'close': float(k[4]),
                    'volume': float(k[5]),
                    'close_time_ms': int(k[6]),
                    'close_time': int(k[6]) // 1000,
                    'quote_volume': float(k[7]),
                    'trade_count': int(k[8])
                })
            return results
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol} ({interval}): {e}")
            return []
