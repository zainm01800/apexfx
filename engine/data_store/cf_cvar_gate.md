# W2 GATE — CORNISH-FISHER CVaR TAIL SIZING: **REJECTED (PBO 0.852 — H1/H2 hold and every in-window metric improves, but the selection does not survive the overfitting gate)**

**Pre-registration:** `engine/data_store/cf_cvar_prereg.md` (written BEFORE any challenger
run; the 2 trials were recorded before execution). **Results:**
`engine/data_store/validation/cf_cvar_gate_2026-07-25.json`. **Script:**
`engine/scripts/run_portfolio_gate_cf_cvar.py`. **Window:** ITERATION only, strictly
< 2025-01-01; certified anchor (mrpt 0.01, EQUITY_CORE panel order, gap-aware engine)
reproduced EXACT by the control (Sharpe 0.86284, 1637 trades, equity 292,551.34).
**Ledger:** 273 → **275**; every DSR deflated by 275.

## What was tested

Sizing each position by **tail-adjusted volatility** instead of raw ATR/vol: a
direction-aware Cornish-Fisher multiplier `τ = |z_CF| / z` (one-sided 99%, z = 2.326;
rolling 60-day skew S and excess kurtosis K per instrument; S clipped ±2, K to [−2, 10],
τ to **[1.0, 2.0]** — contraction only, never upsizing) applied to the per-unit risk
measure in `RiskManager` step 6a. Stops, targets, exits, and the recorded raw
planned-loss risk_fraction are unchanged; τ only shrinks units on heavy-tailed /
adversely-skewed names. Flag `risk.cf_cvar_enabled`, default OFF; `config.yaml`
untouched; certified path byte-identical (anchor re-verified).

## The pre-registered scoreboard

| | control (certified ATR sizing) | challenger (CF-CVaR τ sizing) |
|---|---|---|
| Sharpe | 0.86284 (anchor, exact) | **0.89388** (+0.0310) |
| Sortino | 0.9341 | **0.9745** |
| Profit factor | 1.3245 | **1.3767** (+0.0522) |
| Trades | 1637 | 1619 |
| Win rate | 55.77% | **56.08%** |
| Expectancy / trade | +120.44 (+1.022%) | +118.69 (+1.076%) |
| Max drawdown | 16.32% | **15.39%** (−0.93pt) |
| **Worst trade P&L** | −3,293.69 | **−3,101.15** (−5.9%) |
| Worst daily loss | −5.09% | −5.04% |
| Worst month | −19,673 | **−16,938** |
| Avg monthly P&L | +1,783 | +1,736 (−46; **cost 2.6%**) |
| Max gross leverage | 2.59× | 2.32× |
| τ binds (positions scaled) | — | **943** of ~1,637 entries (88 distinct τ ∈ [1.00, 2.00]) |
| DSR @ n=275 | 0.9975 ✓ | **0.9982 ✓** |
| CPCV median / frac positive | +0.0476 / 15-of-15 ✓ | **+0.0571 / 15-of-15 ✓** |
| **PBO (2-config set)** | — | **0.85175 ✗ (≥ 0.5)** |

**Pre-registered rule:** H1 (tail: ≥5% smaller worst day OR worst trade OR ≥1pt smaller
maxDD) **HOLDS** — worst trade −5.9% (worst month also −14%). H2 (monthly-profit cost
≤ 5%) **HOLDS** — cost 2.6%. Challenger gates: DSR ✓ (0.9982), CPCV ✓ (median *higher*,
0.0571 vs 0.0476, 15-of-15 both), **PBO ✗ (0.852)**. **Verdict: REJECTED.**

## The honest reading

The mechanism is real and broadly active: τ ≠ 1 on 943 entries (58% of the book), with
88 distinct multipliers up to the 2.0 clip — the sizing genuinely responds to estimated
tail shape, name by name and regime by regime. And the in-window scoreboard is uniformly
one-directional: every tail metric improved (worst trade −5.9%, worst month −14%,
maxDD −0.93pt, leverage 2.59×→2.32×) and every performance metric also improved
(Sharpe +0.031, PF +0.052, win rate +0.31pt, Sortino +0.040) — the protection cost 2.6%
of monthly profit, half the pre-committed 5% budget. Even the out-of-sample evidence is
supportive: the challenger's CPCV median is *higher* (0.0571 vs 0.0476) with 15-of-15
positive paths on both sides, and its DSR is higher (0.9982 vs 0.9975).

What the overfitting gate says: across the 2-config CSCV selection set, the in-sample
winner ranks below-median out-of-sample **85%** of the time. With two configs sharing
~100% of their universe and differing only in a sizing multiplier, PBO's discriminative
power is limited by construction (pre-registered caveat) — but 0.852 is what it
computed, the threshold 0.5 was pre-committed, and the rule makes no exception for "all
the metrics moved the right way". **REJECTED as a certified change — recorded, not
adopted. The certified ATR-sized book remains the book of record.**

**Note for the owner:** this is the same shape as the W3 notional-cap gate (H1/H2 hold,
performance mildly better, PBO kills it). The tail evidence here is arguably stronger
(worst month −14%, DSR and CPCV both higher, cost only 2.6%). Adopting CF tail sizing as
a *risk policy* (as the 0.75% risk-per-trade was adopted un-gated on 2026-07-23) is a
defensible owner call on this evidence — but it is a policy call, not a gate-certified
improvement, and is flagged as such here.

## Pre-registered caveats, restated where they bound the result

- The 60-day window and 99% quantile are point choices, deliberately not swept (§7).
- CF is a 4-moment approximation; the clips ([1.0, 2.0] on τ) cap the contraction at
  halving and forbid upsizing on thin-tailed names (that is a different claim).
- In-window tail measurement is dominated by single episodes; the CPCV distribution is
  the more robust evidence, and it favours the challenger.
- CPCV fold edge effect: each fold's first 59 bars have τ = 1 (rolling-window restart),
  deterministic and conservative.

## Determinism

Full gate executed twice (seed 42): metrics payload **byte-identical** modulo
`generated_at` and the ledger bookkeeping line (first pass 273 → 275, second pass dedups
275 → 275); the control reproduced the certified anchor exactly in both passes. Unit
tests: `tests/test_cf_cvar.py` (formula: Gaussian neutral, negative skew widens the left
tail only, kurtosis widens both; vectorised parity + NaN neutrality; flag-off
byte-identical sizing; contraction with stops/targets/raw-risk untouched; direction
selects the adverse tail; τ < 1 no-op; τ_max clip; backtest labels + accounting) +
full suite green.

## Ledger

- **n before this gate: 273** (271 + 2 for the W1 order-invariant gate)
- **+2** (`book_h_gold_252`, `book_h_gold_252_cf_cvar`, kind `cf_cvar_gate`, recorded
  before the first run) → **275**. Rerun deduped (275 → 275).
