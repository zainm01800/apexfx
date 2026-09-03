# Book R-252 Stop Overlay — Frozen Research Protocol

**Frozen on:** 2026-09-03 before the stop-overlay result script is run.

**Status:** research-only challenger to the unchanged Book R-252 baseline.  This
protocol does not modify the live forward-paper book and cannot inherit the
baseline's historical statistics.

## Question

Can a causal emergency stop plus stop-distance position sizing materially reduce
Book R-252 drawdown without destroying its return or risk-adjusted performance?

## Data and segments

Use the same fixed USD ETF universe, economic clusters, common-session daily
OHLCV cache, month-end 252-session momentum signal, 63-session volatility rank,
next-session-open rebalance, and 5 bps-per-side base cost as frozen Book R-252.

The historical labels remain:

| Segment | Dates | Permitted use |
|---|---|---|
| Research | 2016-01-04–2022-12-30 | Evaluate the frozen primary and sensitivity variants |
| Retrospective validation | 2023-01-03–2024-12-31 | Apply unchanged; primary pass/fail segment |
| Known-data replication | 2025-01-02–2026-08-27 | Informational robustness only |
| True blind evidence | Sessions collected after this freeze | Forward paper only; currently too short for inference |

None of the local history is described as a true blind lockbox because it was
already accessible to this project.

### Frozen input snapshot

- Common panel: 2,678 sessions per ETF, 2016-01-04 through 2026-08-27.
- Snapshot: `engine/data_store/validation/book_r_stop_inputs_2026-09-03.parquet`
- Snapshot SHA-256:
  `efc75fb7056efe2d03d0cd13de955616882c2c7c54ac794c53cdfdbac0cc7974`
- Source manifest:
  `engine/data_store/validation/book_r_stop_inputs_2026-09-03.manifest.json`
- Manifest SHA-256:
  `d5c1f9b664e6ec5a520313f58946d4169907fc32498cf2101796e5f19f6fe1ee`

The result runner must load this snapshot, not the mutable daily-cache paths,
and fail if its hash differs.

## Frozen primary variant

- Base signal: unchanged R-252 selection.
- ATR: the simple trailing 20-session mean of true range, matching the existing
  A/B/C engine convention and calculated through the decision close only. True
  range is `max(high-low, abs(high-prev_close), abs(low-prev_close))`.
- Initial long stop: next-session fill price minus **2.5 × ATR(20)** known at the
  preceding decision close.
- Intended account risk: **0.85% of contemporaneous equity per selected ETF**.
- Units: `0.0085 × equity / stop_distance`, capped by the baseline equal-weight
  allocation and by 95% total gross exposure.  No borrowing or leverage.
- A retained ETF receives a newly calculated monthly stop only when that stop is
  higher than its existing stop; stops never loosen.
- If the daily open is at or below the stop, sell at that open (gap loss is not
  capped at the stop).  Otherwise, if the daily low touches the stop, sell at
  the stop.  Apply transaction costs to every stop fill.
- After a stop, hold the proceeds in cash until a later month-end decision.  Do
  not immediately re-enter on the same pending rebalance.
- Other exit: unchanged monthly rank/absolute-momentum rebalance.
- Fixed take-profit and partial-profit exits: none.  The experiment isolates the
  stop and sizing question and lets momentum winners run.

## Frozen sensitivity variants

Run **2.0 × ATR(20)** and **3.0 × ATR(20)** with every other rule identical.
They are robustness checks only.  The primary 2.5× result will not be replaced
by whichever neighbour looks best.

## Frozen decomposition controls

Two diagnostics prevent a lower drawdown being mislabelled as a stop benefit
when it is merely lower market exposure:

1. **Stop-only control:** 2.5×ATR(20) with the baseline's equal-weight 95%-gross
   sizing and no 0.85% risk-size cap.
2. **Exposure-matched no-stop control:** unchanged R-252 rerun with its gross
   target set to the primary overlay's realised average gross exposure in that
   segment.  This is an explicitly ex-post diagnostic, never a selectable
   candidate or validation gate.

Report both beside the baseline and primary package.  Neither can replace the
predeclared primary overlay after results are visible.

## Execution stresses

- Base: 5 bps per side, zero extra stop slippage.
- Stress: 10 bps per side plus 25 bps adverse slippage on stop exits.  Opening
  gaps still fill from the worse opening price before the extra slippage.
- Final positions pay a liquidation cost at the last close.
- Dividends and cash interest remain excluded; results are price-return tests.

## Frozen validation gates

The primary variant passes only if all of the following hold on the independent
2023–2024 retrospective-validation segment:

1. base-cost maximum drawdown is at least 20% lower than the unchanged baseline
   and no greater than 12%;
2. base-cost annualized return is at least 60% of the baseline;
3. base-cost Sharpe is no more than 0.10 below the baseline;
4. stressed total return remains positive; and
5. both 2.0× and 3.0× sensitivity variants have positive base-cost return and
   lower maximum drawdown than the unchanged baseline.

Also report, without adding post-hoc gates: total and annualized return, Sharpe,
Sortino, Calmar, maximum drawdown, worst day, worst month, drawdown durations,
turnover, costs, stop counts, gap-stop counts, average gross exposure, and
counts of 5%, 8%, 10%, and 12% high-water drawdown breaches.

Run five independent flat-start regime checks (2017–2018, 2019–2020,
2021–2022, 2023–2024, and 2025–2026-08-27), plus a paired circular 21-session
block bootstrap of baseline versus primary daily returns with 10,000 resamples
and seed 42.  These diagnose stability and uncertainty but do not override the
five frozen validation gates.  Report a Deflated Sharpe Ratio using an explicit
effective trial count of 372 as a conservative multiplicity diagnostic; it is
not a promotion gate for this already-designated primary challenger.

## Decision rule

- **PASS:** every frozen validation gate passes.  The overlay may proceed to a
  separate forward-paper challenger, but must not rewrite the existing Book R
  history or be called funded-ready.
- **FAIL:** any gate fails.  Do not deploy it.  Report the failure plainly; do
  not tune another stop or threshold on validation/replication data.

The multiplicity report must count the six original Book R selection cells
(three lookbacks × two cost assumptions), the primary and stop-only base/stress
cells, and both neighbour sensitivity cells.  Do not imply this is a one-shot
discovery.
