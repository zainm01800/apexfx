# W3 GATE — NOTIONAL CAP 15%: **REJECTED (PBO 0.793 — the in-window improvement does not survive the selection gate)**

**Pre-registration:** `engine/data_store/notional_cap_prereg.md` (written BEFORE any capped
run; the 2 trials were recorded before execution). **Results:**
`engine/data_store/validation/notional_cap_gate_2026-07-24.json`. **Script:**
`engine/scripts/run_portfolio_gate_notional_cap.py`. **Window:** ITERATION only, strictly
< 2025-01-01; certified anchor (mrpt 0.01, EQUITY_CORE panel order, gap-aware engine).
**Ledger:** 262 → **264**; every DSR deflated by 264.

## The pre-registered scoreboard

| | control (uncapped) | challenger (cap 15%) |
|---|---|---|
| Sharpe | 0.86284 (certified anchor, exact) | **0.88162** (+0.0188) |
| Profit factor | 1.3245 | **1.3525** (+0.0280) |
| Trades | 1637 | 1619 |
| Win rate | 55.77% | 55.71% |
| Expectancy / trade | +120.44 (+1.022%) | +111.11 (+1.020%) |
| Max drawdown | 16.32% | **15.91%** |
| **Worst daily loss** | **−5.09%** | **−3.89%** (−24%) |
| Notional share max / p95 / median | **1.151** / 0.257 / 0.065 | **0.157** / 0.151 / 0.066 |
| Trades over 15% notional | 16.7% | 8.7% (displaced, not capped — see below) |
| `max_position_notional` binds | 0 | **280** |
| DSR @ n=264 | 0.998 ✓ | 0.998 ✓ |
| CPCV median / frac positive | +0.048 / 15-of-15 ✓ | +0.047 / 14-of-15 ✓ |
| **PBO (2-config set)** | — | **0.793 ✗ (≥ 0.5)** |

**Pre-registered rule:** H1 (tail) **HOLDS** — worst daily loss −24% (pre-committed bar: −10%).
H2 (no degradation) **HOLDS** — Sharpe and PF actually *improved*. Challenger gates **FAIL** on
PBO. **Verdict: REJECTED.**

## The honest reading

The mechanism is real and measured: uncapped, the book's largest single-name position reached
**115% of equity** (a low-vol mega-cap with a tight ATR stop — the work order's AAPL-type case,
verified worse than stated), and 16.7% of trades exceeded 15% notional. The cap did exactly what
the hypothesis said: it bound 280 times, cut the worst daily loss by a quarter, and removed the
>15% tail entirely (max share 0.157, p95 0.151). It even *added* performance in-window
(+0.019 Sharpe, +0.028 PF).

What the gate says about that improvement: across the 2-config CSCV selection set, the
in-sample winner (the capped book) ranks below-median out-of-sample **79% of the time** — the
in-window edge is indistinguishable from selection luck. The CPCV OOS distributions agree:
medians are a dead heat (+0.0473 vs +0.0476), and the challenger turns the control's one
marginally-positive path (+0.0009) marginally negative (−0.0005). **The tail improvement is
measured fact; the performance improvement is not certified.** The pre-registered rule makes no
exception for "the metrics all moved the right way", so the cap is REJECTED as a certified
change — recorded, not adopted.

**Note for the owner:** adopting the 15% cap as a *risk policy* (like the 0.75% risk-per-trade
adoption of 2026-07-23, taken un-gated by owner decision) is defensible on the tail evidence
alone — worst-day −24%, single-name ceiling 115%→16% — but that is a policy call, not a
gate-certified improvement, and it is flagged as such here.

## Pre-registered caveats, restated where they bound the result

- The 2 configs share ~100% of their universe; PBO's discriminative power is limited by
  construction — reported as computed (0.793), pass or fail, per prereg §3.
- The 15% level is a point estimate, deliberately not swept (§6 of the prereg).
- Notional share uses fill-day equity (one bar after the decision; immaterial at daily
  frequency).
- "Trades over 15%" in the challenger (8.7%) are entries that were capped AT 15% plus
  mark-to-market drift of positions whose notional grew above 15% while open — the cap binds
  at entry only, by design.

## Determinism

Full gate executed twice (seed 42): outputs **byte-identical** modulo `generated_at` (ledger
dedups 264 → 264 on the second pass). Control full-window numbers and CPCV paths reproduce the
certified 2026-07-22 anchor exactly. Unit tests: `tests/test_notional_cap.py` (cap off =
byte-identical sizing; cap binds and shrinks risk, never re-levers; no-op above the cap) +
full suite green.

## Ledger

- **n before this gate: 262** (258 + 4 W2 measurement trials)
- **+2** (`book_h_gold_252` and `book_h_gold_252_notional_cap15`, kind `notional_cap_gate`,
  recorded before the first run) → **264**. Rerun deduped (264 → 264).
