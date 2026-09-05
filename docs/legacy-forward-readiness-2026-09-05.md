# Legacy book forward-readiness audit — 5 September 2026

This is an engineering and saved-ledger audit, not a new backtest or funded-account certification. Existing histories and strategies have not been altered. A working website is not proof of a working forward engine.

## Public snapshot findings

Read-only source: `/api/paper?book={book}&table=state` on apexfx.vercel.app.

| Book | Latest equity date | Reported equity | Readiness finding |
| --- | --- | --- | --- |
| A | 2026-09-02 | GBP-labelled 95,199.02 | Not reliable GBP performance: raw quote-currency P&L is summed without conversion. |
| B | 2026-09-02 | GBP-labelled 99,387.61 | Same currency-accounting defect. |
| C | 2026-09-02 | GBP-labelled 99,403.92 | Same currency-accounting defect. |
| R | 2026-09-02 | USD 100,986.597972 | Cash plus marked holdings reconciles; forward updating still unverified. No position stops/targets by design. |
| S | 2026-09-04 | USD 106,236.14 | Backfilled history, FX sizing and restart-state accounting defects; not untouched forward evidence. |
| F | 2026-08-19 | USD 100,198.03 | Stale equity history and incorrect added-lot entry accounting; not reliable performance evidence. |

These are the stored claims, not independently corrected profit figures. Fresh timestamps must not automatically clear the findings.

## Reconciliation and runner evidence

- A/B/C's raw `(mark - entry) * units`, direction-adjusted, sums to 135.618628, 28.483789 and -353.202998 respectively. These match equity minus cash to rounding despite different instrument quote currencies. `Portfolio._unrealized` and `_pnl` in `engine/apex_quant/backtest/portfolio.py` return raw price difference times units without FX conversion; `paper.py` adds this to account equity.
- R's cash plus USD holdings agrees with its saved equity within 0.000001 USD. This checks arithmetic only, not execution, data freshness or funded rules.
- S's rounded closed-trade sum is USD 6,236.16 versus a saved cash gain of USD 6,236.14. Matching rounded sums does not validate the FX unit sizing or forward provenance. The independent 4 September audit found restart/call-partition-dependent results and no untouched forward trades.
- F's saved unrealized 28.026859 plus partial realized 170 explains the reported gain. However, `book_f_forward.py` increases units at the pyramid trigger without recording a separate entry price for the added lot, crediting it with movement before it existed.
- GitHub `paper-portfolio` run 33824827778 reported success on 4 September, but A/B/C made no progress beyond 2 September and R had no new common sessions. A green workflow is not evidence of fresh processing.
- The checked-in legacy workflow includes A/B/C/R/F, with B/F allowed to fail without failing the job. No S writer is present in that workflow. This does not rule out other externally configured runners.
- Legacy restore paths can fall back to local or historical seeding. Blindly rerunning writers is not a safe repair and could contaminate provenance.

## Existing evidence inspected

- `engine/data_store/validation/book_abc_book_r_audit_2026-08-28.md`
- `tmp/research/book_s_independent_audit_verdict_2026-09-04.md`
- `tmp/research/book_f_corrected_findings_2026-09-04.md`

## Changes and remaining work

The website now labels legacy totals as reported, shows dated book-specific audit findings, and does not translate successful loading into a forward-ready status. Saved SMC `take_profit` fields are displayed when present. Closed-P&L labels distinguish fully closed trades from partial realization on open trades.

No writer was rerun, ledger reset, live/broker trading enabled, or strategy replaced during this audit. V6/V10 remain separate experimental forward-paper books; their historical validation failed and neither is funded-approved.

Before certifying any repaired legacy book: fix accounting and execution defects, verify restart and batch invariance, verify fresh settled-session data, enforce durable fail-closed restore and idempotency, then prove scheduled end-to-end persistence against an isolated paper ledger. Preserve current histories as clearly labelled archives. Obtain the user's choice before creating replacement accounts or restarting histories; do not silently rewrite them.
