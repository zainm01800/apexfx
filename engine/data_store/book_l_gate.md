# Book L gate — UCITS re-wrap REJECTED (2026-07-22)

**Verdict: reject.** The swapped sleeve failed its own gates (PBO 0.81 ≥ 0.5) and was worse
than the baseline on every direct metric. Per `book_l_ucits_prereg.md` §3, the honest
conclusion is stated as pre-registered: **the sector-ETF legs are not viable on a UK retail
account and the sleeve should be dropped, not re-wrapped.**

| | Baseline (39 inst, untradeable) | UCITS swap (38 inst, tradeable) |
|---|---|---|
| Sharpe | **1.032** | 0.931 |
| Total return | **266%** | 237% |
| Max drawdown | **15.8%** | 17.1% |
| Profit factor | **1.396** | 1.358 |
| Trades | 1,639 | 1,677 |
| DSR (n=212) | 0.9983 ✓ | 0.9972 ✓ |
| **PBO** | **0.81 ✗** | **0.81 ✗** |
| CPCV positive | **15/15** | 13/15 |
| Verdict | REJECT | REJECT |

## What was swapped
XLK→IITU.L, XLE→IUES.L, XBI→BTEC.L, SMH→SMH.L, SOXX dropped (SEMI.L is a global
near-duplicate of SMH.L), ISWD.L→ISDW.L (same fund, USD line — the GBp line embeds GBP/USD in
a USD trend signal). All swaps had usable in-window data (`missing_swaps: []`).

## Why it degraded — the pre-registered reasons held
The prereg (§5) predicted degradation for three specific reasons, and the result is consistent
with all three: **XBI→BTEC is an INDEX change** (S&P Biotech equal-weight → Nasdaq Biotech
cap-weighted; the evidence does not transfer), **SMH.L has only 1,029 in-window bars** vs SMH's
full window, and **dropping SOXX** removed an instrument outright. Drawdown got worse
(15.8% → 17.1%) and two CPCV paths flipped negative.

## The practical bind this leaves
The baseline is **untradeable** on the account it is meant to run on — five of its
instruments are PRIIPs/KID-blocked, confirmed live by IBKR error 201 on 2026-07-22. The
tradeable re-wrap is worse and does not pass. So neither option is both good and executable.

The prereg's own answer stands: **drop the sector-ETF sleeve** rather than swap it. That is a
different universe (Book H core minus XLK/XLE/XBI/SMH/SOXX) and would need its own
pre-registered gate — it has NOT been tested here and must not be adopted on the strength of
this run.

## Mechanics
Trial was charged to the ledger by an earlier aborted run and dedup'd on re-run
(`n_trials 212 → 212`), so recovering the evidence cost nothing additional. Determinism: two
full runs **byte-identical** including `n_trials_before` (the charge was already sunk).
Iteration window strictly < 2025-01-01; 2025+ holdout untouched.
Results: `validation/book_l_gate_2026-07-22.json`.
