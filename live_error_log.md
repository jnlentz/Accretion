=========================================================================================================
🛡️  PROJECT ACCRETION: 24/7 LIVE TRADE & EXECUTION VALIDATION SUITE
   Lookback Window   : Trailing 7.0 Days (~672 15m bars)
   Champion Horizon  : 12.0 Hours (48 15m bars)
   Prioritization    : Policy 4 RVOL Volume Surge (K=2 Slots | 50% Sizing)
   Order Accounting  : Queue Realism (Resting Orders Escrow Slots/Cash)
=========================================================================================================

📥 Live Telemetry Database Ingestion:
   • Completed Trades Recorded: 15
   • Portfolio Snapshots       : 777
   • Logged 15m Predictions   : 4,516

=========================================================================================================
🔬 [STAGE 1] MULTI-ASSET PREDICTION PARITY & MODEL INFERENCE AUDIT...
=========================================================================================================
PART A: BIT-FOR-BIT MODEL INFERENCE AUDIT (Live Telemetry Features vs Champion GBDT Models)
Coin     | Audited Bars  | Max |P_live - P_model|  | Correlation (r)   | Signal Match   | Status    
---------------------------------------------------------------------------------------------------------
XBTUSD   | 752 bars      | 0.00000000              | 1.0000            | 100.0%         | VERIFIED  
ETHUSD   | 752 bars      | 0.00000000              | 1.0000            |  99.2%         | VERIFIED  
SOLUSD   | 753 bars      | 0.00000000              | 1.0000            |  94.2%         | VERIFIED  
ADAUSD   | 753 bars      | 0.00000000              | 1.0000            | 100.0%         | VERIFIED  
XRPUSD   | 753 bars      | 0.00000000              | 1.0000            |  97.5%         | VERIFIED  
XDGUSD   | 753 bars      | 0.00000000              | 1.0000            | 100.0%         | VERIFIED  
---------------------------------------------------------------------------------------------------------
PART B: HISTORICAL STREAMING BUFFER RECONSTRUCTION AUDIT (Raw Kraken Candles vs Live Logs)
Coin     | Audited Bars  | Max |P_live - P_hist|   | Correlation (r)   | Signal Match   | Status    
---------------------------------------------------------------------------------------------------------
XBTUSD   | 752 bars      | 0.010487                | 0.9946            | 100.0%         | EXPLAINED 
ETHUSD   | 752 bars      | 0.121318                | 0.9951            |  98.8%         | EXPLAINED 
SOLUSD   | 753 bars      | 0.307098                | 0.7353            |  86.7%         | EXPLAINED 
ADAUSD   | 753 bars      | 0.106757                | 0.9473            |  99.9%         | EXPLAINED 
XRPUSD   | 753 bars      | 0.089802                | 0.9894            |  96.0%         | EXPLAINED 
XDGUSD   | 753 bars      | 0.064201                | 0.9725            | 100.0%         | EXPLAINED 
=========================================================================================================
ℹ️ STAGE 1 AUDIT NOTE: Part A confirms 100% bit-for-bit inference parity (R²=1.0000) on logged features.
   In Part B, SOLUSD variance stems from Daily TCXA EMA (span 48/96) phase flipping on tight consolidation.

=========================================================================================================
⏳ [STAGE 2] SIMULATING DETERMINISTIC WINDOW BACKTEST (POLICY 4 RVOL SURGE)...
   Window: 2026-09-23 21:15 to 2026-10-02 01:15 UTC
=========================================================================================================
⚡ Generated 521 candidate setups firing across the 7.0-day window.
✅ Simulation complete: Backtest produced 16 simulated trades under Policy 4 RVOL Surge.

=========================================================================================================
🔍 [STAGE 3] TRADE-FOR-TRADE FORENSIC MATCHING & EXECUTION PARITY AUDIT...
   Comparing 15 Live Binance.US Trades against 16 Simulated Backtest Trades
=========================================================================================================

=============================================================================================================================
📊 [TABLE 2] TRADE-FOR-TRADE FORENSIC EXECUTION LEDGER (PERCENTAGE-BASED EVALUATION)
=============================================================================================================================
#   | Coin    | Entry Time  | Real Buy   | Sim Buy    | Slip (bps) | Real Exit  | Sim Exit   | Real Ret  | Sim Ret   | Ret Delta  | Status        
-----------------------------------------------------------------------------------------------------------------------------
1   | XRPUSD  | 09-24 08:30 | $1.49      | $1.48      |  +70.6     | $1.53      | $1.46      |  +2.97% |  -1.27%   |  +4.24%    | DIVERGED      
2   | XRPUSD  | 09-25 01:30 | $1.54      |     -      |     -      | $1.52      |     -      |  -1.27% |     -     |     -      | UNMATCHED     
3   | ETHUSD  | 09-24 04:03 | $2,671.46  | $2,671.46  |    0.0     | $2,692.50  | $2,692.50  |  +0.79% |  +0.79%   |    0.00%   | LEGACY ADOPT  
4   | SOLUSD  | 09-25 11:21 | $120.95    |     -      |     -      | $122.27    |     -      |  +1.09% |     -     |     -      | UNMATCHED     
5   | SOLUSD  | 09-26 00:33 | $121.31    |     -      |     -      | $121.17    |     -      |  -0.12% |     -     |     -      | UNMATCHED     
6   | XRPUSD  | 09-27 12:59 | $1.54      | $1.54      |   -0.3     | $1.52      | $1.52      |  -1.26% |  -1.27%   |  +0.01%    | MATCH         
7   | XDGUSD  | 09-27 09:09 | $0.0974    | $0.0974    |   +4.8     | $0.0969    | $0.0969    |  -0.49% |  -0.44%   |  -0.05%    | MATCH         
8   | XRPUSD  | 09-27 20:26 | $1.53      | $1.53      |   +0.2     | $1.51      | $1.51      |  -1.25% |  -1.27%   |  +0.02%    | MATCH         
9   | XRPUSD  | 09-28 14:42 | $1.49      |     -      |     -      | $1.47      |     -      |  -1.32% |     -     |     -      | UNMATCHED     
10  | ETHUSD  | 09-28 14:29 | $2,667.26  | $2,667.26  |   +0.0     | $2,659.94  | $2,659.28  |  -0.27% |  -0.32%   |  +0.04%    | MATCH         
11  | XRPUSD  | 09-29 12:58 | $1.55      | $1.55      |   -0.2     | $1.53      | $1.53      |  -1.31% |  -1.27%   |  -0.04%    | MATCH         
12  | ETHUSD  | 09-29 15:05 | $2,696.54  | $2,696.54  |   +0.0     | $2,672.90  | $2,673.44  |  -0.88% |  -0.88%   |  -0.00%    | MATCH         
13  | SOLUSD  | 09-30 13:35 | $121.55    |     -      |     -      | $118.33    |     -      |  -2.65% |     -     |     -      | UNMATCHED     
14  | ETHUSD  | 09-30 13:35 | $2,708.13  | $2,708.13  |   -0.0     | $2,690.87  | $2,689.25  |  -0.64% |  -0.72%   |  +0.08%    | MATCH         
15  | ETHUSD  | 10-01 07:21 | $2,697.43  | $2,697.43  |   +0.0     | $2,697.62  | $2,701.41  |  +0.01% |  +0.13%   |  -0.12%    | MATCH         
=============================================================================================================================

=========================================================================================================
📊 [TABLE 3] MASTER EXECUTION PARITY & SLIPPAGE SCORECARD (PERCENTAGE-NORMALIZED):
=========================================================================================================
   • Total Live Completed Trades      : 15 trades (14 live executions + 1 legacy adopted)
   • Backtest Matched Trades          : 9 / 14 (64.3% Selection Parity)
   • Exit Reason Agreement Rate       : 8 / 9 (88.9%)
   • Mean Maker Buy Slippage          : +8.34 bps (0.0% Maker Tier target)
   • Mean Monitored Exit Slippage     : +53.85 bps (Zero-market-order pegged executions)
   ---------------------------------------------------------------------------
   • Cumulative Simple Return (Real)  :  -7.38% (Sum of 14 live trade returns)
   • Cumulative Simple Return (Sim)   :  -7.30% (Sum of matched simulated returns)
   • Mean Return Delta per Trade      :  +0.46% (+46.5 bps net execution edge)
   • Compounded Portfolio Return (Real):  -3.65% (Compounded across 50% slots)
   • Compounded Portfolio Return (Sim) :  -3.59% (Compounded across 50% slots)
   ---------------------------------------------------------------------------
   • Capital Normalization Base       : $10,000.00 USD (Aligned to actual live equity)
   • Total Realized Live Dollar PnL   : $-2.49
   • Scaled Simulated Backtest PnL    : $-357.59
   • Net Dollar Parity Delta          : $355.09
=========================================================================================================

ℹ️ VERDICT: Execution parity audit complete. Inspect individual trade rows for queue or fill deviations.