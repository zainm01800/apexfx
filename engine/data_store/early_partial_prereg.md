# PRE-REGISTRATION — Earlier first partial on the certified trend book (2026-07-24)

**Status: pre-registered BEFORE any run.** This document fixes the hypothesis, the
configuration set, the success criteria, and the ledger plan before execution. The mechanism
already exists (`TradeManager(p1_r=...)`, default 1.0 — the certified book is byte-identical)
but has NOT been gated and NO trials are charged. Charges happen at gate-run time. Changing
anything after the run requires a new pre-registration and new ledger charges.

**Base book:** `book_h_gold_252` — the certified gap-aware anchor
(`engine/data_store/validation/book_h_gapaware_2026-07-22.json`: Sharpe **0.86284**, 1637
trades, final equity 292,551.34 on £100k at `max_risk_per_trade = 0.01`). This experiment
changes **the first-partial trigger only** — signal, universe (certified panel insertion
order: EQUITY_CORE first, then SGLD.L, crypto, FX majors), sizing, regime, HTF gate, costs,
caps, window (< 2025-01-01), seed 42, and gate machinery are byte-identical. Any delta is
attributable to the trigger change alone.

---

## 1. Hypothesis (falsifiable, stated before the run)

The certified exit ladder banks the first 50% at **+1R** and moves the stop to breakeven at
the same moment. Trades that reach +0.6R/+0.8R and reverse are full −1R losers today; with an
earlier trigger they become small winners (half banked) plus a breakeven-scratch remainder.

**H:** moving the first partial EARLIER (+0.75R, +0.5R — breakeven move travels with it)
**raises win rate** and **smooths the equity curve** (lower max drawdown and/or less severe
worst day), at an **expectancy cost**: the banked half is smaller and the breakeven stop is
tighter in noise terms, so more would-be winners get scratched before reaching +1.5R.

**The honest counter-hypothesis, pre-registered:** earlier breakeven may mostly convert
−1R losers into scratches without adding winners (the +0.5R/+0.75R bank is small), while the
tighter stop kills trend legs that currently survive to +1.5R — expectancy could fall MORE
than the comfort is worth, or win rate could even FALL if +0.5R scratches outnumber converted
losers. That is exactly why this is gated and priced, not adopted because it sounds safe.

**This is an OWNER-TRADE experiment, not a performance claim.** The owner's stated intent is
more safety / a smoother curve, priced honestly. We do NOT require Sharpe or expectancy to
rise — we measure what the safety costs in £/month and decide by the pre-registered rule
below (§4), the same category of decision as the 2026-07-23 0.75%-risk override (an owner
comfort choice, documented as such).

## 2. The change (one variable)

`TradeManager(p1_r=X)`. Everything else in the ladder is UNCHANGED: 50% partial fraction,
stop → breakeven at the moment Partial 1 fires (it travels with the earlier trigger — that is
the point of the experiment), Partial 2 (25% at +1.5R + lock 0.5R), 2×ATR Chandelier trail,
squeeze tightening, 21-bar time exit, gap-aware fills. Downside before the trigger is
untouched — the hard initial stop still protects exactly as before (unit-tested:
`tests/test_early_partial.py`).

One variable moves: the R-multiple at which the first 50% is banked and the stop goes to
breakeven.

## 3. Configs — exactly 3 (the full selection set)

| Config | p1_r | Role |
|---|---|---|
| `book_h_gold_252` | 1.00 (certified) | baseline / anchor hard-check |
| `book_h_gold_252_p1_075` | 0.75 | challenger |
| `book_h_gold_252_p1_050` | 0.50 | challenger |

Ledger: exactly **3 NEW trials** (kind `early_partial_gate`, `max_risk_per_trade` 0.01,
`p1_r` in the params so the keys cannot dedup against the certified book's existing entry),
recorded BEFORE the first run. Ledger 266 → **269**; the full updated count deflates every
DSR. Re-runs dedup (269 → 269).

## 4. Success criteria — the OWNER-TRADE rule (binding)

**Adopt the EARLIEST config** (0.50R is "earlier" than 0.75R; evaluate 0.50 first, then 0.75)
that satisfies ALL THREE:

1. **Cost ≤ 10%:** average in-window monthly profit (£/month on the £100k book) is at most
   10% below the baseline's;
2. **Win rate +≥ 2pts:** win rate is at least 2.0 percentage points above the baseline's;
3. **No worse drawdown:** max drawdown is not worse than the baseline's (≤ baseline maxDD).

If neither challenger qualifies: **ADOPT NOTHING — the certified +1R ladder stands**, reported
as the honest answer. DSR / PBO / CPCV are run with identical machinery and thresholds as
every prior gate (DSR > 0.95 at the full ledger count, PBO < 0.5 across the 3-config set,
CPCV 15 paths median > 0 with > 50% positive) and are **recorded for information** — the
adoption criterion is the owner's stated comfort-for-cost trade above, not a gate pass.
(Pre-registered caveat: with 3 near-identical configs sharing ~100% of their universe, PBO's
discriminative power is limited by construction — reported as computed, same caveat as every
prior overlapping-family PBO.)

## 5. Measurements

Per config: Sharpe (annualised), profit factor, win rate, max drawdown, **worst daily
return** and worst daily P&L (£), expectancy per trade (£ and %), trade count, total return,
final equity, **average monthly profit (£/month on £100k)** = mean of month-on-month equity
differences over the in-window curve, CPCV 15-path OOS Sharpe distribution, DSR at n=269,
PBO across the 3-config set. The £/month cost of each challenger vs baseline is the FIRST
table of the gate report.

## 6. Honesty rules (identical to every prior gate)

- ITERATION window only: data strictly < 2025-01-01. The 2025+ holdout is never touched.
- **Certified-anchor reproduction:** the baseline config (p1_r = 1.0, mrpt 0.01) MUST
  reproduce book_h_gapaware_2026-07-22.json exactly (Sharpe 0.862838, 1637 trades, final
  equity 292,551.34, PF 1.324524, win rate 0.557728, maxDD 0.163153, expectancy 120.442657)
  — the run hard-fails otherwise. Panel insertion order is the certified one (EQUITY_CORE
  first; alphabetical order is a known ~0.33-Sharpe artifact).
- Seed 42. **Determinism:** the full gate is run twice; outputs byte-identical modulo
  `generated_at` and the ledger pre-state (`n_trials_before`; the rerun dedups 269 → 269).
- Frozen paper test (workflow, state.json, engine/config.yaml live sections) untouched.
- If an earlier partial surprises and RAISES expectancy, that is the headline — but the
  adoption rule in §4 still governs (it is a comfort-for-cost decision, not a profit claim).

## 7. Known limitations

- The breakeven stop at +0.5R sits inside normal daily noise for the vol-scaled sizes this
  book trades; scratch frequency is the mechanism being measured, not a modelling error.
- £ figures are the book's account-currency units on the £100k certified anchor
  (initial_equity 100000); no FX conversion is applied, same as every prior report.
- CPCV for the challengers forwards the SAME `TradeManager(p1_r=X)` into every fold —
  measuring the baseline exit OOS while reporting the challenger would be an invalid gate
  (the runner-exit seam exists precisely for this).
