# Pre-Registration — Vol-Managed Trend Super-Sleeve (Sleeve A) — 2026-07-19

Written **before** the recorded gate run. Script: `engine/scripts/run_volmanaged_book_gate.py`.
Machine-readable output: `engine/data_store/validation/volmanaged_book_gate_2026-07-19.json`.
Gate report: `engine/data_store/volmanaged_book_gate.md` (written after the run, against this document).

## Hypothesis

Book D (`book_d_multiasset_252` — the frozen forward-paper trend book, clean-data gate:
full-window Sharpe 0.97, maxDD 19.1%, CPCV 14/15 positive, DSR 0.934 ✗ at n=150, PBO 0.056 ✓)
wrapped per instrument in a conditional vol-target overlay will show:

- **H1 (uplift):** full-window Sharpe improves by **+0.1 to +0.3** vs the plain book
  (documented range for plain conditional vol targeting on a trend core;
  Barroso & Santa-Clara 2015 JFE: 0.53 → 0.97 scaling momentum by its own realized vol;
  Bongaerts et al. 2020 FAJ: unlevered vol targeting ~doubles Sharpe with LOWER turnover).
- **H2 (left tail):** max drawdown decreases vs the plain book's 19.1%, with the
  Daniel & Moskowitz (2016) panic stand-down as the mechanism (momentum crashes cluster
  in high-vol post-decline states).
- **H0 (stated alternative, redundancy):** Book D's position sizing is ALREADY vol-scaled
  via RiskManager (vol_target caps bound ×54 in the clean re-run, regime_scale on every
  trade), so a signal-level vol overlay may be redundant — little or no Sharpe uplift.
  The run must distinguish these plainly.

## Exact configuration (the full selection set: 2 NEW trials + 1 re-run baseline)

Universe: the SAME 42 instruments as Book D's clean-data gate (24 equities/ETF +
11 crypto + 7 FX majors; MATIC/USD absent from cache). Window: ITERATION only,
strictly < 2025-01-01. Costs: unchanged per-class v5 mechanics. Managed exits,
warmup 250, periods 252, seed 42 (`cfg.seed`), CPCV purge = holding horizon 21,
one shared equity curve with config risk caps binding.

| | book_a_plain_252 | book_a_vm_252 | book_a_vm_252_standdown_only |
|---|---|---|---|
| role | baseline re-run (exact Book D config; NOT a new trial — dedupes against `book_d_multiasset_252`) | the vol-managed book (NEW trial) | ablation diagnostic (NEW trial) |
| base per instrument | RegimeGatedMomentum(lookback 252, vol 63, hold 21, rr 1.5, rule_based) + MultiTimeframeMomentum(1w×50) | same, wrapped in `VolTargetOverlay` | same, wrapped in `VolTargetOverlay(vol_scale=False)` |
| target_vol | — | 0.10 (fixed) | 0.10 (unused) |
| proxy_window (signal vol) | — | 21 | 21 |
| median_window (inst vol median) | — | 126 | 126 |
| stand_mult / panic_ret_window | — | 1.5 / 21 | 1.5 / 21 |
| CPCV | yes (re-verify) | yes | **no — full-window diagnostic only** |

Overlay construction (frozen in `engine/apex_quant/strategies/vol_target_overlay.py`,
documented there): (a) damp each signal by `min(1, target_vol / proxy)` where `proxy`
is the 21d annualized (√252) std of the instrument's OWN shadow signal returns
(time-stop shadow, strictly point-in-time, one-time replay pre-warm); scaling applied
via the Kelly probability remap (`full_kelly(p',b) == f·full_kelly(p,b)`), never
levering up; (b) force FLAT when the instrument's 21d realized vol > 1.5× its 126d
median AND its 21d return < 0 (Daniel-Moskowitz panic state).

## Ledger and deflation (committed before running)

- Ledger n before: **182**. Recorded BEFORE the run: exactly **2 new trials**
  (`book_a_vm_252`, `book_a_vm_252_standdown_only`, universe `multiasset_43`,
  factory `vol_target_overlay_trend_book`) → n = **184** for DSR deflation of ALL books.
- `trial_sharpes` for DSR = the 3 evaluated full-window Sharpes (plain, vm, ablation).
- PBO headline = **3-way** across all evaluated configs (16 splits, 4000 combos, seed 42);
  the 2-way plain-vs-vm PBO is also reported for comparability with Book D's prior gate.

## PASS criteria (unchanged from the standing gate)

Applied to `book_a_vm_252`: **DSR > 0.95 AND PBO < 0.5 AND CPCV median OOS Sharpe > 0
with > 50% of 15 paths positive.** The uplift question (H1/H2 vs H0) is answered by the
side-by-side full-window comparison regardless of gate verdict, and the stand-down-only
ablation answers "does the stand-down alone help drawdown?" The 2025+ holdout is not
touched in any way; no `--final` look is implied by any outcome of this run.

## Pre-run wiring verification (done, --no-ledger smoke, 2026-07-19)

3-instrument smoke (SPY, BTC/USD, USD/JPY): overlay fires — 117/470 signals stood down
(24.9%), 179/470 damped (38.1%); ablation variant shows identical stand-down count and
zero damping as designed; vm smoke Sharpe 0.61 vs plain 0.54 with maxDD 11.2% vs 12.1%;
CPCV path runs clean for both books through `run_portfolio_cpcv` (fresh overlay
instances per fold, pre-warm replay active from each fold's first bar).
