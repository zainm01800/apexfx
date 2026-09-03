# Book R-252 stop-overlay audit

**Verdict: FAIL_DO_NOT_DEPLOY**

This is a causal retrospective test on a newly frozen 2026-09-03 OHLCV snapshot, not a true blind backtest. The running Book R forward-paper strategy was not changed.

## Segment results (5 bps/side)

| Segment | Variant | CAGR | Sharpe | Max DD | Total return | Worst day | Avg gross | Stops |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| research | Baseline | 12.42% | 0.756 | 23.63% | 126.65% | -6.54% | 78.22% | 0 |
| research | Stop + 0.85% price-risk sizing | 4.06% | 0.629 | 10.06% | 32.04% | -2.72% | 34.86% | 84 |
| research | Stop only | 6.77% | 0.555 | 19.21% | 58.10% | -4.65% | 55.27% | 84 |
| research | Exposure-matched no stop | 5.68% | 0.753 | 10.84% | 47.10% | -2.83% | 34.86% | 0 |
| research | 2.0ATR sensitivity | 4.15% | 0.586 | 9.43% | 32.88% | -2.97% | 35.47% | 104 |
| research | 3.0ATR sensitivity | 4.19% | 0.683 | 8.34% | 33.24% | -2.60% | 34.49% | 68 |
| retrospective validation | Baseline | 12.65% | 0.857 | 13.21% | 26.73% | -5.10% | 91.26% | 0 |
| retrospective validation | Stop + 0.85% price-risk sizing | 4.00% | 0.589 | 7.40% | 8.12% | -1.53% | 45.70% | 31 |
| retrospective validation | Stop only | -0.05% | 0.054 | 12.91% | -0.10% | -3.77% | 64.20% | 31 |
| retrospective validation | Exposure-matched no stop | 6.36% | 0.849 | 6.72% | 13.05% | -2.52% | 45.70% | 0 |
| retrospective validation | 2.0ATR sensitivity | 4.75% | 0.623 | 7.21% | 9.66% | -1.78% | 46.49% | 34 |
| retrospective validation | 3.0ATR sensitivity | 5.72% | 0.853 | 7.56% | 11.70% | -1.58% | 44.01% | 25 |
| known data replication | Baseline | 37.54% | 1.629 | 16.49% | 68.61% | -6.06% | 90.50% | 0 |
| known data replication | Stop + 0.85% price-risk sizing | 22.17% | 2.293 | 5.28% | 38.84% | -3.53% | 47.43% | 13 |
| known data replication | Stop only | 38.04% | 1.966 | 10.21% | 69.60% | -5.86% | 76.68% | 13 |
| known data replication | Exposure-matched no stop | 18.59% | 1.623 | 8.83% | 32.23% | -3.18% | 47.43% | 0 |
| known data replication | 2.0ATR sensitivity | 23.96% | 2.184 | 6.98% | 42.19% | -4.33% | 52.64% | 15 |
| known data replication | 3.0ATR sensitivity | 16.45% | 2.034 | 5.03% | 28.36% | -2.98% | 40.61% | 13 |
| full history | Baseline | 17.63% | 0.972 | 23.63% | 404.65% | -6.54% | 89.49% | 0 |
| full history | Stop + 0.85% price-risk sizing | 7.27% | 0.969 | 10.06% | 101.23% | -3.53% | 42.25% | 129 |
| full history | Stop only | 10.77% | 0.780 | 19.21% | 177.24% | -5.86% | 65.70% | 129 |
| full history | Exposure-matched no stop | 8.31% | 0.967 | 11.46% | 121.63% | -3.00% | 42.25% | 0 |
| full history | 2.0ATR sensitivity | 7.71% | 0.936 | 11.25% | 109.60% | -4.33% | 43.40% | 155 |
| full history | 3.0ATR sensitivity | 6.84% | 0.987 | 9.01% | 93.39% | -2.98% | 40.40% | 107 |

## Validation execution stress

| Variant | Total return | CAGR | Sharpe | Max DD | Costs |
|---|---:|---:|---:|---:|---:|
| Baseline, 10 bps/side | 25.78% | 12.23% | 0.833 | 13.27% | $1,620.02 |
| Stop + sizing, 10 bps/side + 25 bps stop slippage | 5.34% | 2.65% | 0.401 | 8.05% | $1,813.30 |
| Stop only, 10 bps/side + 25 bps stop slippage | -3.95% | -2.01% | -0.113 | 14.15% | $2,597.70 |

## Frozen validation gates

- PASS — drawdown reduction at least 20pct
- PASS — drawdown no greater than 12pct
- FAIL — annualized return retention at least 60pct
- FAIL — sharpe no more than 0 10 below baseline
- PASS — stressed total return positive
- PASS — both sensitivity returns positive
- PASS — both sensitivity drawdowns below baseline

The primary retained 31.64% of baseline CAGR (required 60.00%) and changed Sharpe by -0.269 (allowed decline: -0.100).

## Attribution and robustness

The exposure-matched no-stop control matched close-average gross exposure to within 0.00% and beat the primary: 6.36% vs 4.00% CAGR, 0.849 vs 0.589 Sharpe, and 6.72% vs 7.40% max drawdown.

The stop-only control produced -0.10% total return with 0.054 Sharpe and 12.91% max drawdown. This shows that most of the primary's apparent drawdown benefit came from lower exposure and cash, not from the stop rule.

Under stressed execution, the paired block-bootstrap Sharpe difference was -0.432, with a 95% interval of [-1.277, +0.313] and one-sided superiority p=0.8759.

The stressed primary had positive return in 4/5 regime blocks, lower drawdown in 5/5, and no-worse Sharpe in only 1/5. The corrected 12-cell Deflated Sharpe diagnostic was 0.893 (below the conventional 0.95 reference level).

## Interpretation

At least one pre-registered 2023–2024 gate failed. The stop overlay must not be deployed or retuned on the validation/replication periods.

## Integrity and limitations

- The result runner verifies the frozen parquet and manifest hashes before loading data.
- A first diagnostic artifact omitted four original Book R cells from Sharpe dispersion. The corrected run includes all 12 pre-registered cells; the non-gating DSR fell, while every validation gate and the verdict stayed unchanged.
- The first exposure control equated target gross with the primary's realised gross and therefore only approximated the match. The corrected non-gating control solves for equal realised close exposure; the primary and frozen gates are unchanged.
- Yahoo quote OHLCV is a price-return dataset; dividends and cash interest are not reconstructed.
- The 0.85% sizing budget covers entry-to-stop price movement before costs and slippage. Trading costs, adverse stop slippage, and opening gaps can make realised loss larger.
- Resting stops take precedence over a same-open monthly rebalance in this conservative simulator; stressed stop fills therefore receive the extra adverse slippage assumption.
- Average exposure is measured at each close before the final close liquidation; a position stopped intraday contributes zero close exposure for that session.
- The 2023–2024 segment is retrospective validation, not an externally held blind lockbox.
- A historical pass would authorize only a separate forward-paper challenger, never funding.
