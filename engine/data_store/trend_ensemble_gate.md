# GATE — MULTI-HORIZON TREND ENSEMBLE: **ADOPTED (both challengers, pre-registered rule: CPCV head-to-head > 7/15 ✓, DSR > 0.95 @ n=279 ✓, added cost drag < 1%/yr ✓) — PBO caveat displayed below; flags default OFF, certified book unchanged**

**Pre-registration:** `engine/data_store/trend_ensemble_prereg.md` (written BEFORE any
challenger run; the 3 trials were recorded before execution). **Results:**
`engine/data_store/validation/trend_ensemble_gate_2026-07-27.json`. **Script:**
`engine/scripts/run_portfolio_gate_trend_ensemble.py`. **Window:** ITERATION only,
strictly < 2025-01-01; certified anchor (mrpt 0.01, EQUITY_CORE panel order, gap-aware
engine) reproduced EXACT by the control (Sharpe 0.86284, 1637 trades, equity 292,551.34).
**Ledger:** 276 → **279**; every DSR deflated by 279.

## What was tested

Replacing the certified single-252 vol-scaled momentum score with an **equal-weight blend
of per-horizon vol-scaled scores** (`s(t) = mean_L s_L(t)`, NaN unless every leg is finite)
— the most replicated robustness result in the trend literature (Moskowitz 2012 JFE;
Hurst 2017 JPM; Benhamou 2025: the 63/252 barbell captures most of a full ensemble).
Score only: regime agreement gate, probability map, HTF 1w×50 gate, vol-scaled sizing,
managed exits, costs, caps are the unchanged certified machinery. Flag
`momentum_lookbacks` on `RegimeGatedMomentum`, default `None` ⇒ `[252]` ⇒ certified;
`config.yaml` untouched; certified path byte-identical (anchor re-verified, unit tests).

## The pre-registered scoreboard

| | control `[252]` (certified) | blend `[63,126,252]` | barbell `[63,252]` |
|---|---|---|---|
| Sharpe | 0.86284 (anchor, exact) | **0.92377** (+0.0609) | **0.90937** (+0.0465) |
| Sortino | 0.9341 | **1.0636** | **1.0230** |
| Profit factor | 1.3245 | **1.3409** | **1.3418** |
| Trades | 1637 | 1654 | 1620 |
| Win rate | 55.77% | 55.20% | **55.86%** |
| Expectancy / trade | +120.44 (+1.022%) | **+132.23 (+0.941%)** | **+133.14 (+1.009%)** |
| Max drawdown | 16.32% | **15.92%** | **14.67%** (−1.65pt) |
| Worst daily loss | −5.09% | **−3.45%** | **−4.19%** |
| Worst month | **−19,673** | −20,634 (worse) | −28,230 (worse) |
| Avg monthly P&L | +1,783 | **+2,002** | **+1,966** |
| Max gross leverage | 2.59× | 2.42× | 2.50× |
| Cost drag/yr (zero-cost twin) | −0.46% | −0.20% (**added +0.26%**) | −0.64% (**added −0.18%**) |
| DSR @ n=279 | 0.9966 ✓ | **0.9984 ✓** | **0.9980 ✓** |
| CPCV median / frac positive | +0.0476 / 15-of-15 | **+0.0603 / 15-of-15** | **+0.0538 / 15-of-15** |
| **CPCV head-to-head vs control** | — | **9 of 15 (> 7 ✓)** | **8 of 15 (> 7 ✓)** |
| **PBO (3-config set)** | — | **0.96025 (≥ 0.5)** | — |

**Pre-registered rule (prereg §5):** a challenger is ADOPTED iff (1) it beats the control
on > 7 of the 15 CPCV paths head-to-head, (2) its full-window DSR > 0.95 at the full
ledger count, and (3) its added zero-cost-twin drag < 1%/yr. **Blend: 9/15 ✓, DSR 0.9984
✓, added drag +0.26%/yr ✓ → ADOPTED. Barbell: 8/15 ✓, DSR 0.9980 ✓, added drag −0.18%/yr
✓ → ADOPTED.** "Adopted" per the prereg means *validated candidate, flag available for a
future certified-change decision* — the certified default stays `[252]`; the anchor is
intact (hard-check EXACT in this gate).

## The honest reading

The stability claim held on every pre-registered leg. Both blends win a majority of the
15 purged OOS paths against the same-machinery control (9 and 8 of 15), both lift the
full-window Sharpe (+0.061 / +0.047) with *higher* deflated significance than the control
(DSR 0.9984 / 0.9980 vs 0.9966 at n=279), both cut max drawdown (−0.4pt / −1.65pt) and
the worst day (−1.6pt / −0.9pt), and the added turnover costs +0.26%/yr (blend) and
−0.18%/yr (barbell — slightly *less* turnover cost than the control; the fast leg exits
some losers before the full spread round-trip). The barbell captures most of the full
blend's effect, as Benhamou et al. predict. One non-binding metric moves the other way
and is reported plainly: the worst single MONTH is worse for both challengers (−20,634
and −28,230 vs −19,673) even though the worst DAY improves — the fast leg can be early
into a month-long reversal before the slow leg confirms. Verdict-binding legs are
unaffected; the number stands here so the scoreboard is not selectively presented.

**The PBO leg reads 0.96025 and must not be waved through.** Across the 3-config CSCV
selection set the in-sample winner ranks below-median OOS 96% of the time. Two things
bound what that means here, both pre-registered: (a) the three configs share ~100% of
their universe and differ only in a score blend — their daily returns are near-collinear,
so the "in-sample best" is decided by noise and *must* flip out-of-sample; the PBO matrix
has no discriminative power by construction (the identical 0.96025 attaches to the
certified control too — under the standard machinery the control "REJECTs" on the same
number, which demonstrates the leg is uninformative for this selection set, not that the
book got worse); (b) the pre-committed adoption question for this campaign was never
"which of 3 near-identical configs is best" (that is what PBO answers) but "does blending
beat 252-only" — answered by the head-to-head CPCV and the full-window DSR legs above.
Reported as computed, per the prereg §4 caveat. If the owner ever promotes a blend to
certified, the honest statement is: *adopted on head-to-head OOS stability and deflated
full-window significance, with the selection-PBO leg uninformative-by-construction and
recorded*.

Choosing between the two adopted candidates is outside this gate's prereg (§5: a separate
certified-change decision). On the numbers: the full blend has the higher Sharpe/DSR; the
barbell has the better drawdown, the cheaper turnover, and the stronger simplicity case.

## Pre-registered caveats, restated where they bound the result

- Horizon sets are literature choices, deliberately not swept (§7) — no 21/42/84/126/189
  grid was run; running one now is a new prereg and new ledger charges.
- In strong persistent trends all legs agree and the challenger ≈ control; the measured
  effect (+0.05 to +0.06 Sharpe) is modest, as expected by construction.
- Zero-cost twins share the same stop/target shift logic, so the drag figure is the honest
  whole-run cost difference (negative drags are real: cost-induced stop shifts are mildly
  protective in this book — zeroing costs *lowers* the control's ann. return by 0.46%/yr).

## Determinism

Full gate executed twice (seed 42): **results payload byte-identical** modulo
`generated_at` and the ledger bookkeeping line (first pass 276 → 279, second pass dedups
279 → 279); the control reproduced the certified anchor exactly in both passes. Unit
tests: `tests/test_trend_ensemble.py` (default = certified single lookback; explicit
`[252]` == certified score exactly; blend == equal-weight mean of vol-scaled legs; NaN
unless every leg finite; min_obs = slowest leg; invalid lookbacks rejected; fit()-cache
== point-in-time compute for blend and single leg) + full suite green.

## Ledger

- **n before this gate: 276**
- **+3** (`trend_ens_control_252`, `trend_ens_blend_63_126_252`,
  `trend_ens_barbell_63_252`, kind `trend_ensemble_gate`, recorded before the first run)
  → **279**. Rerun deduped (279 → 279).
