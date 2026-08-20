# Pre-registration — Book C risk frontier (2026-08-20)

## Question

Find the best strictly interior compromise between certified Book C at 1.00% risk per
trade and the audited defensive variant at 0.75%, balancing total return and maximum
drawdown without changing signals, exits, universe, costs, or any other constraint.

## Frozen iteration data and engine

- Data: cached 39-instrument panel, strictly before 2025-01-01.
- Book: multi-horizon `[63, 126, 252]`, all other certified Book C parameters unchanged.
- Calendar metrics: 365 periods/year; legacy 252 figures are not used for selection.
- New grid, fixed before execution: **0.80%, 0.825%, 0.85%, 0.875%, 0.90%, 0.925%, 0.95%**.
- Previously measured anchors: 0.75% and 1.00%.
- Each new grid point is recorded in the trial ledger before its result is observed.

## Selection rule

For each point, normalize against the two anchors:

- `return_progress = (return - return_075) / (return_100 - return_075)`
- `drawdown_progress = (dd_100 - dd) / (dd_100 - dd_075)`

The ideal point would have `(return_progress, drawdown_progress) = (1, 1)`. Select the
strictly interior point minimizing Euclidean distance to that ideal. This makes return
retention and drawdown reduction equally important. Ties within `1e-12` go to lower risk.

## Post-selection checks

The selected point is a useful compromise only if all hold:

1. Total return and average monthly P&L exceed the 0.75% anchor.
2. Maximum drawdown is lower than the 1.00% control.
3. Paired block-bootstrap probability of improvement is reported, not used to redefine
   the already-frozen compromise objective.
4. DSR is reported using the full ledger count.
5. CPCV: at least 12/15 paths positive and at least 8/15 paths beat control.
6. Doubled-cost Sharpe remains positive and no more than 0.10 below its base Sharpe.
7. Post-2024 confirmation remains positive. It is explicitly non-blind.
8. Rolling funded-account proxies are reported with the same limitations as the deep audit.

No second grid, interpolation, or parameter refinement is allowed after seeing results.
