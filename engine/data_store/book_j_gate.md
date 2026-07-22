# Book J gate — expansion REJECTED, but breadth was genuinely tested this time (2026-07-22)

**Verdict: adopt nothing. The certified Book H + gold stands.** Unlike Book I, this was a
clean answer rather than a broken test: **both books PASSED all three gates**, and the
challenger lost on the head-to-head comparison the prereg made binding.

| | Baseline (39 inst) | +24 names (63 inst) |
|---|---|---|
| Sharpe | **1.032** | 0.899 |
| Total return | **266%** | 232% |
| Max drawdown | 15.8% | **15.3%** |
| Profit factor | **1.396** | 1.368 |
| Trades | 1,639 | 1,764 |
| Win rate | **55.8%** | 54.6% |
| **DSR (n=209)** | **0.9966** ✓ | 0.9903 ✓ |
| PBO | 0.3835 ✓ | 0.3835 ✓ |
| CPCV paths positive | **15/15** | 14/15 |
| **Gate verdict** | PASS | PASS |

**Decision rule (prereg §4): adopt only if the challenger passes AND beats the baseline's
DSR. 0.9903 < 0.9966 → ADOPT NOTHING.** Lower drawdown at lower Sharpe was pre-registered as
*not* a pass; that ruling is honoured rather than reinterpreted after seeing the numbers.

## The design fix worked — that is the main result

Book I rejected an 18-name expansion at **PBO 0.602**, condemning all four configs *including
the certified baseline*. This 2-config design produced **PBO 0.3835** on the same machinery
and the same window. The Book I failure really was set-level rank instability from four
near-identical overlapping books, not a verdict on breadth. **We now have a real answer where
before we had a procedural one.**

## What the answer is

Adding 24 screened diversifiers does not break the book — it passes every honesty test on its
own merits — it just makes it **slightly worse**: Sharpe 1.03 → 0.90, win rate −1.2pts, and
one CPCV path flipped negative. The pre-registered counter-hypothesis is supported: **the
trend edge is concentrated in high-momentum mega-caps**, and defensive staples/healthcare/
energy dilute the signal faster than they add independence.

## Post-hoc diagnosis (NOT part of the gate, recorded as a lead)

A correlation audit run after the verdict — structural, no performance data — shows Book J's
24 included genuine near-duplicates of instruments the book already held:

| Added name | max abs corr | duplicates |
|---|---|---|
| XOM | 0.885 | XLE (already in book) |
| CVX | 0.859 | XLE |

Grinold's breadth (`IR ≈ IC·√breadth`) requires **independent** bets; a name correlating 0.88
with an existing holding adds turnover and cost but almost no breadth. So part of Book J's
dilution is explained by adding clones. This is a lead for a differently-specified experiment
(Book K), **not** a re-litigation of this verdict, and not evidence of anything until gated.

## Mechanics
Prereg written before the run; 1 new trial charged (ledger **208 → 209**; the baseline's
2026-07-19 key dedups). All 24 additions had usable in-window data (no missing-addition
warning). Determinism: two full runs, results JSONs identical modulo `generated_at` and the
expected `n_trials_before` (208 pre-charge vs 209 post). Iteration window strictly
< 2025-01-01; the 2025+ holdout was not touched. Results:
`validation/book_j_gate_2026-07-22.json`.

## Side result worth recording
The certified baseline **re-passed on today's data snapshot at DSR 0.9966 with 15/15 CPCV
paths positive**. That is an independent re-confirmation of Book H's edge on refreshed
parquets — at a lower Sharpe (1.03 vs the certified 1.086, the documented data-drift issue),
but the edge itself held.
