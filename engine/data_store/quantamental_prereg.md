# Pre-registration: Quantamental gate vs pure technical momentum (owner-requested measurement)

Date locked: 2026-08-03 (BEFORE any backtest run). Author: quant engineering pass for the owner.

**Status: comparative MEASUREMENT, not an adoption gate.** The owner requested an A/B
measurement of a fundamental-score gate on the daily equity momentum book. No DSR/PBO
adoption verdict is produced or required; one measurement trial is recorded in the
shared TrialLedger (`kind: quantamental_comparison`) before the runs, so the honest
trial count still reflects this experiment.

## 1. Universe & window

- Universe (12, daily OHLCV): NVDA, MSFT, AAPL, PLTR, TSM, NFLX, AMD, META, AMZN, GME, SPY, QQQ.
- ITERATION window only: bars strictly before 2025-01-01 (the project's sealed 2025+
  holdout is never touched). Effective panel: 2016-01-04 → 2024-12-31 (2264 bars for
  10 names; PLTR lists 2020-09-30 → 1070 bars; GME store backfilled 2010 → clipped to
  the same window). Data: engine ParquetStore (`data_store/*_1d.parquet`); GME was
  fetched via the engine's YahooAdapter and cached (3774 bars 2010-01-04 → 2024-12-31).

## 2. Fundamental score (0–100, 4 pillars × 25)

Per the owner's spec, with these boundary conventions (locked):

- Valuation, trailing P/E: `PE ≤ 30 → 25`; `30 < PE ≤ 50 → 18`; `50 < PE ≤ 90 → 10`;
  `PE > 90 or PE ≤ 0 or missing → 0`.
- Profitability, gross margin: `GM ≥ 60% → 25`; `35% ≤ GM < 60% → 18`; `GM < 35% → 5`;
  missing → 5 (conservative floor).
- Growth, YoY revenue: `g ≥ 25% → 25`; `10% ≤ g < 25% → 18`; `0 ≤ g < 10% → 10`;
  `g < 0 or missing → 0`.
- Balance sheet (25): `FCF > 0 → +15` else +0; `D/E ≤ 100% → +10`; `100% < D/E ≤ 200% → +5`;
  `D/E > 200% or missing → +0`.

**Data source (locked):** the spec named FMP (`/key-metrics`, `/ratios`, …), but
`FMP_API_KEY` is NOT in `engine/.env` — confirmed directly and by
`engine/scripts/build_earnings_calendar.py` ("FMP_API_KEY lives only in the Vercel
deployment environment … FMP is unavailable locally"). No Vercel CLI session exists on
this machine. Substitution: Yahoo Finance `quoteSummary` v10 (crumb-authenticated,
same five raw inputs: `trailingPE`, `grossMargins`, `revenueGrowth`, `debtToEquity`,
`freeCashflow`), cached one JSON per ticker in
`engine/data_store/fundamentals_cache/`. The comparison script is cache-first and only
hits the network on a cache miss.

**As-of date: 2026-08-03** — a CURRENT snapshot, not point-in-time. The score applied
across the whole 2016–2024 backtest uses 2026 fundamentals. This is a deliberate,
owner-acknowledged simplification and it FLATTERS Strategy B (survivors' current
quality is known in advance). It is stated prominently in the report caveats.

**ETF handling (locked):** SPY and QQQ have no company fundamentals → pass-through,
tradeable in BOTH strategies, flagged "N/A — index ETF". They never affect the gate.

### Locked screening table (computed 2026-08-03, pre-run)

| Ticker | P/E pts | Margin pts | Growth pts | Solvency pts | Score | Gate ≥70 |
|---|---|---|---|---|---|---|
| NVDA | 18 (30.79) | 25 (74.1%) | 25 (85.2%) | 25 (FCF+, D/E 6.6%) | **93** | PASS |
| MSFT | 25 (25.90) | 25 (67.9%) | 18 (17.7%) | 25 (FCF+, D/E 29.1%) | **93** | PASS |
| AAPL | 18 (35.47) | 18 (48.7%) | 18 (16.4%) | 25 (FCF+, D/E 78.4%) | **79** | PASS |
| PLTR | 0 (138.27) | 25 (84.1%) | 25 (84.7%) | 25 (FCF+, D/E 2.5%) | **75** | PASS |
| TSM  | 18 (35.59) | 25 (64.2%) | 25 (36.0%) | 25 (FCF+, D/E 15.2%) | **93** | PASS |
| NFLX | 25 (22.55) | 18 (49.1%) | 18 (13.4%) | 25 (FCF+, D/E 55.2%) | **86** | PASS |
| AMD  | 0 (158.72) | 18 (53.1%) | 25 (37.8%) | 25 (FCF+, D/E 6.0%) | **68** | **FAIL** |
| META | 25 (20.96) | 25 (81.7%) | 25 (28.0%) | 25 (FCF+, D/E 43.0%) | **100** | PASS |
| AMZN | 25 (21.83) | 18 (50.8%) | 18 (19.6%) | 25 (FCF+, D/E 45.6%) | **86** | PASS |
| GME  | 25 (16.21) | 5 (34.4%) | 18 (14.1%) | 10 (FCF−, D/E 74.3%) | **58** | **FAIL** |
| SPY  | N/A — index ETF (pass-through) | | | | — | allowed both |
| QQQ  | N/A — index ETF (pass-through) | | | | — | allowed both |

Strategy B therefore trades 10 of 12 names (AMD and GME blocked by the gate).

## 3. Technical strategy (both books share it)

Certified machinery reused: the `TrendBook` stack of
`engine/scripts/run_portfolio_gate.py` — `RegimeGatedMomentum` wrapped in
`MultiTimeframeMomentum` — run by `PortfolioBacktester` (`exit_mode="managed"`,
`TradeManager`), with the spec's entry rule added through the certified
`DirectionalEntryGate` seam (`strategies/entry_gates.py`).

- Base: `RegimeGatedMomentum(momentum_lookback=252, vol_window=63,
  regime_method="rule_based", timeframe="1d", enable_mean_reversion=False)` —
  the 252-day regime-gated momentum lookback. Mean reversion is DISABLED (the spec
  has no Bollinger leg; this also means no signal bypasses the entry gate).
- HTF: `MultiTimeframeMomentum(htf_rule="1w", htf_ma_window=50)` — 1-week trend
  filter. **Deviation, flagged:** the certified filter is a 50-week SMA; the spec
  says "EMA" without a window. The certified SMA-50W is used (reuse mandate);
  entry-side EMAs below are implemented exactly as specified.
- Entry (the spec's rule, as precomputed point-in-time gate masks):
  LONG allowed only on bars where EMA20 > EMA50 (the bullish EMA structure —
  the spec's "EMA20 > EMA50 breakout") AND relative volume =
  volume / SMA20(volume) > 1.2 (the trigger). All other bars blocked; SHORTs
  blocked on every bar (long-only: the spec's setup and fundamental gate are
  directional). See Amendment A1 below for why this is the state reading, not
  the fresh-cross reading.
- Signal `reward_risk = 10.0`: the fixed full-target exit is placed beyond reach so
  the spec's partial ladder + trail govern (the certified 1.5 would amputate the
  runner at 1.5R before the trail). The full-target exit remains as a deep backstop.
- `holding_horizon = 21` (certified): the TradeManager's certified time-exit
  (bars_open > 21 AND < +0.25R → close) is retained.

## 4. Sizing (spec), mapped onto the certified RiskManager

Spec: `S = min(0.20 × (Capital / (ATR14 × 2.5)), Capital × 0.065)` — risk-based units
at 20% of equity per 2.5×ATR14 stop, capped at 6.5% of equity in notional. Mapped to
an in-memory config copy (config.yaml untouched):

- `kelly_fraction = 0.0` → risk layer uses `max_risk_per_trade` directly;
- `max_risk_per_trade = 0.20` (the spec's 0.20 term);
- `atr_stop_mult = 2.5`, `atr_window = 14` (certified defaults, unchanged);
- `target_portfolio_vol = 10.0` (raised so the certified vol-target ceiling is inert;
  the spec defines its own cap);
- `max_position_notional_pct = 0.065` (the spec's 6.5% notional term; certified step 8.5).

Effective per-trade risk after the notional cap ≈ 6.5% × 2.5 × ATR% (≈ 0.3–1.0% of
equity for these names) — the cap is the binding term, exactly the spec's `min()`.

Certified book-level caps retained (part of the honest machinery): gross exposure ≤
3.0× equity, correlated-cluster ≤ 1.5×, aggregate open risk ≤ 6.5%, swing slots ≤ 10,
global ≤ 12, sequential allocation in fixed universe order.

## 5. Exits (spec via certified TradeManager)

Initial stop 2.5×ATR14 (certified `atr_stop`); managed exits, certified defaults:
partial 1 = 50% at +1.0R with breakeven stop; partial 2 = 25% at +1.5R with +0.5R
lock; Chandelier trail (22-bar high − 2.0×ATR) on the remaining 25%; certified
time-exit for stagnant trades (§3). Gap-aware stop fills (worse of stop/open) — the
certified honest tail.

## 6. Circuit breakers (spec)

- 100% halt of new entries at 15% peak-to-trough drawdown: certified breaker,
  `drawdown_breaker = 0.15`.
- 50% size scale-down at 10% drawdown: the certified amber zone is a LINEAR ramp
  (1.0→0.0 between thresholds), not the spec's step. Implemented as a thin
  `RiskManager` subclass in the script that multiplies permitted size ×0.50 while
  10% ≤ DD < 15% (recorded in `constraint_log` as `dd_step_scale=0.50`);
  `drawdown_reducing_limit = 0.15` empties the certified ramp so the step is the
  only de-risking. No other logic touched.

## 7. Costs (honest, certified equity model)

Per fill: half-spread 1.0 bps (= 2.0 bps quoted spread halved) + 1.0 bps slippage;
commission $1.09 per side (equity `commission_per_trade` overridden in-memory from
the certified 0.0 — the one cost parameter the spec changes; config.yaml untouched).
Fills at next bar's open; stops fill intrabar, gap-aware.

## 8. The two books

- **Strategy A (pure technical):** all 12 names tradeable; the fundamental score is
  computed and reported but ignored.
- **Strategy B (quantamental):** identical machinery; LONGs blocked on every bar for
  tickers scoring < 70 (AMD 68, GME 58). SPY/QQQ pass through.

## 9. Determinism, ledger, analysis plan

- Seed 42 (`set_global_seeds`); the pipeline has no stochastic component beyond the
  (cached) fundamentals fetch. Acceptance: two consecutive runs produce a
  byte-identical canonical results payload (script prints `RESULT_SHA256`).
- Ledger: 1 trial recorded BEFORE the runs
  (`{kind: quantamental_comparison, books: [A, B], universe: 12-name equity, …}`),
  idempotent under the ledger's canonical-JSON dedup. n≈300 → n+1.
- Metrics per book (certified `compute_metrics`): win rate, total net return %,
  annualized Sharpe, max DD %, profit factor, trade count, expectancy/trade; £/month
  on the £100,000 book (`initial_equity` unchanged) = net P&L / months.
- Drawdown analysis windows (locked): 2018 Q4 = 2018-10-01→2018-12-31;
  COVID = 2020-02-01→2020-04-30; 2022 bear = 2022-01-01→2022-12-31. Per window per
  book: window-local max peak-to-trough DD of the equity curve, plus
  "false-breakout" count = trades ENTERED inside the window that closed with
  P&L < 0 (and their win rate).
- Report caveats (locked): look-ahead bias of the as-of-2026 score (flatters B);
  single-window measurement; N=12 universe; static gate vs real point-in-time
  fundamentals; AMD misses the gate by 2 points (threshold sensitivity).

## Amendment A1 (2026-08-03, still pre-run): entry-rule interpretation

The prereg initially read "EMA20 > EMA50 breakout" as the FRESH cross (prev bar
EMA20 ≤ EMA50, decision bar EMA20 > EMA50). A code-path smoke run
(`--no-ledger`, output to /tmp, before any official run) showed that reading is
degenerate: 21 cross events per name over 9 years, ~3-5 surviving the relvol
condition, and after the 252-day momentum + regime + HTF gates only **6 trades
in book A and 2 in book B across the whole 2016-2024 window** (SPY/QQQ never
trade). No A/B measurement is possible at n=6 vs n=2.

The entry rule is therefore locked to the STATE reading — allowed bars =
(EMA20 > EMA50) AND (relvol > 1.2 × 20d average) — the same conjunction the
task brief itself uses ("implement the spec's entry rule (EMA20>50 + relvol)").
Density: ~290-400 allowed bars per name per 9 years. This choice is driven by
implementability alone and was made BEFORE any official (ledger-recorded) run
and before seeing any state-reading A/B performance contrast. The fresh-cross
degeneracy is disclosed in the report caveats.
