# APEX Quant — Parameter Optimization Results

*Generated: 2026-07-09 20:14:54 UTC*

**Search method:** Random Search (200 iterations × 10 instruments × 2 timeframes = 4000 backtest runs)

**Date range:** 2022-01-01 → 2024-12-31

**Workers:** 10 parallel processes

---

## Composite Score Formula

```
score = Sharpe × ProfitFactor × CAGR / (1 + MaxDrawdown)²
```

Drawdown is penalised quadratically to favour configs with strong
risk-adjusted returns. Runs with fewer than 5 trades score zero.

---

## Top 10 Parameter Configurations

| Rank | Score | ATR Mult | Kelly | Risk/Trade | Mom Lookback | R:R | Hold | Sharpe | PF | CAGR | MDD | WinRate | Trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.0207 | 2.5 | 0.20 | 0.020 | 126 | 2.0 | 20 | -0.062 | 1.05 | 0.2% | 9.2% | 44% | 124 |
| 2 | 0.0185 | 1.5 | 0.10 | 0.020 | 126 | 3.0 | 20 | -0.061 | 1.00 | 0.1% | 11.5% | 33% | 170 |
| 3 | 0.0176 | 1.5 | 0.10 | 0.030 | 126 | 3.0 | 20 | -0.090 | 0.98 | -0.1% | 13.0% | 33% | 142 |
| 4 | 0.0149 | 3.0 | 0.05 | 0.030 | 63 | 3.0 | 20 | 0.030 | 1.10 | 0.5% | 9.7% | 46% | 110 |
| 5 | 0.0128 | 5.0 | 0.30 | 0.030 | 126 | 1.5 | 20 | -0.003 | 1.07 | 0.3% | 7.4% | 47% | 96 |
| 6 | 0.0119 | 3.0 | 0.10 | 0.030 | 63 | 1.5 | 10 | 0.006 | 1.03 | 0.5% | 10.5% | 48% | 168 |
| 7 | 0.0116 | 2.0 | 0.00 | 0.010 | 21 | 2.0 | 20 | 0.140 | 1.16 | 0.5% | 5.2% | 42% | 148 |
| 8 | 0.0115 | 3.5 | 0.00 | 0.020 | 126 | 3.0 | 20 | -0.071 | 1.03 | 0.1% | 7.8% | 47% | 103 |
| 9 | 0.0113 | 2.0 | 0.30 | 0.020 | 126 | 3.0 | 10 | -0.042 | 1.00 | 0.2% | 10.3% | 45% | 209 |
| 10 | 0.0113 | 2.0 | 0.20 | 0.020 | 126 | 3.0 | 10 | -0.042 | 1.00 | 0.2% | 10.3% | 45% | 209 |

---

## Recommendation

**Recommended configuration** (highest composite score 0.0207):

- ATR stop multiplier: **2.5**
- Kelly fraction: **0.20**  (edge gate; 0 = disabled)
- Max risk per trade: **2.0%**
- Momentum lookback: **126 bars**
- Reward:risk ratio: **2.0**
- Holding horizon: **20 bars**

Expected (average across instruments):
- Sharpe ratio: **-0.062**
- Profit factor: **1.05**
- CAGR: **0.2%**
- Max drawdown: **9.2%**
- Win rate: **44%**


---

### Full parameter space searched

| Parameter | Values tested |
| --- | --- |
| atr_stop_mult | 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0 |
| kelly_fraction | 0.0, 0.05, 0.1, 0.2, 0.3 |
| max_risk_per_trade | 0.005, 0.01, 0.02, 0.03 |
| momentum_lookback | 21, 42, 63, 126 |
| reward_risk | 1.0, 1.5, 2.0, 3.0 |
| holding_horizon | 5, 10, 20 |

### Instruments tested

| Asset Class | Instruments |
| --- | --- |
| Forex | EUR/USD · GBP/USD · USD/JPY · AUD/USD |
| Equity/ETF | AAPL · MSFT · SPY · QQQ |
| Crypto | BTC/USD · ETH/USD |
