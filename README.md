# Salvaged Crypto Quantitative Trading Engine

A clean, production-grade modular quantitative trading and backtesting system salvaged from legacy algorithmic trading research.

## Features
- **Signed Volume Accumulation:** Vectorized directional volume pressure tracking.
- **Micro-Regime Segmentation:** Continuous candle stream slicing between EMA crossovers with stack position bitmasking.
- **RSI Polynomial Slope Modifier:** Dynamic volatility entry thresholding using 1st-derivative OLS slope.
- **Linear Regression Channels (`LRA`):** Statistical rolling trendline residual bounds.
- **Deterministic 4-State Machine:** Self-healing position state management synchronized directly with Binance spot API order queries.
- **Realistic Limit Fill Simulation Engine:** Backtesting engine simulating high/low order queue fills, expiration timers, and fee friction.

## Quick Start
1. Copy `config.example.py` to `config.py` and add your Binance API keys.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run unit tests:
   ```bash
   python -m unittest discover tests
   ```
4. Run backtesting demo:
   ```bash
   python main_backtest.py
   ```
