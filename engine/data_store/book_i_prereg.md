# PRE-REGISTRATION — Book I: universe expansion + pruning of Book H + gold (2026-07-20)

**Status: pre-registered BEFORE any run.** 3 new ledger trials charged before execution (§6).
Changing anything after the run requires a new pre-registration and new ledger charges.

**Base book:** `book_h_gold_252` (certified 2026-07-19: DSR 0.996 @ n=205 — 0.966 at its own
gate count deflated further since, PBO 0.272, CPCV 14/15; see
`data_store/validation/book_h_gate_2026-07-19.json`). Book I changes **the universe only** —
lookback 252, vol 63, hold 21, rr 1.5, `rule_based` regime, HTF 1w×50 gate, managed exits,
vol-scaled sizing, config caps (portfolio risk 6.5%, gross ≤3×, correlation clusters ≤1.5×),
v5 per-class costs, iteration window strictly < 2025-01-01, seed 42 — all byte-identical to
Book H. Any delta is attributable to the universe change alone.

## 1. Hypotheses (falsifiable, stated before the run)

- **H-prune:** XLE and ISWD.L are the book's two *documented* in-window drags — per the Book H
  gate JSON's per-instrument table, XLE is the worst instrument (−£18,612 across 35 trades) and
  ISWD.L fifth-worst (−£8,110 across 51 trades). ISWD.L's loss is consistent with a mechanical
  artifact: the LSE line is GBp-denominated, so its price series embeds GBP/USD and contaminates
  the USD-book trend signal (see `data_store/ucits_mapping_2026-07-20.md` — the USD line is
  ISDW). Removing both should not degrade — and may improve — the gated metrics.
- **H-exp:** 18 additional halal-screened names in under-represented sectors (healthcare,
  consumer, industrials, semis/equipment) add independent trend bets; breadth should raise
  portfolio Sharpe (Grinold) *if* the trend edge generalises beyond the mega-cap/tech names it
  was certified on. If it does not generalise, the gate must and will say REJECT.
- **Null outcome is acceptable and will be reported:** if no variant passes, the certified book
  stands unchanged.

## 2. Universe changes

### 2a. Pruned (H-prune)
| Out | Reason (documented, not retrospective) |
|---|---|
| XLE | Worst in-window instrument of the certified book (−£18,612 / 35 trades). Energy trend exposure retains a channel via the 18-name expansion's industrials and the crypto/FX sleeves; no dedicated replacement pre-registered. |
| ISWD.L | 5th-worst (−£8,110 / 51 trades) + GBp pence-line FX contamination (mapping doc). World-Islamic exposure remains via ISDU.L (USA Islamic, USD line) and ISDE.L (EM Islamic). |

### 2b. Added (H-exp) — 18 names, all with cached 1d history through the full window
Screen method: AAOIFI-style activity screen per name (no banks/insurance/riba income >5%, no
alcohol/gambling/pork/adult/weapons) + constituency evidence in AAOIFI/Shariah-screened retail
products (SP Funds SPUS, Wahed HLAL, iShares ISDU — public holdings) as the financial-ratio
proxy. Same honesty note as Book H §2: this engine has no point-in-time fundamentals feed, so
debt ratios cannot be recomputed over 2016–2024; the constituency screen is **present-day**
(survivorship/lookahead caveat §5.3).

| # | Name | Sector | Activity note | Screen evidence |
|---|---|---|---|---|
| 1 | JNJ | Healthcare | pharma/medtech | SPUS/ISDU constituent |
| 2 | MRK | Healthcare | pharma | SPUS constituent |
| 3 | PFE | Healthcare | pharma | SPUS/HLAL constituent |
| 4 | ABBV | Healthcare | pharma (debt elevated post-Allergan; passes present-day ratio via mcap growth — flagged) | SPUS constituent |
| 5 | PG | Consumer staples | household products | SPUS top-20 |
| 6 | KO | Consumer staples | non-alcoholic beverages | DJIM/ISDU vintage constituent |
| 7 | PEP | Consumer staples | beverages/snacks | Islamic index constituent |
| 8 | NKE | Consumer disc. | apparel/footwear | SPUS constituent |
| 9 | HD | Consumer disc. | home improvement retail | SPUS top-20 |
| 10 | LIN | Industrials/materials | industrial gases | SPUS top-20 |
| 11 | UNP | Industrials | railroad | SPUS constituent |
| 12 | ITW | Industrials | diversified industrial products | SPUS constituent |
| 13 | AMAT | Semis equipment | wafer-fab equipment | SPUS constituent |
| 14 | TXN | Semis (analog) | analog chips | SPUS constituent |
| 15 | QCOM | Semis | wireless chips/licensing | SPUS constituent |
| 16 | MU | Semis (memory) | DRAM/NAND | HLAL constituent |
| 17 | INTC | Semis | chips/foundry (chronic 2021-24 downtrend — the book may SHORT it; long+short permitted by the user's ruling) | DJIM/SPUS vintage constituent |
| 18 | CSCO | Networking hardware | switches/routers | SPUS constituent |

**Candidates examined and EXCLUDED (do not re-propose without new evidence):**
CAT & DE (captive finance arms — interest income ≈5-7% of revenue, activity fail),
BA / GE / ITA / XLI (defense revenue >5% — activity fail; XLI holds the defense primes),
MCD (pork/haram food service), COST & WMT (alcohol/tobacco/pork grocery lines, compliance
contested across screeners — conservative exclusion), XLV (holds UNH/CI/ELV insurers — fund-level
activity fail), all financials, AVGO/CRM/ADBE/ORCL (screen-passing but mega-cap tech — violates
the diversification intent of this experiment).

**Deviation from the work order, stated:** the order suggested "healthcare, industrials,
semis-equipment". Consumer staples/discretionary (5 names) are added beyond that list because
they serve the order's actual *goal* — uncorrelated additions — better than more semis;
the semis sleeve (6) is capped to avoid deepening an existing factor tilt. Accepted risk:
semis additions still correlate with AMD/NVDA/TSM/SMH/SOXX already in the book (§5.2).

**Data provenance:** 15 of 18 already cached by prior research; LIN, UNP, ITW fetched
2026-07-20 via the engine's standard yahoo adapter (2015-01→, `clean()`ed, same pipeline as
every other equity parquet). All 18 verified ≥2016-01-04 → ≥2026-07-17 daily coverage.

## 3. Configs (the FULL selection set — exactly these, nothing else)

| Config | Universe | Count (equity+ETC) | Ledger |
|---|---|---|---|
| `book_h_gold_252` | certified baseline (comparator) | 21 | dedup — already charged 2026-07-19 |
| `book_i_prune_252` | gold − {XLE, ISWD.L} | 19 | **NEW charge** |
| `book_i_exp_252` | gold + 18 | 39 | **NEW charge** |
| `book_i_exp_prune_252` | gold + 18 − {XLE, ISWD.L} | 37 | **NEW charge** |

Every panel additionally carries the unchanged 11-crypto + 7-FX sleeves (MATIC/USD drops via
the standard MIN_BARS skip, as in Books D/H). Params for all four: `carry_filter: False`,
`momentum_lookback: 252`, plus `COMMON_PARAMS` — byte-identical to the Book H run.

## 4. Gates (identical machinery, thresholds, and code paths as Book H)

1. **DSR > 0.95**, deflated by the FULL ledger count after charging (expected n = 208).
2. **PBO < 0.5** computed across this 4-config selection set (pre-registered caveat, same as
   Book H: overlapping universes limit PBO's discriminative power — reported as computed).
3. **CPCV 15 paths** (C(6,2), purge = 21-bar horizon, 1% embargo): median OOS Sharpe > 0 and
   >50% of paths positive.

**Decision rule (binding):** among configs passing ALL three gates, adopt the highest-DSR one;
tie → fewer instruments (parsimony). If only the baseline passes — or nothing does — **adopt
nothing.** Adoption here means: recommend to the user for the NEXT book iteration. The frozen
forward paper test (Book D) and the certified Book H record are untouched by any outcome.

## 5. Pre-registered caveats
1. Determinism: seed 42; the gate is run twice; results JSONs must match exactly modulo the
   `generated_at` timestamp and wall-clock log lines.
2. Factor tilt: 6 semis additions load on a factor the book already holds; the correlation-
   cluster cap (≤1.5×) is the mitigation and its binding frequency will be reported.
3. Present-day screening = survivorship/lookahead in universe selection (names chosen knowing
   they exist and are screened TODAY). Identical compromise as STOCKS_12 in Books D/H,
   carried, not fixed, here — a point-in-time universe is out of scope.
4. The 2025+ holdout is not touched. Iteration window ends 2024-12-31.

## 6. Ledger plan
Charge exactly 3 new trials (canonical keys `book_i_prune_252` / `book_i_exp_252` /
`book_i_exp_prune_252`, factory `trend_book_mtf`, timeframe 1d) BEFORE the first run;
`book_h_gold_252`'s record dedups against its 2026-07-19 key. Expected ledger: 205 → 208.
Every DSR in this experiment deflates by 208. Re-runs dedup (no further growth).

## 7. Deliverables
`scripts/run_portfolio_gate_book_i.py` (thin sibling of the Book H gate),
`data_store/validation/book_i_gate_2026-07-20.json` (+ a `_run2` twin for the determinism
check, then deleted after byte-comparison), `data_store/book_i_gate.md` (honest report),
and this prereg. Exit code 0 only if at least one non-baseline config passes.
