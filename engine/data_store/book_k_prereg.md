# PRE-REGISTRATION — Book K: independence-selected breadth (2026-07-22)

**Status: pre-registered BEFORE the run.** 1 new trial charged at run time (ledger 209 → 210).

**Base book:** `book_h_gold_252` (certified). Universe-only change; signal, sizing, exits,
regime, HTF gate, caps, costs, window (< 2025-01-01) and seed 42 byte-identical.

## 1. Why this is a different hypothesis, not a re-roll of Book J

Book J is REJECTED and stays rejected (`book_j_gate.md`). Its 24 names were chosen by
**sector label**, and a post-verdict correlation audit found that proxy was measurably wrong:
the set contained near-duplicates of instruments the book already held —

| Book J addition | max abs corr | against |
|---|---|---|
| XOM | 0.885 | XLE (already in book) |
| CVX | 0.859 | XLE |

Grinold's `IR ≈ IC·√breadth` requires **independent** bets. A name correlating 0.88 with an
existing holding adds turnover and cost while contributing ~no breadth. So Book J tested
"more names", not "more independent bets" — a specific, structural defect with a specific fix.

**This is the distinction being tested, and it is the last one.** If Book K fails, breadth is
closed permanently for this book: no further selection rules, no further config counts. That
boundary is fixed here, in advance, precisely because "one more variant" is how discipline
erodes into rolling dice until the answer is liked.

## 2. The selection rule (mechanical, ex-ante, no performance data)

Applied to the 59 names screened in `halal_screen_2026-07-22.md`, using daily returns over
the iteration window only:

1. Compute each candidate's correlation against **every** instrument in the certified book.
2. **Reject any candidate with max |corr| ≥ 0.50 to any existing holding** (near-duplicate).
3. Rank survivors by **mean |corr|** ascending; take the **12** most independent.

Rule 2 removes 22 of 59 — every semiconductor (KLAC 0.895, LRCX 0.881, AMAT 0.872 vs SOXX)
and every energy name (COP 0.869, CVX 0.859, EOG 0.856 vs XLE). No performance figure enters
the rule at any point; correlation is a structural property.

**The resulting 12 (fully specified before the run):**
`ABBV, MRK, PG, KMB, PEP, CL, MDLZ, PFE, ABT, ORLY, KO, MNST`
(mean |corr| 0.048–0.084 to the book; 9 also appeared in Book J, 3 are new: KMB, ORLY, MNST)

Count = 12, i.e. half Book J's 24, chosen because the dilution hypothesis says fewer-but-
independent beats more-but-redundant. Not swept — one count, pre-registered.

## 3. Configs — exactly 2 (Book I's rank-stability lesson)

| Config | Universe | Ledger |
|---|---|---|
| `book_h_gold_252` (baseline) | certified | dedup — already charged |
| `book_k_indep_252` | baseline + the 12 above | **1 NEW charge** |

## 4. Hypothesis and the honest counter

**H-independence:** Book J diluted because it added redundant names. Adding only maximally
independent, non-duplicate names should add genuine breadth and raise risk-adjusted quality.

**Pre-registered counter-hypothesis (I consider this at least as likely):** these 12 are
overwhelmingly defensive staples (PG, KO, PEP, CL, KMB, MDLZ, MNST) and large pharma (ABBV,
MRK, PFE, ABT). **They are independent of the book precisely BECAUSE they do not participate
in the momentum regime the strategy harvests.** Low correlation is not the same as a
tradeable trend edge — a name can be beautifully uncorrelated and simply never trend enough
to clear the 1.5R barrier after costs. If so, Book K dilutes for a *different* reason than
Book J did, and breadth is genuinely dead for this signal.

## 5. Gates + binding decision rule

DSR > 0.95 at the full ledger count; PBO < 0.5 across the 2-config set; CPCV 15 paths, median
OOS Sharpe > 0 and >50% positive.

**Adopt ONLY if the challenger passes all three AND its DSR exceeds the baseline's on the same
snapshot.** Higher return with worse risk-adjustment is a REJECT. Same rule as Book J, applied
the same way regardless of which direction the numbers fall.

## 6. Caveats
1. Correlations are computed on the iteration window — the same data the book was certified
   on. That is still selection using evaluation data; the trial charge and the CPCV/DSR/PBO
   stack are what defend against it. A pass here is weaker evidence than a pass on a
   point-in-time universe would be.
2. Present-day halal constituency screening = lookahead in universe construction
   (`halal_screen_2026-07-22.md` §3). Carried, not fixed.
3. Determinism: seed 42, two runs, JSONs identical modulo `generated_at`.
4. 2025+ holdout untouched.

## 7. Deliverables
`scripts/run_portfolio_gate_book_k.py`, `validation/book_k_gate_<date>.json` (+ determinism
twin), `data_store/book_k_gate.md` (verdict first sentence), this prereg.
