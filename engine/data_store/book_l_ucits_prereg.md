# PRE-REGISTRATION — Book L: UCITS re-wrapping of the Book H equity sleeve (2026-07-22)

**Status: pre-registered BEFORE the run.** 1 new trial charged at run time.

**Base book:** `book_h_gold_252` (certified). Universe-only change; signal, sizing, exits,
regime, HTF gate, caps, costs, window (< 2025-01-01) and seed 42 byte-identical.

## 1. Why — this is a tradeability problem, not a performance hunt

On 2026-07-22 IBKR rejected live orders with error 201: *"This product does not have a KID...
Retail clients can trade packaged retail products only if an appropriate KID is available."*
Under PRIIPs, **a UK retail account cannot buy US-domiciled ETFs at all.** Five of the
certified book's equity-sleeve instruments are therefore permanently unreachable on the
account it is meant to run on: **XLK, XLE, XBI, SMH, SOXX.**

This experiment asks: *does the book still hold up when those legs are replaced with the
KID-compliant UCITS equivalents the account can actually trade?*

## 2. The swap (mapping from `ucits_mapping_2026-07-20.md`, data verified 2026-07-22)

| Out | In | LSE ticker | In-window bars |
|---|---|---|---|
| XLK | iShares S&P 500 Information Technology Sector UCITS | **IITU.L** | 2,301 |
| XLE | iShares S&P 500 Energy Sector UCITS | **IUES.L** | 2,301 |
| XBI | iShares Nasdaq US Biotechnology UCITS | **BTEC.L** | 1,818 |
| SMH | VanEck Semiconductor UCITS | **SMH.L** | 1,029 |
| SOXX | *(dropped — no clean US-only UCITS equivalent; SEMI.L is a global index and near-duplicate of SMH.L)* | — | — |
| ISWD.L | iShares MSCI World Islamic, **USD line** | **ISDW.L** | 2,779 |

The ISWD→ISDW change is included because it is the same fund: LSE `ISWD` is the **GBp pence
line**, which embeds GBP/USD in the price series and contaminates a USD-denominated trend
signal. `ISDW` is the USD line of the identical fund with *more* usable history (2,779 vs
2,273 bars). Not a new exposure — a currency-line correction.

Result: a **fully KID-compliant, UK-retail-tradeable** equity sleeve (20 instruments vs 21).

## 3. The decision rule is DIFFERENT here, and why

Books J and K required the challenger to **beat** the baseline's DSR, because in those the
baseline was a genuine alternative — you could simply keep the certified book.

**Here the baseline is untradeable.** Requiring the tradeable book to beat a book that cannot
be bought would be demanding the impossible for no benefit. So:

**Adopt if the swapped book PASSES all three gates on its own merits (DSR > 0.95, PBO < 0.5,
CPCV median > 0 with >50% paths positive).** The baseline comparison is computed and reported
in full — but it is *information*, not the bar.

This rule is written down before the run precisely so it cannot be mistaken for goalpost-
moving afterwards. If the swapped book FAILS its own gates, the honest conclusion is that the
sector-ETF legs are not viable on a UK retail account and the sleeve should be dropped, not
that the rule should be relaxed again.

## 4. Configs — exactly 2

| Config | Universe | Ledger |
|---|---|---|
| `book_h_gold_252` (baseline) | certified, contains untradeable US ETFs | dedup |
| `book_l_ucits_252` | swapped per §2 — fully tradeable | **1 NEW charge** |

## 5. Hypothesis and honest counter

**H-wrapper:** these are the same underlying exposures in a different wrapper, so the book's
edge should survive largely intact.

**Pre-registered counter-hypothesis — I expect real degradation, for three specific reasons:**
1. **XBI → BTEC is an INDEX change, not a wrapper change.** XBI tracks S&P Biotech
   **equal-weight**; BTEC tracks Nasdaq Biotechnology **cap-weighted**. Different
   concentration, different return profile. XBI's backtest evidence does **not** transfer.
2. **SMH.L has only 1,029 in-window bars** (launched Dec 2020) against SMH's full window, and
   **BTEC 1,818** (Oct 2017). The swapped panel is materially shorter on those legs, so their
   contribution is estimated from far less data.
3. **Dropping SOXX** removes an instrument outright; the semis exposure is now carried by a
   single, shorter series.

A pass here is therefore **weaker evidence** than the certified book's own pass, and must be
described that way in the report.

## 6. Caveats
1. LSE-listed UCITS trade in London hours; the engine models daily bars only, so intraday
   session differences vs US-listed originals are not captured. Cost model unchanged
   (equity 2.0 bps spread + 1.0 slippage per side) — plausible but unverified for these lines.
2. TER is not modelled by the engine (0.15–0.35% for these funds); a real-money drag the
   backtest does not show. Documented, not corrected.
3. Determinism: seed 42, two runs, JSONs identical modulo `generated_at`.
4. 2025+ holdout untouched.

## 7. Deliverables
`scripts/run_portfolio_gate_book_l.py`, `validation/book_l_gate_<date>.json` (+ determinism
twin), `data_store/book_l_gate.md`, this prereg.
