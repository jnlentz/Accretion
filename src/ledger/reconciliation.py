"""
Exchange State Reconciliation Engine
Detects and resolves discrepancies between Binance exchange state and the local SQLite ledger.
"""
import time
import logging
from typing import Optional, Dict, Any, List
from src.adapters.binance_client import BinanceSpotAdapter
from src.ledger.database import SQLiteLedger

logger = logging.getLogger("accretion.reconciliation")

class ReconciliationEngine:
    """
    Reconciles balances, open orders, and fills between Binance.US / Binance and local SQLite ledger.
    Ensures that out-of-band fills, manual order cancellations, and connection interruptions
    do not compromise ledger honesty.
    """
    def __init__(self, adapter: BinanceSpotAdapter, ledger: SQLiteLedger, target_symbols: List[str]):
        self.adapter = adapter
        self.ledger = ledger
        self.target_symbols = [s.upper() for s in target_symbols]

    def reconcile_all(self) -> Dict[str, Any]:
        """
        Executes a complete reconciliation cycle:
        1. Balances reconciliation
        2. Open orders reconciliation
        3. Fills discovery & sync
        4. Account snapshot generation
        """
        summary = {
            'timestamp': int(time.time()),
            'balances_updated': 0,
            'balance_mismatches': 0,
            'orders_checked': 0,
            'orders_healed': 0,
            'new_fills_recorded': 0,
            'errors': []
        }

        try:
            bal_res = self.reconcile_balances()
            summary['balances_updated'] = bal_res['balances_updated']
            summary['balance_mismatches'] = bal_res['mismatches']
        except Exception as e:
            logger.error(f"Error during balance reconciliation: {e}")
            summary['errors'].append(f"Balances: {str(e)}")

        for symbol in self.target_symbols:
            try:
                ord_res = self.reconcile_orders_for_symbol(symbol)
                summary['orders_checked'] += ord_res['orders_checked']
                summary['orders_healed'] += ord_res['orders_healed']
            except Exception as e:
                logger.error(f"Error reconciling orders for {symbol}: {e}")
                summary['errors'].append(f"Orders ({symbol}): {str(e)}")

            try:
                fill_res = self.sync_fills_for_symbol(symbol)
                summary['new_fills_recorded'] += fill_res['new_fills']
            except Exception as e:
                logger.error(f"Error syncing fills for {symbol}: {e}")
                summary['errors'].append(f"Fills ({symbol}): {str(e)}")

        # Create periodic snapshot
        try:
            self._take_snapshot()
        except Exception as e:
            logger.error(f"Error recording account snapshot: {e}")

        return summary

    def reconcile_balances(self) -> Dict[str, int]:
        """
        Fetches current exchange balances and reconciles against local ledger.
        """
        exchange_balances = self.adapter.get_account_balances()
        local_balances = self.ledger.get_balances()
        mismatches = 0

        for asset, exch_bal in exchange_balances.items():
            loc_bal = local_balances.get(asset, {'free': 0.0, 'locked': 0.0, 'total': 0.0})
            diff = abs(exch_bal['total'] - loc_bal['total'])
            if diff > 1e-6:
                mismatches += 1
                logger.warning(f"Balance drift detected for {asset}: Local={loc_bal['total']} vs Exch={exch_bal['total']} (Diff={diff:.8f})")
                self.ledger.record_reconciliation_event(
                    mismatch_type="BALANCE_DRIFT",
                    symbol=asset,
                    local_state=loc_bal,
                    exchange_state=exch_bal,
                    resolution=f"Synchronized local balance to exchange total {exch_bal['total']}"
                )

        self.ledger.upsert_balances(exchange_balances)
        return {'balances_updated': len(exchange_balances), 'mismatches': mismatches}

    def reconcile_orders_for_symbol(self, symbol: str) -> Dict[str, int]:
        """
        Reconciles open orders for a specific symbol.
        - Detects untracked open orders on the exchange and imports them.
        - Detects closed/filled orders locally marked open and queries the exchange for their terminal status.
        """
        symbol = symbol.upper()
        exchange_orders = self.adapter.get_open_orders(symbol)
        exchange_order_map = {str(o['orderId']): o for o in exchange_orders}
        local_active_orders = self.ledger.get_active_orders(symbol)
        local_order_map = {o['exchange_order_id']: o for o in local_active_orders if o.get('exchange_order_id')}

        healed = 0

        # Check 1: Find untracked exchange open orders
        for exch_id, exch_order in exchange_order_map.items():
            if exch_id not in local_order_map:
                logger.info(f"Importing untracked open order from exchange: {symbol} ID={exch_id}")
                self.ledger.record_order({
                    'local_order_id': exch_order.get('clientOrderId', f"exch_{exch_id}"),
                    'exchange_order_id': exch_id,
                    'symbol': symbol,
                    'side': exch_order.get('side', 'BUY'),
                    'order_type': exch_order.get('type', 'LIMIT'),
                    'price': float(exch_order.get('price', 0.0)),
                    'orig_qty': float(exch_order.get('origQty', 0.0)),
                    'executed_qty': float(exch_order.get('executedQty', 0.0)),
                    'cummulative_quote_qty': float(exch_order.get('cummulativeQuoteQty', 0.0)),
                    'status': exch_order.get('status', 'NEW'),
                    'time_in_force': exch_order.get('timeInForce', 'GTC'),
                    'created_at': int(exch_order.get('time', time.time() * 1000)) // 1000
                })
                self.ledger.record_reconciliation_event(
                    mismatch_type="UNTRACKED_OPEN_ORDER",
                    symbol=symbol,
                    local_state=None,
                    exchange_state=exch_order,
                    resolution=f"Recorded untracked open order {exch_id} into local ledger"
                )
                healed += 1

        # Check 2: Check local active orders that are no longer in exchange open orders
        for exch_id, loc_order in local_order_map.items():
            if exch_id not in exchange_order_map:
                # Query actual terminal status from exchange
                try:
                    terminal_status = self.adapter.get_order_status(symbol=symbol, order_id=exch_id)
                    if terminal_status:
                        new_status = terminal_status.get('status', 'CANCELED')
                        exec_qty = float(terminal_status.get('executedQty', loc_order.get('executed_qty', 0.0)))
                        quote_qty = float(terminal_status.get('cummulativeQuoteQty', loc_order.get('cummulative_quote_qty', 0.0)))
                        logger.info(f"Updating closed order {exch_id} -> {new_status} (exec_qty={exec_qty})")
                        self.ledger.update_order_status(
                            status=new_status,
                            exchange_order_id=exch_id,
                            executed_qty=exec_qty,
                            cummulative_quote_qty=quote_qty
                        )
                        self.ledger.record_reconciliation_event(
                            mismatch_type="STATUS_DESYNC",
                            symbol=symbol,
                            local_state=loc_order,
                            exchange_state=terminal_status,
                            resolution=f"Updated order {exch_id} status to {new_status}"
                        )
                        healed += 1
                    else:
                        # Order not found on exchange
                        self.ledger.update_order_status(status="CANCELED", exchange_order_id=exch_id)
                        self.ledger.record_reconciliation_event(
                            mismatch_type="STATUS_DESYNC",
                            symbol=symbol,
                            local_state=loc_order,
                            exchange_state=None,
                            resolution=f"Order {exch_id} not found on exchange, marked CANCELED locally"
                        )
                        healed += 1
                except Exception as e:
                    logger.error(f"Failed to query terminal status for order {exch_id}: {e}")

        return {'orders_checked': len(local_active_orders) + len(exchange_orders), 'orders_healed': healed}

    def sync_fills_for_symbol(self, symbol: str) -> Dict[str, int]:
        """
        Polls recent trade executions from exchange and records missing fills into SQLite ledger.
        """
        symbol = symbol.upper()
        last_trade_time = self.ledger.get_latest_trade_time(symbol)
        start_time_ms = (last_trade_time * 1000 + 1) if last_trade_time else None

        fills = self.adapter.get_recent_fills(symbol=symbol, limit=100, start_time=start_time_ms)
        new_count = 0

        for f in fills:
            trade_id = str(f['id'])
            order_id = str(f['orderId'])
            trade_time = int(f.get('time', time.time() * 1000)) // 1000
            side = 'BUY' if f.get('isBuyer', True) else 'SELL'
            
            fill_data = {
                'exchange_trade_id': trade_id,
                'exchange_order_id': order_id,
                'local_order_id': '',
                'symbol': symbol,
                'side': side,
                'price': float(f.get('price', 0.0)),
                'qty': float(f.get('qty', 0.0)),
                'quote_qty': float(f.get('quoteQty', 0.0)),
                'commission': float(f.get('commission', 0.0)),
                'commission_asset': f.get('commissionAsset', ''),
                'trade_time': trade_time
            }

            inserted = self.ledger.record_fill(fill_data)
            if inserted:
                new_count += 1
                logger.info(f"Recorded new fill: {symbol} {side} {fill_data['qty']} @ {fill_data['price']} (trade_id={trade_id})")

        return {'new_fills': new_count}

    def _take_snapshot(self) -> None:
        """Calculates total estimated equity and writes full account checkpoint."""
        balances = self.ledger.get_balances()
        active_orders = self.ledger.get_active_orders()

        total_usd = 0.0
        for asset, bal in balances.items():
            if asset in ('USD', 'USDT', 'USDC'):
                total_usd += bal['total']
            else:
                pair = f"{asset}USDT"
                try:
                    price = self.adapter.get_ticker_price(pair)
                    if price > 0:
                        total_usd += bal['total'] * price
                except Exception:
                    pass

        self.ledger.record_snapshot(
            total_equity_usd=total_usd,
            balances=balances,
            open_orders=active_orders
        )
