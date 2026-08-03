# Quantamental comparison report: does the ≥70 fundamental gate help?

**Date:** 2026-08-03 · **Status:** owner-requested comparative MEASUREMENT — **not an
adoption gate; no DSR/PBO verdict is produced or implied** (1 measurement trial
recorded in the shared TrialLedger before the runs, `kind: quantamental_comparison`,
ledger n 304 → 305). **Prereg:** `engine/data_store/quantamental_prereg.md` (locked
pre-run, incl. Amendment A1). **Results:**
`engine/data_store/validation/quantamental_comparison_2026-08-03.json` (committed
compact digest — repo convention keeps validation JSONs ≤ ~100KB; trades/equity
omitted) + `…2026-08-03.full.json` (679KB local audit artifact with every trade and
bar, regenerated deterministically by rerun; the sha256 below covers the FULL
payload, so the digest certifies the omitted sections).
**Determinism:** two consecutive official runs, seed 42 —
`RESULT_SHA256 0e22a9751ed8742f989b14daa71cf48a8ff515b9707153e240570e10a339e0d7`
identical both times; canonical payloads byte-identical.

**Window:** ITERATION only, 2016-01-04 → 2024-12-31 (strictly < the sealed 2025+
holdout, which was never loaded). Daily bars from the engine ParquetStore; bar counts
per name in the window: 2264 for NVDA, MSFT, AAPL, TSM, NFLX, AMD, META, AMZN, SPY,
QQQ; PLTR 1070 (lists 2020-09-30); GME 2264 after clipping its 2010→2024 YahooAdapter
backfill (3774 bars fetched, cached as `GME_1d.parquet`) to the common window.

**Machinery:** certified `PortfolioBacktester` + `RiskManager` + `TradeManager`
(managed exits: partials 50% @1R / 25% @1.5R, breakeven, Chandelier 2.0×ATR trail,
21-bar time-exit) + `RegimeGatedMomentum`(252) + `MultiTimeframeMomentum`(1w, 50) +
certified `DirectionalEntryGate` seam for the spec's entry rule. Costs: 2.0bps spread
(1.0bps half per fill) + 1.0bps slippage per side + $1.09 commission per side, next-bar
open fills, gap-aware stops. Sizing per spec: `S = min(0.20 × Cap/(2.5×ATR14), 0.065 ×
Cap)` — the 6.5% notional cap was the binding term on ~99% of permitted trades
(`max_position_notional` bound 634/562 times), so realised risk per trade ≈ 0.3–1.0%
of equity. Breakers: 50% size step at 10% DD (fired twice, book A only), certified
halt at 15% DD (never fired). `config.yaml` and every `apex_quant` module untouched —
overrides are in-memory in `engine/scripts/run_quantamental_comparison.py`.

## (b) Fundamental screening table (as-of 2026-08-03)

Source: Yahoo Finance `quoteSummary` v10 (cached per ticker in
`engine/data_store/fundamentals_cache/`). **Substitution disclosed:** the spec named
FMP, but `FMP_API_KEY` is not in `engine/.env` (it lives only in the Vercel deployment
environment — see `engine/scripts/build_earnings_calendar.py`) and no Vercel CLI
session exists locally. Same five raw inputs were used. **The snapshot is as-of
2026-08-03, not point-in-time — see caveat C1.**

| Ticker | P/E pts (trailing P/E) | Margin pts (GM%) | Growth pts (YoY rev) | Solvency pts (FCF, D/E) | **Score** | Gate ≥70 |
|---|---|---|---|---|---|---|
| NVDA | 18 (30.8) | 25 (74%) | 25 (+85%) | 25 (FCF+, 6.6%) | **93** | PASS |
| MSFT | 25 (25.9) | 25 (68%) | 18 (+18%) | 25 (FCF+, 29.1%) | **93** | PASS |
| AAPL | 18 (35.5) | 18 (49%) | 18 (+16%) | 25 (FCF+, 78.4%) | **79** | PASS |
| PLTR | 0 (138.3) | 25 (84%) | 25 (+85%) | 25 (FCF+, 2.5%) | **75** | PASS |
| TSM  | 18 (35.6) | 25 (64%) | 25 (+36%) | 25 (FCF+, 15.2%) | **93** | PASS |
| NFLX | 25 (22.6) | 18 (49%) | 18 (+13%) | 25 (FCF+, 55.2%) | **86** | PASS |
| AMD  | 0 (158.7) | 18 (53%) | 25 (+38%) | 25 (FCF+, 6.0%) | **68** | **FAIL** |
| META | 25 (21.0) | 25 (82%) | 25 (+28%) | 25 (FCF+, 43.0%) | **100** | PASS |
| AMZN | 25 (21.8) | 18 (51%) | 18 (+20%) | 25 (FCF+, 45.6%) | **86** | PASS |
| GME  | 25 (16.2) | 5 (34%) | 18 (+14%) | 10 (FCF−, 74.3%) | **58** | **FAIL** |
| SPY  | N/A — index ETF (pass-through, both books, by prereg) | | | | — | allowed |
| QQQ  | N/A — index ETF (pass-through, both books, by prereg) | | | | — | allowed |

**Strategy B trades 10/12 names; AMD (68) and GME (58) are blocked.** AMD misses by 2
points on valuation alone (P/E 158.7 → 0 pts); GME fails on margins (34% → 5 pts) and
negative FCF.

## (a) Performance table (2016-01-04 → 2024-12-31, net of costs, £100,000 book)

| Metric | **A — pure technical** | **B — quantamental ≥70** | B − A |
|---|---|---|---|
| Trades | 629 | 554 | −75 |
| Win rate | 59.6% | 60.6% | +1.0 pp |
| Total net return | +64.7% | +53.0% | −11.7 pp |
| Annualised return | 5.7% | 4.8% | −0.9 pp |
| Annualised Sharpe | 0.76 | **0.86** | **+0.10** |
| Max drawdown | 11.1% | **7.1%** | **−4.0 pp** |
| Profit factor | 1.41 | **1.57** | **+0.16** |
| Expectancy / trade | £101.33 | £96.85 | −£4.48 |
| £/month on £100k (107.9 mo) | **£591** | £497 | −£94 |
| Worst single day | −6.06% | −1.82% | −4.2 pp |
| Bars at ≥10% DD | 6 (2024-08-05→12) | 0 | — |

Exit-reason mix (both books): ~90% stop-outs (mostly breakeven/trailed stops after
partials), ~10% certified time-exits; the 10R full-target backstop never fired.

Per-name net P&L (A): NVDA +10.7k, PLTR +10.1k, AAPL +8.9k, NFLX +8.3k, **GME +8.3k**,
META +6.1k, MSFT +5.7k, QQQ +4.1k, TSM +2.7k, SPY +1.8k, AMZN −0.1k, **AMD −2.8k**.
The gate removed 76 AMD+GME trades that netted **+£5.4k** (39/76 losers): it dodged
AMD's −£2.8k but also forfeited GME's +£8.3k — the book's #5 contributor, thanks to the
2021 meme squeeze a static quality score cannot see.

## (c) Drawdown analysis — the three sell-off windows

Window-local peak-to-trough DD of each book's equity curve; "false breakouts" = trades
entered inside the window that closed red.

| Window | A maxDD | B maxDD | A entries (false BO, win%) | B entries (false BO, win%) | A window P&L | B window P&L |
|---|---|---|---|---|---|---|
| 2018 Q4 | 4.0% | **2.6%** | 6 (5, 17%) | 5 (5, 0%) | −£1,993 | −£2,324 |
| 2020 COVID | 6.9% | **6.6%** | 26 (16, 38%) | 23 (14, 39%) | −£5,372 | −£4,060 |
| 2022 bear | 7.6% | **4.8%** | 12 (9, 25%) | 10 (7, 30%) | −£5,843 | −£2,497 |

Findings:

- **The gate reduced every sell-off window's drawdown** (−1.4pp, −0.3pp, −2.8pp) and
  the full-window maxDD by 4.0pp (11.1% → 7.1%). Mechanically it is a concentration
  cut: removing 2 of 12 names (17% of the roster, including the two highest-beta ones)
  frees slots and trims correlated exposure in risk-off tapes (`max_correlated_exposure`
  binds 150× in A vs 169× in B on fewer names — the remaining book is less crowded per
  unit of risk budget).
- **It did not filter "false breakouts" any better than chance.** Window win rates are
  17–38% for A vs 0–39% for B — statistically indistinguishable at these counts (5–26
  entries per window). In 2018 Q4 B was actually worse per trade. The DD benefit comes
  from *fewer simultaneous positions*, not from better entries.
- The only full-window breach of the 10% DD step breaker was book A around the
  2024-08-05 carry-unwind spike (6 bars; size halved twice). B's worst point (7.1%)
  never reached the amber zone. The 15% halt never fired in either book.

## (d) Honest caveats

- **C1 — Look-ahead bias, stated prominently: the score uses 2026-08-03 fundamentals
  for a 2016–2024 backtest.** Today's quality winners were selected with tomorrow's
  newspaper. This **flatters B**: in 2016–2019 several gate-passers would not have
  passed (NFLX's FCF turned durably positive only in 2020; META's 2022 margin trough;
  PLTR was pre-profitability until 2023), and AMD would have passed comfortably for
  much of 2016–2021. Every B number in this report is an **upper bound** on what a
  point-in-time gate would have done.
- **C2 — Single window, single universe.** One 9-year path, N=12 (10 scored). ~600
  trades sounds like a lot, but the A/B *difference* rests on 2 names and ~76 trades.
  A Sharpe difference of 0.10 over ~9 years has a standard error of roughly ±0.3 —
  the improvement is **statistically indistinguishable from noise**, which is exactly
  why this was run as a measurement, not an adoption gate.
- **C3 — Threshold sensitivity.** AMD fails by 2 points (68 vs 70) on one pillar's
  cliff edge (P/E 158.7 → 0 instead of 10 at P/E 90). Move the P/E bracket or the
  threshold slightly and book B's membership changes; the result is not robust to the
  scoring formula's exact edges.
- **C4 — The gate's stock-picking was wrong in-window.** It kept every mega-cap winner
  (easy with 2026 data) but its two rejects netted +£5.4k — GME was a top-5
  contributor. A fundamental quality gate on a *momentum* book caps exactly the
  high-octane names momentum exploits.
- **C5 — Fresh-cross degeneracy disclosure.** The literal "EMA20 crosses above EMA50"
  reading of the entry rule produced 6 trades in 9 years (SPY/QQQ: zero) — no
  measurement possible. The state reading (EMA20>EMA50 AND relvol>1.2) was locked by
  prereg Amendment A1 *before* any official run, on implementability grounds only.
- **C6 — HTF filter deviation.** The spec says "1-week HTF EMA trend filter"; the
  certified `MultiTimeframeMomentum` filter is a 50-week **SMA**, reused per the
  certified-machinery mandate (window unspecified in the spec). Flagged, not hidden.
- **C7 — FMP → Yahoo substitution** (see §(b) header). Different vendor, same five
  fields; scores are reproducible from the cached JSONs.
- **C8 — ETFs pass through unscored** in both books (SPY+QQQ contributed +£5.9k in A).
  They dilute the gate's measured effect — with them, B's gate covers only 10 of 12
  slots' worth of risk.

## Headline verdict

**A risk-adjusted wash, flattering B.** Net of costs, the ≥70 gate costs return
(−11.7pp total, −£94/month on £100k) and buys disproportionately less drawdown
(maxDD 11.1% → 7.1%, worst day −6.1% → −1.8%), leaving Sharpe 0.76 → 0.86 and PF
1.41 → 1.57. On this single 9-year, 12-name window that trade-off is *mildly*
attractive but **within statistical noise**, and caveat C1 means even that is an upper
bound — a point-in-time gate would have looked worse. As measured, the gate's benefit
is concentration control, not breakout selection. Recommendation: no adoption; if the
idea has legs, the next honest test is a point-in-time fundamentals rebuild (FMP
historical or Compustat-style snapshots) with the same prereg discipline.

## Reproduce

```bash
cd engine
.venv-mac/bin/python scripts/run_quantamental_comparison.py          # ledger-recorded run
.venv-mac/bin/python scripts/run_quantamental_comparison.py --no-ledger   # smoke test
```

Cache-first: with `data_store/fundamentals_cache/` present the run is offline and
deterministic. `--refresh-fundamentals` re-fetches (a new experiment — the gate
membership above is the locked one).
