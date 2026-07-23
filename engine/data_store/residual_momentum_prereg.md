# PRE-REGISTRATION — Book R: residual (idiosyncratic) momentum (2026-07-23)

**Status: written BEFORE the gate run.** **12 trials charged** (ledger 232 → 244).

## 1. Honest disclosure of the search that produced this hypothesis

This did NOT come from a clean slate, and the disclosure matters because the effect size is
already known to me:

1. A **screen** (`scratch/screen_residual_wide.py`) swept `top_n ∈ {5,10,15,20,30}` for BOTH
   total-return and residual momentum — **10 cells**. Residual top-15 scored best
   (Sharpe 0.998). **That is outcome selection**, so all 10 cells are charged, plus the 2
   gated configs below.
2. A **deployment cost model** (`scratch/deploy_100k_residual.py`) then re-priced the winner
   with per-asset-class spreads and IBKR order minimums: **CAGR 8.77%, £731/month, Sharpe
   1.066, forward p95 DD 11.69%** on £100k.

Neither is a gated result. Both are in-sample, on a 7.9-year window (2017–2024) that contains
one large equity bull market and no sustained bear market.

**The screen and the engine will NOT agree**, and the differences are known in advance:
the screen had no stops, no slot caps, no regime filter, and it standardised the residual by an
*unshifted* volatility — a bug found by the strategy's own unit test and fixed (the vol window
is now shifted with the numerator). The engine implementation is the corrected one, so a lower
number here is expected and is not evidence of anything going wrong.

## 2. Mechanism — falsifiable, stated in advance

Total-return momentum ranks on raw past return, so in an equity-heavy panel the ranking is
dominated by market beta. Every additional position is then largely **the same bet**. This is
already measured on this engine: `frontier_breadth_slots.json` shows Sharpe falling
**0.922 → 0.704 → 0.460** as concurrent slots go 12 → 20 → 30, i.e. the marginal position
carries negative edge.

Residual momentum regresses out the cross-sectional market factor and ranks on the accumulated
residual, standardised by its own volatility. Blitz–Huij–Martens and Blitz–Hanauer–Vidojevic
report this roughly doubles momentum Sharpe on large stock cross-sections, via a ~halving of
strategy volatility rather than higher returns.

**Falsifiable prediction, recorded in advance:** if the mechanism is real, **breadth should
help residual momentum and hurt total momentum on the SAME panel**. The screen shows exactly
that (residual 0.757 → 0.963 → 0.998 at top 5/10/15; total 0.876 → 0.747). If the engine run
shows residual momentum degrading with breadth the way total momentum does, **the mechanism is
disproved** and the screen result was a fluke of construction, not a factor effect.

**Second prediction:** the residual book's returns should be materially **less correlated with
the equity market** than Book H's. If correlation is unchanged, residualisation is not doing
what it claims regardless of the Sharpe number.

## 3. Configs — 2 gated (12 charged)

| Config | signal | top_n | Ledger |
|---|---|---|---|
| `book_r_total_top15` (control) | total-return momentum | 15 | charged |
| `book_r_resid_top15` (challenger) | residual momentum | 15 | charged |
| *(10 screen cells — examined, charged, NOT gated)* | — | — | charged |

Both run through `PortfolioBacktester` with `slot_allocation="expected_value"`, gap-aware
fills, per-asset-class costs, and the SAME universe and risk settings, so the residualisation
is the only variable. `max_concurrent_trades` is raised to 15 to match `top_n` — otherwise the
12-slot cap would silently truncate the signal being tested.

Universe: every instrument in the store with ≥ `MIN_BARS` history, restricted to dates with
≥40 live names (the screen's harness bug — ragged start dates left 1,494 of 3,798 dates with
≤5 scored names, making every `top_n` identical — must not recur).

## 4. Gates + binding decision rule

1. **DSR > 0.95** at the full ledger count (n=244).
2. **CPCV, 15 paths**: median OOS Sharpe > 0 and >50% of paths positive.
3. **PBO** — computed and REPORTED, **not binding**. Across ten prior gates it ran 0.15–0.86 on
   near-identical machinery and rejected eight. Control and challenger here share a universe
   and a rebalance clock, so they are exactly the near-twin case it cannot discriminate.
4. **PAIRED TEST (binding):** circular block bootstrap on the daily return difference,
   residual vs total (`validation/paired_tests.py`, block 21, B=10,000, seed 42), **p < 0.05**.
5. **DRAWDOWN WALL (binding):** 95th-percentile forward 1-year drawdown **≤ 11%**.
6. **PROFIT FLOOR (reported, NOT binding):** CAGR ≥ 9.6% (£800/month on £100k). Recorded so the
   gate cannot be quietly re-scored against a target it was always going to miss — the honest
   expectation from the cost model is **~£731/month**, which FAILS this floor.

**Adopt `book_r_resid_top15` only if 1, 2, 4 and 5 all pass.** A Sharpe improvement that
breaches the drawdown wall is a REJECT. If it passes 1–5 but misses 6, the correct report is
"better than Book H, still short of the stated target".

## 5. Pre-registered counter-hypotheses

- **It is a bull-market artifact.** 2017–2024 contains one dominant equity regime. Residual
  momentum's apparent crash protection (2018 +1.2%, 2022 +0.8%) may be luck across two
  observations. Two data points are not a distribution, and this cannot be resolved on this
  sample — it is a reason to hold the 2025+ holdout in reserve, not to trust the result.
- **Turnover eats it live.** 710%/yr with ~221 orders/yr; commission minimums already cost
  0.44%/yr on £100k, nearly 3× the spread cost. Any slippage worse than modelled hits hard.
- **The market proxy is crude.** An equal-weight mean across 49 equities, 22 FX pairs and 2
  crypto is not a real factor model. The residual may be partly mis-specification.
- **Small-sample cross-section.** The papers use hundreds to thousands of stocks; 73 mixed
  instruments is far short, which is the likely reason the measured gain is well under the
  ~2× reported.

## 6. Caveats

1. In-sample, one snapshot; Yahoo re-bases adjusted prices.
2. 7.9-year active window after signal warmup (~15 months produce no position).
3. Determinism: seed 42, two runs, identical modulo `generated_at`.
4. **2025+ holdout untouched.** Iteration window < 2025-01-01.

## 7. Deliverables

`apex_quant/strategies/residual_momentum.py`, `tests/test_residual_momentum.py`,
`scripts/run_portfolio_gate_book_r.py`, `data_store/validation/book_r_gate_2026-07-23.json`,
and `data_store/book_r_gate.md` with the verdict in the first sentence.
