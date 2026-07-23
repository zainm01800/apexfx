# PRE-REGISTRATION — Book T: meta-label gate on Book H (2026-07-23)

**Status: written BEFORE any meta-labelled run.** **6 trials charged** (ledger 252 → 258).

## 1. Why this, and why now

Every sizing lever is exhausted (~110 configurations across risk, slots, portfolio cap, vol
overlay and universe — all landed on one frontier). Profit = Sharpe × volatility × capital, and
volatility IS the drawdown constraint, so more profit at constant risk requires **more Sharpe**.
Sizing cannot produce that; only a better decision process can.

Meta-labelling is the one remaining mechanism that can raise Sharpe **without touching the risk
profile**, because it can only ever REMOVE trades — never add exposure, never resize, never
widen a stop. `max_risk_per_trade`, slots, and stop distances stay exactly as they are.

**Honest prior: this is expected to FAIL.** Meta-labelling was tested on single-pair FX in this
repo and did not beat its baseline. Eight experiments failed their gates today. Stated up front
so a negative result is not spun as a surprise.

## 2. Design — leakage control is the whole game

A meta-label model predicts whether the PRIMARY's trade will hit target before stop. Fitting
and evaluating on the same bars would be circular and would manufacture a spectacular fake
result. Therefore:

- **Train window:** start → **2019-01-01**. The secondary model sees only these bars.
- **Test window:** **2019-01-01 → 2025-01-01**. Never seen during fitting.
- Baseline and challenger are backtested over the **identical test window**, same universe,
  same risk config, same EV slot allocation, same gap-aware fills.
- The 2025+ holdout remains untouched.

The model is fitted **once** and held fixed across six test years. That is deliberately
conservative — a real deployment would refit periodically, so a stale-model result is a LOWER
bound, not an upper one. If a stale model cannot help, a refit one is unlikely to be the
difference.

## 3. Configs — 3 gated (6 charged)

| Config | threshold | Ledger |
|---|---|---|
| `book_t_baseline` (no meta gate) | — | charged |
| `book_t_meta_050` | 0.50 | charged |
| `book_t_meta_055` | 0.55 | charged |
| `book_t_meta_060` | 0.60 | charged |
| *(2 further thresholds examined in dev, charged, not gated)* | — | charged |

Secondary model: LightGBM (`model="gbm"`) with the wrapper's conformal calibrator, seed 42.

## 4. Gates + binding decision rule

1. **PAIRED TEST (binding):** circular block bootstrap on the daily return difference vs
   `book_t_baseline` over the test window (block 21, B=10,000, seed 42). Requires **p < 0.05**.
2. **DRAWDOWN NEUTRALITY (binding):** forward p95 1-year drawdown must not exceed the
   baseline's by more than **1 percentage point**. The entire premise is "more profit at the
   same risk" — if drawdown rises materially, the premise is void regardless of return.
3. **DSR > 0.95** at the full ledger count (n=258), reported; **not binding on its own** given
   the documented set-dependence (the same control scored 0.744, 0.9044 and 0.999 in three
   different gates today depending on the trial-Sharpe set).
4. **PBO** — reported, not binding (near-twin books).

**Adopt the highest-Sharpe threshold satisfying 1 AND 2.**

## 5. Falsifiable predictions, recorded in advance

- **The gate must actually bite.** If the meta model vetoes <5% or >60% of primary signals it
  is not doing the job — either inert, or it has destroyed the strategy. Veto rate is reported.
- **Precision must improve.** The mechanism is "skip the weakest trades", so **win rate and
  per-trade expectancy must both RISE** on the surviving trades. If trade count falls while
  expectancy is flat, the gate is removing trades at random — that is a coin-flip filter, not a
  model, and it should be rejected even if total return happens to look better.

## 6. Pre-registered counter-hypotheses

- **It removes winners too.** Momentum P&L is right-skewed: a minority of large winners carry
  the book. A filter that trims tails symmetrically will cut return more than risk.
- **Six years on a fixed model is a long time.** Regimes shift; a 2019-fitted model may be
  actively misleading by 2024. Reported per-year if it passes.
- **Fewer trades = thinner statistics.** A higher threshold means a smaller sample, so an
  apparent Sharpe gain is easier to produce by chance. The paired test is the guard.

## 7. Caveats

1. Test window is 6 years (2019-2024), shorter than the 12.8y full history, and contains one
   dominant equity bull regime plus two sharp drawdowns (Mar 2020, 2022).
2. Yahoo re-bases adjusted prices; quote figures with this snapshot date.
3. Determinism: seed 42 throughout.

## 8. Deliverables

`scripts/run_portfolio_gate_book_t.py`, `data_store/validation/book_t_gate_2026-07-23.json`,
`data_store/book_t_gate.md` with the verdict in the first sentence.
