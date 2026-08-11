# Pre-registration — trend ensemble × spillover COMBINATION gate

**Date:** 2026-08-08 · **Status:** pre-registered BEFORE any run · **Mode:** ITERATION ONLY (strictly < 2025-01-01; holdout untouched)

## Why this gate exists

Two upgrades passed their own gates independently: the trend ensemble [63,126,252]
(2026-07-27, Sharpe 0.92377, ADOPTED for the funded runner) and the SPY-50d momentum
spillover on crypto/FX entries (2026-08-08, Sharpe 0.9385 on the 252-only book, CONFIRMED).
**Their combination is untested.** House rule: no untested interaction deploys. This gate
tests the funded-runner candidate stack directly.

## Design

- Base: certified Book H gold (39-instrument panel), certified params verbatim, mrpt 0.01,
  gap-aware fills, seed 42, warmup 250.
- Selection set (exactly 3 trials, ledger-charged BEFORE the run):
  1. `control` — certified 252-only (hard-check: Sharpe 0.86284, 1637 trades)
  2. `ensemble` — momentum_lookbacks [63,126,252], no spillover (hard-check vs its gate:
     Sharpe 0.92377, 1654 trades)
  3. `combo` — ensemble [63,126,252] + spill50 wrapper (crypto/FX longs only when SPY 50d
     return > 0, shorts only when < 0)
- DSR deflated by the FULL post-record ledger count. CPCV purge 21 / 15 paths. PBO 3-config set.

## Verdict legs (pre-committed)

- **L1:** combo Sharpe ≥ max(control, ensemble) — the combination must not dilute either parent.
- **L2:** combo DSR ≥ 0.95 @ full ledger count.
- **L3:** PBO < 0.5.
- **L4:** combo CPCV ≥ 12/15 positive paths AND median OOS ≥ control's.
- **L5:** combo net P&L ≥ ensemble's net P&L (spillover must ADD on top of the ensemble).
- **CONFIRMED = L1 ∧ L2 ∧ L3 ∧ L4 ∧ L5** → the combo becomes the funded-runner config
  (pending owner decision + the proof's graduation). Anything else → the funded runner keeps
  the ensemble alone, and spillover stays a certified-but-separate candidate.
