# Pre-registration — Funded-100K strategy and account-safety gate (2026-09-03)

## Status and scope

This protocol was frozen before any new Funded-100K candidate result was run. It does
not alter Books A, B, C, or R, and it does not authorize broker or paid-challenge
deployment. The only permissible retrospective promotion is to a new, isolated
`funded_100k_shadow` paper account seeded at 100,000 account-currency units.

There is no universal "funded account" rule set. Until the owner identifies the exact
firm, product, account currency, platform, and executable symbols, the primary research
envelope is deliberately strict:

- 3.00% official daily loss allowance, measured on live equity including floating P&L,
  fees, financing, and swaps;
- 10.00% official maximum-loss allowance, evaluated both as a static floor and as an
  end-of-day trailing floor in separate scenarios;
- a firm-day reset in an explicit IANA timezone (the FTMO-shaped scenario uses
  `Europe/Prague`);
- no claim of weekend/news/automation compatibility until the exact contract is frozen.

The current FTMO 2-Step Swing structure is the closest named operational fit for a
multi-day strategy, but this synthetic 3%/10% envelope is stricter on daily loss and is
not a representation that FTMO, The5ers, or another firm offers identical terms.

## Existing controls (ineligible sanity checks)

1. `C085_CONTROL`: current Book C `[63, 126, 252]`, 0.85% maximum planned risk per
   instrument, current managed exits and current universe/order. It is ineligible because
   existing evidence shows 14.01% maximum drawdown and a -3.68% worst close-to-close day.
2. `R095_CONTROL`: current Book R-252. It is ineligible because existing evidence shows
   23.63% maximum drawdown and a -6.54% worst day. The already-rejected ATR stop overlay
   is not revived.

## Eligible candidates (bounded before execution)

### `C_FUNDED`

The signal is frozen Book C: multi-horizon `[63, 126, 252]` momentum, weekly 50-bar
higher-timeframe alignment, 21-session holding horizon, 1.5 reward/risk signal target,
rule-based regime, the existing gap-aware managed exits and existing transaction-cost
model. The signal, lookbacks, exit parameters, and instrument data are not tuned here.

The candidate differs only in funded risk geometry:

- same-bar candidates are ranked by point-in-time expected value with instrument-name
  tie-breaks;
- the aggregate stop-risk budget is shared simultaneously, not consumed in panel order;
- the risk scale is calculated once from build data by the formula below;
- aggregate planned stop loss is capped at 35% of the 3% daily allowance (1.05% of
  account equity);
- a single instrument's stressed loss is capped at 15% of the daily allowance (0.45%);
- gross exposure is capped at 0.75x equity, correlated gross exposure at 0.30x, each
  position at 10% notional, and concurrent positions at five;
- new risk is blocked at a 1.20% firm-day loss, all pending entries are cancelled and the
  book is flattened at 1.80%, and the cycle is latched off at 6.00% drawdown;
- the candidate cannot re-enter during a halted firm day and cannot clear a cycle halt
  without explicit acknowledgement.

### `C_FUNDED_075_SCALE` (robustness only)

Exactly `C_FUNDED` at 75% of its build-calibrated risk scale. It cannot become the winner;
it exists only to show whether conclusions are stable under further de-leveraging.

### `R_FUNDED` (conditional diagnostic)

Frozen Book R-252 signals/rebalancing with uniform build-calibrated de-leveraging and the
same account guard. No ATR stop or other exit is added after its rejection. It is
automatically ineligible unless the selected firm offers every exact traded instrument
and the strategy remains profitable at the safe scale.

### `PLATFORM_NATIVE_DIVERSIFIED_TREND` (data-blocked candidate)

A simple, point-in-time, diversified time-series trend strategy across only the chosen
firm's exact equity indices, commodities/metals, major FX, and any other contract-approved
markets. It excludes present-day-selected individual stocks and crypto by default. Its
parameters and universe must be frozen only after the provider symbol list and raw
bid/ask history are obtained. It receives no result and cannot pass in this run; inventing
ETF proxies after seeing their history is prohibited.

No other family, lookback, stop multiple, risk point, universe deletion, or post-result
refinement is allowed under this registration.

## Build-only scale calibration

For each eligible implemented family, first run the unscaled frozen strategy on build
data only (2016-01-01 through 2020-12-31). Define:

- `L_day`: 99.9th percentile of positive daily loss;
- `D_1y`: 99th percentile rolling one-year maximum drawdown;
- `L_gap`: worst single-instrument loss after doubling the observed entry/overnight gap
  component and doubling ordinary costs.

Then set, once:

```text
scale = floor_to_0.05(min(
    1.00,
    0.50 * 0.03 / L_day,
    0.50 * 0.10 / D_1y,
    0.35 * 0.03 / L_gap
))
```

`floor_to_0.05` rounds down to the nearest 0.05. Validation data may never recalibrate
the scale. A non-finite or non-positive denominator is a hard error, not a reason to use
full size.

## Safety guard (fixed, not optimized)

The account guard is evaluated independently of whether the current signal is flat,
already held, or a new entry:

1. Resolve the firm session in the profile's timezone.
2. Read account-scoped persisted opening balance/equity and the latched session/cycle
   state. Missing, corrupt, stale, or unwritable safety state after initialization fails
   closed.
3. Mark all positions at executable bid/ask and convert to account currency. Invalid or
   stale account/price/FX state fails closed.
4. At 40% of the official daily allowance, block new orders.
5. At 60%, cancel pending entries, flatten, verify fills, and preserve the session latch.
6. At 60% of the official maximum-loss allowance, flatten and latch the entire cycle off
   until explicit owner acknowledgement.
7. Continue evaluating the official floor during liquidation; a requested close is not a
   successful close.

Sizing uses the smaller of current equity, initial balance, and the remaining official
loss buffer. Planned portfolio stop loss may not exceed 35% of the daily allowance, and
stressed one-symbol loss may not exceed 15%.

## Evidence partitions

- Build/calibration: 2016-01-01 through 2020-12-31.
- Model-selection audit: 2021-01-01 through 2022-12-31.
- Frozen pseudo-out-of-sample validation: 2023-01-01 through 2024-12-31.
- Previously inspected confirmation only: 2025-01-01 through the latest frozen local bar.
- Genuine blind evidence: observations arriving after this registration and code/data
  manifest freeze.

All existing historical files were accessible before this protocol. Therefore none of
the historical partitions may be described as a truly blind test.

## Required deterministic replay

The primary test is chronological and rolling-start replay, not trade-level iid Monte
Carlo. Each firm-clock event must include live equity, closed balance, floating P&L, all
costs, the opening gap, stop-market execution at the first available price, partial/rejected
fills, and account-currency conversion. If stop and target order is unresolved inside one
bar, the stop is assumed first.

Where only daily OHLC exists, the audit must additionally mark every remaining long at
the day's low and every short at the day's high simultaneously. This is labelled a
conservative lower bound on intraday equity, not an observed path. A daily-only result is
automatically `PROVISIONAL_PAPER_ONLY` regardless of its statistics.

## Supplemental path simulation

Use 100,000 common-random-number paths per candidate/scenario. Resample synchronized
whole firm-day records with stationary bootstrap mean block lengths 5, 10, and 21; never
sample individual trades or closure counts independently. Preserve opening gaps,
cross-asset dependence, intraday minimum equity, closed P&L, costs, and trade counts as a
single row. Report Wilson 95% confidence intervals.

Scenarios are base; doubled costs; 1.5x volatility with 2x gaps; one 30-minute liquidation
outage per year; 50% fills in the worst-liquidity sessions; one missed stop per year;
winners cut by 50% with losses unchanged; and a combined severe case.

## Binding historical gates

A candidate passes only if every applicable gate passes:

- zero official-rule breaches in base build, selection, and validation replay;
- worst base intraday daily loss no greater than 60% of the official daily allowance;
- worst base drawdown no greater than 60% of the official maximum-loss allowance;
- zero official breach in combined historical stress replay;
- lower Wilson 95% bound on reaching the evaluation target within 252 sessions at least
  70%, and upper bound on evaluation hard-breach probability at most 5%;
- lower Wilson 95% funded-survival bound at least 99% over 12 months and 97.5% over 24
  months; combined-stress 12-month lower bound at least 95%;
- validation Sharpe at least 0.75 and profit factor at least 1.15 after costs;
- positive validation return with doubled costs and when winners are cut by 50%;
- CPCV at least 12/15 positive paths with 21-session purge/embargo;
- DSR at least 0.95 using the complete dedicated funded-trial ledger, plus the repository's
  already-spent strategy-family count;
- PBO at most 0.25 across the bounded eligible set;
- positive result after removing the top profit-contributing cluster, with no instrument
  or cluster contributing more than 35% of profit;
- exact causal future-poison test and identical decisions/results under 100 input-order
  permutations.

The winner is the passing candidate with the highest lower 95% confidence bound on net
payout per month. A statistical tie goes to lower risk. If none passes, the verdict is
`NO_FUNDED_STRATEGY`; thresholds may not be weakened and the runner-up may not be
promoted as funded-ready.

## Data-adequacy and promotion rule

Firm/platform bid/ask history at one-minute resolution or better, an exact symbol/contract
map, variable spread/commission/swap schedules, account-currency conversion, news/weekend
rules, and automated-trading permission are mandatory for a funded-ready verdict. Yahoo
underlying daily bars cannot satisfy this gate.

A retrospective pass authorizes only the isolated shadow account. Paid challenge/live
promotion additionally requires at least six months and 100 completed forward trades
(twelve months for monthly Book R), zero rule breaches, no unexplained parity differences,
and no parameter change. Any change starts a new registration and forward clock.
