import os
import sys
import logging
import sqlite3
import pandas as pd
import asyncio
from datetime import datetime, timezone
from binance import AsyncClient
try:
    from tqdm import tqdm
except ImportError:
    class DummyPbar:
        def __init__(self, *args, **kwargs): pass
        def update(self, *args, **kwargs): pass
        def close(self): pass
    def tqdm(iterable=None, *args, **kwargs):
        if iterable is not None:
            return iterable
        return DummyPbar()
from pathlib import Path

# --- Config Handling ---
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

try:
    import config
except ImportError:
    logging.warning("config.py not found. API calls will fail.")
    class ConfigPlaceholder:
        BASE_DIR = project_root
        BINANCE_API_KEY = ''
        BINANCE_API_SECRET = ''
        DATABASES_DIR = os.path.join(project_root, 'databases')
        MarketDataConfig = {}
    config = ConfigPlaceholder()

class BinanceData:
    def __init__(self, symbol: str, timeframes: list, exchange: str, start_date: str = '2020-01-01', update_on_init: bool = True):
        self.symbol = symbol  # Config Key (e.g., 'BTCUSDT_com' or 'BTCUSDT')
        self.timeframes = timeframes
        self.start_date_str = start_date
        self.exchange = exchange

        # 1. API Symbol & TLD Setup
        # We must strip '_com' for the API call, but keep it for DB lookup
        if self.exchange == 'binance_com':
            self.binance_tld = 'com'
            self.api_symbol = self.symbol.replace('_com', '').upper()
        elif self.exchange == 'binance_us':
            self.binance_tld = 'us'
            self.api_symbol = self.symbol.upper()
        else:
            raise ValueError(f"Unsupported exchange: {self.exchange}. Use 'binance_com' or 'binance_us'.")

        # 2. Path Resolution via MarketDataConfig
        try:
            self.db_path = config.MarketDataConfig[self.exchange][self.symbol]
        except (AttributeError, KeyError):
            logging.warning(f"Path not found in MarketDataConfig for {self.exchange}:{self.symbol}. Using fallback.")
            fallback_dir = os.path.join(config.DATABASES_DIR, self.exchange)
            os.makedirs(fallback_dir, exist_ok=True)
            self.db_path = os.path.join(fallback_dir, f'{self.symbol}.sqlite')
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        self._setup_logging()

        if update_on_init:
            self.update()

    def _setup_logging(self):
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s',
                handlers=[logging.StreamHandler(sys.stdout)]
            )

    def _get_tf_delta(self, timeframe: str) -> pd.Timedelta:
        if 'm' in timeframe: return pd.Timedelta(minutes=int(timeframe[:-1]))
        if 'h' in timeframe: return pd.Timedelta(hours=int(timeframe[:-1]))
        if 'd' in timeframe: return pd.Timedelta(days=int(timeframe[:-1]))
        raise ValueError(f"Unknown timeframe format: {timeframe}")

    def get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def update(self):
        logging.info(f"--- Initializing Data Sync for {self.exchange}:{self.symbol} ---")
        logging.info(f"    API Symbol: {self.api_symbol} | DB: {self.db_path}")
        try:
            asyncio.run(self._update_async())
        except Exception as e:
            logging.error(f"Update failed: {e}")
        logging.info(f"--- Data Sync Complete for {self.exchange}:{self.symbol} ---")

    async def _update_async(self):
        client = None
        try:
            client = await AsyncClient.create(
                config.BINANCE_API_KEY, 
                config.BINANCE_API_SECRET, 
                tld=self.binance_tld,
                requests_params={'timeout': 60} 
            )
            for tf in self.timeframes:
                await self._sync_timeframe(client, tf)
        except Exception as e:
            logging.error(f"Async client error: {e}")
        finally:
            if client:
                await client.close_connection()

    async def _sync_timeframe(self, client, timeframe):
        table_name = f"klines_{timeframe}"
        last_ts = self._get_last_timestamp(table_name)
        now_utc = pd.Timestamp.now(timezone.utc)

        if last_ts:
            start_dt = last_ts + pd.Timedelta(milliseconds=1)
            logging.info(f"[{self.exchange}:{self.symbol} {timeframe}] Resuming from {start_dt}...")
        else:
            start_dt = pd.to_datetime(self.start_date_str, utc=True)
            logging.info(f"[{self.exchange}:{self.symbol} {timeframe}] Initializing from {start_dt}...")

        tf_delta = self._get_tf_delta(timeframe)
        if start_dt >= (now_utc - tf_delta):
            logging.info(f"[{self.exchange}:{self.symbol} {timeframe}] Up to date.")
            return

        # FETCH
        new_data = await self._fetch_historical_klines(client, timeframe, start_dt, now_utc)

        # SAVE
        if not new_data.empty:
            self._save_to_db(new_data, table_name)

    def _get_last_timestamp(self, table_name):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
                if not cursor.fetchone(): return None
                
                # We store time as REAL/INTEGER (Unix Seconds)
                cursor.execute(f"SELECT MAX(time) FROM {table_name}")
                result = cursor.fetchone()
                if result and result[0]:
                    return pd.to_datetime(result[0], unit='s', utc=True)
        except Exception as e:
            logging.error(f"Error checking last timestamp: {e}")
        return None

    def _save_to_db(self, df, table_name):
        try:
            df_to_save = df.copy()
            df_to_save.index.name = 'time'

            # CONVERT TO UNIX SECONDS (Integer)
            # This is critical for compatibility with your training pipeline
            if pd.api.types.is_datetime64_any_dtype(df_to_save.index):
                df_to_save.index = df_to_save.index.astype('int64') // 10**9

            with self.get_connection() as conn:
                df_to_save.to_sql(table_name, conn, if_exists='append', index=True)
            
            logging.info(f"Appended {len(df)} records to {table_name}.")
        except Exception as e:
            logging.error(f"Failed to save to DB: {e}")

    async def _fetch_historical_klines(self, client, timeframe, start_dt, end_dt):
        all_klines_data = []
        fetch_start = start_dt
        
        tf_delta = self._get_tf_delta(timeframe)
        total_duration = (end_dt - start_dt)
        pbar_desc = f"Fetching {self.api_symbol} {timeframe}"
        pbar = tqdm(total=int(total_duration.total_seconds()), desc=pbar_desc, unit='sec', leave=False)

        while fetch_start < end_dt:
            retries = 3
            success = False
            
            while retries > 0 and not success:
                try:
                    # Use self.api_symbol (e.g. 'BTCUSDT') not self.symbol (e.g. 'BTCUSDT_com')
                    klines = await client.get_historical_klines(
                        self.api_symbol,
                        timeframe,
                        start_str=fetch_start.isoformat(),
                        end_str=end_dt.isoformat(),
                        limit=1000 
                    )
                    
                    if not klines:
                        success = True
                        fetch_start = end_dt + pd.Timedelta(seconds=1)
                        break

                    all_klines_data.extend(klines)

                    last_close_time_ms = int(klines[-1][6])
                    new_fetch_start = pd.to_datetime(last_close_time_ms + 1, unit='ms', utc=True)

                    delta_seconds = (new_fetch_start - fetch_start).total_seconds()
                    pbar.update(int(delta_seconds))

                    fetch_start = new_fetch_start
                    success = True

                    await asyncio.sleep(0.3) 

                except (BinanceAPIException, BinanceRequestException) as e:
                    retries -= 1
                    wait_time = 5 * (4 - retries)
                    logging.warning(f"API Error: {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                
                except Exception as e:
                    logging.error(f"Unexpected error: {e}")
                    retries = 0 
            
            if not success and retries == 0:
                break

        pbar.close()

        if not all_klines_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_klines_data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'quote_asset_volume', 'number_of_trades', 
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        
        # Parse Dates to Timestamp Objects
        df['time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
        df = df.drop_duplicates(subset=['time'])
        df.set_index('time', inplace=True)
        
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'trade_count']
        df.rename(columns={'number_of_trades': 'trade_count'}, inplace=True)
        
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        cutoff = pd.Timestamp.now(timezone.utc) - tf_delta
        df = df[df.index < cutoff]

        return df[['open', 'high', 'low', 'close', 'volume', 'trade_count']].sort_index()