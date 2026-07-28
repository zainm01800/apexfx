# GATE — COMOMENTUM CROWDING GATE: **REJECTED — certified ungated entries stand**

**Pre-registration:** `engine/data_store/comomentum_prereg.md` (written BEFORE any run;
the 2 trials were recorded before execution). **Results:**
`engine/data_store/validation/comomentum_gate_2026-07-28.json` (+ determinism twin
`..._twin.json`). **Script:** `engine/scripts/run_portfolio_gate_comomentum.py`.
**Window:** ITERATION only, strictly < 2025-01-01; certified anchor (mrpt 0.01, certified
panel insertion order). **Ledger:** 302 → **304**; every DSR deflated by 304.

**Certified-anchor reproduction: EXACT** — the control reproduced
book_h_gapaware_2026-07-22.json to the digit (Sharpe 0.86284, 1637 trades, final equity
292,551.34) before any challenger number was believed. Control gate verdict: **PASS**
(DSR 0.975 > 0.95 @ n=304, PBO 0.140 < 0.5, CPCV 15/15 positive).

## The mechanism runs backwards in this book

The pre-registered mechanism diagnostic (control trades split by whether the gate would
have blocked their (entry date, direction) — abnormal comomentum z > +1.5 in the trade's
direction; 252 long-blocked + 132 short-blocked dates over ~9y) **refutes the Lou & Polk
hypothesis as an entry gate for this book**:

| Cohort at entry | Trades | Expectancy | Win rate |
|---|---|---|---|
| Kept (normal comomentum) | 1,494 | £115.64 | 55.35% |
| **Blocked (crowded, z > +1.5)** | 143 | **£170.60** | **60.14%** |

Crowded-state entries are the book's *best* trades, not its reversal-prone ones: when the
same-direction momentum cohort moves in lockstep, that IS the broad trending state this
vol-scaled trend book is built to harvest. Vetoing those 2,376 entries removes the right
tail — expectancy per trade falls £120.44 → £81.00 (−33%), average month −£587 (−33%) —
while slot recycling keeps the trade count identical (1,637).

## The pre-registered adoption rule (prereg §5, binding)

Adopt iff worst day AND worst month improve, Sharpe cost ≤ 0.03, DSR > 0.95 @ n=304,
PBO < 0.5:

| Leg | Required | Observed | Pass? |
|---|---|---|---|
| Worst day improves | strictly less negative | −5.09% → **−5.09%** (unchanged) | **NO** |
| Worst month improves | strictly less negative | −£19,673 → **−£14,873** | yes |
| Sharpe cost | ≤ 0.03 | **0.147** | **NO** |
| DSR (challenger, n=304) | > 0.95 | **0.927** | **NO** |
| PBO (2-config set) | < 0.5 | 0.14025 | yes |

**DECISION: ADOPT NOTHING — REJECTED.** Three of five legs fail; the kill rule engages.
The one genuine improvement (worst month −£4.8k) is real but nowhere near sufficient:
the insurance costs 5× the pre-registered Sharpe budget and the worst DAY is untouched
(the gate never fired during the single worst session).

## Full scoreboard (baseline vs challenger)

| | `comom_control_252` (certified) | `comom_gate_252` (challenger) |
|---|---|---|
| Sharpe (ann.) | **0.86284** | 0.71549 (−0.147) |
| Profit factor | **1.3245** | 1.2741 |
| Win rate | **55.77%** | 55.04% |
| Expectancy / trade | **£120.44 (1.022%)** | £81.00 (0.817%) |
| Max drawdown | **16.32%** | 17.80% (worse) |
| Worst day (ret) | −5.09% (−£8,527) | −5.09% (−£6,760) |
| Worst month | −£19,673 | **−£14,873** |
| £-per-month (avg) | **£1,782.88** | £1,196.26 (−33%) |
| Trades | 1,637 | 1,637 (2,376 vetoes — slots recycle) |
| Total return (~9y) | **+192.6%** | +129.2% |
| Final equity | **£292,551** | £229,196 |
| CPCV median / frac pos | **+0.048 / 15-of-15** | +0.043 / 13-of-15 |
| DSR @ n=304 | **0.975 ✓** | 0.927 ✗ |
| PBO (2-config set) | 0.14025 ✓ | — |

## The honest reading

1. **Comomentum as an ENTRY gate contradicts what this book is.** Lou & Polk's crowded-
   momentum state predicts reversals for a *cross-sectional long-short momentum* book
   that shorts the crowded cohort. This book is *time-series trend*: it WANTS the
   cohort moving together — that is what a durable multi-asset trend looks like from
   inside. Blocking the state amputates the best trends (blocked-cohort expectancy
   +£55/trade HIGHER than kept).
2. **The worst-month improvement is genuine but mispriced**: −£14.9k vs −£19.7k worst
   month for −0.147 Sharpe and −£587/month average. At 5× the pre-registered budget the
   insurance is declined by rule.
3. **All three conditioning experiments agree on one system-level fact**: this book is
   slot-constrained (`timeframe_bucket_full` binds 16–18k times), so entry VETOES
   recycle rather than remove. Conditioning that wants to help must change *which*
   candidate gets the slot (ranking) or *how big* it is (sizing), not whether it exists.
   That is a different layer and a new pre-registration.

## Determinism

Full gate executed twice (seed 42): outputs **byte-identical** modulo `generated_at`
(the rerun dedups the ledger 304 → 304). Unit tests: `tests/test_entry_gates.py`
(11 tests, incl. comomentum z-score: lockstep cohort blocked at crowding onset, calm
history ~never blocked, empty cohort never blocks, pre-window undefined) + full suite
green.

## Ledger

- **n before this gate: 302** (per the factor-confirmation gate) → **304** (+2:
  `comom_control_252`, `comom_gate_252`, kind `comomentum_gate`, mrpt 0.01), recorded
  before the first run. Rerun deduped (304 → 304).

## Caveats (pre-registered and observed)

- £ figures are account-currency units on the £100k certified anchor; no FX conversion,
  same as every prior report.
- Cohorts are momentum-sign cohorts (the faithful Lou & Polk reading), not the book's
  open positions — pre-registered.
- Cross-asset correlation on the union timeline mixes 5-day and 7-day calendars
  (pairwise-complete, the certified correlation machinery's own convention).
- ~9 years of iteration data contain few true momentum crashes; the left-tail legs are
  decided by a small number of episodes — exactly why the DSR/PBO legs also bind
  (pre-registered §7).
- The frozen paper test (workflow, state.json, engine/config.yaml live sections) was not
  touched; `entry_gate=None` remains the certified default.
