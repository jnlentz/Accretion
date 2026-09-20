"""
====================================================================================================
PROJECT ACCRETION: LIVE 24/7 INFERENCE FEED & PREDICTION LOGGER
====================================================================================================
Purpose:
  Runs streaming 15-minute feature extraction and GBDT model inference for live validation.
  1. Bridges historical SQLite data gap to the current moment via Kraken public REST API.
  2. Warms up CryptoLiveFeatureEngine with trailing 672+ bars (7 days of 15m).
  3. Evaluates GBDT champion models using calibrated cutoff thresholds from {symbol}_meta.json.
  4. Records full feature vectors and predictions to logs/live_predictions_{symbol}.jsonl.
  5. Feeds directly into crypto_live_prediction_validation_lab.py for offline parity verification.

Usage:
  python scripts/run_live_inference_feed.py --symbol XBTUSD
  python scripts/run_live_inference_feed.py --symbol XBTUSD --run-once
====================================================================================================
"""

import sys
import os
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.kraken_data import KrakenData, INTERVAL_SECONDS_15M
from research_import.crypto_live_engine import (
    CryptoLiveFeatureEngine,
    CryptoLiveInferenceEngine,
    LivePredictionLogger,
    WARMUP_BARS_MIN,
    CRYPTO_CHAMPIONS
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("accretion.live_feed")


def sleep_until_next_15m_boundary(buffer_seconds: int = 5) -> None:
    """
    Calculates time remaining until the next 15-minute candle close (:00, :15, :30, :45)
    and sleeps until that moment + buffer_seconds for exchange bar publication.
    """
    now = datetime.now(timezone.utc)
    current_minute = now.minute
    current_second = now.second
    current_microsecond = now.microsecond

    # Next 15m boundary
    minutes_to_next = 15 - (current_minute % 15)
    seconds_to_next = (minutes_to_next * 60) - current_second - (current_microsecond / 1_000_000.0)

    target_sleep = seconds_to_next + buffer_seconds
    next_bar_time = now + timedelta(seconds=target_sleep)

    logger.info(f"Sleeping {target_sleep:.1f}s until next 15m close at {next_bar_time.strftime('%H:%M:%S')} UTC...")
    time.sleep(max(1.0, target_sleep))


def run_inference_feed(
    symbol: str = "XBTUSD",
    models_dir: Optional[Path] = None,
    log_dir: Optional[Path] = None,
    db_dir: Optional[Path] = None,
    run_once: bool = False
) -> None:
    symbol = symbol.upper()
    if symbol in ("BTCUSD", "BTCUSDT"):
        symbol = "XBTUSD"
    elif symbol in ("DOGEUSD", "DOGEUSDT"):
        symbol = "XDGUSD"

    models_dir = models_dir or PROJECT_ROOT / "research_import" / "models"
    config_path = PROJECT_ROOT / "research_import" / "config" / "crypto_champions_config.json"
    log_dir = log_dir or PROJECT_ROOT / "logs"
    db_dir = db_dir or PROJECT_ROOT / "databases" / "kraken"

    log_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 80)
    print(f"🚀 STARTING LIVE STREAMING INFERENCE FEED: {symbol}")
    print("=" * 80)
    print(f"Symbol Target    : {symbol}")
    print(f"Models Directory : {models_dir}")
    print(f"Prediction Logs  : {log_dir / f'live_predictions_{symbol}.jsonl'}")
    print(f"Database Path    : {db_dir / f'{symbol}.sqlite'}")
    print(f"Mode             : {'RUN_ONCE (Single Bar Test)' if run_once else 'STREAMING (24/7 15m Polling)'}")
    print("=" * 80 + "\n")

    # 1. Initialize Kraken Data Adapter & Bridge Gap
    kraken_data = KrakenData(db_dir=str(db_dir))
    logger.info(f"Checking historical database status for {symbol}...")
    inserted_bars = kraken_data.bridge_gap(symbol)
    logger.info(f"Gap bridging complete. {inserted_bars} new bar(s) added.")

    # 2. Warm up Feature Engine with Historical SQLite Data
    logger.info(f"Loading {WARMUP_BARS_MIN} warmup bars from SQLite...")
    df_warmup = kraken_data.get_warmup_candles(symbol, limit=WARMUP_BARS_MIN)
    logger.info(f"Warmup data span: {df_warmup.index.min()} to {df_warmup.index.max()} ({len(df_warmup)} bars)")

    feature_engine = CryptoLiveFeatureEngine(symbol)
    feature_engine.warmup(df_warmup)
    logger.info(f"CryptoLiveFeatureEngine for {symbol} successfully warmed and primed.")

    # 3. Initialize Inference Engine & Load GBDT Model + Metadata
    inference_engine = CryptoLiveInferenceEngine(
        models_dir=models_dir,
        discount_pct=0.50,
        sell_premium_pct=0.15
    )
    # Ensure config from crypto_champions_config.json is loaded
    if config_path.exists():
        inference_engine.load_models_from_dir(models_dir, config_path=config_path)

    if symbol not in inference_engine.models:
        raise RuntimeError(f"Model for {symbol} could not be loaded from {models_dir}!")

    cutoff = inference_engine.cutoffs.get(symbol, 0.50)
    champ = inference_engine.champions.get(symbol, {})
    x_star = champ.get('x_star', 4.0)
    y_star = champ.get('y_star', 2.0)
    logger.info(f"Model {symbol} loaded | Cutoff P*: {cutoff:.6f} | Target +x*: +{x_star}% | Stop -y*: -{y_star}%")

    # 4. Initialize Prediction Logger
    pred_logger = LivePredictionLogger(log_dir)

    # 5. Single-bar evaluation if --run-once requested
    if run_once:
        logger.info("Executing --run-once check on the latest closed candle...")
        latest_row = df_warmup.iloc[-1]
        latest_dt = df_warmup.index[-1]
        feats = feature_engine._compute_latest_features()
        if feats:
            sig = inference_engine.evaluate(symbol, latest_dt, latest_row['close'], feats)
            if sig:
                pred_logger.log_prediction(sig)
                status = "🚨 BUY_SIGNAL" if sig.is_signal else "⚪ NO_SIGNAL"
                logger.info(f"[RUN_ONCE EVAL] {latest_dt} | Close: ${latest_row['close']:,.2f} | P: {sig.p_pred:.4f} (Cutoff: {sig.cutoff_threshold:.4f}) | {status}")
                if sig.is_signal:
                    logger.info(f"  -> Limit Buy: ${sig.limit_buy_price:,.2f} | Limit Sell: ${sig.limit_sell_price:,.2f} | Stop: ${sig.stop_loss_price:,.2f}")
        print("\n✅ Run-once evaluation complete. Logged to: " + str(log_dir / f"live_predictions_{symbol}.jsonl"))
        return

    # 6. Continuous 15-Minute Streaming Loop
    logger.info("Entering 24/7 continuous 15-minute streaming cycle. Press Ctrl+C to stop.")
    while True:
        try:
            # Sleep until the next 15m candle close + 5s latency buffer
            sleep_until_next_15m_boundary(buffer_seconds=5)

            # Poll for the newly completed 15m bar
            bar = kraken_data.poll_latest_closed_bar(symbol)
            if bar is None:
                # If exchange hasn't published yet, wait 3 seconds and retry once
                time.sleep(3.0)
                bar = kraken_data.poll_latest_closed_bar(symbol)

            if bar is None:
                logger.warning(f"No new closed bar received for {symbol}. Will check again next cycle.")
                continue

            # Update Feature Engine
            feats = feature_engine.on_new_bar(
                dt=bar['dt'],
                open_p=bar['open'],
                high_p=bar['high'],
                low_p=bar['low'],
                close_p=bar['close'],
                volume=bar['volume']
            )

            if feats is None:
                logger.warning(f"Insufficient buffer history for {symbol} to compute features.")
                continue

            # Run GBDT Inference
            sig = inference_engine.evaluate(symbol, bar['dt'], bar['close'], feats)
            if sig is not None:
                pred_logger.log_prediction(sig)

                signal_str = "🚨 BUY_SIGNAL" if sig.is_signal else "⚪ NO_SIGNAL"
                logger.info(
                    f"[{bar['dt'].strftime('%Y-%m-%d %H:%M:%S')} UTC] "
                    f"Close: ${bar['close']:,.2f} | P(Hit): {sig.p_pred:.4f} (Cutoff: {sig.cutoff_threshold:.4f}) | {signal_str}"
                )
                if sig.is_signal:
                    logger.info(
                        f"   -> Placed Limit Buy: ${sig.limit_buy_price:,.2f} (-0.50%) | "
                        f"TP Limit: ${sig.limit_sell_price:,.2f} (+{x_star + 0.15:.2f}%) | "
                        f"Stop: ${sig.stop_loss_price:,.2f} (-{y_star:.2f}%)"
                    )

        except KeyboardInterrupt:
            logger.info("Shutdown signal received. Exiting live streaming loop cleanly.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in live streaming cycle: {e}", exc_info=True)
            time.sleep(10.0)


def main():
    parser = argparse.ArgumentParser(description="Live 15m Streaming Inference Feed & Prediction Logger")
    parser.add_argument("--symbol", type=str, default="XBTUSD", help="Kraken symbol (default: XBTUSD)")
    parser.add_argument("--run-once", action="store_true", help="Run a single-bar evaluation test and exit")
    parser.add_argument("--models-dir", type=str, default=None, help="Path to models directory")
    parser.add_argument("--log-dir", type=str, default=None, help="Path to logs directory")
    parser.add_argument("--db-dir", type=str, default=None, help="Path to Kraken SQLite database directory")
    args = parser.parse_args()

    run_inference_feed(
        symbol=args.symbol,
        models_dir=Path(args.models_dir) if args.models_dir else None,
        log_dir=Path(args.log_dir) if args.log_dir else None,
        db_dir=Path(args.db_dir) if args.db_dir else None,
        run_once=args.run_once
    )


if __name__ == "__main__":
    main()
