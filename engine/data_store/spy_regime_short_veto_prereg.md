# Pre-registration — SPY-regime short veto gate (E2-gate)

**Date:** 2026-08-08 · **Status:** pre-registered BEFORE any run · **Mode:** ITERATION ONLY (strictly < 2025-01-01; holdout untouched)

## Hypothesis

Single-name equity SHORT entries taken while SPY is above its 200-day SMA are negative-edge
for this book and should be vetoed; shorts taken while SPY is below the 200dma are ~breakeven
and carry the crash payoff.

## Motivating measurement (already completed, informational)

`direction_regime_measurement_2026-08-08.json` (certified anchor reproduced EXACT):
- equity single-name shorts, SPY>200dma at entry: n=128, net **−£8,534**, expectancy **−£66.67**, PF 0.789
- equity single-name shorts, SPY<200dma: n=152, net −£715, expectancy −£4.70, PF 0.988
- per-trade counterfactual (no slot contention): removing SPY-above shorts → net +£8,534 vs certified;
  2022 P&L: certified +£8,336 → veto variant +£1,330 (stays positive, insurance reduced).
- ETF shorts are profitable (+£2,316, PF 1.238) → veto applies to single-name stocks ONLY.

## Design

- Base: Book H gold equity sleeve (21 instruments), certified params verbatim
  (lookback 252, vol 63, hold 21, rr 1.5, rule_based regime, HTF 1w×50), mrpt 0.01,
  gap-aware fills, seed 42, warmup 250, horizon per `run_portfolio_gate`.
- Challenger wrapper: `SpyShortVeto` — on single-name stocks (STOCKS_12 only), a SHORT signal
  on bar t becomes FLAT when SPY's close at t is above SPY's 200d SMA. Point-in-time safe
  (state computed at the same bar the signal is generated on). Longs and ETFs/SGLD untouched.
- Selection set (exactly 2 trials, ledger-charged BEFORE the run): control, spyveto.
- DSR deflated by the FULL post-record ledger count. CPCV purge 21 / 15 paths. PBO 2-config set.

## Verdict legs (pre-committed)

- **L1:** challenger full-window Sharpe ≥ control (no degradation).
- **L2:** challenger DSR ≥ 0.95 @ full ledger count.
- **L3:** PBO < 0.5.
- **L4 (crash preservation):** challenger 2022 calendar-year return stays > 0.
- **GATE PASS = L1 ∧ L2 ∧ L3. CONFIRMED (adoptable) = PASS ∧ L4.**
  Anything else → REJECTED, and "spy-regime short veto" joins the dead-end list.

## Known limitations

Per-trade motivating measurement ignores slot contention; the gate's portfolio runs are the
arbiter. 2022 preservation is measured on the equity curve, not per-trade. Two configs shares
the near-collinear PBO caveat from prior gates.
