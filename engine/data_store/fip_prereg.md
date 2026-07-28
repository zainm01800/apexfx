# PRE-REGISTRATION — FIP / information-discreteness entry gate on Book H gold (2026-07-28)

**Status: pre-registered BEFORE any challenger run.** This document fixes the hypothesis, the
exact gate rule, the configuration set, the adoption/falsification rule, and the ledger plan
before execution. Changing anything after the run requires a new pre-registration and new
ledger charges.

**Base book:** `book_h_gold_252` — the certified halal trend book (lookback 252, vol 63,
hold 21, rr 1.5, `rule_based` regime, HTF 1w×50 gate, managed exits, vol-scaled sizing,
**certified risk anchor `max_risk_per_trade = 0.01`**, `book_h_gapaware_2026-07-22.json`:
Sharpe 0.86284, 1637 trades, final equity 292,551.34; reproduced EXACT on 2026-07-28 before
this gate). Iteration window strictly < 2025-01-01, seed 42, warmup 250, CPCV purge 21,
15 paths. The 2025+ holdout is not touched.

---

## 1. Hypothesis

Da, Gurun & Warachka (RFS 2014, "Frog in the Pan"): the momentum premium concentrates in
stocks whose past return arrived as **many small same-sign days** (continuous information —
investors underreact), while trends made of **few big jumps** (discrete information —
investors overreact) reverse. Conditioning on the *path shape* of the formation return, not
just its sign, should separate persistent trends from reversal-prone ones.

**H:** trades whose entry signal sits in the **continuous half** of the information-
discreteness (ID) distribution have **higher net expectancy per trade** than those in the
discrete half; gating entries to the continuous half raises the book's per-trade expectancy
out-of-sample.

## 2. Exact rule (pre-registered constants)

Per instrument `i`, per decision bar `t`, point-in-time (closes up to and including `t`
only — the same causality as the certified score):

- Formation window **F = 126** trading days (the literature's 6-month formation; one value,
  not swept).
- Past return `R_i(t) = close_t / close_{t−126} − 1`.
- Daily simple returns `r_s` over the same 126-day formation; `up_i(t) = mean(r_s > 0)`,
  `down_i(t) = mean(r_s < 0)` (fractions of up/down days; flat days count in neither).
- **Information discreteness:** `ID_i(t) = sign(R_i(t)) × (down_i(t) − up_i(t))`
  (the task formula, identical to the paper's `sign(PRET) × (%neg − %pos)`).
  **Sign convention (binding):** a *continuous* trend has many same-sign days, so
  continuous ⟺ **LOW (more negative) ID**; discrete ⟺ HIGH ID. The keep set is the
  continuous half — i.e. `ID_i(t)` **at or below** the cross-sectional median. (The task
  statement's "top half of ID distribution (more continuous)" is read as the top half of the
  *continuity* ranking; with the formula above, that is the bottom half of raw ID values.
  This document fixes that reading before any run.)
- **Distribution:** the cross-section of `ID_j(t)` over **all certified-panel instruments
  with a defined ID at `t`** (39-name gold panel, each instrument's ID forward-filled onto
  the book's union timeline so non-trading days carry the last known value; undefined IDs —
  first 126 bars of an instrument's history — are excluded from the median).
- **Gate:** a **momentum-mode** entry signal at `t` is kept iff `ID_i(t) ≤ median_t`;
  vetoed (FLAT) otherwise. Ties at the median keep (≤). An **undefined** `ID_i(t)` at a
  tradable bar vetoes (a filter cannot verify continuity it cannot measure; warmup 250 makes
  this case ≈ never — occurrences are counted and reported). Bollinger mean-reversion
  signals (`mode=mean_reversion`) are **not** gated: the ID is computed on the momentum
  formation and has no meaning for a counter-trend bounce.
- Sizing, exits, costs, caps, HTF gate, regime gate: **unchanged certified machinery**.
  The gate only refuses entries.

## 3. Configurations (the full selection set: exactly 2)

| Config | `entry_gate` | Question |
|---|---|---|
| `fip_control_252` (control) | `None` (certified) | anchor — must reproduce the certified numbers exactly |
| `fip_gate_252` (challenger) | `{"kind": "fip", "formation": 126}` | do continuous-information entries raise expectancy OOS? |

Universe, params, costs, caps, warmup, window: identical; the ONLY difference is the entry
gate. Certified panel insertion order (EQUITY_CORE first — the certified numbers are
ordering-sensitive; alphabetical is a known artifact and is NOT used). Implementation:
`TrendBook(..., entry_gate=None)` default ⇒ certified behaviour, byte-identical; the gate
wraps each per-instrument strategy (same wrapper seam as `CarryTrendFilter`) and vetoes
momentum-mode signals whose ID sits in the discrete half.

## 4. Gates (identical machinery and thresholds to every prior gate)

For EACH config: DSR > 0.95 (the machinery's strict threshold, deflated by the **full
updated TrialLedger count**, §6) **and** CPCV median OOS Sharpe > 0 with > 50% of 15 paths
positive (purge 21) **and** PBO < 0.5 across the 2-config selection set (16 splits,
seed 42; standing caveat: ~100% universe overlap — reported as computed). The gate spec
flows into every CPCV fold via the model factory params; per-path trade lists are captured
so expectancy is measured on the SAME 15 folds.

## 5. Pre-committed adoption / falsification rule

The challenger is **ADOPTED** (flag available for a future certified-change decision) iff
ALL FOUR legs hold:

- **Expectancy leg:** mean net pnl per trade (trades entered inside each CPCV test window)
  of the challenger **strictly greater** than the control's on **≥ 12 of the 15** paths
  (a path where either side has zero trades counts as not-improved).
- **DSR leg:** challenger full-window DSR > **0.95** at the full updated ledger count
  (n = 298, §6).
- **PBO leg:** PBO < **0.5** across the 2-config set.
- **Sharpe-noise leg:** the trade-count reduction must not degrade the book beyond noise —
  challenger full-window Sharpe ≥ control Sharpe − **0.05** (absolute).

**KILL: any leg fails ⇒ REJECTED.** The control failing to reproduce the anchor aborts the
gate (hard check, §7).

Measured but not verdict-binding (reported for the record): the mechanism diagnostic —
control trades split by ID half at entry (continuous-cohort expectancy vs discrete-cohort
expectancy, the direct test of §1) — plus the full metric set (Sharpe, PF, win rate, maxDD,
worst day, £-per-month, trades), CPCV path distributions, veto counts.

## 6. Ledger plan

`TrialLedger` at **n = 296** at writing (per the universe-expansion gate, 2026-07-28). This
campaign evaluates exactly 2 configs, so exactly **2 new trials** (`fip_control_252`,
`fip_gate_252`, kind `fip_gate`, mrpt 0.01) are recorded BEFORE the first run → **n = 298**
deflates every DSR in this gate. No other formation windows, quantile splits, or ID variants
will be evaluated; any follow-up is a new pre-registration.

## 7. Known limitations

- **The formation window and the median split are literature defaults, not optimised** —
  deliberately not swept (sweeping inflates selection bias; the ledger discipline exists to
  charge for exactly that).
- **The cross-sectional median mixes asset classes** (stocks, UCITS, gold, crypto, FX):
  crypto's 7-day calendar and jumpier return distributions could dominate one side of the
  split. That is the book the gate would trade, so the split is taken across the whole
  panel — reported, not verdict-binding, per class.
- **Vetoing ~half of signals changes slot competition** (`timeframe_bucket_full` binds
  18k× in the certified book): freed slots recycle into other entries, so the trade-count
  reduction is not mechanically 50% — the Sharpe-noise leg exists precisely because the
  system-level effect is not the mean of the per-signal effect.
- **Control must reproduce the anchor** (Sharpe 0.86284, 1637 trades, equity 292,551.34) —
  hard-checked before any comparison; a mismatch aborts the gate.
