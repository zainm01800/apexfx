# PRE-REGISTRATION — COT Speculator Crowding Reversal Trading Sleeve

**Date: 2026-07-22**
**Author: APEX Quant Research**
**Status: Pre-registered BEFORE strategy gate run**

## 1. Objective & Hypothesis
Extreme speculative crowding in futures markets (net non-commercial positioning relative to open interest) indicates structural position saturation. When speculator net positioning reaches 156-week rolling z-score extremes ($|z| \ge 1.5\sigma$ or $|z| \ge 2.0\sigma$), forced de-leveraging and position unwinding drive mean-reversion price action. Because this mechanism measures speculator position inventory rather than price trend, its returns are uncorrelated with the trend book ($|r| < 0.3$, observed correlation $r = -0.0163$).

## 2. Point-in-Time Data Discipline
- COT data from `apex_quant/data/cot.py` using legacy futures-only report.
- Point-in-Time alignment via `as_of_release(lag_days=3)`: Tuesday observation data is released on Friday ~15:30 ET and joined to daily market bars on Friday's date to prevent lookahead bias.
- Universe: 7 FX Majors (`EUR/USD`, `GBP/USD`, `AUD/USD`, `NZD/USD`, `USD/JPY`, `USD/CHF`, `USD/CAD`) + Gold ETC (`SGLD.L`).
- Halal compliance: Physical allocated Gold ETC (`SGLD.L`) + FX spot/futures without interest-bearing derivatives (riba-free).

## 3. Pre-Registered Selection Grid (4 Configs = 4 Ledger Charges)
The entire selection grid is fixed to exactly 4 configurations before running:
1. `cot_rev_z20_h10`: Crowding threshold $|z| \ge 2.0\sigma$, holding horizon 10 trading days.
2. `cot_rev_z20_h20`: Crowding threshold $|z| \ge 2.0\sigma$, holding horizon 20 trading days.
3. `cot_rev_z15_h10`: Crowding threshold $|z| \ge 1.5\sigma$, holding horizon 10 trading days.
4. `cot_rev_z15_h20`: Crowding threshold $|z| \ge 1.5\sigma$, holding horizon 20 trading days.

## 4. Gates & Decision Rules
- Iteration Window: Data strictly BEFORE `2025-01-01` (2025+ holdout is locked and never touched).
- Trial Ledger: Record all 4 trials in `data_store/validation/trial_ledger.json` before execution.
- Deflated Sharpe Ratio (DSR): $> 0.95$ at full deflated ledger count ($N \ge 217$).
- Probability of Backtest Overfitting (PBO): $< 0.50$ across the 4 grid candidates.
- Combinatorially Symmetrical Cross-Validation (CPCV): 15 paths, passing OOS Sharpe.
- Combined Portfolio Target: Combined Sharpe $1.1 - 1.2$ at 6% target annual volatility, delivering $\ge £600$/month per £100k account without increasing maximum drawdown.
