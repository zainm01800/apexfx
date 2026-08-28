# Book R — USD ETF Momentum Control

**Frozen on:** 2026-08-28 before the Book R result script is run.
**Status:** research-only control; not funded, not connected to a broker, and not
eligible to replace A, B, or C without the promotion gates below.

## Why this book exists

The A/B/C review identified two separate problems that must not be confused:

1. A's short sleeve has performed badly in its brief live paper sample, but that
   is not enough evidence to invert a strategy.  The existing historical
   short-veto experiment also failed its overfitting gate.
2. The multi-asset engine currently labels raw quote-currency P&L as GBP.  That
   makes a GBP ranking across USD equities, crypto, and FX crosses unsuitable
   until an as-of currency conversion layer is added and the historical series
   is recomputed.

Book R is a narrow USD-account control that avoids both traps.  It does not
claim to be SMC/Forex: the cached 1m/5m FX data is far too short for an honest
multi-year SMC test, and subjective chart zones are not mechanically
backtestable.  A separate SMC project requires versioned bid/ask or tick data,
fully specified zone/trigger rules, and a true lockbox.

## Fixed account and universe

- Account currency: **USD**.
- Initial NAV: **$100,000**.
- Instruments, fixed alphabetically in the implementation:
  `GLD, IWM, QQQ, SMH, SOXX, SPY, TLT, XBI, XLE, XLK`.
- These are explicitly declared US-listed, USD-quoted ETFs.  No FX crosses,
  crypto, foreign listings, CFDs, futures multipliers, or single stocks are
  allowed.
- Economic clusters are fixed before results: broad equity (`SPY/QQQ/IWM`),
  technology (`XLK/SMH/SOXX`), gold (`GLD`), rates (`TLT`), energy (`XLE`), and
  biotech (`XBI`).  At most one ETF per cluster can be held.
- Input data is daily cached OHLCV.  The run writes a SHA-256 manifest for every
  parquet input.  It is a **price-return** test, not a verified total-return
  test; dividends are not reconstructed.

## Fixed execution and risk convention

1. At the close of the final common session of a calendar month, calculate each
   ETF's lookback return divided by its 63-session log-return volatility.
2. Exclude an ETF unless its raw lookback return is positive.  This is an
   absolute-momentum cash gate, not a reversal or a short signal.
3. Rank the remaining ETFs by score.  Hold up to three, observing the fixed
   one-per-cluster cap, at equal weights.
4. The decision is filled at the **next common trading-session open**.  There is
   no same-bar close fill and no intrabar stop/target assumption.
5. Target gross exposure is 95%; the remaining 5% is cash.  No shorting,
   borrowing, or leverage is allowed.
6. Apply 5 bps per side to all notional changes.  The research selection also
   tests a 2x cost stress of 10 bps per side.  The final marked portfolio pays a
   close liquidation cost so it is not a free-exit result.

The only candidates are:

| Candidate | Lookback | Other parameters |
|---|---:|---|
| R-63 | 63 sessions | 63 vol, 3 slots, 95% gross, 5 bps/side |
| R-126 | 126 sessions | same |
| R-252 | 252 sessions | same |

No other model, universe, stop, filter, signal, cost, or sizing variant may be
introduced in this study after this document is frozen.

## Date protocol and selection rule

This repository's post-2025 data was already examined by earlier research, so
no in-repository historical period can honestly be called a true blind lockbox.
The run must use these labels instead:

| Segment | Dates | Use |
|---|---|---|
| Research selection | 2016-01-04–2022-12-30 | Select exactly one fixed candidate |
| Retrospective validation | 2023-01-03–2024-12-31 | Report only for the selected candidate |
| Known-data replication | 2025-01-02–latest complete cached session | Report only; never call it blind/OOS |
| True blind evidence | first completed session after this freeze onward | Forward paper / separately held vendor lockbox |

Selection uses **only** the research-selection segment.  A candidate is eligible
only if all conditions hold:

- positive base-cost and 2x-cost total return;
- at least four positive calendar-year blocks;
- maximum drawdown no greater than 25%; and
- at least 48 scheduled selections.

Among eligible candidates, select highest 2x-cost Calmar; break ties by lower
base-cost drawdown, then longer lookback, then candidate name.  If none are
eligible, no Book R candidate is selected and the study must report failure.

## Non-negotiable promotion gates

A selected historical candidate is still **not a winner**.  It is only a
research candidate until all are satisfied:

1. positive retrospective-validation return after 2x costs, validation max DD
   no worse than 25%, and no single ETF or calendar year dominates results;
2. no code/data modification after the frozen manifest and candidate selection;
3. an externally held unseen historical lockbox or meaningful forward paper
   evidence; and
4. before any multi-currency comparison or live sizing, a tested account-currency
   conversion layer, reconciled NAV, explicit fee model, and batch/paper parity.

The generated output must state that failing any gate means **do not promote or
fund Book R**.
