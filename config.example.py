from binance.enums import *
import os

# --- Exchange & Service API Credentials (Fill in your own) ---
BINANCE_API_KEY = 'YOUR_BINANCE_API_KEY'
BINANCE_API_SECRET = 'YOUR_BINANCE_API_SECRET'

KRAKEN_API_KEY = 'YOUR_KRAKEN_API_KEY'
KRAKEN_PRIVATE_KEY = 'YOUR_KRAKEN_PRIVATE_KEY'

X_CONSUMER_KEY = 'YOUR_X_CONSUMER_KEY'
X_CONSUMER_SECRET = 'YOUR_X_CONSUMER_SECRET'
X_AUTH_TOKEN = 'YOUR_X_AUTH_TOKEN'
X_AUTH_SECRET = 'YOUR_X_AUTH_SECRET'
X_CLIENT_ID = 'YOUR_X_CLIENT_ID'
X_CLIENT_SECRET = 'YOUR_X_CLIENT_SECRET'

GEMINI_API_KEY = 'YOUR_GEMINI_API_KEY'

POLYGON_API_KEY = 'YOUR_POLYGON_API_KEY'
POLYGON_S3_KEY_ID = 'YOUR_POLYGON_S3_KEY_ID'
POLYGON_S3_SECRET_KEY = 'YOUR_POLYGON_S3_SECRET_KEY'
POLYGON_S3_ENDPONT = 'https://files.polygon.io'
POLYGON_S3_BUCKET = 'flatfiles'

ALPACA_API_KEY = 'YOUR_ALPACA_API_KEY'
ALPACA_API_SECRET = 'YOUR_ALPACA_API_SECRET'

# --- Root Directory Definitions ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASES_DIR = os.path.join(BASE_DIR, 'databases')
MODELS_ROOT_DIR = os.path.join(BASE_DIR, 'models')
RESULTS_ROOT_DIR = os.path.join(BASE_DIR, 'results')
LOG_DIR = os.path.join(BASE_DIR, 'logs')
STATE_DIR = os.path.join(BASE_DIR, 'state')

# --- Market Data Configurations ---
MarketDataConfig = {
    'binance_com': {
        'ETHUSDT_com': os.path.join(DATABASES_DIR, 'binance_com', 'ETHUSDT_com.sqlite'),
        'BTCUSDT_com': os.path.join(DATABASES_DIR, 'binance_com', 'BTCUSDT_com.sqlite'),
        'ADAUSDT_com': os.path.join(DATABASES_DIR, 'binance_com', 'ADAUSDT_com.sqlite'),
        'SOLUSDT_com': os.path.join(DATABASES_DIR, 'binance_com', 'SOLUSDT_com.sqlite'),
        'BNBUSDT_com': os.path.join(DATABASES_DIR, 'binance_com', 'BNBUSDT_com.sqlite'),
        'DOGEUSDT_com': os.path.join(DATABASES_DIR, 'binance_com', 'DOGEUSDT_com.sqlite'),
        'XRPUSDT_com': os.path.join(DATABASES_DIR, 'binance_com', 'XRPUSDT_com.sqlite')
    },
    'binance_us': {
        'ETHUSDT': os.path.join(DATABASES_DIR, 'binance_us', 'ETHUSDT.sqlite'),
        'BTCUSDT': os.path.join(DATABASES_DIR, 'binance_us', 'BTCUSDT.sqlite'),
        'ADAUSDT': os.path.join(DATABASES_DIR, 'binance_us', 'ADAUSDT.sqlite'),
        'SOLUSDT': os.path.join(DATABASES_DIR, 'binance_us', 'SOLUSDT.sqlite'),
        'BNBUSDT': os.path.join(DATABASES_DIR, 'binance_us', 'BNBUSDT.sqlite'),
        'DOGEUSDT': os.path.join(DATABASES_DIR, 'binance_us', 'DOGEUSDT.sqlite'),
        'BTCUSDC': os.path.join(DATABASES_DIR, 'binance_us', 'BTCUSDC.sqlite'),
        'BTCUSD': os.path.join(DATABASES_DIR, 'binance_us', 'BTCUSD.sqlite'),
        'BTCUSD_synth': os.path.join(DATABASES_DIR, 'binance_us', 'BTCUSD_synth.sqlite'),
        'BNBUSD': os.path.join(DATABASES_DIR, 'binance_us', 'BNBUSD.sqlite'),
        'ETHUSD': os.path.join(DATABASES_DIR, 'binance_us', 'ETHUSD.sqlite'),
        'SOLUSD': os.path.join(DATABASES_DIR, 'binance_us', 'SOLUSD.sqlite'),
        'ADAUSD': os.path.join(DATABASES_DIR, 'binance_us', 'ADAUSD.sqlite')
    },
    'kraken': {
        'XBTUSD': os.path.join(DATABASES_DIR, 'kraken', 'XBTUSD.sqlite'),
        'ETHUSD': os.path.join(DATABASES_DIR, 'kraken', 'ETHUSD.sqlite'),
        'XDGUSD': os.path.join(DATABASES_DIR, 'kraken', 'XDGUSD.sqlite'),
        'XRPUSD': os.path.join(DATABASES_DIR, 'kraken', 'XRPUSD.sqlite'),
        'ADAUSD': os.path.join(DATABASES_DIR, 'kraken', 'ADAUSD.sqlite'),
        'BNBUSD': os.path.join(DATABASES_DIR, 'kraken', 'BNBUSD.sqlite'),
    },
    'polygon': {
        'TSLA': os.path.join(DATABASES_DIR, 'polygon', 'TSLA.sqlite'),
        'SPY': os.path.join(DATABASES_DIR, 'polygon', 'SPY.sqlite'),
        'NVDA': os.path.join(DATABASES_DIR, 'polygon', 'NVDA.sqlite'),
        'QQQ': os.path.join(DATABASES_DIR, 'polygon', 'QQQ.sqlite'),
        'DIA': os.path.join(DATABASES_DIR, 'polygon', 'DIA.sqlite'),
    },
    'synth': {
        'TSLA_synth': os.path.join(DATABASES_DIR, 'synth', 'TSLA_synth.sqlite'),
        'SPY_synth': os.path.join(DATABASES_DIR, 'synth', 'SPY_synth.sqlite'),
        'NVDA_synth': os.path.join(DATABASES_DIR, 'synth', 'NVDA_synth.sqlite'),
        'DIA_synth': os.path.join(DATABASES_DIR, 'synth', 'DIA_synth.sqlite'),
        'QQQ_synth': os.path.join(DATABASES_DIR, 'synth', 'QQQ_synth.sqlite'),
    }
}

# --- Market Database Paths ---
MarketDatabases = {
    'ETHUSDT': os.path.join(DATABASES_DIR, 'ETHUSDT.sqlite'),
    'BTCUSDC': os.path.join(DATABASES_DIR, 'BTCUSDC.sqlite'),
    'BTCUSDC2': os.path.join(DATABASES_DIR, 'BTCUSDC2.sqlite'),
    'BTCUSDT': os.path.join(DATABASES_DIR, 'BTCUSDT.sqlite'),
    'BTCUSDT_com': os.path.join(DATABASES_DIR, 'BTCUSDT_com.sqlite'),
    'BTCUSD_us': os.path.join(DATABASES_DIR, 'BTCUSD_us.sqlite'),
    'XRPUSDT': os.path.join(DATABASES_DIR, 'XRPUSDT_com.sqlite'),
    'XRPUSD': os.path.join(DATABASES_DIR, 'XRPUSD.sqlite'),
    'ADAUSDT': os.path.join(DATABASES_DIR, 'ADAUSDT.sqlite'),
    'ADAUSDT_com': os.path.join(DATABASES_DIR, 'ADAUSDT_com.sqlite'),
    'ADAUSD_us': os.path.join(DATABASES_DIR, 'ADAUSD_us.sqlite'),
    'DOGEUSDT': os.path.join(DATABASES_DIR, 'DOGEUSDT_com.sqlite'),
    'SOLUSDT': os.path.join(DATABASES_DIR, 'SOLUSDT.sqlite'),
    'LTCUSDT': os.path.join(DATABASES_DIR, 'LTCUSDT.sqlite'),
    'XBTUSD': os.path.join(DATABASES_DIR, 'XBTUSD.sqlite'),
    'ETHUSD': os.path.join(DATABASES_DIR, 'ETHUSD.sqlite'),
    'XDGUSD': os.path.join(DATABASES_DIR, 'XDGUSD.sqlite'),
}

PolygonDatabases = {
    'SPY': os.path.join(DATABASES_DIR, 'polygon', 'SPY.sqlite'),
    'QQQ': os.path.join(DATABASES_DIR, 'polygon', 'QQQ.sqlite'),
    'DIA': os.path.join(DATABASES_DIR, 'polygon', 'DIA.sqlite'),
    'TSLA': os.path.join(DATABASES_DIR, 'polygon', 'TSLA.sqlite'),
    'NVDA': os.path.join(DATABASES_DIR, 'polygon', 'NVDA.sqlite'),
    'AAPL': os.path.join(DATABASES_DIR, 'polygon', 'AAPL.sqlite'),
    'AMZN': os.path.join(DATABASES_DIR, 'polygon', 'AMZN.sqlite'),
}

# --- Kline Interval Mapping ---
KLINE_INTERVAL_MAP = {
    KLINE_INTERVAL_1MINUTE: {'minutes': 1, 'table': '1m'},
    KLINE_INTERVAL_3MINUTE: {'minutes': 3, 'table': '3m'},
    KLINE_INTERVAL_5MINUTE: {'minutes': 5, 'table': '5m'},
    KLINE_INTERVAL_15MINUTE: {'minutes': 15, 'table': '15m'},
    KLINE_INTERVAL_1HOUR: {'minutes': 60, 'table': '1h'},
    KLINE_INTERVAL_4HOUR: {'minutes': 240, 'table': '4h'},
    KLINE_INTERVAL_1DAY: {'minutes': 1440, 'table': '1d'}
}
