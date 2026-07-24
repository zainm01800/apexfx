# W2 — SHORT BORROW-FEE COST MODEL (2026-07-24): **the gate verdict does NOT flip**

Book H gold passes all gates with honest short-side financing: DSR **0.998 > 0.95** (deflated
by the full ledger, n=262), CPCV median **+0.047 with 15/15 paths positive**, PBO 0.0 across the
2-run measurement set. The certified standing is unchanged; the true cost of the previously
missing borrow leg is **−0.014 Sharpe** (−5.6 pts of total return over ~9 years).

Kind: **cost-model correction, not a strategy change** — recorded in the ledger as measurement
trials before running. Machine-readable results:
`engine/data_store/validation/borrow_fee_measurement_certified_2026-07-24.json` (certified
anchor) and `borrow_fee_measurement_2026-07-24.json` (current-config cross-check).

## What was missing and what was added

The v5 equity cost model charged 2.0 bps spread + 1.0 bps slippage per side on fills and
**nothing to carry a short**. Added `AssetClassConfig.short_borrow_bps_annual` (default 0.0 =
certified behaviour byte-identical), accrued in `PortfolioBacktester`'s mark-to-market loop per
bar held on the mark-to-market short notional (`units × price × bps/1e4 / bars_per_year`),
deducted from realised equity, per-trade P&L, and per-instrument P&L, and surfaced as
`metrics["short_borrow_fees_total"]`. The measurement charges **50 bps/yr on short equity
notional** — the easy-to-borrow large-cap assumption (IBKR easy-to-borrow large caps run
~25–75 bps/yr). Forex and crypto stay at 0 (the FX tom-next riba question is deliberately
unmodelled per book_h_prereg §7; crypto perps financing is a separate question).

## Certified anchor — reproduced EXACTLY before measuring

The certified book is the 2026-07-22 gap-aware state (`book_h_gapaware_2026-07-22.json`, risk
1.00%): **Sharpe 0.86284, PF 1.32452, 1637 trades, win 55.77%, maxDD 16.32%, final equity
292,551.34**. The measurement's borrow-OFF config reproduces those numbers **byte-for-byte**
(built-in hard check in the script) — but only with the certified **panel insertion order**
(`EQUITY_CORE` first, exactly how `run_portfolio_gate_book_h.py` builds per-book panels out of a
sorted master). An early draft of the measurement loaded the panel alphabetically and produced
Sharpe 0.329 — the 2026-07-22 ordering-audit finding (certified ordering = top of the
distribution, alphabetical ≈ a shuffle) reproduced live. No engine or data drift was found:
current code is byte-identical to `dab9955` for this path, zero gold-universe parquets changed
since the certified run, and `slot_allocation="order"` reproduces the historic single-pass
behaviour exactly.

**Work-order headline discrepancy, stated plainly:** the order's "corrected headline Sharpe
0.893, win 55.7%, PF ~1.4, maxDD 19.3%" mixes two different artifacts. **0.893 is the Book P/S
prop-campaign book** (0.75% risk / 12 slots, config comment in `e376291`), not Book H gold;
55.7%/1.42/19.3% are the **original 2026-07-19 gate** numbers (risk 2%, pre-gap-fix code,
Sharpe 1.086). Book H gold's newest certified anchor is **0.863 / 55.8% / 1.32 / 16.3%**
(2026-07-22, risk 1%, gap-aware). The borrow-fee deltas below are measured against that
reproduced anchor.

## Results — Book H gold, certified anchor (risk 1.00%), iteration window only

| Metric | baseline (borrow off) | borrow 50 bps/yr | Δ |
|---|---|---|---|
| Sharpe (ann.) | 0.86284 | 0.84880 | **−0.0140** |
| Profit factor | 1.3245 | 1.3194 | −0.0051 |
| Expectancy / trade | +120.44 (+1.022%) | +116.45 (+1.005%) | −3.99 (−0.016pt) |
| Win rate | 55.77% | 55.71% | −0.06pt |
| Max drawdown | 16.32% | 16.35% | +0.04pt |
| Total return (~9y) | +192.55% | +186.93% | −5.6pt |
| Trades | 1637 | 1637 | 0 |
| **Gates** | **PASS** | **PASS** | **no flip** |
| DSR @ n=262 | 0.998 | 0.998 | |
| CPCV median / frac | +0.048 / 15-of-15 | +0.047 / 15-of-15 | |
| PBO (2-run set) | 0.0 | 0.0 | |

**Why so small:** the book took 313 short equity trades over the decade whose gross P&L was
already **net-negative (−9.7k)** — the trend book's shorts are few, short-lived (managed exits),
and vol-scaled small. Total borrow charged: **£649.51** against £197k of net book P&L. The
honesty fix costs about a hundredth of a Sharpe point; the shorts' drag on the book was never
financing, it is signal.

**Current-config cross-check** (config.yaml drifted to risk 0.75% by owner decision 2026-07-23;
alphabetical panel — a non-certified ordering): the same correction costs −0.0048 Sharpe
(£251.68 on 206 short equity trades), verdict PASS both ways at n=260. Direction and magnitude
of the borrow effect are consistent across anchors.

## Determinism

Full measurement run executed twice (seed 42): outputs **byte-identical** modulo `generated_at`
(ledger dedups 262 → 262 on the second pass). Unit tests: `tests/test_short_borrow_fee.py`
(accrual-off zero, equity drag ≈ accrued fees exactly, longs unaffected, per-trade/per-instrument
accounting includes fees) + full suite green.

## Ledger

- **n before the 2026-07-24 work order: 258**
- **+2** W2 current-config measurement (`book_h_gold_252{,_borrow50bps}`, kind
  `cost_model_measurement`, mrpt 0.0075) → 260
- **+2** W2 certified-anchor measurement (same books, mrpt 0.01) → **262**
- Certified-anchor rerun deduped (262 → 262). Every DSR above is deflated by the full count
  used at run time.

## Limitations

- 50 bps/yr is a flat easy-to-borrow assumption; hard-to-borrow names (TSLA, PLTR in squeeze
  episodes) can run 1–10%+. A name×time-varying borrow curve is out of scope (no data feed);
  the sensitivity is linear — at 200 bps/yr the Sharpe delta would be ≈ 4× (−0.056), still
  not verdict-changing.
- Accrual is per bar held on the entry-day-to-day-before-exit convention; fees do not feed back
  into sizing (sizing is risk-based on entry). Both documented in the code.
- PBO across a 2-run set differing only by a cost leg is near-degenerate by construction
  (reported as computed, same caveat as every overlapping-family gate).
