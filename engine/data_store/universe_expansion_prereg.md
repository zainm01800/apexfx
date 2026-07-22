# PRE-REGISTRATION — Book J: breadth expansion of Book H + gold (2026-07-22)

**Status: pre-registered BEFORE any run.** No trials charged yet; charged at gate-run time.
This supersedes nothing — Book I's REJECT stands as recorded. This is a *second, cleaner*
test of the same hypothesis, and the reason for re-testing is stated below rather than being
quietly re-rolled until it passes.

**Base book:** `book_h_gold_252` (certified). Universe-only change; signal, sizing, exits,
regime, HTF gate, caps, costs, window (< 2025-01-01) and seed 42 are byte-identical.

## 1. Why re-test something that already failed

Book I (2026-07-20) rejected an 18-name expansion — **but not on its own merits.** All four
configs, *including the certified baseline*, failed the same shared leg: **PBO 0.602**. With
four near-identical overlapping books the in-sample winner's OOS rank is unstable, so PBO
condemned the whole set regardless of any individual book's quality. Meanwhile DSR was ~1.0
and **15/15 CPCV paths were positive for every config**, and the expanded book posted
**Sharpe 1.05 with maxDD 13.3%** against the baseline's 1.03 / 15.8%.

So the honest reading is: *the 4-config set design was the failure, not necessarily breadth.*
The pre-registered fix — written into the runner prereg the same week — is to test **two**
configs, the minimum that still computes PBO and the most rank-stable possible.

**This is the one and only re-test.** If a 2-config design also fails, breadth is dead for
this book and must not be re-proposed with yet another config count. Recording that here so
the boundary is fixed in advance, not negotiated afterwards.

## 2. Configs — exactly 2

| Config | Universe | Ledger |
|---|---|---|
| `book_h_gold_252` (baseline) | certified 21 equity+ETC + 11 crypto + 7 FX | dedup — already charged |
| `book_j_breadth_252` | baseline **+ 24 halal-screened large caps** | **1 NEW charge** |

Expected ledger: 209 → 210 (or 208 → 209 if the runner gate has not yet run; the DSR deflates
by whatever the FULL count is at run time).

**The 24 additions** (drawn from the 59 screened in `halal_screen_2026-07-22.md`, chosen for
sector spread and to avoid deepening the existing mega-cap tech tilt):

- Healthcare (8): JNJ, MRK, PFE, ABBV, LLY, TMO, ABT, ISRG
- Consumer staples (5): PG, KO, PEP, CL, MDLZ
- Consumer discretionary (4): NKE, HD, LOW, TJX
- Industrials/materials (5): LIN, UNP, ITW, HON, APD
- Energy (2): XOM, CVX

Deliberately NOT added: more semis or software (AVGO, AMAT, LRCX, KLAC, ADI, NXPI, TXN,
QCOM, MU, INTC, ADBE, CRM, ORCL, NOW, INTU, ACN, ANET, CSCO). The book is already long
semis/tech via AAPL/MSFT/NVDA/AMD/TSM + SMH/SOXX/XLK; adding more would raise breadth on
paper while concentrating the existing factor — the opposite of the hypothesis.

## 3. Hypothesis

**H-breadth:** 24 uncorrelated halal-screened names raise the book's risk-adjusted quality
(Grinold: IR ≈ IC·√breadth), *if* the trend edge generalises beyond the mega-cap names it was
certified on.

**Pre-registered counter-hypothesis:** the edge may be specific to high-momentum mega-caps.
Defensive staples and energy trend differently; adding them could dilute the signal and lower
Sharpe even as drawdown improves. **A lower-Sharpe/lower-drawdown outcome is NOT a pass.**

## 4. Gates + binding decision rule

DSR > 0.95 at the full ledger count; PBO < 0.5 across the 2-config set; CPCV 15 paths with
median OOS Sharpe > 0 and >50% positive.

**Adopt the expansion ONLY if it passes all three AND its DSR exceeds the baseline's on the
same snapshot.** Otherwise the certified book stands unchanged and breadth is closed as a
line of enquiry for this book.

## 5. Pre-registered caveats
1. **Present-day screening = survivorship/lookahead** in universe selection (halal_screen doc
   §3). Carried, not fixed. This alone means a pass here is weaker evidence than a pass on a
   point-in-time universe would be.
2. **Snapshot dependence:** the baseline reproduces at ~1.03 Sharpe on current parquets, not
   the certified 1.086. Both configs run on the SAME snapshot; the verdict is relative.
3. Determinism: seed 42, run twice, JSONs identical modulo `generated_at`.
4. 2025+ holdout untouched; iteration window ends 2024-12-31.
5. Newly fetched parquets (36 of the 59) have never been used in any prior gate, so they carry
   no accumulated selection bias from this project — but they are also unaudited for splits or
   adjustment artefacts beyond the standard `clean()` pass.

## 6. Deliverables
`scripts/run_portfolio_gate_book_j.py`, `data_store/validation/book_j_gate_<date>.json`
(+ determinism twin), `data_store/book_j_gate.md` (verdict in the first sentence), this prereg.
Exit code 0 only if the expansion passes AND beats the baseline DSR.
