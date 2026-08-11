# Pre-registration — exit-side earnings de-risk gate

**Date:** 2026-08-08 · **Status:** pre-registered BEFORE any run · **Mode:** ITERATION ONLY (strictly < 2025-01-01; holdout untouched)

## Hypothesis

De-risking OPEN single-name equity positions on the bar before a scheduled earnings report
reduces gap-through-stop tail losses without giving back the book's trend edge.

## Why this and not an entry veto

The entry-side blackout family is REJECTED (2026-07-24: −0.143 Sharpe; counterfactual proof
that both live gap losses — MSFT −£1,103 real fill, PLTR −£1,189 — were entered 9–12 trading
days BEFORE their events, so no entry window catches them). The mechanism that actually
addresses held-into-earnings risk operates on exits. This is that experiment.

## Design

- Base: Book H gold EQUITY SLEEVE (21 instruments), certified params verbatim, mrpt 0.01,
  gap-aware fills, seed 42, warmup 250. Control reproduction hard-check: Sharpe ~0.9955,
  1,546 trades (2026-07-24 blackout-gate control on the identical sleeve).
- Rule under test: on the last bar BEFORE a covered earnings date (instrument's own calendar),
  exit a fraction of any open position in that instrument at that bar's close, before
  TradeManager management. Implemented as default-OFF `earnings_derisk`/`earnings_derisk_frac`
  on `PortfolioBacktester`, forwarded through CPCV per the fold-parity rule.
- Data: `data_store/earnings_calendar/*.json` (SEC EDGAR 8-K dates). 11 of 12 stocks covered;
  TSM (6-K filer) uncovered and trades unprotected — documented.
- Selection set (exactly 3 trials, ledger-charged BEFORE the run): control, derisk_flat (100%),
  derisk_half (50%, remainder keeps certified management).
- DSR deflated by the FULL post-record ledger count. CPCV purge 21 / 15 paths. PBO 3-config set.

## Verdict legs (pre-committed)

- **L1:** best challenger full-window Sharpe ≥ control − 0.02.
- **L2:** best challenger DSR ≥ 0.95 @ full ledger count.
- **L3:** PBO < 0.5.
- **L4 (tail):** best challenger's worst daily return ≥ 10% smaller in magnitude than control's,
  OR max drawdown ≥ 1pt smaller.
- **CONFIRMED (adoptable) = L1 ∧ L2 ∧ L3 ∧ L4** for at least one challenger. Anything else →
  REJECTED; "earnings de-risk (exit-side)" joins the dead-end list.

## Diagnostics (reported, not verdict-binding)

Event-adjacent stop exits (exit_reason=stop within ±1 bar of a covered event): count and total £,
control vs challengers; derisk-fire count; per-instrument P&L for covered names.

## Known limitations

EDGAR dates are filing dates (≈ release dates, date-only, no BMO/AMC flag): de-risking the bar
before covers BMO gaps; AMC reports announce after that bar's close and are only caught if the
gap lands on the event bar itself. Equity sleeve only. De-risking forgoes the post-event drift
(PEAD-adjacent upside) — that cost is exactly what L1 measures.
