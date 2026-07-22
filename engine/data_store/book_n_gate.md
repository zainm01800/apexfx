# Book N gate — lookback 126 REJECTED; lookback is now CLOSED (2026-07-22)

**Verdict: adopt nothing. Lookback 252 stands.** The 126-day variant lost outright on the
direct metrics and both books failed the shared PBO leg (0.576).

| | Baseline (252) | Lookback 126 |
|---|---|---|
| Sharpe | **0.863** | 0.786 |
| Total return | **193%** | 160% |
| Max drawdown | **16.3%** | 16.9% |
| Trades | 1,637 | 1,646 |
| DSR (n=218) | 0.9936 ✓ | 0.9874 ✓ |
| CPCV positive | 15/15 ✓ | 15/15 ✓ |
| **PBO** | **0.576 ✗** | **0.576 ✗** |
| Verdict | REJECT | REJECT |

## Why it was worth testing
Book E (2026-07-17) tested the frozen TrendBook config on a wide 77-instrument universe at two
lookbacks and nothing else: **252 → Sharpe 0.807 (REJECT), 126 → Sharpe 1.152 (PASS)**. A ledger
audit then showed **every one of the ten halal-lineage configs (H, I, J, K, L, M) used lookback
252 — 126 had never been tried on the halal universe.** The hypothesis therefore came from an
independent universe, not from selecting a winner on this one.

## Why it failed — the pre-registered counter-hypothesis held
Book E's universe was dominated by broad-index, rates and commodity ETFs, which trend on a
slower, cleaner cadence. The halal book is mega-cap equities, crypto and FX, which whipsaw at a
126-day window: 9 more trades for 33 percentage points less return and a slightly worse
drawdown. Faster is not better here.

**Per the prereg, lookback is now CLOSED — no 63, no 189, no grid search.** Sweeping it after a
single failed value is the fishing expedition the prereg was written to prevent.

## Mechanics
Prereg before the run; 1 trial charged (ledger **217 → 218**). Gap-aware fills active on both
sides. Determinism: two runs identical modulo `generated_at` and the expected pre/post-charge
`n_trials_before`. Iteration window < 2025-01-01; holdout untouched.

## Caveat that outweighs this result
Both figures here are **single-ordering measurements**. The same-day audit
(`ordering_sensitivity_audit.md`) shows the baseline's Sharpe moves between 0.217 and 0.863
purely on instrument iteration order, with the certified ordering the luckiest of seven. A
0.077 Sharpe gap between two books is well inside that noise, so this rejection rests on the
consistent direction of the direct metrics (return −33pts, drawdown worse) rather than on the
Sharpe difference alone.
