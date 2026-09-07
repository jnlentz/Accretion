"""
OrderExecutionManager - Monitored Limit & Resting Order Execution Router
Enforces the Zero-Market-Order mandate by executing urgent actions as actively monitored,
chased limit orders and managing passive resting limit bids for structural accumulation.
"""
import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from src.strategy.trade_engine import TradeAction, ActionType
from src.adapters.binance_client import BinanceSpotAdapter
from src.ledger.database import SQLiteLedger

logger = logging.getLogger("accretion.runtime.order_manager")


@dataclass
class MonitoredOrder:
    """Tracks the lifecycle of an urgent limit order undergoing high-frequency chase monitoring."""
    local_order_id: str
    exchange_order_id: Optional[str]
    symbol: str
    side: str                            # 'BUY' or 'SELL'
    target_qty: float
    executed_qty: float
    initial_price: float
    last_quote_price: float
    quote_time: float
    timeout_window_sec: float = 20.0     # Re-evaluate and chase after W seconds
    max_drift_pct: float = 0.005         # 0.5% max drift protection ceiling
    reason: str = ""
    replace_count: int = 0
    status: str = "ACTIVE"               # 'ACTIVE', 'FILLED', 'CANCELED', 'DRIFT_EXCEEDED'


class OrderExecutionManager:
    """
    Translates TradeEngine directives into exchange-compliant limit orders.
    Manages resting limit bids and high-frequency active order chasing.
    """
    def __init__(
        self,
        adapter: BinanceSpotAdapter,
        ledger: SQLiteLedger,
        default_quote_allocation_usd: float = 100.0,
        default_timeout_sec: float = 20.0,
        default_max_drift_pct: float = 0.005
    ):
        self.adapter = adapter
        self.ledger = ledger
        self.default_quote_allocation_usd = float(default_quote_allocation_usd)
        self.default_timeout_sec = float(default_timeout_sec)
        self.default_max_drift_pct = float(default_max_drift_pct)

        # In-memory tracking registers
        self.resting_orders: Dict[str, Dict[str, Any]] = {}       # {symbol: order_info}
        self.monitored_orders: Dict[str, MonitoredOrder] = {}      # {local_order_id: MonitoredOrder}

    def handle_action(
        self,
        action: TradeAction,
        symbol: str,
        quantity: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Routes incoming TradeAction to either resting limit or monitored limit handlers.
        """
        symbol = symbol.upper()
        act_type = action.action_type

        # 1. Passive Resting Limit Buy (Purple Accumulation)
        if act_type == ActionType.PLACE_RESTING_LIMIT_BUY:
            qty = quantity or self._calculate_buy_qty(symbol, action.price)
            return self.place_resting_purple_bid(symbol, action.price, qty, action.reason)

        # 2. Cancel Passive Resting Limit Buy
        elif act_type == ActionType.CANCEL_RESTING_LIMIT_BUY:
            return self.cancel_resting_purple_bid(symbol, action.reason)

        # 3. Urgent Monitored Limit Buy (Green Breakout, Yellow Reload, Fallback Buy)
        elif act_type in (ActionType.SUBMIT_MONITORED_LIMIT_BUY, ActionType.EXECUTE_MARKET_BUY):
            qty = quantity or self._calculate_buy_qty(symbol, action.price)
            return self.submit_monitored_limit(
                symbol=symbol,
                side="BUY",
                price=action.price,
                quantity=qty,
                reason=action.reason,
                timeout_sec=self.default_timeout_sec,
                max_drift_pct=self.default_max_drift_pct
            )

        # 4. Urgent Monitored Limit Sell (Profit Target, Structural Stop Loss)
        elif act_type in (ActionType.SUBMIT_MONITORED_LIMIT_SELL, ActionType.EXECUTE_MARKET_SELL):
            qty = quantity or self._get_sellable_qty(symbol)
            return self.submit_monitored_limit(
                symbol=symbol,
                side="SELL",
                price=action.price,
                quantity=qty,
                reason=action.reason,
                timeout_sec=self.default_timeout_sec,
                max_drift_pct=self.default_max_drift_pct
            )

        elif act_type == ActionType.NO_ACTION:
            return None

        else:
            logger.warning(f"Unhandled action type: {act_type} for {symbol}")
            return None

    def place_resting_purple_bid(
        self,
        symbol: str,
        price: float,
        quantity: float,
        reason: str
    ) -> Optional[Dict[str, Any]]:
        """
        Submits a passive resting limit bid placed at a discount (Purple entry).
        """
        symbol = symbol.upper()
        formatted_price = self.adapter.format_price(symbol, price)
        formatted_qty = self.adapter.format_quantity(symbol, quantity)

        # Cancel any existing resting bid on this symbol first
        if symbol in self.resting_orders:
            logger.info(f"Cancelling prior resting bid before placing new one for {symbol}")
            self.cancel_resting_purple_bid(symbol, reason="REPLACE_RESTING_BID")

        local_id = f"purple_{uuid.uuid4().hex[:10]}"

        try:
            order_res = self.adapter.place_limit_order(
                symbol=symbol,
                side="BUY",
                price=formatted_price,
                quantity=formatted_qty,
                client_order_id=local_id
            )
            exch_id = str(order_res.get('orderId', ''))

            # Persist to SQLite ledger
            self.ledger.record_order({
                'local_order_id': local_id,
                'exchange_order_id': exch_id,
                'symbol': symbol,
                'side': 'BUY',
                'order_type': 'LIMIT',
                'price': formatted_price,
                'orig_qty': formatted_qty,
                'status': 'NEW',
                'time_in_force': 'GTC',
                'client_tag': f"PURPLE_RESTING_BID:{reason}"
            })

            self.resting_orders[symbol] = {
                'local_order_id': local_id,
                'exchange_order_id': exch_id,
                'price': formatted_price,
                'quantity': formatted_qty,
                'created_at': time.time(),
                'reason': reason
            }

            self.ledger.log_event(
                level="INFO",
                component="ORDER_MANAGER",
                message=f"Placed RESTING PURPLE LIMIT BID on {symbol}: {formatted_qty} @ ${formatted_price:,.2f} ({reason})",
                metadata={'local_order_id': local_id, 'exchange_order_id': exch_id}
            )
            return order_res

        except Exception as e:
            logger.error(f"Failed to place resting purple bid on {symbol}: {e}")
            self.ledger.log_event(
                level="ERROR",
                component="ORDER_MANAGER",
                message=f"Failed to place resting purple bid on {symbol}: {e}"
            )
            return None

    def cancel_resting_purple_bid(self, symbol: str, reason: str = "") -> Optional[Dict[str, Any]]:
        """
        Cancels an active resting purple limit bid.
        """
        symbol = symbol.upper()
        resting = self.resting_orders.get(symbol)
        if not resting:
            logger.info(f"No active resting bid registered in memory for {symbol}")
            return None

        exch_id = resting.get('exchange_order_id')
        loc_id = resting.get('local_order_id')

        try:
            res = self.adapter.cancel_order(symbol=symbol, order_id=exch_id, client_order_id=loc_id)
            self.ledger.update_order_status(status="CANCELED", local_order_id=loc_id)
            del self.resting_orders[symbol]

            self.ledger.log_event(
                level="INFO",
                component="ORDER_MANAGER",
                message=f"Cancelled RESTING PURPLE BID on {symbol} (Reason: {reason})",
                metadata={'local_order_id': loc_id, 'exchange_order_id': exch_id}
            )
            return res
        except Exception as e:
            logger.error(f"Failed to cancel resting bid for {symbol}: {e}")
            return None

    def submit_monitored_limit(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        reason: str,
        timeout_sec: float = 20.0,
        max_drift_pct: float = 0.005
    ) -> Optional[Dict[str, Any]]:
        """
        Submits an immediate limit order and registers it for active price-chasing.
        """
        symbol = symbol.upper()
        side = side.upper()

        # If price is zero or unquoted, grab current top-of-book ticker price
        if price <= 0.0:
            price = self.adapter.get_ticker_price(symbol)

        formatted_price = self.adapter.format_price(symbol, price)
        formatted_qty = self.adapter.format_quantity(symbol, quantity)

        local_id = f"chase_{uuid.uuid4().hex[:10]}"

        try:
            order_res = self.adapter.place_limit_order(
                symbol=symbol,
                side=side,
                price=formatted_price,
                quantity=formatted_qty,
                client_order_id=local_id
            )
            exch_id = str(order_res.get('orderId', ''))
            now = time.time()

            # Record in SQLite ledger
            self.ledger.record_order({
                'local_order_id': local_id,
                'exchange_order_id': exch_id,
                'symbol': symbol,
                'side': side,
                'order_type': 'LIMIT',
                'price': formatted_price,
                'orig_qty': formatted_qty,
                'status': 'NEW',
                'time_in_force': 'GTC',
                'client_tag': f"MONITORED_LIMIT:{reason}"
            })

            # Register in active monitored orders
            monitored = MonitoredOrder(
                local_order_id=local_id,
                exchange_order_id=exch_id,
                symbol=symbol,
                side=side,
                target_qty=formatted_qty,
                executed_qty=0.0,
                initial_price=formatted_price,
                last_quote_price=formatted_price,
                quote_time=now,
                timeout_window_sec=timeout_sec,
                max_drift_pct=max_drift_pct,
                reason=reason,
                replace_count=0,
                status="ACTIVE"
            )
            self.monitored_orders[local_id] = monitored

            self.ledger.log_event(
                level="INFO",
                component="ORDER_MANAGER",
                message=f"Submitted MONITORED LIMIT {side} on {symbol}: {formatted_qty} @ ${formatted_price:,.2f} ({reason})",
                metadata={'local_order_id': local_id, 'exchange_order_id': exch_id}
            )
            return order_res

        except Exception as e:
            logger.error(f"Failed to submit monitored limit order {side} on {symbol}: {e}")
            self.ledger.log_event(
                level="ERROR",
                component="ORDER_MANAGER",
                message=f"Failed to submit monitored limit order {side} on {symbol}: {e}"
            )
            return None

    def tick_monitored_orders(self) -> List[Dict[str, Any]]:
        """
        Active monitoring and chase loop called frequently (e.g. every 5 seconds).
        Checks order statuses, handles partial fills, and auto-chases unfilled limits.
        """
        completed_events: List[Dict[str, Any]] = []
        now = time.time()
        active_ids = list(self.monitored_orders.keys())

        for loc_id in active_ids:
            order = self.monitored_orders.get(loc_id)
            if not order or order.status != "ACTIVE":
                continue

            try:
                # 1. Query order status on exchange
                status_res = self.adapter.get_order_status(
                    symbol=order.symbol,
                    order_id=order.exchange_order_id,
                    client_order_id=order.local_order_id
                )
                if not status_res:
                    continue

                ex_status = status_res.get('status', 'NEW')
                executed_qty = float(status_res.get('executedQty', 0.0))
                cummulative_quote_qty = float(status_res.get('cummulativeQuoteQty', 0.0))
                order.executed_qty = executed_qty

                # 2. Handle Complete Fill
                if ex_status == 'FILLED':
                    order.status = "FILLED"
                    self.ledger.update_order_status(
                        status="FILLED",
                        local_order_id=loc_id,
                        executed_qty=executed_qty,
                        cummulative_quote_qty=cummulative_quote_qty
                    )
                    self.ledger.log_event(
                        level="INFO",
                        component="ORDER_MANAGER",
                        message=f"Monitored order FILLED: {order.side} {order.symbol} {executed_qty} @ ${order.last_quote_price:,.2f} ({order.reason})",
                        metadata={'local_order_id': loc_id, 'exchange_order_id': order.exchange_order_id}
                    )
                    completed_events.append({
                        'event': 'ORDER_FILLED',
                        'order': order,
                        'avg_price': cummulative_quote_qty / executed_qty if executed_qty > 0 else order.last_quote_price
                    })
                    del self.monitored_orders[loc_id]
                    continue

                # 3. Handle External Cancellation or Rejection
                elif ex_status in ('CANCELED', 'REJECTED', 'EXPIRED'):
                    order.status = ex_status
                    self.ledger.update_order_status(
                        status=ex_status,
                        local_order_id=loc_id,
                        executed_qty=executed_qty,
                        cummulative_quote_qty=cummulative_quote_qty
                    )
                    logger.info(f"Monitored order {loc_id} ended with status {ex_status}")
                    del self.monitored_orders[loc_id]
                    continue

                # 4. Check If Order Needs Price Chasing (Timeout Elapsed)
                elapsed = now - order.quote_time
                if elapsed >= order.timeout_window_sec:
                    # Time window expired and order is still unfilled/partially filled
                    current_ticker = self.adapter.get_ticker_price(order.symbol)
                    if current_ticker <= 0.0:
                        continue

                    # Check max drift protection
                    drift_pct = abs(current_ticker - order.initial_price) / order.initial_price
                    if drift_pct > order.max_drift_pct and order.side == "BUY":
                        logger.warning(
                            f"Max drift protection triggered for {order.symbol} BUY: Drift={drift_pct*100:.2f}% > {order.max_drift_pct*100:.2f}%. Cancelling chase to prevent top-buying."
                        )
                        self.adapter.cancel_order(symbol=order.symbol, order_id=order.exchange_order_id)
                        order.status = "DRIFT_EXCEEDED"
                        self.ledger.update_order_status(status="CANCELED", local_order_id=loc_id)
                        self.ledger.log_event(
                            level="WARNING",
                            component="ORDER_MANAGER",
                            message=f"Chased BUY halted due to max drift: {order.symbol} drifted {drift_pct*100:.2f}%",
                            metadata={'order': str(order)}
                        )
                        del self.monitored_orders[loc_id]
                        continue

                    # Calculate remaining quantity
                    remaining_qty = self.adapter.format_quantity(order.symbol, order.target_qty - order.executed_qty)
                    filters = self.adapter.get_symbol_filters(order.symbol)
                    min_qty = filters.get('min_qty', 0.0)
                    min_notional = filters.get('min_notional', 5.0)

                    if remaining_qty < min_qty or (remaining_qty * current_ticker) < min_notional:
                        logger.info(f"Remaining qty {remaining_qty} below min lot size. Concluding order {loc_id}.")
                        order.status = "FILLED"
                        del self.monitored_orders[loc_id]
                        continue

                    # Cancel unfilled portion on exchange
                    try:
                        self.adapter.cancel_order(symbol=order.symbol, order_id=order.exchange_order_id)
                        self.ledger.update_order_status(status="CANCELED", local_order_id=loc_id)
                    except Exception as e:
                        logger.warning(f"Notice while cancelling order {order.exchange_order_id} during chase: {e}")

                    # Re-quote at updated price
                    new_loc_id = f"chase_{uuid.uuid4().hex[:10]}"
                    new_formatted_price = self.adapter.format_price(order.symbol, current_ticker)

                    try:
                        new_res = self.adapter.place_limit_order(
                            symbol=order.symbol,
                            side=order.side,
                            price=new_formatted_price,
                            quantity=remaining_qty,
                            client_order_id=new_loc_id
                        )
                        new_exch_id = str(new_res.get('orderId', ''))
                        order.replace_count += 1
                        order.exchange_order_id = new_exch_id
                        order.last_quote_price = new_formatted_price
                        order.quote_time = time.time()

                        # Persist replacement in ledger
                        self.ledger.record_order({
                            'local_order_id': new_loc_id,
                            'exchange_order_id': new_exch_id,
                            'symbol': order.symbol,
                            'side': order.side,
                            'order_type': 'LIMIT',
                            'price': new_formatted_price,
                            'orig_qty': remaining_qty,
                            'status': 'NEW',
                            'time_in_force': 'GTC',
                            'client_tag': f"CHASE_REPLACE_{order.replace_count}:{order.reason}"
                        })

                        # Update map with new local id
                        del self.monitored_orders[loc_id]
                        order.local_order_id = new_loc_id
                        self.monitored_orders[new_loc_id] = order

                        self.ledger.log_event(
                            level="INFO",
                            component="ORDER_MANAGER",
                            message=f"CHASE ADJUST #{order.replace_count}: {order.side} {order.symbol} re-quoted to ${new_formatted_price:,.2f} (qty={remaining_qty})",
                            metadata={'new_local_id': new_loc_id, 'new_exchange_id': new_exch_id}
                        )

                    except Exception as e:
                        logger.error(f"Error placing chase replacement order for {order.symbol}: {e}")

            except Exception as e:
                logger.error(f"Error ticking monitored order {loc_id}: {e}")

        return completed_events

    def _calculate_buy_qty(self, symbol: str, price: float) -> float:
        """Computes buy quantity based on configured USD allocation."""
        if price <= 0.0:
            price = self.adapter.get_ticker_price(symbol)
        if price <= 0.0:
            return 0.001  # Safe fallback for BTC

        qty = self.default_quote_allocation_usd / price
        return self.adapter.format_quantity(symbol, qty)

    def _get_sellable_qty(self, symbol: str) -> float:
        """Determines base asset free balance available to sell."""
        filters = self.adapter.get_symbol_filters(symbol)
        base_asset = filters.get('base_asset', 'BTC')
        bal = self.adapter.get_asset_balance(base_asset)
        return self.adapter.format_quantity(symbol, bal.get('free', 0.0))
