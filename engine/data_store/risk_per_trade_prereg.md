# PRE-REGISTRATION — Book P: risk-per-trade 0.50% / 0.75% vs 1.00% (2026-07-22)

**Status: pre-registered BEFORE the gate run.** **5 trials charged** (ledger 221 → 226).

## 1. Honest disclosure of the search that produced this hypothesis

A diagnostic sweep of **five** risk-per-trade values was run before this prereg existed:

| risk/trade | ann | vol | Sharpe | maxDD | portfolio-cap hits |
|---|---|---|---|---|---|
| 0.50% | 7.32% | 6.44% | **1.138** | **10.3%** | **0** |
| 0.75% | **10.65%** | 9.51% | 1.119 | 14.3% | 3 |
| 1.00% (current) | 7.72% | 10.66% | 0.725 | 18.2% | 163 |
| 1.50% | 3.50% | 7.88% | 0.444 | 20.5% | 595 |
| 2.00% | 3.39% | 8.16% | 0.416 | 21.0% | 1,204 |

**0.50% and 0.75% were selected BECAUSE they scored best.** That is outcome selection, and the
honest correction is to charge the ledger for **all five values examined**, not the two being
gated. DSR therefore deflates by n=226, not 223. Anything less would understate the search.

## 2. Mechanism — why this is not merely a volume knob

Position sizing is capped by `max_portfolio_risk = 0.065`. When risk-per-trade is large the cap
**binds and truncates positions arbitrarily**: the trade receives whatever budget remains rather
than the size the signal warranted. Cap-hit counts rise 0 → 163 → 1,204 across the sweep, and
trade count collapses 1,694 → 458 at 2.0%. This is the same class of defect as the slot-ordering
artifact (`ordering_sensitivity_audit.md`): an arbitrary rule overriding a considered decision.

**Falsifiable prediction stated in advance:** the Sharpe gain tracks the *cap-hit count*, not the
risk level per se. If a configuration with few cap hits fails to improve Sharpe, the mechanism is
wrong and the result was a fluke of the sweep.

## 3. Configs — 3 gated (5 charged)

All configs use `slot_allocation="expected_value"` so the ordering artifact is eliminated
(measured spread 0.000) and risk-per-trade is the ONLY variable. Gap-aware fills ACTIVE.

| Config | risk/trade | Ledger |
|---|---|---|
| `book_p_rpt100` (baseline, current setting) | 1.00% | charged |
| `book_p_rpt075` | 0.75% | charged |
| `book_p_rpt050` | 0.50% | charged |
| *(1.50% and 2.00% — examined, charged, NOT gated)* | — | charged |

## 4. Gates + binding decision rule

1. **DSR > 0.95** at the full ledger count (n=226).
2. **CPCV 15 paths**: median OOS Sharpe > 0, >50% positive.
3. **PBO** — computed and REPORTED, but **not binding**. Across eight prior gates it ran
   0.15–0.86 and rejected six, because it cannot discriminate books sharing a signal and
   universe (~0.99 correlated). This is exactly that case.
4. **PAIRED TEST (binding for the A/B comparison):** circular block bootstrap on the daily
   return difference vs baseline (`apex_quant/validation/paired_tests.py`, block 21, B=10,000,
   seed 42). Requires **p < 0.05** that mean(challenger − baseline) > 0.
5. **DRAWDOWN WALL (binding, and the reason this experiment exists):** the configuration must
   keep the **95th-percentile forward 1-year drawdown ≤ 10%** at the vol it actually runs.
   A configuration with higher Sharpe but a breaching drawdown is a **REJECT** — on a funded
   account a breach ends the account regardless of return.

**Adopt the highest-Sharpe config that satisfies ALL of 1, 2, 4 and 5.** On the sweep numbers
0.75% (maxDD 14.3%) is expected to FAIL rule 5 while 0.50% (10.3%) is expected to pass — that
prediction is recorded here so it cannot be revised afterwards.

## 5. Pre-registered counter-hypothesis

Lower risk-per-trade may simply be **shrinking the book toward cash**: a smaller bet is
mechanically less volatile, and Sharpe can rise while the strategy captures less of its own
edge. Evidence for this would be materially lower total return at 0.50% — which the sweep does
show (7.32% vs 10.65% at 0.75%). If the paired test shows no significant *risk-adjusted*
improvement, the correct conclusion is that 1.00% was simply oversized for a 6.5% portfolio
cap, and the honest fix is to raise the cap or lower the size — not to claim an edge.

## 6. Caveats
1. In-sample, one snapshot; Yahoo re-bases adjusted prices.
2. Gap-aware fills active on all sides — not comparable to any pre-2026-07-22 figure.
3. Determinism: seed 42, two runs, identical modulo `generated_at`.
4. 2025+ holdout untouched. Iteration window < 2025-01-01.

## 7. Deliverables
`scripts/run_portfolio_gate_book_p.py`, `validation/book_p_gate_2026-07-22.json` (+ determinism
twin), `data_store/book_p_gate.md` with the verdict in the first sentence and the forward
drawdown distribution for every gated config.
