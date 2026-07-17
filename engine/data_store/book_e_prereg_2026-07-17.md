# Pre-registration — Book E: frozen TrendBook config on a WIDE universe — 2026-07-17

Registered BEFORE any Book E gate run and BEFORE the 2 ledger trials are recorded.
Reference point: Book D clean-data re-run (`engine/data_store/portfolio_gate_multiasset_2026-07-17.md`,
clean re-run section; JSON `validation/portfolio_gate_multiasset_2026-07-17.json`).

## Hypothesis

Book D (`book_d_multiasset_252`, 42 instruments) is positive OOS on clean data (CPCV 14/15
paths positive, PBO 0.056 pass) and sits one DSR notch under the bar (0.934 vs 0.95 at n=150).
Its binding constraint on trade frequency is breadth: only 42 signal sources feed a 10-slot
swing book. **Book E tests the breadth hypothesis: the SAME frozen TrendBook configuration on a
~1.8× wider universe should produce ~1.7–2× the entries with Sharpe preserved or better** —
more independent bets through the same risk caps, not a new edge. Explicitly part of the test:
the countervailing mechanism is that the config caps (10-slot swing bucket, 6.5% portfolio
risk, 1.5× corr-cluster, 3× gross) may throttle the entry gain — if the book was already
slot-limited at 42 instruments, entries/week will rise by much less than 1.8×, and the
constraint log will show it.

## Frozen configuration (identical to Book D in every parameter except the universe)

- Signal per instrument: `RegimeGatedMomentum` wrapped in `MultiTimeframeMomentum`
  (htf_rule="1w", htf_ma_window=50 — the live 1d stack), `instrument=` passed explicitly
  (per-instrument Bollinger cache; per-class regime eps equity 1.5× / crypto 5× / forex 1×;
  crypto mean-reversion disabled). `carry_filter=False`.
- vol_window 63, holding_horizon 21, reward_risk 1.5, regime_method "rule_based", timeframe "1d".
- **Headline: momentum_lookback 252 (`book_e_252`). Single variant: momentum_lookback 126
  (`book_e_126`)** — the exact Book C/D lookback pair, so E-252 vs D and E-126 vs C are
  universe-only comparisons.
- PortfolioBacktester, exit_mode="managed", vol-scaled sizing via RiskManager, config caps
  binding unchanged: max_risk_per_trade 0.02, max_total_exposure 3.0, max_correlated_exposure
  1.5 (corr threshold per config), max_portfolio_risk 0.065, drawdown breakers 0.10/0.20,
  swing bucket 10 concurrent / global hard cap 12.
- Costs: v5 per-asset-class, unchanged — equities 2.0 bps/side, crypto 1.25 bps/side,
  forex per-pair pips; crypto vol annualized 365, equity/forex 252.
- CPCV purge = holding horizon 21; warmup 250; MIN_BARS 300 (in-window).
- **Cost note (pre-registered):** more instruments → more round trips at unchanged per-trade
  costs. Total cost drag scales with trade count; per-trade expectancy and PF must not degrade
  vs Book D clean (Book D clean: expectancy +273.13 pnl/trade, +1.109%/trade, PF 1.41).

## Universe — 77 instruments (existing 42 + 35 new)

Existing 42 (unchanged from the Book D clean re-run):
- Equity/ETF 24: AAPL MSFT NVDA META AMZN GOOGL TSLA AMD PLTR TSM NFLX UBER | SPY QQQ IWM GLD TLT | XLK XLE XLF XBI ARKK SMH SOXX
- Crypto 11: BTC/USD ETH/USD SOL/USD BNB/USD XRP/USD ADA/USD AVAX/USD DOGE/USD LINK/USD ARB/USD SUI/USD
  (MATIC/USD remains absent from the cache — the Book D skip stands; DOGE is already in.)
- FX majors 7: EUR/USD GBP/USD USD/JPY USD/CHF AUD/USD USD/CAD NZD/USD

New 35 (rationale per group; Yahoo symbol conventions in parentheses):
- Broad/size/intl equity ETFs (6): DIA VTI EFA EEM RSP MDY — DM ex-US (EFA) and EM (EEM) are
  genuinely new exposures; equal-weight (RSP) and mid-cap (MDY) diversify the cap-weighted beta.
  **Acknowledged redundancy:** DIA and VTI are ~0.99-correlated with existing SPY — included per
  the task's specified candidate list; the corr-cluster cap (1.5×) is the mitigant and the
  constraint log will show whether they crowd out other entries.
- Rates/credit ETFs (4): TIP LQD HYG AGG — inflation-linked, IG credit, high yield, aggregate
  bonds; diversifies the lone TLT rates exposure.
- Commodity ETFs (3): SLV USO GSG — silver, crude, broad commodities; diversifies lone GLD.
- Real estate (1): IYR.
- Equity sectors not already held (8): XLU XLV XLP XLI XLY XLB IBB ITA — utilities, health care,
  staples, industrials, discretionary, materials, biotech (IBB; overlaps existing XBI, ~0.85–0.9
  corr — acknowledged), aerospace/defense. Completes the GICS sector set around existing
  XLK/XLE/XLF/XBI.
- Mega-cap single stocks (11): JPM JNJ XOM WMT PG KO V MA HD BA GS — defensive/value/bank/
  energy/payments exposure the existing 12 single-names (tech-heavy) lack.
- Crypto (2): LTC/USD DOT/USD — both classify as crypto via `CRYPTO_BASES`; Yahoo mapping
  "LTC-USD"/"DOT-USD" through the adapter's generic crypto branch (LTC also explicitly mapped).

All new equity/ETF tickers are Yahoo-native US tickers passed through unchanged; no `=X`/`-USD`
mapping applies to them. Data fetched 2026-07-17 via the normal store path
(`ParquetStore.get_or_fetch` + `YahooAdapter`, 2016-01-01 → present; session-normalized,
forming-bar trimmed, off-calendar rejected on write — same conventions as the rebuilt data
layer). Verified: zero weekend bars and zero duplicate timestamps in all new equity/ETF caches;
crypto correctly retains weekend bars.

**History check (drop rule applied):** every candidate has ≥300 bars strictly < 2025-01-01 —
new equity/ETFs 2264 each (2016-01-04 → 2024-12-31), LTC/USD 3288, DOT/USD 1595 (lists
2020-08-20; treated like the other late-listers, PLTR/SOL/ARB/SUI-style). **No drops; final
universe is exactly 77.**

## Gate (unchanged bar, same machinery)

`validation/portfolio_report.py` thresholds via `run_portfolio_gate.py`'s `_gate`:
**DSR > 0.95** (deflated by the shared TrialLedger's full updated count — 150 + 2 = **152**,
both of this run's trials included) **and PBO < 0.5** (across the 2 books — the whole
pre-registered selection set) **and CPCV median OOS Sharpe > 0 with >50% of 15 paths positive**.

- ITERATION window only: data strictly < 2025-01-01. The 2025+ holdout is never touched. No
  `--final` under any outcome of this run.
- Exactly **2 new ledger trials** recorded BEFORE the runs: `book_e_252`, `book_e_126`
  (universe label "book_e_wide_77"). n: 150 → 152. DSR of both books deflated by 152.
- Script: `engine/scripts/run_portfolio_gate_book_e.py` — thin orchestration reusing
  `run_portfolio_gate.py`'s TrendBook adapter / `_gate` / helpers and
  `run_portfolio_gate_multiasset.py`'s `_class_breakdown`, unchanged.
- Determinism: seed 42 (PBO via `cfg.seed`); book_e_252 full-window run repeated twice,
  byte-identical required. No-lookahead is structural (signals via PointInTimeAccessor ≤ t;
  entries at next bar open) and re-verified on NEW instruments' fills (entry = bar open ×
  (1 ± side cost)).
- Success criteria for the breadth claim (reported regardless of gate verdict): entries/week
  and trades/year E vs D; constraint profile (timeframe_bucket_full, max_portfolio_risk_exceeded,
  portfolio_risk_cap, max_correlated_exposure counts); per-class P&L; per-trade expectancy.
- **Compute fallback:** if the two-book gate threatens to exceed ~25 min, drop `book_e_126`
  mid-run and report 252 only (the ledger charge for 126 stands — it is recorded before the
  runs; that is the pre-registered cost).
