# PRE-REGISTRATION — Book N: momentum lookback 126 on the halal book (2026-07-22)

**Status: pre-registered BEFORE the run.** 1 new trial charged at run time (ledger 217 → 218).

**Base book:** `book_h_gold_252` (certified). **Parameter-only change**: `momentum_lookback`
252 → 126. Universe, sizing, exits, regime, HTF gate, caps, costs, window (< 2025-01-01) and
seed 42 all byte-identical. Gap-aware stop fills ACTIVE on both sides (the honest model).

## 1. Why — prior evidence from a different universe

Book E (2026-07-17, `portfolio_gate_book_e_2026-07-17.json`) tested the frozen TrendBook
config on a wide 77-instrument universe at two lookbacks and nothing else:

| Config | lookback | Sharpe | return | maxDD | DSR | verdict |
|---|---|---|---|---|---|---|
| book_e_252 | 252 | 0.807 | 248% | 20.2% | 0.712 | REJECT |
| **book_e_126** | **126** | **1.152** | **649%** | **16.8%** | 0.962 | **PASS** |

Halving the lookback produced a **+0.35 Sharpe swing and a lower drawdown** on that universe.
Book E is not certified for an unrelated reason: its 77 instruments include rates and credit
ETFs, which are riba and fail the halal screen outright. The *parameter finding* was never
carried across.

**A ledger audit confirms every halal-lineage book — H, I, J, K, L, M, 10 configs in total —
has used lookback 252. Lookback 126 has never been tested on the halal universe.**

This is a hypothesis carried from an independent universe, not a parameter selected after
seeing it work on *this* one. That distinction is the difference between a legitimate test and
the outcome-selection that killed the Book J/PFE line of reasoning.

## 2. Configs — exactly 2

| Config | lookback | Ledger |
|---|---|---|
| `book_h_gold_252` (baseline) | 252 | dedup — already charged |
| `book_n_lb126_252` | **126** | **1 NEW charge** |

Only ONE variable moves. No sweep: 126 is the specific value with prior evidence, not the best
of a grid. **If this fails, lookback is closed — no 63, no 189, no grid search.** That boundary
is fixed here, in advance.

## 3. Hypothesis and honest counter

**H-lookback:** a 126-day momentum window responds faster to trend changes than 252, capturing
more of each move and exiting deteriorating trends sooner. Book E suggests this is worth a
material Sharpe gain.

**Pre-registered counter-hypothesis:** Book E's universe was dominated by broad-index, rates and
commodity ETFs, which trend on a different cadence from the mega-cap equities and crypto that
drive the halal book. A faster lookback may simply add whipsaw here — more trades, more cost,
lower expectancy. The halal book already sits at 15 trades/month; a shorter lookback will
increase that, and every added trade pays spread and slippage.

**Also possible: 126 was itself the lucky draw of a 2-config comparison.** Book E charged both
lookbacks, so it is not undisclosed selection — but n=2 is a thin basis and the result may not
replicate. Treat a Book N pass as suggestive, not decisive.

## 4. Gates + binding decision rule

DSR > 0.95 at the full ledger count (expected n=218); PBO < 0.5; CPCV 15 paths with median OOS
Sharpe > 0 and >50% positive.

**Adopt ONLY if it passes all three AND beats the baseline's DSR on the same snapshot.**

**Known caveat on PBO:** across seven prior gates PBO ran 0.15–0.86 and rejected five, because
it cannot discriminate between books sharing a signal and universe. Book N is a near-twin of
the baseline, so PBO is expected to be uninformative here. **It is still applied as the binding
rule and will NOT be overridden** — but if Book N fails on PBO alone while winning on DSR, CPCV
and the direct metrics, that is evidence for building the paired test, not for adopting Book N.

## 5. Caveats
1. Both sides run gap-aware fills, so this is not comparable to any pre-2026-07-22 figure.
2. Snapshot dependence: Yahoo re-bases adjusted prices; the verdict is relative, on one snapshot.
3. Determinism: seed 42, two runs, identical modulo `generated_at`.
4. 2025+ holdout untouched.

## 6. Deliverables
`scripts/run_portfolio_gate_book_n.py`, `validation/book_n_gate_2026-07-22.json` (+ determinism
twin), `data_store/book_n_gate.md` with the verdict in the first sentence.
