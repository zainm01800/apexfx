# Book U Cluster-Balanced Trend — Frozen Research Protocol

**Frozen on:** 2026-09-03, before Book U code is run on any price history.

**Status:** a new, isolated research candidate. It does not modify or inherit
the statistics, paper state, website state, or live configuration of Books A,
B, C, or R. Historical success can qualify it only for an unchanged shadow
paper trial; it cannot establish that it is ready for a paid funded account.

## Research question

Can a deliberately broad, cluster-balanced momentum portfolio retain enough of
Book R's trend return while reducing the single-stock/technology concentration,
drawdown, and one-day loss that make Book C unsuitable for a funded-account
claim?

This is a mechanism test, not another Book C risk-scalar search. The Book C
0.75%-1.00% scalar range has already been inspected. Book U changes the signal
universe, selection rule, portfolio construction, and loss budget.

## Frozen product target and evidence ceiling

The eventual product target is a **USD 100,000 FTMO Challenge: 2-Step Swing**
account. The research simulator will encode the currently published 10% then 5%
profit targets, 5% maximum daily loss, 10% static maximum loss, four minimum
trading days, and a CE(S)T firm-day boundary.

ETF bars are only USD proxy data. They are not the exact FTMO CFD feed and do
not contain executable bid/ask quotes, contract multipliers, lot steps, swaps,
financing, dividend adjustments, platform trading hours, rejected orders, or
stop acknowledgements. Therefore the only possible historical statuses are:

- `NO_RESEARCH_CANDIDATE`
- `DATA_BLOCKED`
- `SHADOW_ELIGIBLE`

`FUNDED_READY` is forbidden. Paid-account promotion additionally requires an
exact platform symbol/contract map, executable intraday data and at least six
unchanged forward months plus 100 closed holding episodes with zero rule or
internal-guard breaches.

## Frozen universe and economic clusters

Use only the existing Book R USD-listed ETF whitelist. Every instrument is
quoted in USD, preventing the mixed-quote-currency accounting defect in the
multi-asset Book C backtest.

| Cluster | Eligible ETFs | Maximum held |
|---|---|---:|
| Broad equity | SPY, QQQ, IWM | 1 |
| Technology | XLK, SMH, SOXX | 1 |
| Gold | GLD | 1 |
| Rates | TLT | 1 |
| Energy | XLE | 1 |
| Biotech | XBI | 1 |

All positive clusters are held, so the maximum is six positions. There is no
post-hoc top-*k*. Ties are broken by instrument symbol. The common-session
intersection is mandatory; stale-price forward filling is prohibited.

## Frozen signal and execution clock

1. Form a decision at the final common-session close of each calendar month.
2. For every ETF, compute its 252-session close-to-close return and the sample
   standard deviation of its last 63 daily log returns, using data available at
   that close only.
3. The score is `252_session_return / daily_volatility`. Within each fixed
   cluster, select the highest score only when its 252-session return is
   strictly positive. Hold the positive winner from every cluster.
4. Fill the complete simultaneous rebalance at the next common-session open.
   Sells precede buys only for cash bookkeeping; all target sizes use the same
   pre-trade equity snapshot and are invariant to input order.
5. A stopped cluster remains in cash until a later month-end decision. There is
   no same-day or mid-month re-entry.

The signal has no fitted model, mutable threshold, fundamental/LLM override, or
current-constituent stock selection.

## Frozen portfolio construction

At each decision close, calculate annualized 63-session volatilities and the
annualized sample covariance matrix for the selected ETFs.

1. Start with inverse-volatility weights, normalized to sum to one.
2. Calculate the projected portfolio volatility from the contemporaneous
   covariance matrix.
3. Scale the whole vector to a **6.00% annual portfolio-volatility target**, but
   never above **95% gross exposure**.
4. No single target notional may exceed **25% of contemporaneous equity**.
5. Cash earns zero interest and borrowing is prohibited.

The 6% target is a policy-derived funded-loss-budget constant, not a searched
parameter. Covariance, volatilities, scores, ATR, and target sizes must be finite
or the affected order is blocked.

## Frozen stop and loss-risk accounting

- ATR is the trailing 20-session simple mean of true range.
- Initial long stop is next-open fill minus **2.5 x decision-close ATR(20)**.
- At a monthly rebalance, a retained position's stop may ratchet upward to the
  newly calculated level but may never loosen.
- An opening gap at or below a resting stop fills at the worse open. Otherwise,
  a daily low at or below the stop fills at the stop. A stress run then applies
  its additional adverse stop slippage.
- There is no fixed take-profit or partial. Trend profits end on a resting stop,
  loss of monthly positive-cluster leadership, or terminal liquidation. This is
  explicit exit logic, not an unbounded position.
- Every final position is liquidated at the last close with transaction cost.

The architecture gate uses a **0.75% maximum planned loss per leg** and a
**2.25% maximum aggregate planned loss**. Capital `C` is
`min(pre_trade_executable_equity, 100000)`.

Planned loss includes ordinary costs:

```text
planned_loss =
    units * (entry_price - stop_price)
  + conservative_entry_cost
  + estimated_stop_exit_cost
```

Units are the minimum of volatility-target units, the 25% notional cap, and
`0.75% * C / planned_loss_per_unit`. If the simultaneous portfolio remains over
2.25% of `C`, every target is scaled by one common factor. Existing positions,
new targets, and pending next-open orders share this single reservation. A
position that becomes over-budget because of a gap or price move must be reduced
at the next executable open; it may not silently retain stale risk.

## Frozen costs and stresses

Base research costs are 5 basis points per side. The binding stress is 10 basis
points per side plus 25 basis points of adverse slippage on every stop exit.
Opening gaps are applied before stop slippage. A separate winner-haircut stress
reduces positive completed-episode P&L by 50% while leaving losses unchanged.

Missing exact CFD commissions, swaps, financing, dividends, bid/ask history, or
contract conversion never default to zero for the readiness decision; they keep
the result `DATA_BLOCKED` above shadow-paper status.

## Evidence partitions

Every segment starts flat at USD 100,000; earlier prices may warm the frozen
indicators but no position or P&L carries into a segment.

| Label | Dates | Permitted use |
|---|---|---|
| Development | 2016-01-04 to 2022-12-30 | engineering and architecture diagnosis |
| Retrospective validation | 2023-01-03 to 2024-12-31 | unchanged pass/fail check, but already research-contaminated |
| Known-data replication | 2025-01-02 onward | descriptive only |
| Sealed historical robustness | 2010-01-04 to 2015-12-31 | downloaded only after this protocol is committed and opened once |
| True blind | sessions after this freeze | required forward evidence |

The sealed historical block is not called forward out-of-sample: it precedes
the design period and is a one-shot, reverse-time robustness test. Its exact
download, adjustment policy, bytes, coverage and hashes must be recorded before
its result is reported.

## Architecture gate

Book U at 0.75% advances to the conditional risk frontier only if every
applicable item below passes:

1. Full available-history base annualized return >= 5.0%, Sharpe >= 0.90,
   maximum drawdown <= 8.0%, and conservative co-extreme worst day >= -1.50%.
2. Full-history binding-stress annualized return >= 2.5%, Sharpe >= 0.65,
   maximum drawdown <= 9.0%, and total return > 0.
3. Retrospective-validation base annualized return >= 4.0%, Sharpe >= 0.75,
   maximum drawdown <= 8.0%; its stressed total return remains positive.
4. The sealed historical robustness block has positive base and stressed total
   return, stressed Sharpe > 0, and maximum drawdown <= 10.0%.
5. At least four of the five fixed two-year blocks 2016-17, 2018-19, 2020-21,
   2022-23, and 2024-25 have positive stressed return.
6. At least 12 of 15 fixed six-group CPCV test combinations have positive
   per-period Sharpe. Because the signal is rule-based and unfitted, the test
   restricts the single causal, terminally reconciled return stream to each
   frozen combination; it does not select or refit parameters.
7. Deflated Sharpe Ratio >= 0.95 using the complete declared project trial count
   plus every Book U cell. If compatible historical trial-Sharpe dispersion is
   unavailable, this item is `DATA_BLOCKED`, never imputed as a pass.
8. No cluster supplies more than 35% of positive net P&L, and the aggregate
   result remains positive after removing the top-profit cluster.
9. Base, stress, and winner-haircut results are positive; planned leg and
   aggregate risk never exceed their frozen caps; gross and position caps never
   overrun; and the conservative daily/static rule replay records zero breaches.
10. Future-poison, input-order permutation, gap-stop, entry-day stop, terminal
    liquidation, cost-inclusive sizing, and fresh-segment tests all pass.

Failure of any numerical gate means `NO_RESEARCH_CANDIDATE`. Missing execution
truth means `DATA_BLOCKED` for funded readiness even when every historical
numerical gate passes.

## Conditional 0.75%-1.00% risk frontier

Only if the 0.75% architecture passes, run exactly these three cells with every
other signal, stop, cost, and portfolio rule unchanged:

| Cell | Per-leg maximum | Aggregate planned-loss maximum |
|---|---:|---:|
| U075 | 0.75% | 2.25% |
| U085 | 0.85% | 2.55% |
| U100 | 1.00% | 3.00% |

Select the highest stressed Calmar subject to zero conservative funded-rule
breaches, maximum drawdown <= 8%, worst conservative day >= -1.50%, positive
winner-haircut return, and at least 70% stressed/base return retention. If two
Calmars are within 5%, choose the lower-risk cell. Validation data cannot change
the architecture or any constant. Any other risk, ATR, lookback, volatility
target, universe, top-*k*, take-profit, or exit variant requires a new protocol
and a new forward clock.

## Controls and claims

The unchanged Book R-252 equal-weight/no-stop and its exposure-matched control
may be reported on the same USD panel. They are non-selectable controls. Existing
Book C statistics may be displayed only as separately scoped historical
references because Book C mixes quote currencies and a different universe; no
paired superiority claim against Book C is permitted from those numbers.

The final report must distinguish nominal risk ceilings from realized risk,
base from stressed results, retrospective from genuinely future evidence, and
research profitability from funded-account readiness.
