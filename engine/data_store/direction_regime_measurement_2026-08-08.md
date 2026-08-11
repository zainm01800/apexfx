# Direction x Regime Measurement — 2026-08-08

Certified anchor reproduced EXACT (Sharpe 0.86284, 1637 trades). Informational measurement — no gate, no strategy change.

### Direction split (all classes)

| cell | n | net £ | expectancy £ | win% | PF |
|---|---|---|---|---|---|
| long | 1236 | 218,252 | 176.58 | 59.4 | 1.487 |
| short | 401 | -21,087 | -52.59 | 44.6 | 0.868 |

### Direction x asset class

| cell | n | net £ | expectancy £ | win% | PF |
|---|---|---|---|---|---|
| crypto|long | 125 | 30,322 | 242.57 | 59.2 | 1.72 |
| crypto|short | 69 | -7,322 | -106.11 | 42.0 | 0.809 |
| equity_single|long | 1007 | 169,495 | 168.32 | 59.5 | 1.457 |
| equity_single|short | 280 | -9,249 | -33.03 | 45.7 | 0.908 |
| etf|long | 73 | 17,589 | 240.95 | 63.0 | 1.737 |
| etf|short | 30 | 2,316 | 77.21 | 53.3 | 1.238 |
| fx|long | 14 | -2,372 | -169.40 | 35.7 | 0.672 |
| fx|short | 19 | -4,082 | -214.86 | 31.6 | 0.449 |
| metals|long | 17 | 3,217 | 189.22 | 58.8 | 1.756 |
| metals|short | 3 | -2,750 | -916.69 | 0.0 | — |

### Single-name equities: direction x SPY-200dma at entry

| cell | n | net £ | expectancy £ | win% | PF |
|---|---|---|---|---|---|
| long|spy_above | 924 | 160,802 | 174.03 | 59.4 | 1.461 |
| long|spy_below | 83 | 8,694 | 104.74 | 60.2 | 1.397 |
| short|spy_above | 128 | -8,534 | -66.67 | 43.0 | 0.789 |
| short|spy_below | 152 | -715 | -4.70 | 48.0 | 0.988 |

### All instruments: direction x SPY-200dma at entry

| cell | n | net £ | expectancy £ | win% | PF |
|---|---|---|---|---|---|
| long|spy_above | 1137 | 203,428 | 178.92 | 59.1 | 1.482 |
| long|spy_below | 99 | 14,823 | 149.73 | 62.6 | 1.563 |
| short|spy_above | 194 | -12,989 | -66.95 | 43.8 | 0.822 |
| short|spy_below | 207 | -8,098 | -39.12 | 45.4 | 0.906 |

## Short-sleeve streaks
```json
{
  "n_windows": 261,
  "frac_negative": 0.5517,
  "p05": -10753.03,
  "median": -1164.16,
  "worst": -14921.81
}
```

## Live streak context
```json
{
  "live_4_stop_sum": -3009,
  "note": "live streak is 4 stopped shorts (~-\u00a33,009 incl. open marks); compare to rolling windows of the SAME length, not the 20-trade ones",
  "hist_4trade_short_windows_p05": -3853.09,
  "hist_4trade_short_windows_worst": -5345.63
}
```

## Counterfactuals (per-trade lens)
```json
{
  "certified": 197164.45,
  "no_equity_shorts": 206413.51,
  "no_equity_shorts_when_spy_above_200": 205698.61,
  "y2022": {
    "certified": 8335.94,
    "no_equity_shorts": -12006.72,
    "no_eq_shorts_spy_above": 1330.46
  }
}
```

## Short sleeve net P&L by year
```json
{
  "2017": -2062.79,
  "2018": -1272.44,
  "2019": -10679.42,
  "2020": 3074.8,
  "2021": -1032.66,
  "2022": 20342.66,
  "2023": -15224.97,
  "2024": -2394.24
}
```

## E4-lite entry-timing delta (gap paid on next-open fills)
```json
{
  "total_gbp": 23422.65,
  "mean_per_trade": 14.31,
  "median_per_trade": 6.59,
  "n": 1637,
  "by_dir": {
    "long": 23716.65,
    "short": -293.99
  },
  "by_class": {
    "crypto": -122.95,
    "equity_single": 24972.99,
    "etf": -303.91,
    "fx": 19.58,
    "metals": -1143.05
  },
  "reading_guide": "|mean| < ~\u00a35/trade vs \u00a3120.44 expectancy => fill timing immaterial"
}
```
