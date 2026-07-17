# Sub-daily candidate validation — BTC/ETH close-momentum & USD fix-flow

**Date:** 2026-07-17 · **Runner:** `engine/scripts/run_intraday_candidates.py` · **Evidence base:** `docs/research/2026-07-17_subdaily_edges_post_cost.md`

**Bottom line: both candidates REJECTED by the gate (6/6 runs, plus a 4-run follow-up).** The only cell showing anything resembling the documented effect — crypto close-momentum with a 2-bar hold + vol filter at 2.5bps RT — is CPCV-positive on 100% of paths but is far too small to survive multiple-testing deflation (DSR 0.03 at n=136), decays post-2021, and dies at 10bps costs. The fix-flow trade shows no gross edge at all at 1h granularity on OANDA majors. Nothing here is tradeable at our costs.

---

## Protocol (honesty rules, as enforced by the runner)

- Iteration window **strictly < 2025-01-01** on every series (hard assert per frame; the 2025+ holdout was never loaded).
- All configs recorded in the shared **TrialLedger BEFORE any validation ran**: ledger **120 → 136** (16 new keys: 2 instruments × 4 configs per candidate; cost levels share keys). Every DSR below is deflated by the **final n=136**, including the follow-up runs.
- Gates (unchanged system rules): DSR > 0.95, PBO < 0.5, CPCV median OOS Sharpe > 0 with > 50% positive paths.
- Exit mode `barrier` with 8×ATR(14,1h) stop/target so the **time barrier** is the binding exit (the academic trades are pure fixed-horizon timing bets; TradeManager's trail/breakeven/partials would change what is measured). Verified: 100% "time" exits except 1–2 single "target" hits per multi-year run.
- No-lookahead verified mechanically: signals read only bars ≤ t via the point-in-time accessor; the engine fills at the **next bar's open**. Entry-timing audit (exact timestamps captured from the engine): 100% of close-momentum entries at the 20:00-UTC bar open (the signal bar's close instant); 100% of fix-flow entries at the 16:00-London bar open (the fix instant).
- Full per-run records: `engine/data_store/validation/intraday_2026-07-17/*.json`. Supabase posting deliberately skipped (research sweep); local validation cache updated for config-cost runs only.

## Data

| Series | Source | Span (iteration window) | Bars | Notes |
|---|---|---|---|---|
| BTC/USD 1h | **Binance public klines API** (`api.binance.com/api/v3/klines`, no keys), BTCUSDT, fetched 2026-07-17 by `scripts/fetch_binance_1h.py` → `data_store/BINANCE_BTC_USD_1h.parquet` | 2018-01-01 → 2024-12-31 | 61,246 | USDT≈USD (no conversion). 27 exchange-outage gaps (mostly 2018–19), reported not filled. Yahoo was unusable (~730d of 1h ⇒ ~6mo in-window). |
| ETH/USD 1h | same, ETHUSDT → `BINANCE_ETH_USD_1h.parquet` | 2018-01-01 → 2024-12-31 | 61,246 | as above |
| EUR/USD 1h | OANDA via existing adapter + store cache (in-memory merge only) | 2021-03-15 → 2024-12-31 | 23,808 | store cache had holes (2022-H1 everywhere; 2024-H1 for JPY/GBP/CHF); gap-filled from OANDA in ~150-day segments (the adapter's own pagination stalls after ~200 days — worked around in the runner, shared code untouched). |
| USD/JPY 1h | same | 2021-03-15 → 2024-12-31 | 23,815 | as above |

**Bar-timestamp convention:** both datasets are labeled by bar **open** time, UTC (the de-facto store convention for 1h — verified: last Friday bar is 21:00, week closes 22:00 UTC). A bar labeled H covers [H, H+1h); its close is the (H+1) price. `data/schema.py` aspires to close-time labels; the strategies document and follow the actual convention.

**Cost levels:** crypto `rt2.5` = config v5 as-is (1.5bps spread + 0.5bps slippage per side ≈ **2.5bps round-trip**); crypto `rt10` = stressed 8+1bps per side = **10bps round-trip**. FX = config v5 per-pair: EUR/USD unlisted→class default ≈ 1.0 pip + 1.0bps ≈ **~1.9bps RT**; USD/JPY pair override 1.4 pips ≈ **~0.9bps RT**.

---

## Candidate 1 — BTC/ETH US-close momentum — **REJECT**

Strategy: `apex_quant/strategies/intraday_close_momentum.py`. Rest-of-day return 00:00 UTC open → 20:00 UTC close; |R| > dead-zone(0) and (optionally) day volume AND |R| above trailing 20-day medians; enter in sign(R) at next bar open; time exit after `hold_bars`. Grid: `hold_bars`∈{1,2} × `vol_filter`∈{on,off} (4 configs). Simplifications vs Shen et al. (2022): fixed 00:00 UTC day anchor (paper uses a volume-spike open and the 17:00 ET CME break), 1h bars instead of 30-min, Binance instead of 5-venue aggregate.

### Gate results (headline config h=1, vol_filter=on)

| Instrument | Cost | DSR (>0.95) | PBO (<0.5) | CPCV med OOS | frac +paths | Verdict | Trades/yr | Net bps/trade |
|---|---|---|---|---|---|---|---|---|
| BTC/USD | rt2.5 | 0.000 ✗ | 0.006 ✓ | −0.002 | 33% ✗ | **REJECT** | 87.9 | **−8.84** |
| BTC/USD | rt10 | 0.000 ✗ | 0.000 ✓ | −0.007 | 0% ✗ | **REJECT** | 87.9 | −16.34 |
| ETH/USD | rt2.5 | 0.000 ✗ | 0.000 ✓ | −0.002 | 27% ✗ | **REJECT** | 97.9 | **−7.46** |
| ETH/USD | rt10 | 0.000 ✗ | 0.000 ✓ | −0.009 | 13% ✗ | **REJECT** | 97.9 | −14.96 |

### Per-config net bps/trade (the grid; all 7y, 615–2,545 trades per cell)

| Config | BTC rt2.5 | BTC rt10 | ETH rt2.5 | ETH rt10 |
|---|---|---|---|---|
| h=1, vf=on (headline) | −8.84 | −16.34 | −7.46 | −14.96 |
| h=1, vf=off (364/yr) | −4.69 | −12.19 | −4.22 | −11.72 |
| **h=2, vf=on** | **+5.00** | −2.48 | **+10.29** | +2.78 |
| h=2, vf=off | −2.10 | −9.59 | −0.72 | −8.21 |

### Follow-up gate: h=2 + vf=on as baseline (no new trials; same n=136 deflation)

| Instrument | Cost | DSR | PBO | CPCV med OOS | frac +paths | Verdict |
|---|---|---|---|---|---|---|
| BTC/USD | rt2.5 | **0.031 ✗** | 0.006 ✓ | **+0.008 ✓** | **100% ✓** | **REJECT** (DSR) |
| BTC/USD | rt10 | 0.000 ✗ | 0.000 ✓ | +0.004 ✓ | 67% ✓ | **REJECT** (DSR) |
| ETH/USD | rt2.5 | **0.001 ✗** | 0.000 ✓ | **+0.006 ✓** | **87% ✓** | **REJECT** (DSR) |
| ETH/USD | rt10 | 0.000 ✗ | 0.000 ✓ | +0.001 ✓ | 60% ✓ | **REJECT** (DSR) |

### Pre/post-2021 splits (h=2, vf=on — the only positive cell)

| Instrument | Cost | pre-2021 Sharpe (n) | post-2021 Sharpe (n) |
|---|---|---|---|
| BTC/USD | rt2.5 | +0.218 (251) | +0.087 (360) |
| BTC/USD | rt10 | +0.136 | −0.011 |
| ETH/USD | rt2.5 | +0.116 (313) | +0.081 (366) |
| ETH/USD | rt10 | +0.036 | −0.013 |

### Commentary

- The headline (1-bar hold) has **no gross edge at all**: net −8.8/−7.5bps at 2.5bps cost ⇒ gross ≈ −6.3/−5.0bps per trade. The paper's +3–10bps *gross* does not show up at that horizon on 2018–2024 Binance data.
- The 2-bar, vol-filtered cell is the single place anything appears: gross ≈ +7.5 (BTC) / +12.8 (ETH) bps/trade — right at the paper's documented gross band. Directionally it matches Shen et al. on both counts they emphasize: the effect needs the **vol/volume filter** (vf=off kills it) and lives **later into the break** (h=2 ≫ h=1). 100% positive CPCV paths on BTC at rt2.5 is a genuinely consistent signature, not one lucky fold.
- **But** the per-bar Sharpe is ~0.006–0.008: even with every path positive, DSR after 136 trials is 0.001–0.031 — the system cannot certify it is not one of the luckier cells of a 136-trial campaign. This is the deflation machinery working as intended, not a technicality: the cell was found *inside* the recorded grid.
- The edge **decays** as the papers warned (Petukhina et al. 2021): BTC subperiod Sharpe 0.218 → 0.087 post-2021; and it is **fee-fragile exactly as documented** (breakeven 3–10bps): at 10bps RT the post-2021 segment is ≤ 0 everywhere.
- Verdict: **REJECT for trading.** If this line is ever revisited, the honest next step is a pre-registered test of exactly `h=2, vf=on` on genuinely new data (2025+ holdout, one logged look) at a ≤2.5bps-RT venue — with size expectations anchored to ~5bps net × ~90 trades/yr, i.e. a few % per year before funding/borrow on a perp/spot book, not a business.

## Candidate 2 — USD fix-flow (16:00 London reversal) — **REJECT**

Strategy: `apex_quant/strategies/fix_flow.py`. Short-USD at the 16:00 London fix (DST-aware Europe/London conversion; signal on the bar closing 16:00 London, entry at the next bar's open = the fix instant): LONG for USD-quote pairs (EUR/USD), SHORT for USD-base pairs (USD/JPY). Exit after `hold_bars`∈{1,2}; optional conditioning on a USD-appreciating pre-move (6h). Grid: 4 configs.

### Gate results (headline config h=1, unconditional)

| Instrument | Cost | DSR (>0.95) | PBO (<0.5) | CPCV med OOS | frac +paths | Verdict | Trades/yr | Net bps/trade |
|---|---|---|---|---|---|---|---|---|
| EUR/USD | ~1.9bps | 0.000 ✗ | 0.008 ✓ | −0.028 | **0%** ✗ | **REJECT** | 257.5 | **−2.03** |
| USD/JPY | ~0.9bps | 0.000 ✗ | 0.158 ✓ | −0.031 | **0%** ✗ | **REJECT** | 257.5 | **−1.77** |

### Per-config net bps/trade

| Config | EUR/USD | USD/JPY |
|---|---|---|
| h=1, unconditional | −2.03 | −1.77 |
| h=2, unconditional | −1.53 | −1.72 |
| h=1, pre-move conditioned (140/yr) | −2.88 | −2.54 |
| h=2, pre-move conditioned | −1.94 | −1.82 |

### Commentary

- **No gross edge exists to save:** EUR/USD net −2.03bps vs ~1.9bps cost ⇒ gross ≈ 0.0bps/trade; USD/JPY net −1.77 vs ~0.9 ⇒ gross ≈ −0.9bps. Every one of the 15 CPCV paths is negative for both instruments — not a borderline case.
- Conditioning on the into-fix USD move (Krohn et al. is a conditional pattern) makes it *worse*, not better. The 2021–2024 OANDA 1h series simply does not contain a harvestable post-fix reversal at this granularity — the documented effect is measured on fix-to-fix windows over 21 years across 9 currencies and likely lives inside the hour/in the fixing window itself, below what an hourly retail series can express, and/or is competed away post-publication.
- Sample caveat: the FX window is entirely post-2021 (cache depth) — no pre-2021 split possible. The paper's sample runs 2000–2021, so this test is strictly "does it still work recently": it does not.
- GBP/USD and USD/CHF legs were not run (headline legs were decisive at 0/15 paths each; adding legs would only re-measure the same USD factor with correlated noise). If ever revisited, do it as a single 4-leg basket, not single pairs.

## Reality check vs the documented 3–10bps breakeven

| Cell | Gross bps/trade | Cost RT | Net bps/trade | vs documented band |
|---|---|---|---|---|
| BTC h=1 vf=on | ≈ −6.3 | 2.5bps | −8.84 | no edge present |
| BTC h=2 vf=on | ≈ +7.5 | 2.5bps | +5.00 | inside the 3–10bps gross band |
| ETH h=2 vf=on | ≈ +12.8 | 2.5bps | +10.29 | top of band |
| ETH h=2 vf=on, stressed | ≈ +12.8 | 10bps | +2.78 | fee-fragile, as documented |
| EUR/USD any config | ≈ 0.0 | ~1.9bps | −1.5…−2.9 | no edge present |
| USD/JPY any config | ≈ −0.9 | ~0.9bps | −1.7…−2.5 | no edge present |

The research note's core claim is confirmed by measurement: sub-daily edges, where detectable at all, are **2–10bps gross and fully consumed by any cost above ~2–5bps RT**. Only one cell here even showed gross edge, and it is too small to certify after honest deflation.

## Caveats (read before quoting any number)

- **Selection within a recorded grid:** the positive crypto cell was the grid's second config, promoted post hoc and re-gated with unchanged n=136 deflation. Its DSR (0.03) already prices in the full trial count; a future pre-registered single-config test would face a lower bar — but would also be a new trial on the same data unless run on 2025+.
- CPCV/DSR operate on per-bar equity returns with the risk layer sizing positions (vol-target caps bind often at 8×ATR stops); per-trade bps are equal-weighted across trades. Both views agree in sign everywhere above.
- Reported `observed_sharpe_ann` inside the per-run JSONs uses the asset-class daily annualization (365/252) — cosmetic at 1h; gates are per-period and unaffected.
- Binance ≠ the paper's 5-venue sample; USDT depegs (e.g. 2023-03 SVB, brief) are inside the sample unadjusted. Binance-only volume understates true market volume — the vol filter is exchange-relative, which is the right way to use it.
- The OANDA adapter's pagination flaw (returns only the first ~200 days of a multi-year request) was worked around in the runner with 150-day segmented calls; shared adapter code untouched. Other scripts fetching multi-year 1h via one `get_history` call inherit that flaw.
- Two manual optimizer scripts (`optimize_high_frequency_portfolio.py`, `optimize_per_pair.py`) glob `data_store/*.parquet` and would list `BINANCE_BTC_USD` as a pseudo-instrument if run; the live daemon builds from the config universe and is unaffected. The files are additive; nothing existing was overwritten.
- The TrialLedger has no file locking; a concurrent agent is also recording trials. This campaign loaded fresh + recorded + saved promptly, and n=136 was re-verified after the runs. If the ledger grows further, every DSR above only gets harsher — no verdict here can flip to PASS.

## Files

- New strategies: `engine/apex_quant/strategies/intraday_close_momentum.py`, `engine/apex_quant/strategies/fix_flow.py`
- New scripts: `engine/scripts/fetch_binance_1h.py`, `engine/scripts/run_intraday_candidates.py`, `engine/scripts/intraday_config_stats.py`
- New data: `engine/data_store/BINANCE_BTC_USD_1h.parquet`, `engine/data_store/BINANCE_ETH_USD_1h.parquet` (Binance klines, 2018→2024, open-time UTC labels, USDT≈USD)
- Ledger: `engine/data_store/validation/trial_ledger.json` (120 → 136)
- Run records: `engine/data_store/validation/intraday_2026-07-17/` (6 run JSONs + `summary.json`)
