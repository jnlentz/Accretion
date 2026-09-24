"""
====================================================================================================
PROJECT ACCRETION: 24/7 CRYPTO LIVE STRATEGY RUNNER (POLICY 4: RVOL VOLUME SURGE)
====================================================================================================
Purpose:
  Production execution orchestrator for the 24/7 continuous crypto strategy:
    1. Cross-exchange hybrid pipeline: Ingests 15m Kraken bars -> Routes dual maker orders to Binance.US.
    2. Policy 4 Cross-Asset Prioritization: Ranks simultaneous signals by RVOL volume surge.
    3. Portfolio Concurrency: K=2 Slots with 50% Compounded Equity Sizing.
    4. Dual Passive Maker Execution: Maker Buy (-0.50%) / Maker Take-Profit (+x* + 0.30%).
    5. Order TTL Management: 1-bar (15-min) auto-cancel on unfilled limit buys.
    6. Zero-Market-Order Stop Invalidation: Chased limit liquidation at -y*.
    7. Supports both --mode paper (simulation against live prices) and --mode live (Binance.US REST).
    8. Lossless state persistence across restarts to state/crypto_live_portfolio.sqlite.

Usage:
  python scripts/run_crypto_live_strategy.py --mode paper
  python scripts/run_crypto_live_strategy.py --mode live
====================================================================================================
"""

import sys
import os
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

import pandas as pd

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Sklearn unpickling compatibility shim
from research_import.crypto_live_engine import (
    CryptoLiveFeatureEngine,
    CryptoLiveInferenceEngine,
    LivePredictionLogger,
    TradeSignal,
    CRYPTO_CHAMPIONS,
    WARMUP_BARS_MIN
)
from src.adapters.kraken_data import KrakenData, INTERVAL_SECONDS_15M
from src.strategy.crypto_portfolio_engine import (
    CryptoPortfolioEngine,
    PortfolioAction,
    PortfolioActionType
)
from src.ledger.activity_ledger import ActivityLedger
from src.runtime.outage_recovery import OutageRecoveryEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("accretion.live_strategy")


def sleep_until_next_15m_boundary(buffer_seconds: int = 5) -> float:
    """Calculates seconds until the next 15m candle close (:00, :15, :30, :45 UTC) + buffer."""
    now = datetime.now(timezone.utc)
    current_minute = now.minute
    current_second = now.second
    current_microsecond = now.microsecond

    minutes_to_next = 15 - (current_minute % 15)
    seconds_to_next = (minutes_to_next * 60) - current_second - (current_microsecond / 1_000_000.0)
    return max(1.0, seconds_to_next + buffer_seconds)


class CryptoLiveStrategyRunner:
    """Master orchestrator for live multi-asset crypto execution."""

    def __init__(
        self,
        mode: str = "paper",
        initial_capital: Optional[float] = None,
        max_slots: int = 2,
        symbols: Optional[List[str]] = None,
        discount_pct: float = 0.50,
        sell_premium_pct: float = 0.30,
        order_ttl_bars: int = 1
    ):
        self.mode = mode.lower()
        self.symbols = symbols or list(CRYPTO_CHAMPIONS.keys())
        self.db_dir = PROJECT_ROOT / "databases" / "kraken"
        self.log_dir = PROJECT_ROOT / "logs"
        self.state_dir = PROJECT_ROOT / "state"
        self.portfolio_db = self.state_dir / "crypto_live_portfolio.sqlite"
        self.models_dir = PROJECT_ROOT / "research_import" / "models"
        self.config_path = PROJECT_ROOT / "research_import" / "config" / "crypto_champions_config.json"

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # 1. Initialize Adapters & Activity Ledger
        self.kraken_data = KrakenData(db_dir=str(self.db_dir))
        self.pred_logger = LivePredictionLogger(self.log_dir)
        self.activity_ledger = ActivityLedger()

        self.binance_adapter = None
        if self.mode == "live":
            from src.adapters.binance_client import BinanceSpotAdapter
            try:
                import config
                api_key = getattr(config, 'BINANCE_API_KEY', '')
                api_secret = getattr(config, 'BINANCE_API_SECRET', '')
                self.binance_adapter = BinanceSpotAdapter(api_key=api_key, api_secret=api_secret, tld='us')
                logger.info("Initialized live Binance.US exchange adapter.")
            except Exception as e:
                logger.error(f"Failed to initialize BinanceSpotAdapter: {e}. Falling back to paper mode.")
                self.mode = "paper"

        # 2. Determine base trading capital (Detect real USD balance in live mode)
        live_bal = None
        if self.mode == "live" and self.binance_adapter:
            live_bal = self._fetch_live_binance_balance()

        if live_bal is not None and live_bal > 0.0:
            actual_capital = live_bal
            logger.info(f"💰 Setting base capital to live Binance.US balance: ${actual_capital:,.2f}")
        elif initial_capital is not None:
            actual_capital = float(initial_capital)
        else:
            actual_capital = 10_000.0 if self.mode == "paper" else 100.0

        # 3. Initialize Engines
        self.feature_engines: Dict[str, CryptoLiveFeatureEngine] = {
            s: CryptoLiveFeatureEngine(s) for s in self.symbols
        }

        self.inference_engine = CryptoLiveInferenceEngine(
            models_dir=self.models_dir,
            discount_pct=discount_pct,
            sell_premium_pct=sell_premium_pct
        )
        if self.config_path.exists():
            self.inference_engine.load_models_from_dir(self.models_dir, config_path=self.config_path)

        self.portfolio = CryptoPortfolioEngine(
            initial_capital=actual_capital,
            max_slots=max_slots,
            position_size_fraction=0.50,
            discount_pct=discount_pct,
            sell_premium_pct=sell_premium_pct,
            order_ttl_bars=order_ttl_bars,
            min_notional_usd=10.0
        )

        # Restore state if previously persisted
        if self.portfolio.load_state(self.portfolio_db):
            logger.info("Successfully restored portfolio state from SQLite.")
            # In live mode, reconcile free cash with the real live exchange balance
            if live_bal is not None and live_bal > 0.0:
                if not self.portfolio.active_positions and not self.portfolio.resting_orders:
                    self.portfolio.initial_capital = live_bal
                    self.portfolio.free_cash = live_bal
                else:
                    self.portfolio.free_cash = live_bal
                logger.info(f"💰 Re-aligned portfolio cash with live Binance.US balance: ${live_bal:,.2f}")
        else:
            logger.info(f"Initialized fresh portfolio state with ${actual_capital:,.2f} capital.")

        self.current_ticker_prices: Dict[str, float] = {}

    def _fetch_live_binance_balance(self) -> Optional[float]:
        """Queries Binance.US for available USD balance (fallback to USDT)."""
        if not self.binance_adapter:
            return None
        try:
            usd_bal = self.binance_adapter.get_asset_balance('USD')
            free_usd = float(usd_bal.get('free', 0.0))
            if free_usd > 0.0:
                logger.info(f"💰 [BINANCE LIVE] Available USD balance: ${free_usd:,.2f}")
                return free_usd

            usdt_bal = self.binance_adapter.get_asset_balance('USDT')
            free_usdt = float(usdt_bal.get('free', 0.0))
            if free_usdt > 0.0:
                logger.info(f"💰 [BINANCE LIVE] USD is $0.00, but detected available USDT: ${free_usdt:,.2f}")
                return free_usdt

            logger.warning("⚠️ [BINANCE LIVE] Both USD and USDT free balances are $0.00 on Binance.US.")
            return 0.0
        except Exception as e:
            err_str = str(e)
            if "-2015" in err_str:
                logger.error(
                    f"❌ [BINANCE LIVE] Binance.US APIError(code=-2015): Invalid API-key, IP, or permissions.\n"
                    f"   -> Ensure your API key has 'Enable Reading' and 'Enable Spot Trading' checked on Binance.US.\n"
                    f"   -> If IP restrictions are enabled, whitelist this machine's public IP on Binance.US."
                )
            else:
                logger.error(f"❌ [BINANCE LIVE] Failed to query account balance: {e}")
            return None

    def startup_and_warmup(self) -> None:
        """Runs outage recovery and warms up streaming feature buffers for all 6 coins."""
        # Step 1: Run Outage Recovery & Self-Healing Protocol (Audits open orders and adopts unrecorded inventory)
        recovery_engine = OutageRecoveryEngine(
            portfolio=self.portfolio,
            kraken_data=self.kraken_data,
            activity_ledger=self.activity_ledger,
            binance_adapter=self.binance_adapter,
            mode=self.mode,
            symbols=self.symbols,
            portfolio_db=self.portfolio_db
        )
        recovery_engine.reconcile()

        # Refresh live USD balance on startup in live mode
        if self.mode == "live" and self.binance_adapter:
            live_bal = self._fetch_live_binance_balance()
            if live_bal is not None and live_bal > 0.0:
                self.portfolio.free_cash = live_bal
                # Base trading capital represents total portfolio equity (cash + open inventory)
                tot_live_eq = self.portfolio.get_total_equity(self.current_ticker_prices)
                self.portfolio.initial_capital = tot_live_eq
                self.portfolio.save_state(self.portfolio_db)

        print("\n" + "=" * 95)
        print(f"🚀 INITIALIZING 24/7 CRYPTO LIVE STRATEGY (MODE: {self.mode.upper()})")
        print("=" * 95)
        print(f"Universe ({len(self.symbols)} assets) : {', '.join(self.symbols)}")
        print(f"Prioritization Policy   : Policy 4 RVOL Volume Surge")
        print(f"Base Trading Capital    : ${self.portfolio.initial_capital:,.2f} (Free Cash: ${self.portfolio.free_cash:,.2f})")
        print(f"Concurrency Slots (K)   : {self.portfolio.max_slots} Slots | 50% Compounded Equity Sizing")
        print(f"Execution Geometry      : Buy Discount -{self.portfolio.discount_pct:.2f}% | Sell Premium +{self.portfolio.sell_premium_pct:.2f}%")
        print(f"Order TTL               : {self.portfolio.order_ttl_bars} Bar(s) ({self.portfolio.order_ttl_bars * 15} min)")
        print(f"Zero-Market-Order Stop  : Monitored Pegged Limit Sell (-y*)")
        print(f"Activity Database       : {self.activity_ledger.db_path.name} (WAL Mode)")
        print("=" * 95 + "\n")

        # Step 2: Warm up streaming feature engines with 672 bars
        logger.info(f"Warming up streaming feature engines with {WARMUP_BARS_MIN} bars...")
        for sym in self.symbols:
            try:
                df_w = self.kraken_data.get_warmup_candles(sym, limit=WARMUP_BARS_MIN)
                self.feature_engines[sym].warmup(df_w)
                self.current_ticker_prices[CRYPTO_CHAMPIONS[sym]['binance_sym']] = float(df_w['close'].iloc[-1])
                logger.info(f"  • {sym:<8}: Warmed ({df_w.index.min()} to {df_w.index.max()}) | Close: ${df_w['close'].iloc[-1]:,.2f}")
            except Exception as e:
                logger.error(f"  • {sym:<8}: Warmup failed: {e}")

        logger.info("All streaming feature engines primed and ready.")

    def run(self) -> None:
        """Main execution loop coordinating high-frequency order checks and 15m bar closes."""
        self.startup_and_warmup()

        logger.info("Entering 24/7 continuous strategy execution loop. Press Ctrl+C to stop.")
        last_processed_15m_bar = None

        while True:
            try:
                now_utc = datetime.now(timezone.utc)
                now_sec = int(now_utc.timestamp())

                # --------------------------------------------------------------
                # 1. High-Frequency Order Monitoring & Price Update (Every 5s)
                # --------------------------------------------------------------
                self._update_current_prices()
                self._tick_order_lifecycle()

                # --------------------------------------------------------------
                # 2. Check 15-Minute Boundary for Completed Kraken Candles
                # --------------------------------------------------------------
                current_15m_bucket = now_sec // INTERVAL_SECONDS_15M
                if (now_sec % INTERVAL_SECONDS_15M) >= 5 and last_processed_15m_bar != current_15m_bucket:
                    # New 15-minute bar has closed
                    self._on_15m_candle_close(now_utc)
                    last_processed_15m_bar = current_15m_bucket
                    self.portfolio.save_state(self.portfolio_db)
                    self._log_portfolio_snapshot()
                    self._print_portfolio_status()

                time.sleep(5.0)

            except KeyboardInterrupt:
                logger.info("Shutdown requested. Committing portfolio state and exiting cleanly.")
                self.portfolio.save_state(self.portfolio_db)
                self._log_portfolio_snapshot()
                break
            except Exception as e:
                logger.error(f"Error in strategy cycle: {e}", exc_info=True)
                time.sleep(5.0)

    def _update_current_prices(self) -> None:
        """Updates top-of-book / current prices for all coins."""
        if self.mode == "live" and self.binance_adapter:
            try:
                tickers = self.binance_adapter.client.get_all_tickers()
                price_map = {t['symbol']: float(t['price']) for t in tickers}
                for sym, champ in CRYPTO_CHAMPIONS.items():
                    bsym = champ['binance_sym']
                    if bsym in price_map:
                        self.current_ticker_prices[bsym] = price_map[bsym]
                    elif champ['binance_alt'] in price_map:
                        self.current_ticker_prices[bsym] = price_map[champ['binance_alt']]
            except Exception as e:
                logger.warning(f"Error updating Binance prices: {e}")

    def _tick_order_lifecycle(self) -> None:
        """Monitors resting orders, fills, and stop loss conditions."""
        # 1. Check Stop Losses (Zero-Market-Order Monitored Liquidation)
        prev_trades_count = len(self.portfolio.closed_trades)
        stop_actions = self.portfolio.check_stop_losses(self.current_ticker_prices)
        for act in stop_actions:
            self._execute_action(act)

        # Log newly closed stop trades to activity ledger
        if len(self.portfolio.closed_trades) > prev_trades_count:
            for tr in self.portfolio.closed_trades[prev_trades_count:]:
                self.activity_ledger.log_completed_trade(tr)
                self.activity_ledger.log_order_event(
                    event_type="STOP_LIQUIDATED",
                    symbol=tr['symbol'],
                    binance_symbol=tr['binance_symbol'],
                    side="SELL",
                    order_type="LIMIT_MONITORED",
                    price=tr['exit_price'],
                    quantity=tr['quantity'],
                    allocated_capital=tr['allocated_capital'],
                    reason="Stop Loss Barrier Hit (-y*)",
                    status="FILLED",
                    details={'dollar_pnl': tr['dollar_pnl'], 'net_ret_pct': tr['net_ret_pct']}
                )

        # 2. Check Order Fills
        if self.mode == "live":
            self._poll_live_order_fills()
        elif self.mode == "paper":
            self._simulate_paper_fills()

    def _poll_live_order_fills(self) -> None:
        """
        Polls Binance.US for fills on resting buy orders and resting TP sell orders.
        Transitions filled buy orders into active positions and pre-places TP limit sells.
        Reconciles filled TP sells, logs completed trades, and frees capital & slots.
        """
        if self.mode != "live" or not self.binance_adapter:
            return

        # 1. Check Resting Buy Orders for Fills
        for sym, order in list(self.portfolio.resting_orders.items()):
            ex_id = order.exchange_order_id
            bsym = order.binance_symbol
            if not ex_id:
                continue

            try:
                order_info = self.binance_adapter.get_order_status(bsym, order_id=ex_id)
                if not order_info:
                    continue

                status = order_info.get('status', '').upper()
                if status == 'FILLED':
                    exec_qty = float(order_info.get('executedQty', order.quantity))
                    quote_qty = float(order_info.get('cummulativeQuoteQty', 0.0))
                    avg_price = quote_qty / exec_qty if exec_qty > 0 and quote_qty > 0 else float(order_info.get('price', order.limit_buy_price))

                    logger.info(f"⚡ [BINANCE LIVE FILL] Limit Buy FILLED: {bsym} Qty: {exec_qty} @ ${avg_price:,.4f}")

                    self.activity_ledger.log_order_event(
                        event_type="BUY_FILLED",
                        symbol=sym,
                        binance_symbol=bsym,
                        side="BUY",
                        order_type="LIMIT",
                        price=avg_price,
                        quantity=exec_qty,
                        allocated_capital=avg_price * exec_qty,
                        order_id=order.order_id,
                        exchange_order_id=ex_id,
                        reason="Binance.US Limit Buy Filled",
                        status="FILLED"
                    )

                    # Transitions resting order to ActivePosition and emits PLACE_RESTING_LIMIT_SELL
                    actions = self.portfolio.on_buy_fill(sym, avg_price, exec_qty)
                    for act in actions:
                        self._execute_action(act)

                    self.portfolio.save_state(self.portfolio_db)

                elif status in ('CANCELED', 'REJECTED', 'EXPIRED'):
                    logger.info(f"ℹ️ [BINANCE LIVE] Buy order {ex_id} for {bsym} was {status}. Removing from resting orders.")
                    popped = self.portfolio.resting_orders.pop(sym, None)
                    if popped:
                        self.portfolio.free_cash += popped.allocated_capital
                    self.portfolio.save_state(self.portfolio_db)

            except Exception as e:
                logger.warning(f"Error polling order status for {bsym} (id={ex_id}): {e}")

        # 2. Check Active Positions for TP Limit Sell Fills
        for sym, pos in list(self.portfolio.active_positions.items()):
            ex_id = pos.tp_order_id
            bsym = pos.binance_symbol

            # If position does NOT have an open TP order on Binance, place it!
            if not ex_id:
                fmt_price = self.binance_adapter.format_price(bsym, pos.limit_sell_price)
                fmt_qty = self.binance_adapter.format_quantity(bsym, pos.quantity)
                try:
                    res = self.binance_adapter.create_limit_sell(bsym, fmt_qty, fmt_price)
                    pos.tp_order_id = str(res.get('orderId'))
                    logger.info(f"⚡ [BINANCE LIVE] Placed missing TP Limit Sell for {bsym}: id={pos.tp_order_id}")
                    self.portfolio.save_state(self.portfolio_db)
                except Exception as e:
                    logger.error(f"Failed to place TP sell for active position {bsym}: {e}")
                continue

            try:
                order_info = self.binance_adapter.get_order_status(bsym, order_id=ex_id)
                if not order_info:
                    continue

                status = order_info.get('status', '').upper()
                if status == 'FILLED':
                    exec_qty = float(order_info.get('executedQty', pos.quantity))
                    quote_qty = float(order_info.get('cummulativeQuoteQty', 0.0))
                    fill_price = quote_qty / exec_qty if exec_qty > 0 and quote_qty > 0 else float(order_info.get('price', pos.limit_sell_price))

                    logger.info(f"🎉 [BINANCE LIVE TP FILL] Take-profit filled: {bsym} Qty: {exec_qty} @ ${fill_price:,.4f}")

                    self.portfolio.on_tp_fill(sym, fill_price)
                    if self.portfolio.closed_trades:
                        tr = self.portfolio.closed_trades[-1]
                        self.activity_ledger.log_completed_trade(tr)
                        self.activity_ledger.log_order_event(
                            event_type="TP_FILLED",
                            symbol=sym,
                            binance_symbol=bsym,
                            side="SELL",
                            order_type="LIMIT",
                            price=fill_price,
                            quantity=exec_qty,
                            allocated_capital=tr['allocated_capital'],
                            order_id=pos.position_id,
                            exchange_order_id=ex_id,
                            reason="Take-Profit Limit Filled on Exchange",
                            status="FILLED",
                            details={'dollar_pnl': tr['dollar_pnl'], 'net_ret_pct': tr['net_ret_pct']}
                        )

                    self.portfolio.save_state(self.portfolio_db)

                elif status in ('CANCELED', 'REJECTED', 'EXPIRED'):
                    logger.warning(f"⚠️ [BINANCE LIVE] TP order {ex_id} for {bsym} is {status}. Resetting tp_order_id to re-place.")
                    pos.tp_order_id = None

            except Exception as e:
                logger.warning(f"Error polling TP order status for {bsym} (id={ex_id}): {e}")

    def _simulate_paper_fills(self) -> None:
        """Paper mode fill simulator against live top-of-book prices."""
        # Check resting limit buys
        filled_buys = []
        for sym, order in list(self.portfolio.resting_orders.items()):
            cur_p = self.current_ticker_prices.get(order.binance_symbol)
            if cur_p is not None and cur_p <= order.limit_buy_price:
                filled_buys.append((sym, order.limit_buy_price, order.quantity, order.allocated_capital, order.order_id))

        for sym, fill_p, qty, alloc_cap, ord_id in filled_buys:
            logger.info(f"📝 [PAPER FILL] Buy order filled for {sym} @ ${fill_p:,.2f}")
            bsym = CRYPTO_CHAMPIONS[sym]['binance_sym']
            self.activity_ledger.log_order_event(
                event_type="BUY_FILLED",
                symbol=sym,
                binance_symbol=bsym,
                side="BUY",
                order_type="LIMIT",
                price=fill_p,
                quantity=qty,
                allocated_capital=alloc_cap,
                order_id=ord_id,
                reason="Paper Limit Buy Hit Discount Level",
                status="FILLED"
            )
            actions = self.portfolio.on_buy_fill(sym, fill_p, qty)
            for act in actions:
                self._execute_action(act)

        # Check resting limit sells (TP)
        filled_tps = []
        for sym, pos in list(self.portfolio.active_positions.items()):
            cur_p = self.current_ticker_prices.get(pos.binance_symbol)
            if cur_p is not None and cur_p >= pos.limit_sell_price:
                filled_tps.append((sym, pos.limit_sell_price))

        for sym, fill_p in filled_tps:
            logger.info(f"📝 [PAPER TP FILL] Take-profit filled for {sym} @ ${fill_p:,.2f}")
            self.portfolio.on_tp_fill(sym, fill_p)
            if self.portfolio.closed_trades:
                tr = self.portfolio.closed_trades[-1]
                self.activity_ledger.log_completed_trade(tr)
                self.activity_ledger.log_order_event(
                    event_type="TP_FILLED",
                    symbol=sym,
                    binance_symbol=tr['binance_symbol'],
                    side="SELL",
                    order_type="LIMIT",
                    price=fill_p,
                    quantity=tr['quantity'],
                    allocated_capital=tr['allocated_capital'],
                    reason="Paper Take-Profit Limit Hit (+x* + 0.30%)",
                    status="FILLED",
                    details={'dollar_pnl': tr['dollar_pnl'], 'net_ret_pct': tr['net_ret_pct']}
                )

    def _on_15m_candle_close(self, now_utc: datetime) -> None:
        """Processes newly closed 15m candles, TTL expirations, and RVOL prioritization."""
        logger.info(f"\n🔔 [15M CANDLE CLOSE] {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC")

        # 1. Manage TTL for resting buy orders (1-bar auto-cancel)
        ttl_actions = self.portfolio.on_bar_close(pd.Timestamp(now_utc))
        for act in ttl_actions:
            self._execute_action(act)

        # 2. Poll newly closed 15m bars from Kraken across all 6 symbols
        new_bars = self.kraken_data.poll_latest_closed_bars_all(self.symbols)

        # 3. Compute structural features and evaluate GBDT models
        candidate_signals: List[TradeSignal] = []
        for sym, bar in new_bars.items():
            if bar is None:
                continue

            feats = self.feature_engines[sym].on_new_bar(
                dt=bar['dt'],
                open_p=bar['open'],
                high_p=bar['high'],
                low_p=bar['low'],
                close_p=bar['close'],
                volume=bar['volume']
            )

            if feats is None:
                continue

            # Update latest price reference
            bsym = CRYPTO_CHAMPIONS[sym]['binance_sym']
            self.current_ticker_prices[bsym] = bar['close']

            sig = self.inference_engine.evaluate(sym, bar['dt'], bar['close'], feats)
            if sig is not None:
                self.pred_logger.log_prediction(sig)
                self.activity_ledger.log_prediction(sig)
                if sig.is_signal:
                    candidate_signals.append(sig)

        # 4. Apply Policy 4 (RVOL Volume Surge) Cross-Asset Prioritization Auction
        if candidate_signals:
            logger.info(f"⚡ Fired {len(candidate_signals)} candidate signal(s). Running Policy 4 RVOL Prioritization...")
            port_actions = self.portfolio.evaluate_signals(candidate_signals, self.current_ticker_prices)
            for act in port_actions:
                self._execute_action(act)

    def _execute_action(self, action: PortfolioAction) -> None:
        """Dispatches portfolio actions to Binance.US (live) or paper ledger (paper)."""
        act_type = action.action_type
        sym = action.symbol
        bsym = action.binance_symbol

        if act_type == PortfolioActionType.PLACE_RESTING_LIMIT_BUY:
            alloc_cap = action.price * action.quantity
            if self.mode == "live" and self.binance_adapter:
                fmt_price = self.binance_adapter.format_price(bsym, action.price)
                fmt_qty = self.binance_adapter.format_quantity(bsym, action.quantity)
                logger.info(f"⚡ [BINANCE LIVE] Placing Maker Limit Buy {bsym} Qty: {fmt_qty} @ ${fmt_price:,.2f}")
                try:
                    res = self.binance_adapter.create_limit_buy(bsym, fmt_qty, fmt_price)
                    ex_id = str(res.get('orderId'))
                    if action.symbol in self.portfolio.resting_orders:
                        self.portfolio.resting_orders[action.symbol].exchange_order_id = ex_id
                    self.activity_ledger.log_order_event(
                        event_type="BUY_SUBMITTED",
                        symbol=sym,
                        binance_symbol=bsym,
                        side="BUY",
                        order_type="LIMIT",
                        price=fmt_price,
                        quantity=fmt_qty,
                        allocated_capital=alloc_cap,
                        order_id=action.order_ref,
                        exchange_order_id=ex_id,
                        reason=action.reason,
                        status="SUBMITTED"
                    )
                except Exception as e:
                    logger.error(f"Failed to place live limit buy on Binance.US: {e}")
                    # Immediately rollback admitted order so slot is not trapped
                    if action.symbol in self.portfolio.resting_orders:
                        popped = self.portfolio.resting_orders.pop(action.symbol, None)
                        if popped:
                            self.portfolio.free_cash += popped.allocated_capital
                    # If rejected due to insufficient balance, sync free cash with real Binance USD
                    if "-2010" in str(e):
                        live_bal = self._fetch_live_binance_balance()
                        if live_bal is not None:
                            self.portfolio.free_cash = live_bal
                    self.portfolio.save_state(self.portfolio_db)
                    self.activity_ledger.log_order_event(
                        event_type="BUY_FAILED",
                        symbol=sym,
                        binance_symbol=bsym,
                        side="BUY",
                        order_type="LIMIT",
                        price=fmt_price,
                        quantity=fmt_qty,
                        allocated_capital=alloc_cap,
                        order_id=action.order_ref,
                        reason=f"Binance order failed: {e}",
                        status="REJECTED"
                    )
            else:
                logger.info(f"📝 [PAPER ROUTE] Resting Limit Buy {bsym} Qty: {action.quantity:.6f} @ ${action.price:,.2f}")
                self.activity_ledger.log_order_event(
                    event_type="BUY_SUBMITTED",
                    symbol=sym,
                    binance_symbol=bsym,
                    side="BUY",
                    order_type="LIMIT",
                    price=action.price,
                    quantity=action.quantity,
                    allocated_capital=alloc_cap,
                    order_id=action.order_ref,
                    reason=action.reason,
                    status="SUBMITTED"
                )

        elif act_type == PortfolioActionType.CANCEL_RESTING_LIMIT_BUY:
            alloc_cap = action.price * action.quantity
            ex_id = action.exchange_order_id
            if not ex_id and sym in self.portfolio.resting_orders:
                ex_id = self.portfolio.resting_orders[sym].exchange_order_id

            if self.mode == "live" and self.binance_adapter and ex_id:
                try:
                    # Check if order already filled right before cancellation
                    st = self.binance_adapter.get_order_status(bsym, order_id=ex_id)
                    if st and st.get('status') == 'FILLED':
                        exec_qty = float(st.get('executedQty', action.quantity))
                        quote_qty = float(st.get('cummulativeQuoteQty', 0.0))
                        avg_p = quote_qty / exec_qty if exec_qty > 0 and quote_qty > 0 else float(st.get('price', action.price))
                        logger.info(f"⚡ [BINANCE LIVE] Limit buy for {bsym} filled right before cancel! Reconciling as fill @ ${avg_p:,.4f}")
                        # Re-deduct capital that was returned in on_bar_close
                        self.portfolio.free_cash -= (avg_p * exec_qty)
                        actions = self.portfolio.on_buy_fill(sym, avg_p, exec_qty)
                        for a in actions:
                            self._execute_action(a)
                        self.portfolio.save_state(self.portfolio_db)
                        return
                    elif st and st.get('status') in ('NEW', 'PARTIALLY_FILLED'):
                        logger.info(f"⚡ [BINANCE LIVE] Cancelling Limit Buy {bsym} (id={ex_id})")
                        self.binance_adapter.cancel_order(bsym, order_id=ex_id)
                except Exception as e:
                    logger.error(f"Failed to cancel buy order {ex_id} on Binance.US: {e}")
            else:
                logger.info(f"📝 [PAPER ROUTE] Cancelled Resting Limit Buy {bsym}")

            self.activity_ledger.log_order_event(
                event_type="BUY_CANCELLED_TTL",
                symbol=sym,
                binance_symbol=bsym,
                side="BUY",
                order_type="LIMIT",
                price=action.price,
                quantity=action.quantity,
                allocated_capital=alloc_cap,
                order_id=action.order_ref,
                exchange_order_id=ex_id,
                reason=action.reason,
                status="CANCELED"
            )

        elif act_type == PortfolioActionType.PLACE_RESTING_LIMIT_SELL:
            alloc_cap = action.price * action.quantity
            if self.mode == "live" and self.binance_adapter:
                fmt_price = self.binance_adapter.format_price(bsym, action.price)
                fmt_qty = self.binance_adapter.format_quantity(bsym, action.quantity)
                logger.info(f"⚡ [BINANCE LIVE] Pre-Placing Resting TP Limit Sell {bsym} Qty: {fmt_qty} @ ${fmt_price:,.2f}")
                try:
                    res = self.binance_adapter.create_limit_sell(bsym, fmt_qty, fmt_price)
                    ex_id = str(res.get('orderId'))
                    if action.symbol in self.portfolio.active_positions:
                        self.portfolio.active_positions[action.symbol].tp_order_id = ex_id
                    self.portfolio.save_state(self.portfolio_db)
                    self.activity_ledger.log_order_event(
                        event_type="TP_PREPLACED",
                        symbol=sym,
                        binance_symbol=bsym,
                        side="SELL",
                        order_type="LIMIT",
                        price=fmt_price,
                        quantity=fmt_qty,
                        allocated_capital=alloc_cap,
                        order_id=action.order_ref,
                        exchange_order_id=ex_id,
                        reason=action.reason,
                        status="SUBMITTED"
                    )
                except Exception as e:
                    logger.error(f"Failed to pre-place live TP limit on Binance.US: {e}")
            else:
                logger.info(f"📝 [PAPER ROUTE] Pre-Placed Maker TP Limit {bsym} Qty: {action.quantity:.6f} @ ${action.price:,.2f}")
                self.activity_ledger.log_order_event(
                    event_type="TP_PREPLACED",
                    symbol=sym,
                    binance_symbol=bsym,
                    side="SELL",
                    order_type="LIMIT",
                    price=action.price,
                    quantity=action.quantity,
                    allocated_capital=alloc_cap,
                    order_id=action.order_ref,
                    reason=action.reason,
                    status="SUBMITTED"
                )

        elif act_type == PortfolioActionType.CANCEL_RESTING_LIMIT_SELL:
            ex_id = action.exchange_order_id
            if not ex_id and sym in self.portfolio.active_positions:
                ex_id = self.portfolio.active_positions[sym].tp_order_id
            if self.mode == "live" and self.binance_adapter and ex_id:
                logger.info(f"⚡ [BINANCE LIVE] Cancelling Resting TP Limit Sell {bsym} (id={ex_id})")
                try:
                    self.binance_adapter.cancel_order(bsym, order_id=ex_id)
                except Exception as e:
                    logger.error(f"Failed to cancel TP sell {ex_id} on Binance.US: {e}")
            else:
                logger.info(f"📝 [PAPER ROUTE] Cancelled Resting TP Limit Sell {bsym}")

        elif act_type == PortfolioActionType.SUBMIT_MONITORED_LIMIT_SELL:
            # Zero-Market-Order Stop Invalidation: Chased limit sell at best bid
            alloc_cap = action.price * action.quantity
            if self.mode == "live" and self.binance_adapter:
                # Cancel resting TP order first if still open so inventory is unlocked
                ex_id = action.exchange_order_id
                if not ex_id and sym in self.portfolio.active_positions:
                    ex_id = self.portfolio.active_positions[sym].tp_order_id
                if ex_id:
                    try:
                        self.binance_adapter.cancel_order(bsym, order_id=ex_id)
                    except Exception:
                        pass

                fmt_price = self.binance_adapter.format_price(bsym, action.price)
                fmt_qty = self.binance_adapter.format_quantity(bsym, action.quantity)
                logger.warning(f"🚨 [BINANCE LIVE] Zero-Market-Order Stop Liquidation {bsym} Qty: {fmt_qty} @ ${fmt_price:,.2f}")
                try:
                    res = self.binance_adapter.execute_monitored_limit_sell(bsym, fmt_qty)
                    logger.info(f"Monitored stop liquidation complete: {res}")
                except Exception as e:
                    logger.error(f"Failed to execute monitored limit stop on Binance.US: {e}")
            else:
                logger.warning(f"📝 [PAPER ROUTE] Stop Loss Liquidated {bsym} Qty: {action.quantity:.6f} @ ${action.price:,.2f}")

            self.activity_ledger.log_order_event(
                event_type="STOP_MONITORED_SUBMITTED",
                symbol=sym,
                binance_symbol=bsym,
                side="SELL",
                order_type="LIMIT_MONITORED",
                price=action.price,
                quantity=action.quantity,
                allocated_capital=alloc_cap,
                order_id=action.order_ref,
                reason=action.reason,
                status="SUBMITTED"
            )

    def _log_portfolio_snapshot(self) -> None:
        """Captures real-time portfolio snapshot in SQLite for mobile app monitoring."""
        tot_eq = self.portfolio.get_total_equity(self.current_ticker_prices)
        tot_pnl = sum(t.get('dollar_pnl', 0.0) for t in self.portfolio.closed_trades)
        self.activity_ledger.log_snapshot(
            mode=self.mode,
            total_equity=tot_eq,
            free_cash=self.portfolio.free_cash,
            slots_occupied=len(self.portfolio.active_positions) + len(self.portfolio.resting_orders),
            max_slots=self.portfolio.max_slots,
            active_positions=self.portfolio.active_positions,
            resting_orders=self.portfolio.resting_orders,
            total_realized_pnl=tot_pnl
        )

    def _print_portfolio_status(self) -> None:
        """Prints a comprehensive real-time portfolio dashboard in console."""
        tot_eq = self.portfolio.get_total_equity(self.current_ticker_prices)
        ret_pct = ((tot_eq - self.portfolio.initial_capital) / self.portfolio.initial_capital) * 100.0

        print("\n" + "-" * 95)
        print(f"📊 PORTFOLIO STATUS ({self.mode.upper()}) | Total Equity: ${tot_eq:,.2f} ({ret_pct:+.2f}%) | Free Cash: ${self.portfolio.free_cash:,.2f}")
        print(f"Slots Occupied: {len(self.portfolio.active_positions) + len(self.portfolio.resting_orders)} / {self.portfolio.max_slots} | Closed Trades: {len(self.portfolio.closed_trades)}")
        print("-" * 95)

        if self.portfolio.resting_orders:
            print("⏳ RESTING BUY ORDERS (Waiting to fill at -0.50% discount):")
            for sym, o in self.portfolio.resting_orders.items():
                cur_p = self.current_ticker_prices.get(o.binance_symbol, 0.0)
                print(f"  • {sym:<8} -> {o.binance_symbol:<8} | Limit: ${o.limit_buy_price:,.2f} | Cur: ${cur_p:,.2f} | Alloc: ${o.allocated_capital:,.2f} | Waiting: {o.bars_waiting}/{self.portfolio.order_ttl_bars} bar(s)")

        if self.portfolio.active_positions:
            print("📈 ACTIVE POSITIONS (Holding inventory with pre-placed TP limits):")
            for sym, p in self.portfolio.active_positions.items():
                cur_p = self.current_ticker_prices.get(p.binance_symbol, p.entry_price)
                unrealized = ((cur_p - p.entry_price) / p.entry_price) * 100.0
                print(f"  • {sym:<8} -> {p.binance_symbol:<8} | Entry: ${p.entry_price:,.2f} | Cur: ${cur_p:,.2f} ({unrealized:+.2f}%) | TP: ${p.limit_sell_price:,.2f} | Stop: ${p.stop_loss_price:,.2f}")

        print("-" * 95 + "\n")


def main():
    parser = argparse.ArgumentParser(description="24/7 Crypto Live Strategy Runner (Policy 4 RVOL)")
    parser.add_argument("--mode", type=str, default="paper", choices=["paper", "live"], help="Execution mode (paper or live)")
    parser.add_argument("--initial-capital", type=float, default=None, help="Initial portfolio capital ($ USD). In live mode, automatically fetches live USD balance from Binance.US if omitted.")
    parser.add_argument("--slots", type=int, default=2, help="Concurrency slots (default: 2)")
    parser.add_argument("--discount", type=float, default=0.50, help="Maker limit buy discount %% (default: 0.50)")
    parser.add_argument("--premium", type=float, default=0.30, help="Maker limit TP sell premium %% (default: 0.30)")
    parser.add_argument("--ttl", type=int, default=1, help="Order TTL in 15m bars (default: 1)")
    args = parser.parse_args()

    runner = CryptoLiveStrategyRunner(
        mode=args.mode,
        initial_capital=args.initial_capital,
        max_slots=args.slots,
        discount_pct=args.discount,
        sell_premium_pct=args.premium,
        order_ttl_bars=args.ttl
    )
    runner.run()


if __name__ == "__main__":
    main()
