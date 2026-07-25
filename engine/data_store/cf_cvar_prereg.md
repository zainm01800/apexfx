# PRE-REGISTRATION — Cornish-Fisher CVaR tail sizing on Book H gold (2026-07-25)

**Status: pre-registered BEFORE any challenger run.** This document fixes the hypothesis, the
configuration set, the formula, the gates, the falsification rule, and the ledger plan before
execution. Changing anything after the run requires a new pre-registration and new ledger
charges.

**Base book:** `book_h_gold_252` — the certified halal trend book (lookback 252, vol 63,
hold 21, rr 1.5, `rule_based` regime, HTF 1w×50 gate, managed exits, vol-scaled sizing,
**certified risk anchor `max_risk_per_trade = 0.01`**, `book_h_gapaware_2026-07-22.json`:
Sharpe 0.86284, 1637 trades, final equity 292,551.34). Remaining caps per config:
max_portfolio_risk 0.065, max_total_exposure 3.0, max_correlated_exposure 1.5, drawdown
breakers 0.10/0.20, swing bucket 10, global 12. Iteration window strictly < 2025-01-01,
seed 42, warmup 250, CPCV purge 21, 15 paths. The 2025+ holdout is not touched.

---

## 1. Hypothesis

Vol-scaled sizing sets `units = risk_fraction × equity / stop_distance`, where the per-unit
risk measure (2.5×ATR stop distance, or annualised vol in the vol-target ceiling) treats
every return distribution as if it were Gaussian-shaped. It is not: equity/crypto daily
returns are negatively skewed and heavy-tailed, so the *tail* loss a position carries per
unit is systematically larger on heavy-tailed names than the stop-distance measure implies
— and the 2026-07-22 gap-aware fills made tail-through-stop losses real in the certified
numbers.

**H:** sizing positions by **tail-adjusted volatility** — a Cornish-Fisher quantile
multiplier on the per-unit risk measure — **contracts allocation on heavy-tailed/negatively
skewed names before shocks**, reducing gap-through-stop tail losses (worst day / worst
trade), **without paying for it in normal regimes** (monthly-profit cost ≤ 5%).

## 2. Formula and implementation (pre-registered constants)

Per instrument, per decision bar, on point-in-time log returns (no lookahead — the same
causality as the existing 63-day vol series):

- `S` = rolling **60-day** skewness of daily log returns (pandas `.rolling(60).skew()`).
- `K` = rolling **60-day excess kurtosis** (pandas `.rolling(60).kurt()`, Fisher convention).
- One-sided 99% quantile `z = 2.326`. Cornish-Fisher expansion:

  `z_CF = z' + (S/6)(z'²−1) + (K/24)(z'³−3z') − (S²/36)(2z'³−5z')`

  evaluated at the ADVERSE tail for the trade's direction: `z' = −z` for LONG (left tail),
  `z' = +z` for SHORT (right tail).
- Tail multiplier `τ = |z_CF| / z`. **Direction-aware** by construction: negative skew
  widens τ for longs, positive skew widens it for shorts, positive excess kurtosis widens
  both.
- Clips (pre-registered CF pathology guards): `S` to [−2, 2], `K` to [−2, 10],
  `τ` to **[1.0, 2.0]** — the mechanism can only CONTRACT a position, never enlarge it
  (τ < 1 is treated as 1: no upsizing on thin-tailed names; that is a different claim and
  would need its own prereg). Fewer than 60 returns / non-finite moments ⇒ τ = 1
  (certified sizing).

Application (RiskManager step 6, behind `risk.cf_cvar_enabled`, default OFF — certified
behaviour; `config.yaml` untouched):

- `units_risk = equity × risk_fraction / (τ × stop_distance_account)` — the SAME risk
  budget denominated in tail-adjusted per-unit risk;
- the vol-target notional ceiling uses `τ × ann_vol` (same multiplier, one consistent
  treatment of both sizing vols);
- **stop and target prices are unchanged** (exits, TradeManager, gap-aware fills all
  untouched); the recorded `risk_fraction` stays the honest planned loss at stop
  (`units × raw stop_distance / equity`), so the portfolio-risk cap accounting is
  unchanged in units and meaning. τ only shrinks units.
- Constraint label `cf_cvar_tau={τ:.2f}` (counted in the run's `constraint_log`);
  `sizing_detail["cf_cvar_tau"]` recorded for transparency.

## 3. Configurations (the full selection set: exactly 2)

| Config | Change | Question |
|---|---|---|
| `book_h_gold_252` (control) | none — certified params, flag OFF | anchor |
| `book_h_gold_252_cf_cvar` (challenger) | `cf_cvar_enabled=true` (z 2.326, window 60, τ ∈ [1,2]) | does tail sizing buy tail protection for ≤5% of monthly profit? |

## 4. Gates (identical machinery and thresholds to every prior gate)

For EACH config: DSR > 0.95 (deflated by the **full updated TrialLedger count**, §6) **and**
CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive (purge 21) **and** PBO < 0.5
across the 2-config selection set (16 splits, seed 42; standing caveat: ~100% universe
overlap, sizing-only difference — reported as computed). The challenger flag flows into
every CPCV fold via `cfg.risk`. (Fold edge effect: each fold's first 59 bars have τ = 1 —
rolling window restart, deterministic, conservative; documented.)

## 5. Pre-committed falsification / decision rule

- **H1 (tail improvement):** at least one of — challenger |worst daily return| ≤ 0.95 ×
  control's; challenger |worst trade P&L| ≤ 0.95 × control's; challenger max drawdown ≤
  control's − 1.0 percentage point.
- **H2 (cost ≤ 5%):** challenger avg monthly P&L ≥ 0.95 × control avg monthly P&L.
- **Verdict CONFIRMED** = H1 and H2 both hold **and** the challenger passes all three gates
  in §4. **REJECTED** otherwise (including: τ binds on ~no trades — mechanism inert —
  reported, not silently accepted).

Measured but not verdict-binding (reported for the record): full metric set (Sharpe, PF,
win rate, maxDD, expectancy, trades), τ distribution and bind counts per instrument,
CPCV path distributions, worst-month figures.

## 6. Ledger plan

`TrialLedger` at **n = 273** at writing (271 + 2 for the W1 order-invariant gate recorded
before its runs). This campaign evaluates exactly 2 configs, both under the certified risk
anchor (`max_risk_per_trade 0.01`), so exactly **2 new trials** (`book_h_gold_252`,
`book_h_gold_252_cf_cvar` with `kind=cf_cvar_gate`) are recorded BEFORE the first run →
**n = 275** deflates every DSR in this gate. No other configs will be evaluated; any
follow-up (z = 1.645, window 126, per-class τ caps, τ < 1 upsizing) is a new
pre-registration.

## 7. Known limitations

- **The 60-day window and 99% quantile are point choices**, not optimised — deliberately
  not swept (sweeping inflates selection bias; the ledger discipline exists precisely to
  charge for that).
- **CF is a 4-moment approximation.** It is known to be non-monotone in extreme tails;
  the clips exist for that reason. τ = 2.0 caps the contraction at halving.
- **In-window tail measurement.** One decade contains a handful of gap episodes; the
  worst-day metric is dominated by single events (the CPCV distribution is the more
  robust evidence). Skew/kurtosis estimated on 60 points are noisy — the mechanism
  contracts on ESTIMATED tail shape, which can be wrong name-by-name; H2 bounds what that
  noise costs.
- **Control must reproduce the anchor** (Sharpe 0.86284, 1637 trades, equity 292,551.34)
  — hard-checked before any comparison; a mismatch aborts the gate.
