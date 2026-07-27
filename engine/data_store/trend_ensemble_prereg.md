# PRE-REGISTRATION — Multi-horizon trend ensemble on Book H gold (2026-07-27)

**Status: pre-registered BEFORE any challenger run.** This document fixes the hypothesis, the
configuration set, the blend formula, the gates, the adoption/falsification rule, and the
ledger plan before execution. Changing anything after the run requires a new
pre-registration and new ledger charges.

**Base book:** `book_h_gold_252` — the certified halal trend book (lookback 252, vol 63,
hold 21, rr 1.5, `rule_based` regime, HTF 1w×50 gate, managed exits, vol-scaled sizing,
**certified risk anchor `max_risk_per_trade = 0.01`**, `book_h_gapaware_2026-07-22.json`:
Sharpe 0.86284, 1637 trades, final equity 292,551.34). Remaining caps per config:
max_portfolio_risk 0.065, max_total_exposure 3.0, max_correlated_exposure 1.5, drawdown
breakers 0.10/0.20, swing bucket 10, global 12. Iteration window strictly < 2025-01-01,
seed 42, warmup 250, CPCV purge 21, 15 paths. The 2025+ holdout is not touched.

---

## 1. Hypothesis

The certified book's trend signal is a **single 252-day lookback** — one point estimate of
"trend". The most replicated robustness result in the trend literature is that **blending
lookback horizons** beats any single horizon out-of-sample, because each horizon is a noisy
estimate of the same underlying phenomenon and averaging diversifies the estimation error:
Moskowitz/Ooi/Pedersen (2012, JFE) show time-series momentum is robust across lookbacks;
Hurst/Ooi/Pedersen (2017, JPM) run horizon ensembles, not single lookbacks, for exactly this
reason (see docs/research/2026-07-17_fx_edges_evidence.md); Benhamou et al. (2025) show a
2-horizon 63/252 "barbell" captures most of a full ensemble's effect. A 63-day leg also
reacts faster to regime turns; the 252-day leg anchors against whipsaw.

**H:** blending the vol-scaled momentum score across horizons **beats the certified
252-only score on net out-of-sample Sharpe via stability** (more CPCV paths won
head-to-head), at a transaction-cost drag from added turnover below 1%/yr.

## 2. Formula and implementation (pre-registered constants)

Per instrument, per decision bar, point-in-time (no lookahead — the same causality as the
certified single-lookback score):

- Certified score (one leg): `s_L(t) = (close_t / close_{t−L} − 1) / σ_63(t)`, where
  `σ_63` is the 63-day rolling std (ddof=1) of daily log returns. This is
  `VolScaledMomentum(L, 63)` — the certified feature.
- **Blended score:** `s(t) = mean_L s_L(t)` over the config's lookback set, equal weight.
  **NaN unless EVERY leg is finite** (no partial blends; NaN propagates). With one leg
  this is exactly the certified score.
- Everything downstream is **unchanged certified machinery**: sign(s) is the momentum
  direction (must still agree with the rule-based regime trend); the probability map
  `clip(0.52 + 0.06·|s|, 0.52, 0.82)`; the HTF 1w×50 gate; vol-scaled sizing; managed
  exits; per-class v5 costs; all risk caps. The blend changes the SCORE ONLY — signal
  gating, sizing, exits, and costs are byte-identical code paths.
- Implementation: new optional constructor kwarg `momentum_lookbacks` on
  `RegimeGatedMomentum` (default `None` ⇒ `[momentum_lookback]` ⇒ certified behaviour).
  The calibration-cache path (`fit`) computes the identical blended series.
  `config.yaml` untouched.

## 3. Configurations (the full selection set: exactly 3)

| Config | `momentum_lookbacks` | Question |
|---|---|---|
| `trend_ens_control_252` (control) | `[252]` | anchor — must reproduce the certified numbers exactly |
| `trend_ens_blend_63_126_252` (challenger B) | `[63, 126, 252]` | does the classic 3-horizon ensemble beat 252-only OOS? |
| `trend_ens_barbell_63_252` (challenger C) | `[63, 252]` | does the 2-horizon barbell (Benhamou 2025) beat 252-only OOS? |

Universe, params, costs, caps, warmup, window: identical for all three; the ONLY difference
is the lookback set. Certified panel insertion order (EQUITY_CORE first — the certified
numbers are ordering-sensitive; alphabetical is a known artifact and is NOT used).

## 4. Gates (identical machinery and thresholds to every prior gate)

For EACH config: DSR > 0.95 (deflated by the **full updated TrialLedger count**, §6)
**and** CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive (purge 21) **and**
PBO < 0.5 across the 3-config selection set (16 splits, seed 42; standing caveat: ~100%
universe overlap, score-only difference — reported as computed). The lookback set flows
into every CPCV fold via the model factory params.

## 5. Pre-committed adoption / falsification rule

A challenger is **ADOPTED** (flag available for a future certified-change decision) iff ALL
THREE legs hold for that challenger:

- **CPCV head-to-head:** challenger OOS Sharpe strictly greater than control's on **> 7 of
  the 15** CPCV paths (same 15 folds, same machinery).
- **DSR:** challenger full-window DSR > **0.95**, deflated by the full updated ledger count
  (n = 279, §6).
- **Cost drag < 1%/yr:** `drag(c) = ann_return_zero_cost(c) − ann_return_net(c)`, measured
  per config c by a twin full-window run with every transaction cost zeroed (spreads,
  slippage, commissions, forex pair tables — same trades, honest whole-run cost measure).
  Adoption requires `drag(challenger) − drag(control) < 0.01` (absolute annualized return).

**KILL: any leg fails ⇒ that challenger is REJECTED.** The control failing to reproduce
the anchor aborts the gate (hard check, §7). If both challengers pass all legs, both are
reported as adopted candidates; choosing between them is a separate certified-change
decision, not this gate's.

Measured but not verdict-binding (reported for the record): full metric set (Sharpe, PF,
win rate, maxDD, worst day, £-per-month, expectancy), PBO, trade counts/turnover, per-class
breakdown, CPCV path distributions.

## 6. Ledger plan

`TrialLedger` at **n = 276** at writing. This campaign evaluates exactly 3 configs, so
exactly **3 new trials** (`trend_ens_control_252`, `trend_ens_blend_63_126_252`,
`trend_ens_barbell_63_252` with `kind=trend_ensemble_gate`) are recorded BEFORE the first
run → **n = 279** deflates every DSR in this gate. No other lookback sets will be
evaluated; any follow-up (other horizon grids, vol-weighted blends, per-class lookbacks)
is a new pre-registration.

## 7. Known limitations

- **The horizon sets are literature choices, not optimised** — deliberately not swept
  (sweeping inflates selection bias; the ledger discipline exists precisely to charge for
  that). 63/126/252 are the standard quarter/half/full-year trading-day grid.
- **The blend can only change the score where legs disagree in magnitude or sign**; in
  strong persistent trends all legs agree and the challenger ≈ control — the expected
  effect size is modest by construction.
- **Turnover rises when the fast (63d) leg flips early** — that is the intended mechanism,
  and the cost-drag leg bounds what it may cost. Zero-cost twin runs share the same
  stop/target shift logic, so the drag figure is the honest whole-run cost difference.
- **Control must reproduce the anchor** (Sharpe 0.86284, 1637 trades, equity 292,551.34)
  — hard-checked before any comparison; a mismatch aborts the gate.
