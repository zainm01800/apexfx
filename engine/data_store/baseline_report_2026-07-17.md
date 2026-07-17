# TRUE BASELINE — live-equivalent portfolio backtest
**Date:** 2026-07-17 · **Engine:** `engine/apex_quant` (config.yaml v4) · **Backtester:** `backtest/portfolio.py` (PortfolioBacktester, book-level caps bind)

---

## 1. Verdict (plain English)

**No. The current live setup does not have positive expectancy after costs — on any timeframe, under either cost model.**

- The 16-system book that the live daemon trades today loses **−£4,053 (−4.1%) over 12 months** at the config's assumed costs (1 pip + 0.5 bps), and **≈ −£14,800 (−14.8%)** at the costs the live account actually paid on cross pairs. Expectancy is negative on 15m, 1h and 1d individually.
- The 3.0× gross-exposure cap now works exactly as configured (max leverage used: **3.000×**, never breached) — but a risk cap cannot fix a signal whose per-trade expectancy is negative. It only slows the bleed.
- The signal's failure is **not** an artifact of single-instrument backtesting: with every book-level rule live (gross cap, cluster cap, TF buckets, portfolio-risk cap, drawdown breaker), the portfolio still loses. This is consistent with `regime_gated_momentum` failing the repo's strict CPCV validation.
- Cost reality check: **1 pip + 0.5 bps is roughly fair for majors** (live realized: 0.36 pips round trip) but **~4–5× understated for the crosses that make up 100% of the active book** (live realized on crosses: 4.85 pips; EUR/NZD 10.1, CHF/JPY 9.4, GBP/NZD 9.2).

---

## 2. What "the current live setup" actually is (discoveries)

These came out of reconciling `scripts/run_live_paper_trading.py` against reality; several contradict the assumptions this task started with:

1. **Only 16 of 88 forex (pair × TF) systems actually trade.** `high_frequency_optimized_configs.json` marks **50 of 66 forex systems `veto: true`**, and the scanner skips vetoed systems entirely. The active 16:
   - **15m (5):** CAD/JPY, CHF/JPY, EUR/NZD, GBP/NZD, NZD/JPY
   - **1h (5):** AUD/NZD, AUD/USD, EUR/AUD, GBP/AUD, GBP/JPY
   - **1d (6):** CHF/JPY, EUR/CAD, EUR/CHF, GBP/AUD, GBP/CAD, NZD/USD
2. **1w is inert live.** No optimized 1w entries exist, so 1w uses the "position" fallback (warmup 180); live fetches only 1000 days (~143 weekly bars) and its own `len(df) < warmup + 15` gate (143 < 195) prevents any 1w scan from producing signals. (1 of 108 live trades is tagged "position" — legacy/manual.)
3. **The veto set is brand new.** The configs file was rewritten 2026-07-17 01:58 (uncommitted working-tree change, 2,369 lines) and the daemon was restarted 02:28, loading it. **The −£1,613 loss window (Jul 13–16) was traded under the previous file** — 83 of the 108 closed trades (−£5,603) are on systems that are vetoed *today*; only 25 (+£4,263) are on currently-active systems. Both configurations are therefore baselined here: **primary = 16 systems (current)**, **sensitivity = all 66 (what traded during the loss window)**.
4. **Live exits are not TradeManager exits.** `TradeManager` is used only by the two backtesters; the live script never imports it. Real live exits are (a) MT4 server-side SL/TP, (b) engine "invalidation" closes on the **15-minute** scan loop (`--interval 900`) when the signal goes flat, (c) reversal closes. Measured on the 108 live trades: **median hold 1.6h, only 3/108 stop-like exits, only 3/39 winners closed at TP**. The backtester's "managed" mode (partials at 1R/1.5R, breakeven, chandelier, time-stops of 8 bars–7 days) holds far longer — a structural divergence no current repo exit mode reproduces.
5. **Live risk sizing uses the Bayesian sizer + per-pair ATR stop on the order, but config 2.5×ATR in the RiskManager** (the per-pair `atr_stop_mult` never reaches `permit()`); the backtest replicates the RiskManager side (2.5×ATR stops).

## 3. Data coverage used

| TF | Coverage after top-up | Notes |
|----|-----------------------|-------|
| 15m | 2021-10 (majors) / 2024-07 + 2025-05-25→2026-07-17 02:00 (crosses) | crosses were stuck at 2024-09-03; refetched |
| 1h  | 2021-03 (majors) / 2022-07 + 2025-05-01→2026-07-17 02:00 (crosses) | crosses were stuck at 2025-01-24; refetched |
| 1d  | 2016-01 → 2026-07-16 (all 22) | topped up |
| 1w  | 2014-01 → 2026-07-10 (all 22) | was missing entirely; fetched (unused — 1w inert) |

All fetches went through the normal data layer (`ParquetStore.get_or_fetch` + `OandaAdapter`; credentials via dotenv, `.env` never read). One adapter quirk found: pagination stops when a 4800×TF-second span returns <4800 candles (weekend gaps guarantee this), so one call covers only ~50 days of 15m / ~200 days of 1h — the gap-fill had to walk in chunks (`engine/scratch/fetch_cross_gaps.py`).

**Backtest window:** 2025-07-17 00:00 UTC → 2026-07-17 02:00 UTC (last closed 15m bar). Per-TF history buffers ≥ live lookbacks (15m 45d, 1h 360d, 1d 410d), so indicator depth matches live at every decision. Last incomplete bar dropped per TF, mirroring live.

## 4. Exact configuration run

- **Systems:** 16 active (pair, TF) above; per-pair params from `high_frequency_optimized_configs.json` via the same mapping as `_load_optimised_configs()` (`hold_horizon`→`holding_horizon`, etc.).
- **Strategy:** `RegimeGatedMomentum(bypass_calibration=True)` wrapped in `MultiTimeframeMomentum` with live HTF rules (15m→1h MA200, 1h→1d MA200 — capped 4000-bar window makes this one always neutral, as live; 1d→1w MA50). Mean-reversion on 1h/1d (default), off on 15m.
- **Exits:** TradeManager "managed" mode (P1 50% @1R + breakeven, P2 25% @1.5R, chandelier 2×ATR, squeeze tighten, time stops 15m:8 / 1h:10 / 1d:7 bars).
- **Costs:** config forex mechanics — 0.5 pip half-spread + 0.5 bps slippage per fill (≈1.13 pips round trip), 0 commission.
- **Risk (config.yaml v4, unmodified):** Kelly 0.20 edge gate, 2% per-trade cap, 10% vol target, **3.0× gross cap, 1.5× cluster cap (ρ>0.6)**, TF buckets (scalp 6 / intraday 8 / swing 10), global 12, portfolio risk 6.5%, DD breaker reduce@10% / halt@20%.
- **Equity:** 100,000. `use_regime=False` (live passes no regime to `permit()`), news-calendar filter stubbed (portfolio.py calls `permit()` without `t` — it would evaluate at real "now" for every bar), warmup 60 bars.

**Deliberate deviations / known distortions** (all conservative-leaning or documented):
1. **No quote→GBP conversion in the backtester** (`quote_to_account_rate` defaults 1.0): P&L and notionals are recorded in quote-currency magnitude — JPY trades ~190× overstated in £ terms, USD ~1.3×. Raw metrics below are in these mixed units; **£ figures use a static 2026-07-16 quote→GBP correction** (USD 0.7425, JPY 0.004571, CHF 0.9180, CAD 0.5289, AUD 0.5185, NZD 0.4333, EUR 1.1772 per unit).
2. No DeepSeek sentiment/structural vetoes, no Bayesian sizer (stateful/online-only); backtest uses fractional Kelly — live trades *fewer, larger* positions.
3. Live min-position floor £15k not replicated (backtest floor 0).
4. Live invalidation exits (§2.4) not reproducible; managed mode is the specified closest available.

## 5. Results — PRIMARY (16 systems, current live book)

**Raw (as-run, mixed-currency units — see §4 deviation 1):**
return **−19.2%**, Sharpe **−2.49** (daily-resampled), Sortino −1.88, **max DD 19.8%**, 3,090 trades, WR 44.8%, PF 0.80, expectancy −7.24 units/trade.

**FX-corrected (approximate £):**

| metric | 15m | 1h | 1d | **book** |
|---|---|---|---|---|
| trades | 2,369 | 704 | 17 | **3,090** |
| win rate | 43.9% | 47.7% | 47.1% | **44.8%** |
| net P&L | −£1,557 | −£2,411 | −£85 | **−£4,053** |
| expectancy/trade | −£0.66 | −£3.42 | −£4.99 | **−£1.31** |
| profit factor | 0.85 | 0.83 | 0.81 | **0.84** |

- **Max leverage actually used:** raw **3.000×** (the cap binds *exactly*; it clipped entries 5,610 times) — GBP-corrected true leverage max **1.98×**, p95 1.59× (the cap over-counts JPY notionals, so real £ leverage stays well under 3×). Measured by instrumenting `permit()` on the Jul–Sep 2025 subset (`engine/scratch/run_leverage_probe.py`).
- **Caps/constraints fired** (proof the book-level rules were exercised): gross cap ×5,610; per-trade cap ×6,089; cluster cap ×1,349; vol-target ×797; portfolio no_edge vetoes ×196; DD reducing scale ×3,646; **DD breaker (halted) ×33,342**.
- **Trading effectively stopped after Dec 2025:** the 20% drawdown breaker tripped on the raw-magnitude equity curve in December and halted new entries for the rest of the window. Note the corrected (approx-£) trade-close drawdown was only **4.4%** — the breaker trip is substantially an artifact of the yen-magnitude distortion (deviation 1); in coherent £ terms the book would have kept trading and losing slowly.
- Per-system (corrected): worst EUR/NZD@15m −£1,010 (415 trades), GBP/AUD@1h −£809, AUD/NZD@1h −£753; best EUR/CHF@1d +£311 on 2 trades (noise). **No system has meaningful positive expectancy.**
- Exit mix: 15m — time 1,055 / stop 841 / target 473; 1h — time 291 / stop 268 / target 145; 1d — time 12 / target 4 / stop 1.

**Cost-realistic adjustment:** replacing the assumed ~1.13 pips RT with live-realized costs on the active systems (15m ≈ 6.6, 1h ≈ 4.9, 1d ≈ 3.8 pips — see §7) at the backtest's own trade sizes gives **adjusted 12-month net ≈ −£14,793 (−14.8%)** and adjusted expectancy 15m −£2.86, 1h −£11.17, 1d −£9.41 per trade.

## 6. Results — SENSITIVITY (all 66 systems = the book that traded Jul 13–16)

Raw: return −16.9%, Sharpe −2.61 (daily), max DD 20.0% (breaker tripped Dec 2025), 4,292 trades, WR 40.1%, PF 0.80.
FX-corrected: net **−£5,456**, expectancy **−£1.27**/trade, PF 0.85 · 15m: −£7,278 (PF **0.74**, 3,312 trades — the bleeder) · 1h: **+£1,850 (PF 1.20)** — the only positive pocket · 1d: −£29 (15 trades, breaker-truncated).
Same December breaker halt; same 3.0× cap discipline.

## 7. Live-vs-backtest cost reconciliation

Source: 108 closed MT4 trades, 2026-07-13→16 (`/api/mt4-trades?status=closed&limit=1000`), times converted from MT4 server clock (verified UTC+3 by matching open prices into 15m bar ranges: 79% containment at −3h vs 13% at 0h). Realized cost per fill = |fill − Oanda 15m-bar mid| signed adverse-positive.

**Live results:** total **−£1,340** over ~3.5 days — scalp (15m) +£505 (11 trades, WR 45%), intraday (1h) −£153 (27, WR 44%), swing (1d) −£1,689 (68, WR 32%), position −£3 (1). Position sizes p50 1.59 lots, p90 8.2, max 18.68 (one EUR/USD swing trade −£3,114 on 10.4 lots — pre-cap-fix sizing era).

**Realized round-trip costs vs backtest assumption (~1.13 pips):**

| bucket | n | mean RT cost | median | vs assumption |
|---|---|---|---|---|
| Majors | 52 | **0.36 pips** | 1.30 | ≈ fair |
| Crosses | 56 | **4.85 pips** | 2.85 | **~4.3× understated** |
| — scalp (15m) | 11 | 0.85 | 0.20 | fair on what it traded |
| — intraday (1h) | 27 | **4.87** | 2.20 | ~4× understated |
| — swing (1d) | 68 | **2.17** | 2.25 | ~2× understated |
| Active 15m pairs (CAD/JPY 2.6, CHF/JPY 9.4, EUR/NZD 10.1, GBP/NZD 9.2, NZD/JPY 2.0) | 33 | **≈6.6** | — | **~6× understated** |

Answer to the task's direct question: **1 pip + 0.5 bps is realistic on 15m majors, but the active book is 100% crosses, where it is 2–6× too optimistic** — worst on 1h/swing crosses. Stop-exit slippage beyond SL: mean 2.6 pips (n=3, small sample; max 7.1).

**Structural gaps beyond costs:**
- Exit behavior (§2.4): live holds median 1.6h via 15-min invalidation scans; backtest managed exits hold hours–days. Per-trade outcome distributions are not directly comparable; portfolio-level bleed sign is consistent (both negative).
- Live bleed rate during the loss window (−£383/day) far exceeds the backtest's (−£11/day) — explained by (a) pre-cap-fix oversizing (p90 8.2 lots vs ~1.6 in the capped backtest) and (b) the unvetoed 66-system book, of which 15m was the worst performer in the sensitivity run.

## 8. Bottom line

The current live setup — whether measured as today's 16-system book or the 66-system book that produced the −£1,613 — has **negative expectancy after costs at portfolio level**, confirming the CPCV failure in the exact risk environment the live account runs. The re-capped 3.0×/1.5× limits demonstrably contain leverage (max 3.000×), turning a blow-up risk into a slow bleed, but the signal itself does not earn its costs — least of all on the wide-spread crosses the optimized configs concentrate on. Keeping 15m/1h enabled ("at any cost") is measured here as keeping the two worst-expectancy buckets active (15m PF 0.74–0.85; 1h positive only in the 66-book mix and negative in the current 16-book).

---

### Appendix — artifacts

- Runner: `engine/scratch/run_baseline_portfolio.py` (veto-aware); data top-up: `engine/scratch/fetch_baseline_data.py`, `engine/scratch/fetch_cross_gaps.py`; leverage probe: `engine/scratch/run_leverage_probe.py`; logs alongside.
- Outputs: `engine/data_store/baseline_portfolio_trades_2026-07-17.csv` (16-sys), `..._fxcorr_2026-07-17.csv` (+£-corrected), `baseline_portfolio_metrics_2026-07-17.json` (16-sys), `..._all66_2026-07-17.{csv,json}` (sensitivity), `/scratch/live_trades_enriched_2026-07-17.csv` (live trades + cost columns), `scratch/quote_to_gbp_rates.json`.
- Not modified: `engine/config.yaml`, any `apex_quant/` source, the MT4 bridge directory, the running daemon (PID 19960).
- Limitations: £-correction uses static rates; corrected DD is trade-close granularity (intraday DD understated); leverage profile measured on a 2.5-month subset; live cost estimates use Oanda mid as proxy for the broker's mid (pair-level ±2 pips noise band, cross-vs-major split far above noise).
