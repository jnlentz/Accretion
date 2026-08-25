import sys
import logging
from pathlib import Path

# --- Path Setup ---
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

try:
    from src.adapters.BinanceData import BinanceData
    import config
except ImportError as e:
    print(f"CRITICAL ERROR: Could not import required modules. {e}")
    print("Ensure 'src/adapters/BinanceData.py' and 'config.py' exist.")
    sys.exit(1)

# ==============================================================================
# 📜 CONFIGURATION
# ==============================================================================

# 1. Select Exchange: 'binance_us' or 'binance' (Global)
EXCHANGE = 'binance_com' 

# 2. List of Symbols to Process
SYMBOLS = [
    'ETHUSDT' ,'BTCUSDT','ADAUSDT','SOLUSDT','BNBUSDT','DOGEUSDT' # ,'ETHUSDT' ,'BTCUSDT','ADAUSDT','SOLUSDT','BNBUSDT','DOGEUSDT','SOLUSD', 'BNBUSD','BTCUSD', 'ETHUSD', 'ADAUSD','XRPUSD'
]

# 3. Timeframes to Download
# Note: Unlike Kraken, Binance downloads these directly. 
# Ensure these match what your strategy needs.
TIMEFRAMES = ['1m', '3m', '15m', '1h', '4h', '6h', '1d']

# 4. Fallback Start Date
# Only used if the database tables are completely empty.
START_DATE = '2018-01-01' 

# ==============================================================================

def main():
    # Setup logging
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logging.info("==========================================")
    logging.info(f"   BINANCE UPDATER ({EXCHANGE})")
    logging.info("==========================================")
    logging.info(f"Timeframes: {TIMEFRAMES}")
    
    total = len(SYMBOLS)

    for i, symbol in enumerate(SYMBOLS, 1):
        logging.info(f"\n[{i}/{total}] Processing: {EXCHANGE}:{symbol}...")

        try:
            # Instantiate BinanceData
            # update_on_init=True triggers the download/update immediately
            market_data = BinanceData(
                symbol=symbol,
                exchange=EXCHANGE,
                timeframes=TIMEFRAMES,
                start_date=START_DATE,
                update_on_init=True
            )
            
            logging.info(f"✅ Success: {symbol}")
            logging.info(f"   DB Path: {market_data.db_path}")
            
        except Exception as e:
            # Log the error but continue to the next symbol
            logging.critical(f"❌ Failed: {symbol} - {e}", exc_info=True)
            continue

    logging.info("\n--- All requested symbols processed ---")

if __name__ == "__main__":
    main()