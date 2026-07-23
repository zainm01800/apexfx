# BOOK R GATE — RESIDUAL MOMENTUM: **REJECTED**

**The residual-momentum screen did not survive the engine.** Inside `PortfolioBacktester` it is
*worse* than the total-return control it was supposed to beat (Δsharpe **−0.243**, p=0.867), and
it fails DSR (0.460 vs the 0.95 threshold). Nothing is adopted. Ledger 232 → **244**.

Prereg: `residual_momentum_prereg.md` (written before the run).
Results: `validation/book_r_gate_2026-07-23.json`.

## Result

| | control: total momentum | challenger: RESIDUAL |
|---|---|---|
| CAGR | 5.74% | 2.36% |
| £/month on £100k | £479 | **£197** |
| Sharpe | 0.697 | **0.454** |
| Backtest maxDD | 11.9% | **8.5%** |
| Forward p95 DD | 13.8% | **9.6%** |
| Trades | 2,420 | 1,860 |
| DSR (n=244) | 0.744 FAIL | 0.460 FAIL |
| Drawdown wall ≤11% | FAIL | **ok** |

**Paired block bootstrap** (block 21, B=10,000, seed 42): Δsharpe **−0.243**, p=**0.8674**,
95% CI **[−0.666, +0.181]**. The challenger is not better; the point estimate says worse.
PBO 0.333 (reported, not binding).

## The honest reading: half the mechanism reproduced

Residual momentum's *risk* claim held up. It cut backtest drawdown 11.9% → **8.5%** and forward
p95 13.8% → **9.6%**, and it is the only config today that **passes the 11% drawdown wall**.
That is exactly the volatility reduction Blitz et al. describe, and it survived contact with
stops, slot caps and real costs.

The *return* half did not come with it. £197/month is less than half the £479 control and well
under Book H's £413. Lower vol at proportionally lower return is not an edge — it is
de-levering, which `max_risk_per_trade` already does more cheaply and without a new strategy.

## Why the screen said £731 and the gate says £197 — tested, and my first answer was WRONG

I initially blamed the **stops**: a rank book expects to hold to the next rebalance, an ATR stop
exits early, so the engine realises losses the screen never took. Plausible, and testable —
widen `atr_stop_mult` until stops barely bind and see if the return recovers.

**It does the opposite** (`scratch/diagnose_resid_stops.py`):

| atr_stop_mult | residual CAGR | residual Sharpe | total CAGR | total Sharpe |
|---|---|---|---|---|
| 2.0 | 4.04% | 0.633 | 6.20% | 0.646 |
| 4.0 | 2.23% | 0.606 | 4.35% | 0.750 |
| 8.0 | 1.22% | 0.619 | 2.34% | 0.762 |
| 20.0 | 0.36% | 0.415 | 1.11% | 0.767 |

Widening the stop **monotonically reduces** return for both signals. In a risk-budgeted engine
the stop distance is the *denominator of position size* — a wider stop means a smaller position
for the same risk, so it is a de-leveraging knob, not a "let winners run" knob. **Stops are not
the explanation.**

(The gate's £197 vs the diagnostic's £337 at nominally the same setting is `config.yaml`
setting `atr_stop_mult: 2.5`, not the 2.0 the diagnostic forced. Both lie on the curve above.)

**Residual momentum is worse than total momentum at EVERY stop width tested** — 4.04 vs 6.20,
2.23 vs 4.35, 1.22 vs 2.34, 0.36 vs 1.11. That is not an execution mismatch; the signal is
simply weaker inside this engine.

The real source of the £731 is **capital deployment, not exits**. The screen was a *fully
invested* book: 15 inverse-vol weights summing to 1.0, always. The engine sizes each position
from a risk budget (`risk_fraction × equity / stop_distance`), caps it, and refuses entries when
slot buckets are full (`timeframe_bucket_full` fired ~3.3k times). Those two constructions
deploy very different amounts of capital, so they were never going to agree.

So the £731 is not a lie about residual momentum as a *portfolio* — it is a number that this
engine's sizing model cannot produce. Believing it would have required swapping the whole
position-sizing layer, not adding a signal.

This is the same gap that killed the "£887/month" and "Sharpe 1.331" figures earlier today.
**Three for three: no screen-level number has survived the gate.**

## Pre-registered predictions — scored

1. *"Breadth should help residual and hurt total momentum."* The screen showed this. The gate
   did not test breadth directly, and the paired result made it moot: the challenger lost
   outright. **Not established inside the engine.**
2. *"The £800/month floor is expected to FAIL (~£731 modelled)."* Recorded in advance, and it
   failed by far more than predicted — **£197**. Recording it beforehand is what stops the
   target being quietly re-scored.

## Counter-hypotheses — which survived

- **"Turnover eats it live."** Plausible but not the main effect here; the engine's own costs
  are applied and the trade count actually *fell* versus the control.
- **"The market proxy is crude."** Still open. An equal-weight mean over 49 equities, 22 FX
  pairs and 2 crypto is not a factor model.
- **"Bull-market artifact."** Untested — and now not worth testing on this construction.

## What would be worth trying, and what would not

**Not worth it:** tuning `top_n`, the lookback, or the beta window. That is parameter search on
a signal that just lost its paired test, and every cell would need charging.

**Already tested and closed:** the stop-mismatch hypothesis. Widening the stop makes it worse,
and residual loses to total momentum at every width. No follow-up needed on exits.

**The only honest remaining question** is whether a *fully-invested, weight-based* portfolio
layer — the screen's construction — is worth building as an alternative to risk-budgeted
position sizing. That is a large change to the sizing engine, not a strategy, and it should be
judged on its own terms rather than smuggled in as "a residual momentum sleeve".

**Still the honest position:** Book H at 0.50% risk (Sharpe 0.922, £413/month, forward p95
8.2%) remains the best gated configuration, and £800–1,000/month at ≤11% drawdown is not
reachable on £100k with anything measured so far.
