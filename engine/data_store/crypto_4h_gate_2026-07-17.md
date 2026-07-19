# 4h crypto trend sleeve — gate results (BTC/USD, ETH/USD)

**Date:** 2026-07-17 · **Runner:** `engine/scripts/run_crypto_4h_gate.py` · **Pre-registration:** `engine/data_store/crypto_4h_prereg_2026-07-17.md` (written before any run) · **Evidence base:** `docs/research/2026-07-17_subdaily_edges_post_cost.md`

**Bottom line: REJECTED — 0 PASS, 4 REJECT (2 instruments × 2 cost levels).** The 4h trend sleeve is the strongest intraday cell this system has produced — BTC at config costs passes DSR (0.986) and CPCV (100% of 15 paths positive) and nets +28bps/trade, comfortably above the documented 3–10bps intraday breakeven — but it fails PBO decisively (0.989 vs < 0.5), ETH fails DSR at both cost levels, and the pre-registered conjunctive gate admits no exceptions. **Per the pre-registered decision rule: intraday is closed for this system at retail costs.**

---

## Protocol (as pre-registered; enforced by the runner)

- Iteration window **strictly < 2025-01-01** (hard assert per series; the 2025+ holdout never loaded — and is burned for the trend family regardless).
- **12 trials (2 instruments × 3 configs × 2 cost levels) recorded in the shared TrialLedger BEFORE any validation ran**, under the ledger file lock: **n 170 → 182** (verified by fresh read-back; 0 pre-existing 4h keys). Cost levels recorded as *distinct* trials — stricter than the 1h campaign. Every DSR below is deflated by the final **n=182**.
- Gates unchanged: DSR > 0.95, PBO < 0.5, CPCV median OOS Sharpe > 0 with > 50% positive paths (C(6,2)=15 paths, purge=horizon, 1% embargo; seed 42).
- Strategy: `RegimeGatedMomentum` (baseline factory), managed exits (TradeManager), rule_based regime, `timeframe="4h"`. Grid: `momentum_lookback` ∈ {42 (headline), 21, 84}, `vol_window` tracking lookback, `holding_horizon` 10, `reward_risk` 1.5.
- Costs: `rt2.5_config` = config v5 crypto model (1.5bps spread + 0.5bps slippage/side ≈ **2.5bps RT**); `rt10_stress` = 8bps + 1bps/side = **10bps RT**.
- Full per-run records: `engine/data_store/validation/crypto_4h_2026-07-17/` (4 run JSONs + `summary.json`). Supabase posting skipped (research sweep).

## Data

| Series | Span (iteration window) | Bars | Notes |
|---|---|---|---|
| BTC/USD 4h | 2018-01-01 → 2024-12-31 | 15,290 | Resampled from the Binance 1h cache by `scripts/build_binance_4h.py`; 00:00-UTC-aligned bins (00/04/08/12/16/20), open-time labels (store 1h convention). 36 partial + 16 empty outage bins dropped, not filled — matches the 27 documented exchange-outage gaps; bar accounting closes exactly (61,246 contributing 1h bars; hand-verified against source). USDT≈USD. |
| ETH/USD 4h | same | 15,290 | same |

**4h handling in the engine, verified:** `bars_per_year(BTC/USD, "4h") = 2,190` (6×365 — audit-E5 per-TF annualization covers 4h cleanly; Sharpe/ann_return correct). `regime_config_for("4h", …)` has **no dedicated tf_scale** — falls through to the daily value 1.0 (nearest handling; 1h uses 0.15). With the crypto ×5 multiplier the regime gate runs at daily-crypto strictness — if anything stricter on 4h, registered as a caveat pre-run, not tuned.

## Gate results (headline config: momentum_lookback 42)

| Instrument | Cost | DSR (>0.95) | PBO (<0.5) | CPCV med OOS | frac +paths | Verdict |
|---|---|---|---|---|---|---|
| BTC/USD | rt2.5 | **0.986 ✓** | **0.989 ✗** | +0.020 ✓ | **100% ✓** | **REJECT** (PBO) |
| BTC/USD | rt10 | 0.937 ✗ | 0.896 ✗ | +0.017 ✓ | **100% ✓** | **REJECT** (DSR, PBO) |
| ETH/USD | rt2.5 | 0.865 ✗ | 0.506 ✗ | +0.009 ✓ | 60% ✓ | **REJECT** (DSR, PBO) |
| ETH/USD | rt10 | 0.833 ✗ | 0.782 ✗ | +0.004 ✓ | 60% ✓ | **REJECT** (DSR, PBO) |

DSR detail (headline): BTC rt2.5 per-bar Sharpe 0.0184 vs deflated benchmark 0.0011 at n=182; BTC rt10 0.0144 vs 0.0022; ETH rt2.5 0.0143 vs 0.0054 (ETH's fatter left tail — skew −0.6 — raises its benchmark). T = 15,289 bars per cell.

## Full-window economics (per config, 7.0y, managed exits)

| Instrument | Cost | Config | Trades | Trades/wk | Net bps/trade | Expectancy %/trade | PF | Win | Total ret | Sharpe (ann) | MaxDD | Exit mix (tgt/stop/time) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | rt2.5 | **lb42** | 937 | 2.57 | **+28.42** | 0.284 | 1.23 | 51% | +43.2% | 0.863 | −7.5% | 171/340/426 |
| BTC | rt2.5 | lb21 | 831 | 2.28 | +28.19 | 0.282 | 1.25 | 52% | +38.8% | 0.828 | −6.8% | 159/306/366 |
| BTC | rt2.5 | lb84 | 895 | 2.45 | +27.64 | 0.276 | 1.23 | 52% | +40.8% | 0.854 | −7.0% | 156/329/410 |
| BTC | rt10 | **lb42** | 935 | 2.56 | **+22.16** | 0.222 | 1.17 | 50% | +31.8% | 0.675 | −7.9% | 168/339/428 |
| BTC | rt10 | lb21 | 830 | 2.27 | +24.15 | 0.242 | 1.22 | 51% | +34.3% | 0.749 | −6.9% | 157/307/366 |
| BTC | rt10 | lb84 | 893 | 2.45 | +21.10 | 0.211 | 1.19 | 51% | +31.9% | 0.701 | −7.4% | 154/329/410 |
| ETH | rt2.5 | **lb42** | 1,085 | 2.97 | **+43.82** | 0.438 | 1.14 | 51% | +35.4% | 0.671 | −13.2% | 197/426/462 |
| ETH | rt2.5 | lb21 | 974 | 2.67 | +30.45 | 0.305 | 1.10 | 51% | +22.5% | 0.487 | −14.5% | 181/378/415 |
| ETH | rt2.5 | lb84 | 1,033 | 2.83 | +49.63 | 0.496 | 1.12 | 51% | +28.8% | 0.572 | −15.1% | 188/399/446 |
| ETH | rt10 | **lb42** | 1,084 | 2.97 | **+35.81** | 0.358 | 1.10 | 50% | +25.1% | 0.507 | −14.0% | 197/421/466 |
| ETH | rt10 | lb21 | 973 | 2.66 | +24.52 | 0.245 | 1.08 | 50% | +17.9% | 0.405 | −15.4% | 182/375/416 |
| ETH | rt10 | lb84 | 1,029 | 2.82 | +43.04 | 0.430 | 1.09 | 50% | +21.7% | 0.454 | −15.8% | 187/395/447 |

## Net-bps-per-trade reality check (vs the documented 3–10bps intraday breakeven)

**The cost/bar rule held.** Every cell nets **+21 to +50bps/trade** — 2–10× the documented 3–10bps breakeven that killed the 1h close-momentum edge (which netted −9 to +10bps). Trend per-trade P&L is measured in hundreds of bps of range (expectancy 0.21–0.50%/trade), so even the 10bps stress costs only ~6–8bps of edge and every cell stays positive. **Fees are not the binding constraint at 4h — statistical certification is.** The kill comes from PBO/DSR at n=182, exactly the multiple-testing machinery working as designed.

## Commentary

- **BTC is a genuine near-miss, and the report says so plainly rather than hiding behind the conjunction:** at config costs it passes DSR (0.986) and CPCV (15/15 paths positive) with +28bps/trade on 937 trades. What kills it is **PBO 0.989** — in-sample config selection among {21,42,84} is overfit with near-certainty — and at rt10 the DSR also slips under (0.937). The three lookbacks are economically near-identical (27.6–28.4bps), so the PBO verdict is about rank instability of IS selection, not about one lucky lookback; but the gate is conjunctive, pre-registered, and makes no exception for "close". REJECT stands.
- **ETH is weaker everywhere**: DSR 0.865/0.833, PBO 0.506/0.782, and its CPCV paths split cleanly in time — the first 9 folds (earlier history) positive, the last 6 (recent) all negative: the post-2021 decay signature the research note warned about (Petukhina et al. 2021), visible in the path list itself.
- **Robustness across lookbacks** is real (all three configs net positive in all four cells, PF 1.08–1.25, win ~50–52%) — the signal is not one tuned knob. At n=182 that is still not enough: the system's bar is "certifiably not one of the luckier cells of a 182-trial campaign", and 0.986 > 0.95 on exactly one of four cells with PBO ~0.99 does not clear it.
- Trade cadence ~2.3–3.0 entries/week per instrument, exit mix ~45% time-stop / 36% stop / 18% target on BTC headline — the TradeManager shapes trades as designed (managed exits, holding_horizon 10 binding on the plurality).
- Per-bar Sharpes (0.011–0.018) are ~2–3× the 1h campaign's best cell (0.006–0.008), which is why DSR got within reach this time; the deflation bar at n=182 (vs n=136 then) is correspondingly higher.

## Decision (executing the pre-registered rule)

- No PASS at `rt2.5_config` ⇒ **no forward-paper sleeve.** The BTC cell does not earn the discussion: passing 2 of 3 gates is what the gate is designed to reject.
- **Intraday is closed for this system at retail costs.** The complete arc, all measured on this engine in one week: 15m/1h FX dead by cost arithmetic (research note §5); 1h BTC/ETH close-momentum dead by measurement (DSR 0.031, `intraday_candidates_2026-07-17.md`); USD fix-flow absent at 1h granularity (same doc); and now 4h crypto trend — the one configuration whose cost/bar arithmetic survives — REJECTED by the certification gate (this doc). If this line is ever reopened, the only honest triggers are genuinely new data (a *single* logged holdout look is explicitly burned and unavailable for the trend family) or a genuinely new hypothesis class — not another lookback grid.

## Caveats (read before quoting any number)

- CPCV/DSR operate on per-bar equity returns with the risk layer sizing positions; per-trade bps are equal-weighted across trades. Both views agree in sign in all 12 cells.
- Reported `observed_sharpe_ann` in the per-run JSONs uses the asset-class daily annualization (365) — cosmetic at 4h (true factor 2,190); gates are per-period and unaffected. The per-config `sharpe` column above IS correctly annualized at 2,190.
- Binance-only venue; USDT≈USD unhedged (depeg episodes inside sample); managed-exit results are engine-TradeManager-specific.
- The TrialLedger has file locking (used); a concurrent agent may still append later — if the ledger grows past 182, every DSR above only gets harsher. No verdict here can flip to PASS.

## Files

- New data: `engine/data_store/BINANCE_BTC_USD_4h.parquet`, `engine/data_store/BINANCE_ETH_USD_4h.parquet` (15,290 bars each, 2018→2024, open-time UTC labels, 00:00-UTC-aligned)
- New scripts: `engine/scripts/build_binance_4h.py` (resample), `engine/scripts/run_crypto_4h_gate.py` (gate runner)
- Pre-registration: `engine/data_store/crypto_4h_prereg_2026-07-17.md` (before any run)
- Run records: `engine/data_store/validation/crypto_4h_2026-07-17/` (4 run JSONs + `summary.json`)
- Ledger: `engine/data_store/validation/trial_ledger.json` (**170 → 182**, 12 pre-registered trials)
- Not touched: `engine/config.yaml`, `run_live_paper_trading.py`, the shared store write path, the 2025+ holdout, the live daemon.
