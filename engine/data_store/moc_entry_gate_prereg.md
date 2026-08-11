# Pre-registration — MOC (close-fill) entry gate

**Date:** 2026-08-08 · **Status:** pre-registered BEFORE any run · **Mode:** ITERATION ONLY (strictly < 2025-01-01; holdout untouched)

## Hypothesis

The certified book fills entries at the next bar's open and systematically pays the overnight
gap on momentum entries. Filling at the decision bar's close (MOC) captures that drift.

## Motivating measurement (already completed, informational)

`direction_regime_measurement_2026-08-08.json` (anchor reproduced EXACT): signed entry delta
= **+£23,422.65 over 1,637 trades (mean +£14.31/trade)** — concentrated in single-name equity
longs (+£24,973); crypto/FX/metals slightly negative. Above the pre-committed £5/trade
immateriality bar, so the question graduates to a full portfolio gate.

## Design

- Base: certified Book H gold (39-instrument panel: EQUITY_CORE + GOLD_ETC + crypto + FX_MAJORS_7),
  certified params verbatim, mrpt 0.01, gap-aware exits, seed 42, warmup 250.
- Implementation: new default-OFF `entry_fill` flag on `PortfolioBacktester` ("open" = certified
  byte-identical; "close" fills at the decision bar's close, stop/target shift handled by the
  existing `_enter` shift). Forwarded through `run_portfolio_cpcv` exactly like `exit_mode`/
  `trade_manager` (the fold-parity rule).
- Selection set (exactly 2 trials, ledger-charged BEFORE the run): control (open), moc (close).
- DSR deflated by the FULL post-record ledger count. CPCV purge 21 / 15 paths. PBO 2-config set
  (near-collinear caveat recorded in advance).

## Verdict legs (pre-committed)

- **L1:** challenger full-window Sharpe ≥ control.
- **L2:** challenger DSR ≥ 0.95 @ full ledger count.
- **L3:** PBO < 0.5.
- **L4:** challenger CPCV ≥ 12/15 positive paths AND median OOS Sharpe ≥ control's.
- **L5:** challenger net P&L ≥ control + £10,000 over the window (the measurement predicted
  +£23.4k; L5 allows ~2.3× slippage before the claim is called unsupported).
- **CONFIRMED (adoptable) = L1 ∧ L2 ∧ L3 ∧ L4 ∧ L5.** Anything else → REJECTED; "MOC entries"
  joins the dead-end list.

## Known limitations (pre-committed)

MOC assumes the close signal is computable just before the close (operationally: MOC orders with
near-close data — an execution change, priced here, not enacted). Crypto/FX have no session close;
the convention is applied uniformly and the per-class decomposition is reported so any
class-specific harm is visible.
