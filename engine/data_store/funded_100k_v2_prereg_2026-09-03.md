# Pre-registration — C_FUNDED_V2 cash-risk qualification (2026-09-03)

## Status and scope

This protocol is frozen before any V2 result is run. Source hashes, the effective
configuration, and the data manifest must be persisted before the first V2 backtest.

This is one isolated candidate with two predetermined operating modes. It does not
modify Books A, B, C, or R, does not authorize a paid challenge or broker deployment,
and may write only to a new `funded_100k_v2_shadow` paper account seeded at 100,000
account-currency units.

The retrospective envelope is a 3.00% official daily-loss allowance and a 10.00%
maximum-loss allowance, tested separately with static and completed-EOD-trailing
maximum-loss floors. Funded-ready status additionally requires the exact firm,
product, account currency, platform, symbol map, costs, swaps, timezone,
news/weekend rules, and automated-trading permission.

## Frozen strategy

Use Book C's frozen 39-instrument `[63, 126, 252]` momentum ensemble, weekly 50-bar
higher-timeframe alignment, rule-based regime, 21-session horizon, 1.5 signal
reward/risk, and its existing frozen stop/partial/breakeven/Chandelier/time-exit
implementation.

The only permitted V2 changes are:

- cash-risk sizing defined below;
- point-in-time expected-value slot ranking with instrument-name tie-breaks;
- simultaneous allocation of the remaining aggregate risk budget;
- funded-account guard and exposure caps; and
- immediate entry-bar stop enforcement, resolving an unobservable daily-bar
  stop/target ordering stop-first.

A protective venue-native stop must be acknowledged with the entry. If protection
cannot be confirmed, cancel or flatten immediately. Missing, invalid, stale, or
unmapped data fails closed. No instrument may be silently removed or substituted;
an invalid member makes the candidate data-blocked.

## Official buffers and cash-risk calculation

At every decision:

```text
C = min(current executable-marked equity, initial balance)
day_buffer = max(0, current equity - official daily-loss floor)
max_buffer = max(0, current equity - official maximum-loss floor)
```

Equity includes floating P&L, commissions, spread, slippage, swaps, and
account-currency conversion at executable bid/ask.

For the synthetic profile:

```text
daily floor = firm-session opening closed balance - 3% of initial balance
static maximum floor = 90% of initial balance
trailing maximum floor = highest completed EOD balance - 10% of initial balance
```

Firm-session state and completed-EOD balance must be authoritative and persisted.
Intraday equity or unconfirmed realised balance may never ratchet the trailing floor.

### Evaluation mode

Used only during challenge/evaluation phases:

```text
base planned stop risk per instrument = 0.35% × C
risk_cash = floor_down(min(
    base planned risk,
    15% × day_buffer,
    6% × max_buffer,
    remaining aggregate planned-stop budget
))
```

Additional limits:

- stressed single-symbol loss no greater than 0.45% of `C`;
- aggregate open-plus-pending planned stop loss no greater than 0.90% of `C`;
- gross exposure no greater than 0.60x equity;
- correlated/economic-cluster gross no greater than 0.20x equity;
- position notional no greater than 0.08x equity; and
- at most five concurrent positions.

Planned loss includes entry-to-stop loss plus ordinary entry/exit costs. Proposed
units must then be reduced until registered doubled-gap/doubled-cost stressed symbol
loss is within 0.45%.

### Funded/payout mode

Activated before the first order after the account becomes funded. It may never
switch automatically back to evaluation mode.

```text
base planned stop risk per instrument = 0.25% × C
risk_cash = floor_down(min(
    base planned risk,
    10% × day_buffer,
    4% × max_buffer,
    remaining aggregate planned-stop budget
))
```

Additional limits:

- stressed single-symbol loss no greater than 0.35% of `C`;
- aggregate open-plus-pending planned stop loss no greater than 0.60% of `C`;
- gross exposure no greater than 0.45x equity;
- correlated/economic-cluster gross no greater than 0.15x equity;
- position notional no greater than 0.06x equity; and
- at most four concurrent positions.

Withdrawals, payouts, or a new firm day do not restore risk automatically. Official
account state controls the buffers, while `C` may never exceed initial balance.

## Persistent guard

Evaluation mode:

- block new risk and cancel pending entries at a 0.90% firm-day loss;
- cancel pending orders, flatten, verify fills, and latch the session at 1.50%; and
- flatten and latch the cycle at 5.00% peak-to-current drawdown.

Funded/payout mode:

- block new risk and cancel pending entries at a 0.60% firm-day loss;
- cancel pending orders, flatten, verify fills, and latch the session at 1.20%; and
- flatten and latch the cycle at 4.00% peak-to-current drawdown.

The guard runs independently of signal state. Missing/corrupt/unwritable state,
stale quotes, failed FX conversion, rejected cancellations, partial liquidation, or
unverified positions fails closed. A cycle halt requires explicit owner
acknowledgement; a new day cannot clear it. Recovery after any official breach does
not erase the breach.

## Evidence protocol

Every partition starts flat at 100,000. Earlier bars may warm indicators but may not
contribute positions, balance, or P&L.

- build sanity only: 2016-01-01 through 2020-12-31;
- sealed interim audit: 2021-01-01 through 2022-12-31;
- frozen pseudo-OOS validation: 2023-01-01 through 2024-12-31;
- previously known confirmation only: 2025-01-01 onward; and
- genuine blind evidence: observations arriving after this freeze.

Run both modes as fixed cells. There is no parameter grid, scale calibration, winner
selection, or retry. All data/order permutations use identical rules. Any parameter,
universe, exit, threshold, or cost change requires a new registration and forward
clock.

## Required replay and stress

Chronological replay must retain synchronized firm-day balance, executable equity,
conservative intraday minimum, gaps, costs, FX conversion, open/pending risk, and
per-symbol loss. With daily OHLC, mark all longs at daily lows and shorts at daily
highs simultaneously; such results can be no better than `PROVISIONAL_PAPER_ONLY`.

Supplement with 100,000 synchronized whole-day stationary-bootstrap paths at mean
block lengths 5, 10, and 21 under base execution, doubled costs, 1.5x volatility with
2x gaps, one 30-minute liquidation outage per year, 50% fills on worst-liquidity
sessions, one missed stop per year, winners reduced 50% with losses unchanged, and a
combined severe stress.

## Non-negotiable pass gates

Common to both modes:

- zero official breaches in base build, interim, and validation replay;
- zero official breaches in combined historical stress;
- validation Sharpe at least 0.75 and profit factor at least 1.15;
- positive validation return under doubled costs and the exact winner haircut;
- at least 12 of 15 positive purged CPCV paths;
- DSR at least 0.95 using a fixed, complete prior-trial Sharpe reference; missing
  prior Sharpe history is `DATA_BLOCKED`, never imputed;
- positive result after removing the top profit cluster;
- no instrument or cluster contributes more than 35% of net profit;
- exact future-poison causality; and
- identical decisions and results under 100 input-order permutations.

Evaluation-mode gates:

- worst conservative intraday firm-day loss no greater than 1.50%;
- maximum drawdown no greater than 5.00%;
- lower Wilson 95% bound on reaching +10% within 252 sessions at least 70%; and
- upper Wilson 95% bound on hard-breach probability no greater than 5%.

Funded/payout-mode gates:

- worst conservative intraday firm-day loss no greater than 1.20%;
- maximum drawdown no greater than 4.00%;
- lower Wilson survival bound at least 99% over 12 months and 97.5% over 24 months;
- combined-stress 12-month survival lower bound at least 95%;
- validation annualized return at least 4% after base costs and at least 2% under
  doubled costs; and
- lower 95% confidence bound on mean monthly return greater than zero.

PBO is `N/A_SINGLE_FIXED_CANDIDATE`; it must not be manufactured from the two
operating modes. Prior search multiplicity is charged through DSR.

## Decision rule

Both modes and every applicable gate must pass. Otherwise the verdict is:

```text
NO_FUNDED_STRATEGY_V2
```

A retrospective pass authorizes only isolated parallel shadow paper. Paid deployment
additionally requires exact one-minute-or-better firm bid/ask replay and at least six
unchanged forward months plus 100 completed trades, with zero official breaches and
zero unexplained paper/backtest/live parity differences.
