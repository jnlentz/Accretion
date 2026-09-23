"""
====================================================================================================
PROJECT ACCRETION: OUTAGE RECOVERY & SELF-HEALING ENGINE
====================================================================================================
Purpose:
  Provides automated, crash/power/internet outage recovery protocol for the 24/7 crypto bot:
    1. Phase 1: Exchange & State Audit
       - Audits open orders on Binance.US and in-memory/SQLite portfolio state.
       - Immediately cancels any open BUY orders (1-bar TTL expired during outage).
       - Frees escrowed capital and slots.
    2. Phase 2: Historical Data Gap Bridging
       - Bridges Kraken 15m candle gaps across all assets for the offline downtime window.
    3. Phase 3: Outage Position Evaluation & Zero-Market-Order Liquidation
       - Evaluates open positions against all candles closed while offline.
       - If stop loss was breached (or current price <= stop_loss_price):
         - Cancels resting TP order.
         - Executes monitored pegged limit sell at best bid (zero-market-order rule).
         - Returns proceeds, logs trade and telemetry, frees slot.
       - If TP limit filled on exchange while offline:
         - Reconciles fill, banks profit, logs trade, frees slot.
       - If healthy:
         - Verifies resting TP limit order exists on Binance.US book (re-places if missing).
         - Resumes holding position.
    4. Phase 4: State Reconciliation & Snapshot
       - Commits healed portfolio state to SQLite.
       - Logs complete audit telemetry to ActivityLedger for Android app backend.
====================================================================================================
"""

import os
import sys
import time
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

from src.adapters.kraken_data import KrakenData, INTERVAL_SECONDS_15M
from src.strategy.crypto_portfolio_engine import (
    CryptoPortfolioEngine,
    ActivePosition,
    RestingBuyOrder,
    CRYPTO_CHAMPIONS
)
from src.ledger.activity_ledger import ActivityLedger

logger = logging.getLogger("accretion.runtime.outage_recovery")


class OutageRecoveryEngine:
    """
    Autonomous crash and outage recovery coordinator.
    Reconciles exchange open orders, downtime candles, and active positions on boot.
    """

    def __init__(
        self,
        portfolio: CryptoPortfolioEngine,
        kraken_data: KrakenData,
        activity_ledger: ActivityLedger,
        binance_adapter: Optional[Any] = None,
        mode: str = "paper",
        symbols: Optional[List[str]] = None,
        portfolio_db: Optional[Path] = None
    ):
        self.portfolio = portfolio
        self.kraken_data = kraken_data
        self.ledger = activity_ledger
        self.binance_adapter = binance_adapter
        self.mode = mode.lower()
        self.symbols = symbols or list(CRYPTO_CHAMPIONS.keys())
        self.portfolio_db = portfolio_db

    def reconcile(self) -> Dict[str, Any]:
        """
        Executes the 4-phase outage recovery lifecycle.
        Returns a summary report of all recovery actions.
        """
        print("\n" + "=" * 95)
        print(f"🛡️  EXECUTING OUTAGE RECOVERY & SELF-HEALING PROTOCOL (MODE: {self.mode.upper()})")
        print("=" * 95)

        summary: Dict[str, Any] = {
            'cancelled_buys': [],
            'liquidated_stops': [],
            'reconciled_tps': [],
            'healthy_positions': [],
            'bridged_bars': {}
        }

        # ----------------------------------------------------------------------
        # Phase 1: Audit and Cancel Stale Buy Orders
        # ----------------------------------------------------------------------
        self._audit_and_cancel_stale_buys(summary)

        # ----------------------------------------------------------------------
        # Phase 2: Bridge Historical Candle Gaps
        # ----------------------------------------------------------------------
        self._bridge_downtime_candles(summary)

        # ----------------------------------------------------------------------
        # Phase 3: Evaluate Open Positions against Downtime Market Action
        # ----------------------------------------------------------------------
        self._reconcile_open_positions(summary)

        # ----------------------------------------------------------------------
        # Phase 4: Commit Healed State & Record Telemetry Snapshot
        # ----------------------------------------------------------------------
        if self.portfolio_db:
            self.portfolio.save_state(self.portfolio_db)
            logger.info("Saved healed portfolio state to SQLite.")

        tot_eq = self.portfolio.get_total_equity()
        self.ledger.log_snapshot(
            mode=self.mode,
            total_equity=tot_eq,
            free_cash=self.portfolio.free_cash,
            slots_occupied=len(self.portfolio.active_positions) + len(self.portfolio.resting_orders),
            max_slots=self.portfolio.max_slots,
            active_positions=self.portfolio.active_positions,
            resting_orders=self.portfolio.resting_orders,
            total_realized_pnl=sum(t.get('dollar_pnl', 0.0) for t in self.portfolio.closed_trades)
        )

        print("-" * 95)
        print(f"✅ OUTAGE RECOVERY COMPLETE:")
        print(f"   • Stale Buys Cancelled   : {len(summary['cancelled_buys'])}")
        print(f"   • Stop Losses Liquidated : {len(summary['liquidated_stops'])}")
        print(f"   • Take Profits Reconciled: {len(summary['reconciled_tps'])}")
        print(f"   • Healthy Positions Kept : {len(summary['healthy_positions'])}")
        print(f"   • Total Equity Post-Heal : ${tot_eq:,.2f} | Free Cash: ${self.portfolio.free_cash:,.2f}")
        print("=" * 95 + "\n")

        return summary

    def _audit_and_cancel_stale_buys(self, summary: Dict[str, Any]) -> None:
        """
        Cancels any open buy orders on the exchange and in local state.
        Rule: Buy orders only have a 1-bar (15m) TTL; any buy standing across an outage is stale.
        """
        logger.info("Phase 1: Auditing exchange and local state for stale buy orders...")

        # 1. Live Exchange Audit via Binance.US REST API
        if self.mode == "live" and self.binance_adapter:
            try:
                open_orders = self.binance_adapter.get_open_orders()
                logger.info(f"Exchange report: {len(open_orders)} open order(s) found on Binance.US.")
                for o in open_orders:
                    side = o.get('side', '').upper()
                    bsym = o.get('symbol', '').upper()
                    order_id = o.get('orderId')
                    price = float(o.get('price', 0.0))
                    qty = float(o.get('origQty', 0.0))
                    notional = price * qty

                    if side == 'BUY':
                        logger.warning(
                            f"🚨 [OUTAGE RECOVERY] Cancelling stale exchange BUY order: "
                            f"{bsym} id={order_id} Qty={qty} @ ${price:,.2f}"
                        )
                        try:
                            self.binance_adapter.cancel_order(symbol=bsym, order_id=order_id)
                        except Exception as e:
                            logger.error(f"Error cancelling stale buy order {order_id} on Binance.US: {e}")

                        # Find Kraken symbol
                        ksym = self._find_kraken_symbol(bsym)
                        self.ledger.log_order_event(
                            event_type="OUTAGE_BUY_CANCELLED",
                            symbol=ksym,
                            binance_symbol=bsym,
                            side="BUY",
                            order_type="LIMIT",
                            price=price,
                            quantity=qty,
                            allocated_capital=notional,
                            exchange_order_id=str(order_id),
                            reason="Outage Recovery: Stale buy order cancelled (1-bar TTL expired)",
                            status="CANCELED"
                        )
                        summary['cancelled_buys'].append({
                            'symbol': ksym,
                            'binance_symbol': bsym,
                            'order_id': order_id,
                            'source': 'exchange'
                        })
            except Exception as e:
                logger.error(f"Error querying open orders on Binance.US during outage audit: {e}")

        # 2. Local State Audit (In-memory / SQLite resting orders)
        if self.portfolio.resting_orders:
            for ksym, order in list(self.portfolio.resting_orders.items()):
                logger.warning(
                    f"🚨 [OUTAGE RECOVERY] Cancelling local stale resting buy order: "
                    f"{ksym} ({order.binance_symbol}) Alloc=${order.allocated_capital:,.2f}"
                )
                self.portfolio.free_cash += order.allocated_capital

                self.ledger.log_order_event(
                    event_type="OUTAGE_BUY_CANCELLED_LOCAL",
                    symbol=ksym,
                    binance_symbol=order.binance_symbol,
                    side="BUY",
                    order_type="LIMIT",
                    price=order.limit_buy_price,
                    quantity=order.quantity,
                    allocated_capital=order.allocated_capital,
                    order_id=order.order_id,
                    exchange_order_id=order.exchange_order_id,
                    reason="Outage Recovery: Local resting buy order cancelled and capital restored",
                    status="CANCELED"
                )
                summary['cancelled_buys'].append({
                    'symbol': ksym,
                    'binance_symbol': order.binance_symbol,
                    'order_id': order.order_id,
                    'source': 'local_state'
                })

            self.portfolio.resting_orders.clear()

    def _bridge_downtime_candles(self, summary: Dict[str, Any]) -> None:
        """Bridges missing 15m candles from Kraken across all active symbols."""
        logger.info("Phase 2: Bridging historical candle gaps for offline window...")
        bridged = self.kraken_data.bridge_gap_all(self.symbols)
        summary['bridged_bars'] = bridged
        for sym, cnt in bridged.items():
            if cnt > 0:
                logger.info(f"  • {sym:<8}: Ingested {cnt} closed bar(s) from downtime.")
            else:
                logger.info(f"  • {sym:<8}: Already up to date.")

    def _reconcile_open_positions(self, summary: Dict[str, Any]) -> None:
        """
        Evaluates active positions against candles closed during downtime.
        Liquidates stop breaches immediately via monitored limit orders; reconciles filled TPs.
        """
        logger.info(f"Phase 3: Evaluating {len(self.portfolio.active_positions)} active position(s)...")
        if not self.portfolio.active_positions:
            logger.info("No active positions to reconcile.")
            return

        for ksym, pos in list(self.portfolio.active_positions.items()):
            bsym = pos.binance_symbol
            logger.info(f"Auditing open position: {ksym} ({bsym}) Entry=${pos.entry_price:,.2f} Stop=${pos.stop_loss_price:,.2f} TP=${pos.limit_sell_price:,.2f}")

            # 1. Fetch downtime candles from SQLite
            downtime_candles = self._get_candles_since(ksym, pos.entry_timestamp)

            # 2. Fetch current market price
            current_price = self._get_current_price(bsym)
            if current_price <= 0 and downtime_candles:
                current_price = downtime_candles[-1]['close']

            # 3. Check for Stop-Loss Breach During Downtime or at Present
            stop_breached = False
            breach_candle_low = current_price
            for c in downtime_candles:
                if c['low'] <= pos.stop_loss_price:
                    stop_breached = True
                    breach_candle_low = min(breach_candle_low, c['low'])
                    break

            if current_price <= pos.stop_loss_price:
                stop_breached = True
                breach_candle_low = min(breach_candle_low, current_price)

            if stop_breached:
                logger.warning(
                    f"🛑 [OUTAGE STOP BREACH] {ksym} ({bsym}) breached stop level while offline! "
                    f"Stop=${pos.stop_loss_price:,.2f} | Current=${current_price:,.2f}"
                )
                self._liquidate_outage_stop(ksym, pos, current_price, summary)
                continue

            # 4. Check for Take-Profit Fill During Downtime
            tp_filled = False
            tp_fill_price = pos.limit_sell_price

            if self.mode == "live" and self.binance_adapter and pos.tp_order_id:
                try:
                    order_status = self.binance_adapter.get_order_status(bsym, order_id=pos.tp_order_id)
                    if order_status and order_status.get('status') == 'FILLED':
                        tp_filled = True
                        tp_fill_price = float(order_status.get('price', pos.limit_sell_price))
                except Exception as e:
                    logger.error(f"Error checking TP order status on Binance.US for {bsym}: {e}")

            if not tp_filled:
                # Check if high breached TP in downtime candles
                for c in downtime_candles:
                    if c['high'] >= pos.limit_sell_price:
                        tp_filled = True
                        break

            if tp_filled:
                logger.info(
                    f"🎉 [OUTAGE TP FILL] {ksym} ({bsym}) hit Take-Profit while offline! "
                    f"TP=${pos.limit_sell_price:,.2f}"
                )
                self._reconcile_outage_tp(ksym, pos, tp_fill_price, summary)
                continue

            # 5. Position is Healthy: Resume Waiting for Sell Signal
            logger.info(f"🛡️  [POSITION HEALTHY] {ksym} ({bsym}) remains active. Current: ${current_price:,.2f} (Between Stop & TP).")
            self._ensure_resting_tp_active(pos)
            self.ledger.log_order_event(
                event_type="OUTAGE_POSITION_HEALTHY",
                symbol=ksym,
                binance_symbol=bsym,
                side="SELL",
                order_type="LIMIT",
                price=pos.limit_sell_price,
                quantity=pos.quantity,
                allocated_capital=pos.allocated_capital,
                reason="Position audited after outage: market remains within healthy corridor",
                status="OPEN"
            )
            summary['healthy_positions'].append({
                'symbol': ksym,
                'binance_symbol': bsym,
                'entry_price': pos.entry_price,
                'current_price': current_price
            })

    def _liquidate_outage_stop(
        self,
        symbol: str,
        pos: ActivePosition,
        current_price: float,
        summary: Dict[str, Any]
    ) -> None:
        """
        Liquidates a position whose stop was breached during downtime.
        Enforces Jesse's Zero-Market-Order rule by using a monitored pegged limit sell.
        """
        bsym = pos.binance_symbol
        exit_price = current_price

        # 1. Cancel resting TP order on Binance.US
        if self.mode == "live" and self.binance_adapter and pos.tp_order_id:
            logger.info(f"Cancelling resting TP order {pos.tp_order_id} on {bsym} prior to stop liquidation.")
            try:
                self.binance_adapter.cancel_order(symbol=bsym, order_id=pos.tp_order_id)
            except Exception as e:
                logger.warning(f"Error cancelling resting TP order: {e}")

        # 2. Execute Monitored Pegged Limit Sell (Zero Market Orders)
        if self.mode == "live" and self.binance_adapter:
            logger.warning(f"🚨 [MONITORED STOP LIQUIDATION] Executing chased limit sell for {bsym} Qty: {pos.quantity}...")
            try:
                res = self.binance_adapter.execute_monitored_limit_sell(
                    symbol=bsym,
                    quantity=pos.quantity,
                    timeout_seconds=60.0,
                    chase_interval=3.0
                )
                exit_price = float(res.get('avg_price', current_price))
            except Exception as e:
                logger.error(f"Error during monitored stop liquidation on {bsym}: {e}")
                exit_price = current_price
        else:
            # Paper mode: realistic liquidation at stop or current price
            exit_price = min(pos.stop_loss_price, current_price)

        # 3. Calculate PnL and restore proceeds
        net_ret_pct = ((exit_price - pos.entry_price) / pos.entry_price) * 100.0
        dollar_pnl = pos.allocated_capital * (net_ret_pct / 100.0)
        self.portfolio.free_cash += pos.allocated_capital + dollar_pnl

        # 4. Record Trade History
        trade_record = {
            'symbol': symbol,
            'binance_symbol': bsym,
            'entry_price': pos.entry_price,
            'exit_price': exit_price,
            'quantity': pos.quantity,
            'allocated_capital': pos.allocated_capital,
            'dollar_pnl': dollar_pnl,
            'net_ret_pct': net_ret_pct,
            'exit_reason': "OUTAGE_STOP_LIQUIDATED",
            'bars_held': pos.bars_held,
            'entry_time': pos.entry_timestamp,
            'exit_time': str(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        }
        self.portfolio.closed_trades.append(trade_record)
        self.ledger.log_completed_trade(trade_record)

        self.ledger.log_order_event(
            event_type="OUTAGE_STOP_LIQUIDATED",
            symbol=symbol,
            binance_symbol=bsym,
            side="SELL",
            order_type="LIMIT_MONITORED",
            price=exit_price,
            quantity=pos.quantity,
            allocated_capital=pos.allocated_capital,
            reason=f"Stop breached during outage. Monitored limit liquidation at ${exit_price:,.2f}",
            status="FILLED",
            details={'dollar_pnl': dollar_pnl, 'net_ret_pct': net_ret_pct}
        )

        # 5. Remove from active positions (freeing slot)
        self.portfolio.active_positions.pop(symbol, None)
        summary['liquidated_stops'].append(trade_record)
        logger.warning(
            f"🛑 Liquidated {symbol} @ ${exit_price:,.2f} | PnL: ${dollar_pnl:,.2f} ({net_ret_pct:.2f}%) | "
            f"Free Cash: ${self.portfolio.free_cash:,.2f} | Slot Freed."
        )

    def _reconcile_outage_tp(
        self,
        symbol: str,
        pos: ActivePosition,
        fill_price: float,
        summary: Dict[str, Any]
    ) -> None:
        """Reconciles a Take-Profit limit order that filled while the bot was offline."""
        bsym = pos.binance_symbol
        net_ret_pct = ((fill_price - pos.entry_price) / pos.entry_price) * 100.0
        dollar_pnl = pos.allocated_capital * (net_ret_pct / 100.0)
        self.portfolio.free_cash += pos.allocated_capital + dollar_pnl

        trade_record = {
            'symbol': symbol,
            'binance_symbol': bsym,
            'entry_price': pos.entry_price,
            'exit_price': fill_price,
            'quantity': pos.quantity,
            'allocated_capital': pos.allocated_capital,
            'dollar_pnl': dollar_pnl,
            'net_ret_pct': net_ret_pct,
            'exit_reason': "OUTAGE_TP_FILLED",
            'bars_held': pos.bars_held,
            'entry_time': pos.entry_timestamp,
            'exit_time': str(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        }
        self.portfolio.closed_trades.append(trade_record)
        self.ledger.log_completed_trade(trade_record)

        self.ledger.log_order_event(
            event_type="OUTAGE_TP_FILLED",
            symbol=symbol,
            binance_symbol=bsym,
            side="SELL",
            order_type="LIMIT",
            price=fill_price,
            quantity=pos.quantity,
            allocated_capital=pos.allocated_capital,
            exchange_order_id=pos.tp_order_id,
            reason=f"Take-Profit filled on exchange during outage at ${fill_price:,.2f}",
            status="FILLED",
            details={'dollar_pnl': dollar_pnl, 'net_ret_pct': net_ret_pct}
        )

        self.portfolio.active_positions.pop(symbol, None)
        summary['reconciled_tps'].append(trade_record)
        logger.info(
            f"🎉 Reconciled TP for {symbol} @ ${fill_price:,.2f} | PnL: +${dollar_pnl:,.2f} (+{net_ret_pct:.2f}%) | "
            f"Free Cash: ${self.portfolio.free_cash:,.2f} | Slot Freed."
        )

    def _ensure_resting_tp_active(self, pos: ActivePosition) -> None:
        """Verifies that the resting maker TP limit order is live on Binance.US; re-places if missing."""
        if self.mode != "live" or not self.binance_adapter:
            return

        bsym = pos.binance_symbol
        tp_live = False

        if pos.tp_order_id:
            try:
                st = self.binance_adapter.get_order_status(bsym, order_id=pos.tp_order_id)
                if st and st.get('status') in ('NEW', 'PARTIALLY_FILLED'):
                    tp_live = True
            except Exception:
                tp_live = False

        if not tp_live:
            logger.info(f"Re-placing missing resting TP limit order on Binance.US for {bsym} @ ${pos.limit_sell_price:,.2f}...")
            try:
                fmt_price = self.binance_adapter.format_price(bsym, pos.limit_sell_price)
                fmt_qty = self.binance_adapter.format_quantity(bsym, pos.quantity)
                res = self.binance_adapter.create_limit_sell(bsym, fmt_qty, fmt_price)
                pos.tp_order_id = str(res.get('orderId'))
                self.ledger.log_order_event(
                    event_type="OUTAGE_TP_REPLACED",
                    symbol=pos.symbol,
                    binance_symbol=bsym,
                    side="SELL",
                    order_type="LIMIT",
                    price=fmt_price,
                    quantity=fmt_qty,
                    allocated_capital=pos.allocated_capital,
                    exchange_order_id=pos.tp_order_id,
                    reason="Resting TP limit order verified and restored to Binance.US book",
                    status="CONFIRMED"
                )
            except Exception as e:
                logger.error(f"Failed to restore resting TP order for {bsym}: {e}")

    def _get_candles_since(self, symbol: str, since_timestamp: Any) -> List[Dict[str, float]]:
        """Queries SQLite database for all closed candles since a given timestamp."""
        db_path = self.kraken_data.resolve_db_path(symbol)
        if not db_path.exists():
            return []

        since_sec = 0
        try:
            if isinstance(since_timestamp, (int, float)):
                since_sec = int(since_timestamp)
            elif isinstance(since_timestamp, str):
                dt = datetime.fromisoformat(since_timestamp.replace("Z", "+00:00"))
                since_sec = int(dt.timestamp())
        except Exception:
            since_sec = int(time.time()) - (86400 * 3)  # Fallback to trailing 3 days

        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                cursor = conn.cursor()
                tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                target_table = None
                for t in ["klines_15m", "bars_15m", "ohlcv_15m"]:
                    if t in tables:
                        target_table = t
                        break

                if not target_table:
                    return []

                cursor.execute(f"""
                    SELECT time, open, high, low, close, volume
                    FROM '{target_table}'
                    WHERE time >= ?
                    ORDER BY time ASC
                """, (since_sec,))
                rows = cursor.fetchall()
                return [
                    {
                        'time': int(r[0]),
                        'open': float(r[1]),
                        'high': float(r[2]),
                        'low': float(r[3]),
                        'close': float(r[4]),
                        'volume': float(r[5])
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"Error querying downtime candles for {symbol}: {e}")
            return []

    def _get_current_price(self, binance_symbol: str) -> float:
        """Fetches top-of-book / current market price."""
        if self.mode == "live" and self.binance_adapter:
            try:
                return float(self.binance_adapter.get_ticker_price(binance_symbol))
            except Exception:
                pass
        return 0.0

    def _find_kraken_symbol(self, binance_symbol: str) -> str:
        """Resolves Kraken symbol from Binance symbol."""
        bsym = binance_symbol.upper()
        for ksym, champ in CRYPTO_CHAMPIONS.items():
            if champ.get('binance_sym') == bsym or champ.get('binance_alt') == bsym:
                return ksym
        return bsym
