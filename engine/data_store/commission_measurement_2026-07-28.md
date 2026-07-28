# COMMISSION HONESTY FIX (2026-07-28): **the book's standing does NOT change**

Book H gold's certified anchor survives the honest equity commission: **−0.013 Sharpe,
−£51/month** once equity orders pay the IBKR-measured $1.09/side. The v5 equity cost
model charged 2.0 bps spread + 1.0 bps slippage per side and **zero commission**
(`asset_classes.equity.commission_per_trade: 0.0`); the IBKR paper mirror shows the
real venue charges on every order. The correction is small because the fee is ~£2.18
round trip against +£120 expectancy per trade — but it was unmodelled, and now it is
measured.

Kind: **cost-model correction measurement, not a strategy change** — 1 trial recorded
in the ledger BEFORE running (kind `commission_measurement`, 296 → 297; the baseline
dedups). Machine-readable results:
`engine/data_store/validation/commission_measurement_2026-07-28.json` (+ determinism
twin, byte-identical modulo `generated_at`/ledger bookkeeping). Harness:
`engine/scripts/run_commission_measurement.py`.

## What was missing and what was measured

The IBKR paper mirror (account DUQ278370 — the venue mirror of the frozen Book D paper
test, `data_store/pre_registration_paper_trend_2026-07-17.md`) records real
commissions per fill. The 6 equity orders filled so far (2026-07-17/21, US large caps,
$4.9k–$14.7k notional) paid **mean $1.0902 per order** ($1.00–$1.21; mean 1.43 bps/side,
median 1.39). IBKR's **$1/order minimum dominates** at the book's order sizes, so the
honest shape is a **flat per-order fee**, not a bps rate — which is exactly what the
existing `AssetClassConfig.commission_per_trade` plumbs: `PortfolioBacktester` charges
it at entry and at every close/trim, i.e. once per order, $2.18 per round trip.

**The flag:** the override lives ONLY in the measurement harness (a `deepcopy` of the
live config with `asset_classes.equity.commission_per_trade = 1.09`).
**engine/config.yaml is untouched — the frozen Book D paper test's cost model and the
funded runner's live config keep commission 0.0.** No engine code changed; the
commission plumbing already existed and is suite-covered.

## Certified anchor — reproduced EXACTLY before measuring

The certified book is the 2026-07-22 gap-aware state (`book_h_gapaware_2026-07-22.json`,
risk 1.00%): **Sharpe 0.86284, PF 1.32452, 1637 trades, win 55.77%, maxDD 16.32%,
final equity 292,551.34**. The commission-OFF control reproduces those numbers
**EXACTLY** (built-in hard check, certified panel insertion order `EQUITY_CORE` first),
in the main run and again in the determinism twin. Iteration window only
(strictly < 2025-01-01), seed 42.

## Results — Book H gold, certified anchor (risk 1.00%), iteration window only

| Metric | baseline (comm 0) | commission $1.09/side | Δ |
|---|---|---|---|
| Sharpe (ann.) | 0.86284 | 0.85006 | **−0.0128** |
| Profit factor | 1.3245 | 1.3171 | −0.0075 |
| Expectancy / trade | +120.44 (+1.022%) | +116.09 (+0.994%) | −4.35 (−0.028pt) |
| Win rate | 55.77% | 55.77% | 0.00pt |
| Max drawdown | 16.32% | 16.34% | +0.03pt |
| Total return (108m) | +192.55% | +187.06% | −5.49pt |
| Final equity | 292,551.34 | 287,059.12 | −5,492.22 |
| Trades | 1637 | 1637 | 0 |
| **£ / month (month-end equity, 108m)** | **+1,782.88** | **+1,732.03** | **−50.85 (−2.9%)** |

Commission accounting: 1,410 equity round trips → fee floor £3,073.80 (2 × 1.09 ×
1,410; managed-exit partials and trims each add one more per-order fee). The all-in
realised drag is **£7,117.29** of net P&L — fees plus the compounding feedback
(commissions skim equity, so subsequent risk-based sizes shrink slightly). Signals,
barriers and trade count are untouched, as they must be: commission never changes a
fill price, only the accounting.

## Recommendation for the funded-runner cost model

1. **Adopt `commission_per_trade = 1.09` for equity in the funded-runner cost model.**
   The measured drag at the certified book's trade frequency (~13 equity round
   trips/month) is **≈ £51/month per £100k**. Because the fee is per ORDER it does
   not shrink with risk-per-trade: at the funded 0.75% risk the currency drag is the
   same order (~£45–55/mo), a **high-single-digit % haircut** on the £587/mo headline
   — material for honesty, not for viability. Re-quote the funded-runner CAGR with
   the honest cost before showing it to a venue/prop firm.
2. **Do NOT touch the frozen Book D paper test.** Its cost model is part of the
   pre-registered design; changing it mid-window would invalidate the paper-vs-mirror
   divergence series that produced this very measurement. When the pre-registered
   window closes, re-certify with the honest cost.
3. **Re-measure when the mirror has LSE fills.** The 6-order sample is US names only;
   the LSE UCITS legs (ISWD/ISDU/ISDE/SGLD) carry a different commission schedule, so
   1.09 applied to them here is a lower bound.

## Determinism

Full measurement executed twice (seed 42): outputs **byte-identical** modulo
`generated_at`/ledger bookkeeping; certified-anchor reproduction EXACT in both
passes; ledger dedups 297 → 297 on the second pass. `py_compile` clean; full suite
green. No engine code changed (harness + ledger + results only).

## Ledger

- **n before: 296**
- **+1** commission measurement (`book_h_gold_252_commission109`, kind
  `commission_measurement`, mrpt 0.01) → **297** — recorded BEFORE the run
- Runs dedup (297 → 297). The baseline `book_h_gold_252` key already existed.

## Limitations

- **6-order sample**, US large caps only, from the first mirror week. The point
  estimate $1.09 is solid (5 of 6 orders sit at the $1 minimum) but thin; re-measure
  as the mirror accrues fills, especially the first LSE UCITS orders.
- **Currency:** commissions are USD; the book's accounting unit is £. Used unconverted
  (1.09 account-units/side) — at GBPUSD ~1.3 the sterling-honest figure is ~£0.84/side,
  so the measured drag is ~25–30% **conservative** (overstates the cost).
- **Size dependence:** the $1 minimum dominates only while orders stay below roughly
  ~$30k notional (IBKR tiered $0.0035/share). If the funded book scales well past
  £100k, the fee transitions toward the per-share schedule and a bps model becomes
  the better shape; the flat 1.09 stays conservative until then.
- Sizing feedback is included in the drag (risk-based sizing on post-commission
  equity), so £7,117 is the honest all-in figure, not just fees (£3,074 floor +
  partials).
