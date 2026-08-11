# Pre-registration — momentum-spillover gate (SPY → crypto/FX entries)

**Date:** 2026-08-08 · **Status:** pre-registered BEFORE any run · **Mode:** ITERATION ONLY (strictly < 2025-01-01; holdout untouched)

## Source

Auto-researcher proposal `momentum-spillover-effect-2026-08-08` (Progress queue): "momentum
spillover from the US equity market to other asset classes (FX, crypto)… herding behaviour
applies momentum in one asset class to related classes."

## Hypothesis (as operationalised for the certified book)

When US equity momentum (SPY trailing L-day return) is positive, risk appetite spills into
crypto and FX risk assets — so the certified book's crypto/FX LONG entries should be permitted
only in risk-on regimes and its SHORT entries only in risk-off regimes.

## Dead-end check

- factor_confirmation gate (REJECTED 2026-07-28) used ISWD.L 63d for EQUITIES and BTC 63d for
  alt-crypto. SPY→crypto/FX sign-conditioning is NOT in the ledger. Admissible.
- Priors honestly noted: FX majors directional sleeves all rejected at retail costs; crypto
  4h variants mostly rejected; factor-family precedent negative.

## Design

- Base: certified Book H gold (39-instrument panel), certified params verbatim, mrpt 0.01,
  gap-aware fills, seed 42, warmup 250. Control hard-check: Sharpe 0.86284, 1,637 trades.
- Wrapper `SpilloverGate` on crypto + FX instruments only: a LONG signal on bar t becomes FLAT
  unless SPY's trailing L-day return at t is > 0; a SHORT signal becomes FLAT unless it is < 0.
  Equity/ETF/metals instruments untouched.
- Selection set (exactly 3 trials, ledger-charged BEFORE the run): control, spill_L20, spill_L50.
- DSR deflated by the FULL post-record ledger count. CPCV purge 21 / 15 paths. PBO 3-config set.

## Verdict legs (pre-committed)

- **L1:** best challenger full-window Sharpe ≥ control.
- **L2:** best challenger DSR ≥ 0.95 @ full ledger count.
- **L3:** PBO < 0.5.
- **L4:** best challenger CPCV ≥ 12/15 positive paths AND median OOS ≥ control's.
- **CONFIRMED = L1 ∧ L2 ∧ L3 ∧ L4.** Anything else → REJECTED; "momentum spillover (SPY→crypto/FX
  entry conditioning)" joins the dead-end list and the proposal is marked closed in the queue.
