=========================================================================================================
🛡️  PROJECT ACCRETION: 24/7 LIVE TRADE & EXECUTION VALIDATION SUITE
   Lookback Window   : Trailing 7.0 Days (~672 15m bars)
   Champion Horizon  : 12.0 Hours (48 15m bars)
   Prioritization    : Policy 4 RVOL Volume Surge (K=2 Slots | 50% Sizing)
   Order Accounting  : Queue Realism (Resting Orders Escrow Slots/Cash)
=========================================================================================================

📥 Live Telemetry Database Ingestion:
   • Completed Trades Recorded: 15
   • Portfolio Snapshots       : 772
   • Logged 15m Predictions   : 4,486

=========================================================================================================
🔬 [STAGE 1] MULTI-ASSET PREDICTION PARITY & FEATURE INTEGRITY VERIFICATION...
=========================================================================================================
Coin     | Audited Bars  | Max |P_live - P_hist|   | Correlation (r)   | Signal Match   | Status    
---------------------------------------------------------------------------------------------------------
XBTUSD   | 747 bars      | 0.131941                | 0.1931            |  99.9%         | DRIFT     
ETHUSD   | 747 bars      | 0.296980                | 0.0083            |  44.0%         | DRIFT     
SOLUSD   | 748 bars      | 0.376817                | 0.4310            |  86.0%         | DRIFT     
ADAUSD   | 748 bars      | 0.163017                | 0.8273            |  99.6%         | DRIFT     
XRPUSD   | 748 bars      | 0.312964                | 0.5044            |  86.4%         | DRIFT     
XDGUSD   | 748 bars      | 0.315265                | 0.2029            |  99.3%         | DRIFT     
=========================================================================================================
⚠️ STAGE 1 NOTE: Minor feature/warmup drift detected on tail bars. Proceeding with trade audit.

=========================================================================================================
⏳ [STAGE 2] SIMULATING DETERMINISTIC WINDOW BACKTEST (POLICY 4 RVOL SURGE)...
   Window: 2026-09-23 08:30 to 2026-10-02 01:15 UTC
=========================================================================================================
⚡ Generated 4 candidate setups firing across the 7.0-day window.
✅ Simulation complete: Backtest produced 0 simulated trades under Policy 4 RVOL Surge.

=========================================================================================================
🔍 [STAGE 3] TRADE-FOR-TRADE FORENSIC MATCHING & EXECUTION PARITY AUDIT...
   Comparing 15 Live Binance.US Trades against 0 Simulated Backtest Trades
=========================================================================================================
#   | Coin    | Entry Time  | Real Buy   | Sim Buy    | Slip (bps) | Real Exit  | Sim Exit   | Real Ret  | Sim Ret   | Reason Match
-------------------------------------------------------------------------------------------------------------------
1   | XRPUSD  | 09-24 08:30 | $1.49      | $1.49      |   0.0      | $1.53      | $1.53      |  +2.97% |  +2.97% | DIVERGED    
2   | XRPUSD  | 09-25 01:30 | $1.54      | $1.54      |   0.0      | $1.52      | $1.52      |  -1.27% |  -1.27% | DIVERGED    
3   | ETHUSD  | 09-24 04:03 | $2,671.46  | $2,671.46  |   0.0      | $2,692.50  | $2,692.50  |  +0.79% |  +0.79% | DIVERGED    
4   | SOLUSD  | 09-25 11:21 | $120.95    | $120.95    |   0.0      | $122.27    | $122.27    |  +1.09% |  +1.09% | DIVERGED    
5   | SOLUSD  | 09-26 00:33 | $121.31    | $121.31    |   0.0      | $121.17    | $121.17    |  -0.12% |  -0.12% | DIVERGED    
6   | XRPUSD  | 09-27 12:59 | $1.54      | $1.54      |   0.0      | $1.52      | $1.52      |  -1.26% |  -1.26% | DIVERGED    
7   | XDGUSD  | 09-27 09:09 | $0.10      | $0.10      |   0.0      | $0.10      | $0.10      |  -0.49% |  -0.49% | DIVERGED    
8   | XRPUSD  | 09-27 20:26 | $1.53      | $1.53      |   0.0      | $1.51      | $1.51      |  -1.25% |  -1.25% | DIVERGED    
9   | XRPUSD  | 09-28 14:42 | $1.49      | $1.49      |   0.0      | $1.47      | $1.47      |  -1.32% |  -1.32% | DIVERGED    
10  | ETHUSD  | 09-28 14:29 | $2,667.26  | $2,667.26  |   0.0      | $2,659.94  | $2,659.94  |  -0.27% |  -0.27% | DIVERGED    
11  | XRPUSD  | 09-29 12:58 | $1.55      | $1.55      |   0.0      | $1.53      | $1.53      |  -1.31% |  -1.31% | DIVERGED    
12  | ETHUSD  | 09-29 15:05 | $2,696.54  | $2,696.54  |   0.0      | $2,672.90  | $2,672.90  |  -0.88% |  -0.88% | DIVERGED    
13  | SOLUSD  | 09-30 13:35 | $121.55    | $121.55    |   0.0      | $118.33    | $118.33    |  -2.65% |  -2.65% | DIVERGED    
14  | ETHUSD  | 09-30 13:35 | $2,708.13  | $2,708.13  |   0.0      | $2,690.87  | $2,690.87  |  -0.64% |  -0.64% | DIVERGED    
15  | ETHUSD  | 10-01 07:21 | $2,697.43  | $2,697.43  |   0.0      | $2,697.62  | $2,697.62  |  +0.01% |  +0.01% | DIVERGED    
===================================================================================================================

=========================================================================================================
📊 [TABLE 3] MASTER EXECUTION PARITY & SLIPPAGE SCORECARD:
=========================================================================================================
   • Total Live Completed Trades  : 15 trades (including 0 legacy adopted)
   • Backtest Matched Trades      : 0 / 15 (0.0% Selection Parity)
   • Exit Reason Agreement Rate   : 0 / 0 (100.0%)
   • Mean Maker Buy Slippage      : +0.00 bps (0.0% Maker Tier target)
   • Mean Monitored Exit Slippage : +0.00 bps (Zero-market-order pegged executions)
   • Total Realized Live PnL      : $-2.49
   • Total Simulated Backtest PnL : $-2.49
   • Net Execution Parity Delta   : $0.00
=========================================================================================================

ℹ️ VERDICT: Execution parity audit complete. Inspect individual trade rows for queue or fill deviations.

📊 Rendering 4-panel dark-mode validation dashboard via plt.show()...
Traceback (most recent call last):
  File "/home/singularity/dev/Accretion/scripts/validate_live_trades.py", line 1108, in <module>
    main()
  File "/home/singularity/dev/Accretion/scripts/validate_live_trades.py", line 1098, in main
    plot_validation_dashboard(
  File "/home/singularity/dev/Accretion/scripts/validate_live_trades.py", line 623, in plot_validation_dashboard
    ax1.plot(df_sim_s['timestamp'], df_sim_s['total_equity'], color='#00e5ff', lw=1.8, linestyle='--', label='Simulated Backtest Equity (Benchmark)')
  File "/home/singularity/dev/Accretion/venv/lib/python3.12/site-packages/matplotlib/axes/_axes.py", line 1792, in plot
    lines = [*self._get_lines(self, *args, data=data, **kwargs)]
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/singularity/dev/Accretion/venv/lib/python3.12/site-packages/matplotlib/axes/_base.py", line 331, in __call__
    yield from self._plot_args(
               ^^^^^^^^^^^^^^^^
  File "/home/singularity/dev/Accretion/venv/lib/python3.12/site-packages/matplotlib/axes/_base.py", line 504, in _plot_args
    axes.xaxis.update_units(x)
  File "/home/singularity/dev/Accretion/venv/lib/python3.12/site-packages/matplotlib/axis.py", line 1907, in update_units
    self._update_axisinfo()
  File "/home/singularity/dev/Accretion/venv/lib/python3.12/site-packages/matplotlib/axis.py", line 1919, in _update_axisinfo
    info = self._converter.axisinfo(self.units, self)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/singularity/dev/Accretion/venv/lib/python3.12/site-packages/matplotlib/dates.py", line 1832, in axisinfo
    return self._get_converter().axisinfo(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/singularity/dev/Accretion/venv/lib/python3.12/site-packages/matplotlib/dates.py", line 1749, in axisinfo
    majloc = AutoDateLocator(tz=tz,
             ^^^^^^^^^^^^^^^^^^^^^^
  File "/home/singularity/dev/Accretion/venv/lib/python3.12/site-packages/matplotlib/dates.py", line 1281, in __init__
    super().__init__(tz=tz)
  File "/home/singularity/dev/Accretion/venv/lib/python3.12/site-packages/matplotlib/dates.py", line 1080, in __init__
    self.tz = _get_tzinfo(tz)
              ^^^^^^^^^^^^^^^
  File "/home/singularity/dev/Accretion/venv/lib/python3.12/site-packages/matplotlib/dates.py", line 224, in _get_tzinfo
    raise TypeError(f"tz must be string or tzinfo subclass, not {tz!r}.")
TypeError: tz must be string or tzinfo subclass, not <matplotlib.category.UnitData object at 0x7c1098bd1970>.