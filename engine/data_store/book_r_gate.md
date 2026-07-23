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

## Why the screen said £731 and the gate says £197

The screen was a continuous-weight, monthly-rebalanced, long-only book with **no stops**. The
engine takes discrete trades with ATR stops, a regime filter and EV slot allocation. Running
the same signal through that machinery cost roughly two thirds of the return.

The most likely culprit is the **stops**: a monthly-rebalanced rank book expects to *hold*
through drawdowns until the next rebalance, and an ATR stop exits those positions early,
realising losses the screen never took and then sitting out the recovery. That is a structural
mismatch between the signal and the execution model, not a flaw in either alone.

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

**Worth it, if anything:** running residual momentum *without ATR stops* — a hold-to-rebalance
exit mode — to test the stop-mismatch hypothesis directly. That is a change to the execution
model, not the signal, and it is a single pre-registered comparison rather than a sweep.

**Still the honest position:** Book H at 0.50% risk (Sharpe 0.922, £413/month, forward p95
8.2%) remains the best gated configuration, and £800–1,000/month at ≤11% drawdown is not
reachable on £100k with anything measured so far.
