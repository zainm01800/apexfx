# GATE — SUKUK/GOLD DEFENSIVE CASH-SUBSTITUTE SLEEVE: **REJECTED (both challengers — the static mix clears every return leg but the sleeve's standalone max drawdown is 18.0% vs the pre-registered 8% cash-substitute cap; inverse-vol fails three legs)**

**Pre-registration:** `engine/data_store/defensive_sleeve_prereg.md` (written BEFORE any
challenger run; the 3 trials were recorded before execution). **Results:**
`engine/data_store/validation/defensive_sleeve_gate_2026-07-27.json`. **Script:**
`engine/scripts/run_portfolio_gate_defensive_sleeve.py`. **Window:** ITERATION only,
strictly < 2025-01-01; certified anchor (mrpt 0.01, EQUITY_CORE panel order, gap-aware
engine) reproduced EXACT by the control (Sharpe 0.86284, 1637 trades, equity 292,551.34).
**Ledger:** 279 → **282**; every DSR deflated by 282.

## What was tested

Routing the certified book's **idle capital** — `max(0, equity − gross open notional)` at
each daily mark; it averages **39–40% of equity** in-window — from zero-yield GBP cash
into a defensive sleeve of **SGLD.L (allocated gold ETC) + SPSK (USD sukuk ETF)**, charged
at the config one-way cost (2 bps/leg) on daily rebalances. Config A: static 50/50.
Config B: inverse-63d-vol weights. The sleeve compounds inside the certified equity curve,
so DSR/PBO/CPCV see exactly what a live book would; flag
`PortfolioBacktester(defensive_sleeve=...)`, default `None` = certified cash,
`config.yaml` untouched; the spec flows into every CPCV fold exactly like `trade_manager`.

## The pre-registered scoreboard

| | control (GBP cash) | A: static 50/50 | B: inverse-vol |
|---|---|---|---|
| Sharpe | 0.86284 (anchor, exact) | **0.98903** (+0.1262) | 0.85717 (−0.0057) |
| Sortino | 0.9341 | **1.1963** | **1.0271** |
| Profit factor | 1.3245 | **1.3678** | **1.3500** |
| Trades | 1637 | 1636 | 1638 |
| Win rate | 55.77% | 55.62% | 55.68% |
| Expectancy / trade | +120.44 (+1.022%) | **+150.89 (+1.006%)** | **+130.81 (+1.012%)** |
| Max drawdown | 16.32% | **15.91%** | 16.85% |
| Worst daily loss | −5.09% | −5.10% | **−4.51%** |
| Worst month | −19,673 | −22,169 (worse) | **−18,967** |
| Avg monthly P&L | +1,783 | **+2,540** (+757) | **+2,024** (+241) |
| Mean idle fraction | — | 39.5% | 39.7% |
| Sleeve net P&L / cost | — | +30,620 / 6,317 | +9,427 / 5,718 |
| Idle-capital yield (net) | 0% (cash) | **+3.20%/yr ✓ (≥2%)** | +1.10%/yr ✗ |
| Sleeve standalone Sharpe | — | **0.523 ✓ (≥0.25)** | **0.355 ✓** |
| Sleeve standalone maxDD | — | **18.03% ✗ (>8%)** | **17.81% ✗ (>8%)** |
| DSR @ n=282 | 0.9884 | **0.9967 ✓ (≥ ctrl)** | 0.9882 ✗ (< ctrl) |
| CPCV median / frac positive | +0.0476 / 15-of-15 | **+0.0543 / 15-of-15** | **+0.0554 / 15-of-15** |
| PBO (3-config set) | — | **0.48675 ✓ (< 0.5)** | — |

**Pre-registered rule (prereg §5):** a challenger is ADOPTED iff idle yield ≥ 2%/yr AND
sleeve standalone net Sharpe ≥ 0.25 AND sleeve standalone maxDD ≤ 8% AND book Sharpe
uplift ≥ +0.05 AND book DSR > 0.95 at full ledger count and not below control.
**Static 50/50: 4 of 5 legs hold — yield ✓, sleeve Sharpe ✓, uplift ✓, DSR ✓ — but sleeve
maxDD 18.03% > 8% ✗ → REJECTED. Inverse-vol: fails yield (1.10%/yr), maxDD (17.81%),
uplift (−0.006), DSR-below-control → REJECTED.** The standard three gates (DSR, PBO,
CPCV) pass for all three configs; the kill comes from the campaign's own risk definition
of "cash substitute", exactly as pre-committed.

## The honest reading

The return-side evidence for the static mix is genuinely strong — the strongest
challenger scoreboard this book has produced: +0.126 Sharpe, +£757/month average profit
(+42%), higher DSR than the control (0.9967 vs 0.9884 at n=282), higher CPCV median with
15-of-15 positive paths, maxDD and leverage essentially unchanged, and a clean PBO pass.
The idle capital really is large (≈40% of equity on average) and the sleeve really did
earn 3.2%/yr net on it in-window.

The kill leg is not a technicality. The sleeve's standalone 18.03% max drawdown runs from
the **2020-08-06 gold peak to the 2022-11-03 trough** (recovery only 2024-04-05): over
that window gold lost −21% **and** sukuk lost −16% *at the same time* — the 2022 rates
shock hit both "defensive" legs together. The crisis-convexity premise (gold and sukuk
uncorrelated in stress) is falsified in the one genuine stress episode the window
contains; a cash substitute that can sit −18% underwater for 21 months is not a cash
substitute by any definition this book may certify. The 8% cap was pre-committed; the
rule makes no exception for "the other four legs all passed". **REJECTED as a certified
change — recorded, not adopted. The certified zero-yield-cash book remains the book of
record.**

Inverse-vol is dominated, not just rejected: it costs nearly as much turnover as the
static mix (5,718 vs 6,317), earns a third of the yield (daily re-weighting churns the
mix for no benefit at 2 bps/side), and its book Sharpe is *below* control. No follow-up
on weighting schemes is warranted without a new prereg.

**Note for the owner:** the static sleeve's return case (4/5 legs, +£757/mo) is strong
enough that a *relaxed-DD* variant (e.g. a 12–15% sleeve-DD tolerance, a capped sleeve
fraction, or a crisis-filter overlay) is a legitimate **new pre-registration** — it would
need its own hypothesis, kill criteria, and ledger charges. What this gate certifies is
only that the 8%-cash-substitute version is false. Adopting the static sleeve un-gated as
a *policy* (as the 0.75% risk-per-trade was adopted on 2026-07-23) remains the owner's
call — flagged, not endorsed, here.

## Pre-registered caveats, restated where they bound the result

- SPSK lists 2019-12-31: the sukuk leg is cash pre-2020 (static mix runs at half yield;
  inverse-vol holds 100% gold once gold has 63 valid returns). The measured sleeve effect
  is mostly a 2020+ effect on a 2016+ window — and 2020+ contains exactly the 2022
  episode that kills it.
- The sleeve is an equity-curve overlay: no margin interaction, daily rebalance
  idealisation costed at 2 bps/leg/side.
- One stress episode is a small sample for a crisis-convexity claim — but the claim was
  the sleeve's raison d'être, and the episode is unambiguous.

## Determinism

Full gate executed twice (seed 42): **results payload byte-identical** modulo
`generated_at` and the ledger bookkeeping line (first pass 279 → 282, second pass dedups
282 → 282); the control reproduced the certified anchor exactly in both passes. Unit
tests: `tests/test_defensive_sleeve.py` (static mix constant + pre-listing cash;
ffill → 0 return on non-trading days; inverse-vol weights sum to 1 with the calm leg
dominant, missing legs zero-weighted and renormalised, all-cash when no valid vol;
flag-off certified and sleeve-metrics-free; idle cash accrues sleeve returns less costs
to a closed-form path; deployed capital does not accrue — entry/exit timing identical
with and without the sleeve; CPCV forwards the sleeve into every fold) + full suite green.

## Ledger

- **n before this gate: 279** (276 + 3 for the trend-ensemble gate, same day)
- **+3** (`defslv_control_cash`, `defslv_static_50_50`, `defslv_inverse_vol`, kind
  `defensive_sleeve_gate`, recorded before the first run) → **282**. Rerun deduped
  (282 → 282).
