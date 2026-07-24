# PRE-REGISTRATION — Notional-cap gate: 15% per-position cap on Book H gold (2026-07-24)

**Status: pre-registered BEFORE any capped run.** This document fixes the hypothesis, the
configuration set, the gates, the falsification rule, and the ledger plan before execution.
Changing anything after the run requires a new pre-registration and new ledger charges.

**Base book:** `book_h_gold_252` — the certified halal trend book: lookback 252, vol 63,
hold 21, rr 1.5, `rule_based` regime, HTF 1w×50 gate, managed exits, vol-scaled sizing,
**certified risk anchor `max_risk_per_trade = 0.01`** — the 2026-07-22 gap-aware certified
state (`book_h_gapaware_2026-07-22.json`: Sharpe 0.863, 1637 trades, PF 1.32, DSR 0.994 @
n=213). `config.yaml` has since moved to 0.0075 by the owner's explicit 2026-07-23 decision
(12% DD tolerance, for the forward book); the gate anchors on the certified state explicitly
rather than the drifted default. Remaining caps per config: max_portfolio_risk 0.065,
max_total_exposure 3.0, max_correlated_exposure 1.5, drawdown breakers 0.10/0.20.
Per-asset-class v5 costs **including the W2 borrow-fee machinery at its default OFF setting**
(0.0 bps — byte-identical to the certified cost model), daily bars, iteration window strictly
< 2025-01-01, seed 42, warmup 250, CPCV purge 21, 15 paths. The 2025+ holdout is not touched.

---

## 1. Hypothesis

Vol-scaled risk sizing sets units from the stop distance: `units = risk_fraction × equity /
stop_distance`. On low-vol names (AAPL-type mega-caps in quiet regimes) the ATR stop is tight
in price terms, so the same 1% risk buys a **large notional — up to ~15% of equity or more**.
The risk budget is fine with that (a tight stop contains the *planned* loss), but a
gap-through-stop does not honour the stop: the tail loss scales with **notional**, not with
planned risk. The 2026-07-22 gap-aware TradeManager fix made this real — stops that gap now
fill at the open, so single-name gap tails are in the certified numbers.

**H:** capping per-position notional at **15% of equity** (vs uncapped) reduces gap-tail
losses on low-vol names **without degrading book performance**.

## 2. Configurations (the full selection set: exactly 2)

| Config | Change | Question |
|---|---|---|
| `book_h_gold_252` (control) | none — certified params, cap flag 0.0 (off) | anchor |
| `book_h_gold_252_notional_cap15` (challenger) | `max_position_notional_pct = 0.15` | does the cap buy tail protection for free? |

The cap is implemented in `RiskManager.permit` as step 8.5 (after vol-target, gross-exposure,
and correlation caps, before the min-position floor): `notional = min(notional, 0.15 × equity)`.
The final `risk_fraction` is recomputed from the capped notional, so a capped trade simply
**bets less** — it is never re-levered back up to the per-trade risk budget. Constraint label:
`max_position_notional` (counted in the run's `constraint_log`).

Exactly 2 configs to keep PBO meaningful (a 2-config selection set, same as the Book C/D gate).

## 3. Gates (identical machinery and thresholds to every prior gate)

For EACH config: DSR > 0.95 (deflated by the **full updated TrialLedger count**, §5) **and**
CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive (purge 21) **and** PBO < 0.5
computed across the 2-config selection set (16 splits, 4000 combos, seed 42; reported as
computed with the standing caveat that the 2 configs share ~100% of their universe and differ
only in sizing).

## 4. Pre-committed falsification / decision rule

- **H1 (tail improvement):** the capped book's **worst daily loss** (equity-curve min daily
  return) is at least 10% smaller in magnitude than the control's, **or** its max drawdown is
  at least 1.0 percentage point smaller.
- **H2 (no degradation):** capped Sharpe ≥ control Sharpe − 0.10 **and** capped profit factor
  ≥ control PF − 0.10.
- **Verdict CONFIRMED** = H1 and H2 both hold **and** the capped config passes all three gates.
  **REJECTED** otherwise (including: cap barely binds — reported, not silently accepted).

Measured but not verdict-binding (reported for the record): trades count, expectancy,
win rate, `max_position_notional` bind count, per-trade notional/equity distribution.

## 5. Ledger plan

`TrialLedger` loaded fresh at **n = 262** (258 at the start of the 2026-07-24 work order + 2
for the W2 borrow-fee measurement at current config + 2 for the W2 certified-anchor rerun,
all recorded before their runs). This campaign evaluates exactly 2 configs, both under the
certified risk anchor (`max_risk_per_trade 0.01`), so exactly **2 new trials**
(`book_h_gold_252` and `book_h_gold_252_notional_cap15` with `kind=notional_cap_gate`) are
recorded BEFORE the first run → **n = 264** deflates every DSR in this gate. No other configs
will be evaluated; any follow-up (cap at 10% or 20%, per-class caps) is a new pre-registration.

## 6. Known limitations

- **The 15% level is a point estimate** from the work order's AAPL observation, not an
  optimised parameter. Sweeping the level would inflate selection bias; it is deliberately
  not swept.
- **Notional/equity at entry** uses the decision-day close equity (the same equity the
  RiskManager saw); quote-currency conversion is ignored, the standing engine approximation.
- **Tail measurement is in-window.** One decade contains a handful of gap events; H1's worst-day
  metric is dominated by single episodes. The CPCV path distribution is the more robust evidence.
- The cap interacts with the slot bucket: a capped position still occupies a full swing slot.
  If the cap binds heavily on the book's best names, H2 can fail through slot opportunity cost
  rather than through sizing — that is still a REJECT, reported as such.
