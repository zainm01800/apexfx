# BOOK S GATE — 2.00% ON 5 SLOTS: **REJECTED (it is leverage, not edge)**

**The pre-registered falsification test killed it.** Per-trade expectancy is
**+1.2758% (control) vs +1.2816% (challenger)** — statistically identical. The prereg said in
advance: *"if expectancy is merely equal and only the leverage differs, this is a leverage
trade dressed as a signal finding."* It is. Ledger 244 → **252**.

Prereg: `concentration_risk_prereg.md`. Results: `validation/book_s_gate_2026-07-23.json`.

## Result

| | control 0.75% / 12 slots | challenger 2.00% / 5 slots |
|---|---|---|
| £/month | £587 | **£744** |
| CAGR | 7.05% | 8.93% |
| Sharpe | 0.893 | 0.901 |
| **Per-trade expectancy** | **+1.2758%** | **+1.2816%** |
| Backtest maxDD | 14.3% | **20.0%** |
| Forward p95 DD | 12.0% | 14.9% |
| Trades | 1,694 | 892 |
| DSR (n=252) | 0.999 ok | 0.999 ok |
| CPCV | ok (93% positive) | ok (93% positive) |
| DD ≤ 16% | ok | ok |
| CAGR ≥ 8.4% | FAIL | ok |

**Paired block bootstrap: Δsharpe +0.008, p=0.4876, CI [−0.389, +0.416].** No significant
risk-adjusted improvement. PBO 0.945 (reported, not binding).

## What this means

The £744/month is **real and available** — but it is bought by risking more per trade, not by
trading better. Expectancy per trade is unchanged to three decimal places. Cutting to 5 slots
did not concentrate the edge; it just left room to lever up, and the drawdown scaled with it
(14.3% → 20.0% backtest, 12.0% → 14.9% forward).

This is the cleanest statement of the session's central finding: **on this book, monthly profit
and drawdown scale together, because sizing cannot change expectancy.** £744/month at 14.9%
drawdown and £587/month at 12.0% drawdown are the same strategy at two volumes.

The earlier concentration screen suggested the 5-slot book had a genuinely higher Sharpe
(0.901 vs 0.893). The paired test shows that gap is noise (p=0.49). Sharpe differences of
±0.01 on 892 vs 1,694 trades are not distinguishable.

## Note on DSR

Both books show DSR 0.999 here, while the same control scored 0.9044 in the Book P gate. That
is the **set-dependence of DSR** already recorded in `gates-and-pbo-limits-2026-07-22` — the
deflation depends on the set of trial Sharpes passed in, and with only two near-identical
configs the variance term collapses. **The 0.999 is not evidence of a stronger edge**, and
should not be quoted as an upgrade over the 0.9044.

## Drawdown probability, since it was asked

For the challenger (realised worst 20.0% over 12.8 years):

| horizon | P(>12%) | P(>15%) | **P(>20%)** |
|---|---|---|---|
| 1 year | 13.6% | 4.9% | **0.7%** |
| 3 years | 45.5% | 23.2% | **6.6%** |
| 5 years | 65.3% | 38.6% | **12.9%** |

**But the bootstrap understates it.** On the REAL rolling windows (clustering preserved), the
median 3-year drawdown is **14.8%** and the 95th percentile is **19.9%** — i.e. a drawdown in
the 15–20% band is the *normal* multi-year experience, not a tail. The 20% is the edge of what
actually happened, not a remote possibility.

For the control: P(>20%) is 0.1% in a year, 2.0% over three; real rolling p95 is 14.3%.

## Decision

**ADOPT NOTHING.** The live config stays at 0.75% / 12 slots.

If the owner wants £744/month anyway, that is a legitimate choice — but it should be made
knowing it is a volume decision with proportionally more drawdown, and that no test supports it
being a *better* strategy.
