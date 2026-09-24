"""
====================================================================================================
PROJECT ACCRETION: 24/7 CRYPTO CROSS-ASSET PORTFOLIO ENGINE (POLICY 4: RVOL SURGE)
====================================================================================================
Purpose:
  Coordinates multi-asset signal evaluation, cross-asset prioritization (Policy 4 RVOL Volume Surge),
  slot allocation (K=2 slots, 50% compounded equity sizing), dual passive maker limit lifecycle,
  and zero-market-order monitored liquidation.

Validated Champion Parameters:
  - Universe          : XBTUSD, ETHUSD, SOLUSD, ADAUSD, XRPUSD, XDGUSD
  - Concurrency (K)   : 2 Slots (Max 2 simultaneous open positions/resting orders)
  - Sizing Fraction   : 50% of Total Portfolio Equity (min(equity * 0.50, free_cash))
  - Order Geometry    : Maker Buy Discount -0.50% / Maker Sell Premium +0.30%
  - Order TTL         : 1 Bar (15 minutes) - Unfilled limit buys auto-cancel after 1 bar
  - Prioritization    : Policy 4 RVOL (When simultaneous signals exceed slots, rank by bar_rvol desc)
  - Stop Mechanism    : Monitored Immediate Limit Sell at -y* (Zero-Market-Order compliant)
====================================================================================================
"""

import os
import sys
import json
import time
import sqlite3
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
import pandas as pd

logger = logging.getLogger("accretion.strategy.crypto_portfolio")

# Validated Champions across Kraken / Binance.US Universe
CRYPTO_CHAMPIONS: Dict[str, Dict[str, Any]] = {
    'XBTUSD': {'x_star': 4.00, 'y_star': 2.00, 'binance_sym': 'BTCUSD',  'binance_alt': 'BTCUSDT'},
    'ETHUSD': {'x_star': 5.00, 'y_star': 2.50, 'binance_sym': 'ETHUSD',  'binance_alt': 'ETHUSDT'},
    'SOLUSD': {'x_star': 5.00, 'y_star': 2.50, 'binance_sym': 'SOLUSD',  'binance_alt': 'SOLUSDT'},
    'ADAUSD': {'x_star': 5.00, 'y_star': 2.50, 'binance_sym': 'ADAUSD',  'binance_alt': 'ADAUSDT'},
    'XRPUSD': {'x_star': 2.50, 'y_star': 1.25, 'binance_sym': 'XRPUSD',  'binance_alt': 'XRPUSDT'},
    'XDGUSD': {'x_star': 5.00, 'y_star': 2.50, 'binance_sym': 'DOGEUSD', 'binance_alt': 'DOGEUSDT'},
}


class PortfolioActionType(Enum):
    PLACE_RESTING_LIMIT_BUY = "PLACE_RESTING_LIMIT_BUY"
    CANCEL_RESTING_LIMIT_BUY = "CANCEL_RESTING_LIMIT_BUY"
    PLACE_RESTING_LIMIT_SELL = "PLACE_RESTING_LIMIT_SELL"
    CANCEL_RESTING_LIMIT_SELL = "CANCEL_RESTING_LIMIT_SELL"
    SUBMIT_MONITORED_LIMIT_SELL = "SUBMIT_MONITORED_LIMIT_SELL"
    NO_ACTION = "NO_ACTION"


@dataclass
class PortfolioAction:
    action_type: PortfolioActionType
    symbol: str
    binance_symbol: str
    price: float
    quantity: float
    reason: str
    target_price: Optional[float] = None
    stop_price: Optional[float] = None
    order_ref: Optional[str] = None
    exchange_order_id: Optional[str] = None


@dataclass
class RestingBuyOrder:
    order_id: str
    symbol: str
    binance_symbol: str
    limit_buy_price: float
    target_sell_price: float
    stop_loss_price: float
    quantity: float
    allocated_capital: float
    signal_timestamp: str
    bars_waiting: int = 0
    exchange_order_id: Optional[str] = None
    status: str = "PENDING"  # PENDING, FILLED, CANCELED

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ActivePosition:
    position_id: str
    symbol: str
    binance_symbol: str
    entry_price: float
    quantity: float
    allocated_capital: float
    limit_sell_price: float
    stop_loss_price: float
    entry_timestamp: str
    bars_held: int = 0
    tp_order_id: Optional[str] = None
    status: str = "OPEN"  # OPEN, CLOSED_TP, CLOSED_STOP

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CryptoPortfolioEngine:
    """
    Stateful Cross-Asset Portfolio & Prioritization Engine.
    Enforces Policy 4 (RVOL Volume Surge) prioritization when simultaneous signals exceed K=2 slots.
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        max_slots: int = 2,
        position_size_fraction: float = 0.50,
        discount_pct: float = 0.50,
        sell_premium_pct: float = 0.30,
        order_ttl_bars: int = 1,
        min_notional_usd: float = 10.0
    ):
        self.initial_capital = float(initial_capital)
        self.free_cash = float(initial_capital)
        self.max_slots = int(max_slots)
        self.position_size_fraction = float(position_size_fraction)
        self.discount_pct = float(discount_pct)
        self.sell_premium_pct = float(sell_premium_pct)
        self.order_ttl_bars = int(order_ttl_bars)
        self.min_notional_usd = float(min_notional_usd)

        # In-memory tracking registers
        self.resting_orders: Dict[str, RestingBuyOrder] = {}   # {symbol: RestingBuyOrder}
        self.active_positions: Dict[str, ActivePosition] = {}  # {symbol: ActivePosition}
        self.closed_trades: List[Dict[str, Any]] = []

    def get_total_equity(self, current_prices: Optional[Dict[str, float]] = None) -> float:
        """
        Total Portfolio Equity = Free Cash + Value of Active Positions + Escrowed Capital in Resting Orders.
        Guarantees strict balance sheet conservation.
        """
        current_prices = current_prices or {}
        pos_val = 0.0
        for pos in self.active_positions.values():
            cur_p = current_prices.get(pos.binance_symbol, pos.entry_price)
            pos_val += pos.quantity * cur_p

        escrowed_val = sum(o.allocated_capital for o in self.resting_orders.values())
        return self.free_cash + pos_val + escrowed_val

    def evaluate_signals(
        self,
        signals: List[Any],
        current_prices: Optional[Dict[str, float]] = None
    ) -> List[PortfolioAction]:
        """
        Evaluates incoming candidate signals at a 15m candle close.
        Applies Policy 4 (RVOL Volume Surge) cross-asset prioritization:
          1. Filters for valid signals (is_signal == True).
          2. Discards symbols already occupying an active position or resting order.
          3. Checks available concurrency slots: max_slots - (active_positions + resting_orders).
          4. If candidate count > available slots, sorts candidates by bar_rvol descending!
          5. Allocates capital to the top available candidates; marks others as starved.
        """
        actions: List[PortfolioAction] = []
        current_prices = current_prices or {}

        # 1. Filter firing candidate signals
        valid_candidates = []
        for sig in signals:
            if not getattr(sig, 'is_signal', False):
                continue

            sym = sig.kraken_symbol
            # Anti-chatter: one trade per symbol at a time
            if sym in self.active_positions or sym in self.resting_orders:
                logger.info(f"[{sym}] Signal fired but symbol already active/pending. Skipping.")
                continue

            valid_candidates.append(sig)

        if not valid_candidates:
            return actions

        # 2. Check available slot capacity
        occupied_slots = len(self.active_positions) + len(self.resting_orders)
        available_slots = max(0, self.max_slots - occupied_slots)

        if available_slots <= 0:
            for cand in valid_candidates:
                rvol = cand.features.get('bar_rvol', 1.0)
                logger.warning(
                    f"🚨 [SLOT SATURATED] {cand.kraken_symbol} signal STARVED | RVOL: {rvol:.2f}x | "
                    f"Slots: {occupied_slots}/{self.max_slots} occupied."
                )
            return actions

        # 3. Policy 4 Prioritization: Sort candidates by RVOL Volume Surge (Descending)
        if len(valid_candidates) > 1:
            valid_candidates.sort(
                key=lambda c: float(c.features.get('bar_rvol', 1.0)),
                reverse=True
            )
            rvol_ranks = [f"{c.kraken_symbol}({float(c.features.get('bar_rvol', 1.0)):.2f}x)" for c in valid_candidates]
            logger.info(f"🏆 [POLICY 4 RVOL AUCTION] Simultaneous candidates ranked: {', '.join(rvol_ranks)}")

        # 4. Admit top available_slots candidates
        admitted = valid_candidates[:available_slots]
        starved = valid_candidates[available_slots:]

        for st in starved:
            rvol = float(st.features.get('bar_rvol', 1.0))
            logger.warning(
                f"🚨 [POLICY 4 REJECT] {st.kraken_symbol} signal STARVED due to slot capacity | "
                f"RVOL: {rvol:.2f}x (Top {len(admitted)} admitted)"
            )

        # 5. Allocate capital and emit PLACE_RESTING_LIMIT_BUY actions
        for cand in admitted:
            tot_equity = self.get_total_equity(current_prices)
            target_cap = tot_equity * self.position_size_fraction
            alloc_cap = min(target_cap, self.free_cash)

            # Adaptive floor: if 50% sizing is below min_notional but free cash covers it, allocate min_notional
            if alloc_cap < self.min_notional_usd and self.free_cash >= self.min_notional_usd:
                alloc_cap = self.min_notional_usd

            if alloc_cap < self.min_notional_usd:
                logger.warning(
                    f"[{cand.kraken_symbol}] Insufficient free cash (${self.free_cash:.2f}) "
                    f"for minimum notional (${self.min_notional_usd:.2f})."
                )
                continue

            limit_buy_price = float(cand.limit_buy_price)
            qty = alloc_cap / limit_buy_price
            order_id = f"BUY_{cand.kraken_symbol}_{cand.timestamp.strftime('%Y%m%d%H%M')}"

            # Escrow capital and reserve slot
            self.free_cash -= alloc_cap

            resting_order = RestingBuyOrder(
                order_id=order_id,
                symbol=cand.kraken_symbol,
                binance_symbol=cand.binance_symbol,
                limit_buy_price=limit_buy_price,
                target_sell_price=float(cand.limit_sell_price),
                stop_loss_price=float(cand.stop_loss_price),
                quantity=qty,
                allocated_capital=alloc_cap,
                signal_timestamp=str(cand.timestamp),
                bars_waiting=0,
                status="PENDING"
            )
            self.resting_orders[cand.kraken_symbol] = resting_order

            rvol = float(cand.features.get('bar_rvol', 1.0))
            logger.info(
                f"🎯 [ADMITTED] {cand.kraken_symbol} -> {cand.binance_symbol} | RVOL: {rvol:.2f}x | "
                f"Allocated: ${alloc_cap:,.2f} | Limit Buy: ${limit_buy_price:,.2f} (-{self.discount_pct:.2f}%)"
            )

            actions.append(PortfolioAction(
                action_type=PortfolioActionType.PLACE_RESTING_LIMIT_BUY,
                symbol=cand.kraken_symbol,
                binance_symbol=cand.binance_symbol,
                price=limit_buy_price,
                quantity=qty,
                reason=f"Policy 4 RVOL Surge ({rvol:.2f}x) Buy Signal",
                target_price=float(cand.limit_sell_price),
                stop_price=float(cand.stop_loss_price),
                order_ref=order_id
            ))

        return actions

    def on_bar_close(self, current_time: pd.Timestamp) -> List[PortfolioAction]:
        """
        Called on every 15-minute candle close.
        Manages resting limit buy TTL:
          - Increments bars_waiting.
          - If bars_waiting >= order_ttl_bars without fill, cancels order and returns escrowed capital!
        """
        actions: List[PortfolioAction] = []
        expired_symbols = []

        # 1. Check resting buy order timeouts (TTL = 1 bar / 15m)
        for sym, order in self.resting_orders.items():
            order.bars_waiting += 1
            if order.bars_waiting >= self.order_ttl_bars:
                expired_symbols.append(sym)

        for sym in expired_symbols:
            order = self.resting_orders.pop(sym)
            self.free_cash += order.allocated_capital
            logger.info(
                f"⏱️ [TTL TIMEOUT] {sym} resting limit buy unfilled after {order.bars_waiting} bar(s). "
                f"Cancelling and returning ${order.allocated_capital:,.2f} to free cash."
            )
            actions.append(PortfolioAction(
                action_type=PortfolioActionType.CANCEL_RESTING_LIMIT_BUY,
                symbol=sym,
                binance_symbol=order.binance_symbol,
                price=order.limit_buy_price,
                quantity=order.quantity,
                reason=f"1-Bar TTL Expired ({self.order_ttl_bars * 15}m)",
                order_ref=order.order_id,
                exchange_order_id=order.exchange_order_id
            ))

        # 2. Increment holding duration on active positions
        for pos in self.active_positions.values():
            pos.bars_held += 1

        return actions

    def on_buy_fill(
        self,
        symbol: str,
        fill_price: float,
        filled_qty: float,
        timestamp: Optional[str] = None
    ) -> List[PortfolioAction]:
        """
        Called when a resting limit buy order fills on Binance.US.
        Transitions the resting order into an active position and pre-places the resting maker TP limit!
        """
        actions: List[PortfolioAction] = []
        clean = symbol.upper()

        order = self.resting_orders.pop(clean, None)
        champ = CRYPTO_CHAMPIONS.get(clean, {'x_star': 4.0, 'y_star': 2.0, 'binance_sym': clean})

        binance_sym = order.binance_symbol if order else champ.get('binance_sym', clean)
        allocated_cap = order.allocated_capital if order else (fill_price * filled_qty)

        x_star = champ['x_star']
        y_star = champ['y_star']

        # Pre-calculate Take-Profit Limit Price at target + premium (+x* + 0.30%)
        limit_sell_price = round(fill_price * (1.0 + (x_star + self.sell_premium_pct) / 100.0), 4)
        # Structural Stop Loss Price at -y*
        stop_loss_price = round(fill_price * (1.0 - y_star / 100.0), 4)

        pos_id = f"POS_{clean}_{int(time.time())}"
        pos = ActivePosition(
            position_id=pos_id,
            symbol=clean,
            binance_symbol=binance_sym,
            entry_price=fill_price,
            quantity=filled_qty,
            allocated_capital=allocated_cap,
            limit_sell_price=limit_sell_price,
            stop_loss_price=stop_loss_price,
            entry_timestamp=timestamp or str(pd.Timestamp.now(tz='UTC')),
            bars_held=0,
            status="OPEN"
        )
        self.active_positions[clean] = pos

        logger.info(
            f"✅ [BUY FILLED] {clean} @ ${fill_price:,.2f} | Qty: {filled_qty} | Alloc: ${allocated_cap:,.2f} | "
            f"Pre-Placing TP Maker Limit: ${limit_sell_price:,.2f} (+{x_star + self.sell_premium_pct:.2f}%) | "
            f"Stop Loss: ${stop_loss_price:,.2f} (-{y_star:.2f}%)"
        )

        # Pre-place passive resting maker limit take-profit (0.0% fee on Binance.US)
        actions.append(PortfolioAction(
            action_type=PortfolioActionType.PLACE_RESTING_LIMIT_SELL,
            symbol=clean,
            binance_symbol=binance_sym,
            price=limit_sell_price,
            quantity=filled_qty,
            reason=f"Pre-placed Maker Take-Profit (+{x_star + self.sell_premium_pct:.2f}%)",
            target_price=limit_sell_price,
            stop_price=stop_loss_price,
            order_ref=pos_id
        ))

        return actions

    def on_tp_fill(
        self,
        symbol: str,
        fill_price: float,
        timestamp: Optional[str] = None
    ) -> None:
        """Called when a resting take-profit limit sell fills on Binance.US."""
        clean = symbol.upper()
        pos = self.active_positions.pop(clean, None)
        if not pos:
            logger.warning(f"on_tp_fill called for {clean} but no active position found.")
            return

        net_ret_pct = ((fill_price - pos.entry_price) / pos.entry_price) * 100.0
        dollar_pnl = pos.allocated_capital * (net_ret_pct / 100.0)

        # Proceeds return to free cash (balance sheet conservation)
        self.free_cash += pos.allocated_capital + dollar_pnl

        trade_record = {
            'symbol': clean,
            'binance_symbol': pos.binance_symbol,
            'entry_price': pos.entry_price,
            'exit_price': fill_price,
            'quantity': pos.quantity,
            'allocated_capital': pos.allocated_capital,
            'dollar_pnl': dollar_pnl,
            'net_ret_pct': net_ret_pct,
            'exit_reason': "TAKE_PROFIT_LIMIT",
            'bars_held': pos.bars_held,
            'entry_time': pos.entry_timestamp,
            'exit_time': timestamp or str(pd.Timestamp.now(tz='UTC'))
        }
        self.closed_trades.append(trade_record)

        logger.info(
            f"🎉 [TP HIT] {clean} @ ${fill_price:,.2f} | PnL: +${dollar_pnl:,.2f} (+{net_ret_pct:.2f}%) | "
            f"Free Cash: ${self.free_cash:,.2f} | Slot Freed (0.0% Maker Fee)!"
        )

    def check_stop_losses(self, current_prices: Dict[str, float]) -> List[PortfolioAction]:
        """
        Monitors active positions against their stop-loss barrier (-y*).
        If breached, enforces the Zero-Market-Order rule:
          1. Cancels the resting TP limit sell.
          2. Emits SUBMIT_MONITORED_LIMIT_SELL to liquidate at best bid and chase until flat.
        """
        actions: List[PortfolioAction] = []
        stopped_symbols = []

        for sym, pos in self.active_positions.items():
            cur_p = current_prices.get(pos.binance_symbol)
            if cur_p is None:
                continue

            if cur_p <= pos.stop_loss_price:
                stopped_symbols.append((sym, cur_p))

        for sym, cur_p in stopped_symbols:
            pos = self.active_positions.pop(sym)
            net_ret_pct = ((cur_p - pos.entry_price) / pos.entry_price) * 100.0
            dollar_pnl = pos.allocated_capital * (net_ret_pct / 100.0)

            # Return remaining capital to free cash
            self.free_cash += pos.allocated_capital + dollar_pnl

            trade_record = {
                'symbol': sym,
                'binance_symbol': pos.binance_symbol,
                'entry_price': pos.entry_price,
                'exit_price': cur_p,
                'quantity': pos.quantity,
                'allocated_capital': pos.allocated_capital,
                'dollar_pnl': dollar_pnl,
                'net_ret_pct': net_ret_pct,
                'exit_reason': "STOP_LOSS_MONITORED",
                'bars_held': pos.bars_held,
                'entry_time': pos.entry_timestamp,
                'exit_time': str(pd.Timestamp.now(tz='UTC'))
            }
            self.closed_trades.append(trade_record)

            logger.warning(
                f"🛑 [STOP LOSS TRIGGERED] {sym} Price ${cur_p:,.2f} <= Stop ${pos.stop_loss_price:,.2f} | "
                f"Loss: ${dollar_pnl:,.2f} ({net_ret_pct:.2f}%) | Cancelling TP & Executing Monitored Limit Sell!"
            )

            # Step 1: Cancel resting TP order
            actions.append(PortfolioAction(
                action_type=PortfolioActionType.CANCEL_RESTING_LIMIT_SELL,
                symbol=sym,
                binance_symbol=pos.binance_symbol,
                price=pos.limit_sell_price,
                quantity=pos.quantity,
                reason="Stop Loss Triggered",
                order_ref=pos.position_id,
                exchange_order_id=pos.tp_order_id
            ))

            # Step 2: Zero-Market-Order compliant chased limit liquidation
            actions.append(PortfolioAction(
                action_type=PortfolioActionType.SUBMIT_MONITORED_LIMIT_SELL,
                symbol=sym,
                binance_symbol=pos.binance_symbol,
                price=cur_p,
                quantity=pos.quantity,
                reason=f"Stop Loss Liquidate (-{pos.stop_loss_price})",
                order_ref=pos.position_id,
                exchange_order_id=pos.tp_order_id
            ))

        return actions

    # ==========================================================================
    # 💾 STATE SERIALIZATION & LOSSLESS PERSISTENCE
    # ==========================================================================
    def to_dict(self) -> Dict[str, Any]:
        """Losslessly serializes portfolio state for SQLite persistence."""
        return {
            'initial_capital': self.initial_capital,
            'free_cash': self.free_cash,
            'max_slots': self.max_slots,
            'position_size_fraction': self.position_size_fraction,
            'discount_pct': self.discount_pct,
            'sell_premium_pct': self.sell_premium_pct,
            'order_ttl_bars': self.order_ttl_bars,
            'resting_orders': {s: asdict(o) for s, o in self.resting_orders.items()},
            'active_positions': {s: asdict(p) for s, p in self.active_positions.items()},
            'closed_trades': self.closed_trades
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Restores portfolio state losslessly across process restarts."""
        self.initial_capital = float(data.get('initial_capital', 10_000.0))
        self.free_cash = float(data.get('free_cash', self.initial_capital))
        self.max_slots = int(data.get('max_slots', 2))
        self.position_size_fraction = float(data.get('position_size_fraction', 0.50))
        self.discount_pct = float(data.get('discount_pct', 0.50))
        self.sell_premium_pct = float(data.get('sell_premium_pct', 0.30))
        self.order_ttl_bars = int(data.get('order_ttl_bars', 1))

        self.resting_orders = {}
        for s, o_data in data.get('resting_orders', {}).items():
            self.resting_orders[s] = RestingBuyOrder(**o_data)

        self.active_positions = {}
        for s, p_data in data.get('active_positions', {}).items():
            self.active_positions[s] = ActivePosition(**p_data)

        self.closed_trades = data.get('closed_trades', [])

    def save_state(self, db_path: Path) -> None:
        """Commits serialized portfolio state to SQLite."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        state_json = json.dumps(self.to_dict())

        with sqlite3.connect(db_path, timeout=10.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    updated_at REAL NOT NULL,
                    state_json TEXT NOT NULL
                )
            """)
            conn.execute("""
                INSERT OR REPLACE INTO portfolio_state (id, updated_at, state_json)
                VALUES (1, ?, ?)
            """, (time.time(), state_json))
            conn.commit()

    def load_state(self, db_path: Path) -> bool:
        """Loads serialized portfolio state from SQLite. Returns True if restored."""
        if not db_path.exists():
            return False

        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT state_json FROM portfolio_state WHERE id = 1")
                row = cursor.fetchone()
                if row and row[0]:
                    data = json.loads(row[0])
                    self.from_dict(data)
                    logger.info(
                        f"Losslessly restored portfolio state: Free Cash=${self.free_cash:,.2f} | "
                        f"Active Positions={len(self.active_positions)} | Resting Orders={len(self.resting_orders)}"
                    )
                    return True
        except Exception as e:
            logger.warning(f"Could not restore portfolio state from {db_path.name}: {e}")

        return False
