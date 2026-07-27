# PRE-REGISTRATION — Sukuk/gold defensive cash-substitute sleeve on Book H gold (2026-07-27)

**Status: pre-registered BEFORE any challenger run.** This document fixes the hypothesis, the
configuration set, the accrual/cost formulas, the gates, the adoption/kill rule, and the
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

The rule-based regime filter scales the book down in hostile regimes (constraint log:
regime scales of 0.3–0.7 bind on a large share of decisions; `timeframe_bucket_full` fires
~18k times). The undeployed fraction of equity — the certified book's peak gross leverage
is 2.59× but its TYPICAL deployment is far lower — sits in **zero-yield GBP cash**. A
defensive sleeve of **sukuk (SPSK: investment-grade USD sukuk, halal carry, low equity
beta) + allocated gold (SGLD.L: crisis convexity, ρ≈0 to equities)** is the natural
cash-substitute: both legs are already in the data store and in the Book H halal universe
research.

**H:** routing the book's idle capital into a gold/sukuk defensive sleeve **beats GBP cash
by ≥ 2%/yr net** on the idle capital, with sleeve-level risk inside defensive bounds
(net Sharpe ≥ 0.25, max drawdown ≤ 8%), lifting the **book by ≥ +0.05 Sharpe without
degrading its deflated significance**.

## 2. Formulas and implementation (pre-registered constants)

**Idle capital (per daily mark t, inside the certified PortfolioBacktester):**

- `gross(t) = Σ_open positions |units × last mark|` (same notional convention as the
  existing `_max_gross_leverage` measure);
- `idle_frac(t) = max(0, 1 − gross(t)/equity(t))` — leverage (gross > equity) ⇒ idle 0.

**Sleeve legs and returns (point-in-time, no lookahead):** SGLD.L and SPSK daily closes,
in-window only. Returns are aligned to the book's union timeline by forward-filling closes
(non-trading days ⇒ 0 return; weekends come from the crypto calendar). **Before a leg's
first bar its return accrues 0% (cash)** — SPSK lists 2019-12-31, so the sukuk leg is cash
pre-2020 (documented, conservative; no backfill, no renormalisation for config A).

**Sleeve mix x_leg(t):**

- Config A (static): `x_SGLD = x_SPSK = 0.5` always.
- Config B (inverse-vol): `x_leg(t) ∝ 1/σ_leg(t)`, `σ_leg(t)` = trailing 63-day std
  (ddof=1) of the leg's aligned daily returns. A leg with < 63 valid returns gets weight 0
  and the other leg is renormalised to 1; if neither is valid the sleeve is all cash.

**Accrual and cost (daily, at mark-to-market, behind a constructor flag — default OFF =
certified GBP cash):**

- Sleeve P&L: `accrual(t) = equity(t−1) × idle_frac(t−1) × Σ_legs x_leg(t−1) × r_leg(t)`
  — the cash idle during day t earns day t's sleeve return. Causal: everything is known by
  the close of t.
- Rebalance cost: target weights `w_leg(t) = x_leg(t) × idle_frac(t)`;
  `cost(t) = Σ_legs |w_leg(t) − w_leg(t−1)| × equity(t) × oneway_leg`, with
  `oneway_leg = (½·spread_bps + slippage_bps)/10⁴` from the config mechanics (2 bps both
  legs). Establishing the sleeve from cash costs the same one-way rate.
- `realized += accrual − cost; equity += accrual − cost` — the sleeve compounds inside the
  certified equity curve, so DSR/PBO/CPCV see exactly what a live book would.
- Flag: `PortfolioBacktester(..., defensive_sleeve=DefensiveSleeveSpec(...))`;
  `None` (default) ⇒ byte-identical certified behaviour. `config.yaml` untouched. The spec
  is forwarded into every CPCV fold exactly like `trade_manager`.

## 3. Configurations (the full selection set: exactly 3)

| Config | Sleeve | Question |
|---|---|---|
| `defslv_control_cash` (control) | none — certified GBP cash | anchor — must reproduce the certified numbers exactly |
| `defslv_static_50_50` (challenger A) | 50/50 SGLD/SPSK on idle capital | does a static defensive mix beat cash? |
| `defslv_inverse_vol` (challenger B) | inverse-vol (63d) SGLD/SPSK on idle capital | does risk-weighting the mix beat cash? |

Universe, params, costs, caps, warmup, window: identical for all three; the ONLY difference
is what idle capital earns. Certified panel insertion order (EQUITY_CORE first).

## 4. Gates (identical machinery and thresholds to every prior gate)

For EACH config: DSR > 0.95 (deflated by the **full updated TrialLedger count**, §6)
**and** CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive (purge 21) **and**
PBO < 0.5 across the 3-config selection set (16 splits, seed 42; standing caveat: ~100%
universe overlap, cash-accrual-only difference — reported as computed).

## 5. Pre-committed adoption / kill rule

A challenger is **ADOPTED** iff ALL of the following hold (measured on the full in-window
run unless stated):

- **Idle-capital yield ≥ 2%/yr net:** `(total net sleeve P&L) / (mean idle capital) /
  (window years) ≥ 0.02`. (Cash yields 0; this is "beats GBP cash by ≥2%/yr net".)
- **Sleeve standalone net Sharpe ≥ 0.25** and **sleeve standalone max DD ≤ 8%** — the
  sleeve as a standalone fully-invested asset on the union timeline:
  `r_sleeve(t) = Σ x_leg(t−1) r_leg(t) − Σ |x_leg(t) − x_leg(t−1)| × oneway_leg`,
  compounded; Sharpe annualised at 252 like the book.
- **Book Sharpe uplift ≥ +0.05:** `Sharpe(challenger) − Sharpe(control) ≥ 0.05`.
- **Book DSR not degraded:** challenger DSR > 0.95 at the full ledger count **and**
  challenger DSR ≥ control DSR (no significance give-back).

**KILL: any leg fails ⇒ that challenger is REJECTED.** The control failing to reproduce
the anchor aborts the gate (hard check, §7).

Measured but not verdict-binding: full metric set (Sharpe, PF, win rate, maxDD, worst day,
£-per-month, expectancy), PBO, mean idle fraction, sleeve accrual/cost totals, CPCV path
distributions.

## 6. Ledger plan

`TrialLedger` at **n = 276** at writing; the multi-horizon trend ensemble gate (same day,
earlier campaign) records 3 → 279 before this campaign runs. This campaign evaluates
exactly 3 configs, so exactly **3 new trials** (`defslv_control_cash`,
`defslv_static_50_50`, `defslv_inverse_vol` with `kind=defensive_sleeve_gate`) are
recorded BEFORE the first run → **n = 282** deflates every DSR in this gate. No other
sleeve mixes or weighting schemes will be evaluated; any follow-up (other legs, caps,
rebalance bands, deployment-fraction variants) is a new pre-registration.

## 7. Known limitations

- **SPSK history starts 2020** — the sukuk leg is cash before then (static mix runs at
  half yield pre-2020; inverse-vol holds 100% gold once gold has 63 valid returns). The
  sleeve's measured effect is therefore mostly a 2020+ effect on a 2016+ window; that is
  reported, not hidden.
- **The sleeve is an equity-curve overlay, not a traded book** — no margin interaction,
  no intraday liquidity model; daily rebalance to the idle fraction is an idealisation,
  costed at the config one-way rate.
- **Gold and sukuk correlate in crises differently than in-window** — the in-window sample
  contains 2020 and 2022, but the sleeve's crisis convexity claim rests on few episodes;
  the CPCV distribution is the more robust evidence.
- **Control must reproduce the anchor** (Sharpe 0.86284, 1637 trades, equity 292,551.34)
  — hard-checked before any comparison; a mismatch aborts the gate.
