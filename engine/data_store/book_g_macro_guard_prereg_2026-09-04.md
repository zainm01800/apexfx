# Book G Macro Guard - Frozen IS/OOS Research Protocol

**Frozen on:** 2026-09-04, before downloading or inspecting the Book G
2020-2026 outcome.

**Status:** isolated research candidate for a USD 100,000 FTMO-shaped paper
account. It does not replace, repair, or inherit the published statistics or
paper state of Books A, B, C, F, R, or U.

## Claim ceiling

The 2020-2026 block is a sealed, one-shot holdout for this implementation, not
genuinely unknown history to its human designer. A historical pass may qualify
Book G only for an unchanged forward paper trial. Daily Yahoo bars do not
contain the complete CE(S)T midnight-to-midnight bid/ask equity path, platform
spreads, swaps, rejected orders, or stop acknowledgements. Therefore
`FUNDED_READY` and `FTMO_PASS_PROVEN` are forbidden conclusions.

Allowed final statuses are:

- `NO_RESEARCH_CANDIDATE`
- `HISTORICAL_GATE_PASS_DATA_LIMITED`
- `DATA_BLOCKED`

## Frozen data request

Download with `yfinance` once using `auto_adjust=True`, `actions=False`,
`repair=False`, `threads=False`, daily interval, start `2014-01-01`, and
exclusive end `2026-09-04`. The final eligible outcome date is therefore
2026-09-03. Preserve one normalized parquet snapshot plus a manifest containing
the request, package version, retrieval time, coverage, file hash, and per-symbol
row counts. A missing symbol or missing OHLC value on an expected XNYS session
is fail-closed; no forward filling is permitted.

`SPY` is a regime input only. The fixed tradable universe is:

- equity sectors: `XLK, XLE, XLV, XLI, XLF, XLP, XLU`
- defensive assets: `GLD, TLT, IEF, SHY, UUP`

All instruments are US-listed and USD-quoted. The universe is defined before
the snapshot and contains no individual stocks or current-winner screen.

## Evidence partitions

Indicators may use earlier warm-up rows, but each evaluation account starts
flat at USD 100,000 and carries no positions or P&L from another segment.

| Segment | Dates | Permitted use |
|---|---|---|
| Warm-up | 2014-01-01 to 2014-12-31 | indicators only |
| In-sample | 2015-01-01 to 2019-12-31 | choose one momentum horizon |
| Sealed OOS | 2020-01-01 to 2026-09-03 | opened exactly once after selection is committed |
| True forward | after 2026-09-04 | required for any funded-readiness claim |

## Frozen candidate family and selection

Exactly three IS candidates are permitted. Their only difference is momentum
lookback `L in {63, 126, 252}` XNYS sessions. No OOS statistic may influence
selection.

For asset `i` at decision close `t`:

```text
momentum_i(t, L) = close_i(t) / close_i(t-L) - 1
vol_i(t) = stdev(log(close_i / close_i[-1]), trailing 63 sessions) * sqrt(252)
score_i(t, L) = momentum_i(t, L) / max(vol_i(t), 1e-12)
sma200_i(t) = mean(close_i, trailing 200 sessions)
```

An asset is eligible only when momentum is strictly positive and close is
strictly above its SMA200. The SPY regime is `bull` when SPY close is at or
above its SMA200 and `bear` otherwise.

Run all three candidates on IS with base costs. Candidates with max drawdown
`>= 8%` or worst regular-session intraday-proxy day `<= -3%` are ineligible.
Among eligible candidates choose highest net daily Sharpe, then lower maximum
drawdown, then the longer lookback. If none is eligible, select the lowest max
drawdown candidate solely so that the frozen architecture receives one OOS
test; it still cannot pass the final gate unless every OOS criterion passes.

The selection artifact, protocol, engine, tests, and snapshot hashes must be
committed before the OOS command is allowed to run. The OOS command must verify
that its selection artifact is byte-identical to the committed copy.

## Decision schedule and holdings

Decisions occur at the final XNYS close of every ISO calendar week. Orders
execute only at the next XNYS session open.

- Bull regime: rank eligible equity-sector ETFs by score and target the top
  four. Gross exposure may not exceed 50% of equity.
- Bear regime: equity-sector exposure is zero. Rank eligible defensive ETFs and
  target the top two. Gross exposure may not exceed 20% of equity.
- Unfilled slots remain cash. Ties use symbol order.
- A holding omitted from the next target set exits at the next XNYS open.
- A stopped asset cannot re-enter before a later weekly decision.
- There are no shorts, leverage, partial profits, pyramids, discretionary
  overrides, or same-session re-entry.

## Sizing, costs, and exits

ATR20 is the simple trailing mean of true range using information through the
decision close. Initial stop distance is `2.5 * ATR20` from the next-open fill.
The fixed planned-loss budget is 0.35% of
`min(current executable equity, 100000)`. Entry and estimated stop-exit costs
are included when solving for units:

```text
loss_per_unit = max(entry - stop, 0) + cost*entry + cost*stop
units_risk = risk_budget / loss_per_unit
```

Each new notional is also capped by the remaining regime gross allowance split
equally across target slots. Aggregate planned stop loss may not exceed 2.5% of
the same capital base. Fractional ETF units are allowed for research. Cash earns
zero. The gross and planned-risk caps are hard checks immediately after every
executable fill set. Passive price movement can change marked exposure between
fills; any marked cap overrun is reported separately, blocks new risk, and is
trimmed at the next session open. It is not silently treated as though a
retroactive close fill were available. With at most four 0.35% initial-risk
legs, ordinary entry risk is at most 1.40%; 2.5% remains an absolute ceiling,
not a target.

Base friction is 5 bps per transaction side. The binding stress is 10 bps per
side plus 25 bps additional adverse slippage on stop fills.

At +1.0R, the resting stop ratchets to the original fill price and never
loosens. Because fees exist, this is price breakeven, not a guaranteed net-zero
trade. Initial or breakeven stops execute as follows:

- opening gap through a stop: fill at the worse open;
- otherwise, if the regular-session daily range crosses the resting stop: fill
  at the stop, then apply any stress stop slippage;
- on a daily bar touching both the old stop and +1R, assume the old stop occurs
  first;
- a weekly signal exit fills at the next open;
- all remaining positions liquidate at the final segment close with costs.

## Daily loss proxy and funded replay

For every XNYS session record start balance, closing balance/equity, opened
positions, and a conservative regular-session minimum equity. For long holdings,
the minimum mark is the worse open on a gap-stop day, the resting stop on an
intraday stop day, or the session low when no stop executes. Assume individual
asset adverse marks occur simultaneously. Include all entry/exit costs.

Replay a USD 100,000 FTMO 2-Step-shaped account with a 10% profit target, 5%
maximum daily loss, 10% static maximum loss, four minimum trading days, and
Europe/Prague firm sessions. This is explicitly an OHLC proxy; absence of
overnight/intraday CE(S)T quotes keeps funded readiness data-limited.

## Frozen metrics

- CAGR uses elapsed calendar time.
- Sharpe is mean daily net return divided by sample standard deviation times
  `sqrt(252)`, with zero risk-free rate.
- Maximum drawdown is peak-relative on closing equity.
- Worst day is the minimum regular-session proxy equity relative to that day's
  starting balance.
- Profit factor and win rate use completed round trips with every cost included.
- Average monthly profit includes every calendar month intersecting the segment,
  including partial endpoint months.

## Final pass/fail gate

Book G earns `HISTORICAL_GATE_PASS_DATA_LIMITED` only if all conditions pass:

1. OOS base CAGR >= 8.4%, average monthly profit >= USD 700, Sharpe >= 1.00,
   profit factor >= 1.60, maximum drawdown < 6.0%, worst proxy day > -2.5%,
   and total return > 0.
2. OOS/IS retention is >= 75% for both positive CAGR and positive Sharpe. A
   non-positive IS denominator fails retention.
3. OOS stress total return and CAGR are positive, Sharpe >= 0.50, profit factor
   >= 1.10, maximum drawdown < 8.0%, and worst proxy day > -3.5%.
4. Base and stress have zero modeled 5% daily-loss and 10% static-loss breaches.
5. Every sizing, gross-exposure, aggregate-risk, next-open, gap-stop,
   future-poison, input-order, cost, and segment-isolation invariant passes.

For condition 5, the gross-exposure invariant applies at executable post-fill
snapshots. Any passive marked overrun must be disclosed with its magnitude,
must not permit a new entry, and must be corrected at the next XNYS open.

Any failed criterion yields `NO_RESEARCH_CANDIDATE`. No parameter may be changed
after OOS is opened. A new variant requires a new protocol and a new future
clock. Historical passage still requires an unchanged forward-paper trial of at
least six months and 100 closed trades plus exact broker bid/ask validation.
