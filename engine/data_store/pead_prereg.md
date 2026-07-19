# PEAD sleeve — pre-registration (2026-07-19)

Written BEFORE any gate run. Registered in the shared TrialLedger (6 trials,
recorded by `scripts/run_pead_gate.py` before the runs; ledger n_trials 199 -> 205,
and 205 is the deflation count for every DSR below).

## Hypothesis

Post-earnings-announcement drift, long-only, on liquid US mega/large-caps: after a
POSITIVE earnings surprise the drift continues for days-to-weeks, and the long side
is the robust, implementable side (Bernard & Thomas 1989, JAE — the original PEAD
result; Chordia, Goh, Lee & Tan 2014, JAE — PEAD attenuated to ~0.14%/month in the
most liquid names vs ~1.60%/month in the least liquid). The honest long-only US
retail estimate from the Task B audit is **net Sharpe 0.4–0.6**. We test whether
that survives this engine's costs and gates.

Expectation going in: the effect is real but thin in liquid names, the sleeve is
sparse (~4 events/stock/yr, ~40% qualifying), and the likely outcome is
"promising, not certified". The gates decide, not the narrative.

## Universe (halal business-activity screen applied)

33 single-name US-listed equities: AAPL MSFT NVDA META AMZN GOOGL TSLA AMD BA HD
JNJ KO MA PG V WMT XOM PLTR NFLX UBER CAT PFE ABBV MRK NKE MCD COST PEP QCOM TXN
AVGO MU AMAT.

- **No banks/financials**: JPM/GS excluded from the cached universe (prohibited
  activity). No insurers, no alcohol/tobacco/gambling names present.
- Borderline kept-and-flagged: V/MA (payment networks — fee-based, not
  interest-based, but "financial services" under strict reads) and BA (defense —
  excluded by strict Islamic screens). Kept per the task brief.
- TSM excluded: foreign private issuer filing 6-K/20-F, not 8-K Item 2.02 — EDGAR
  yields no earnings dates (verified 2026-07-19).
- ETFs (SPY/QQQ/...) excluded — no earnings events.
- **AAOIFI debt-ratio screen NOT applied** (needs balance-sheet data; FMP key is
  unavailable engine-side — see below). Flagged friction from the audit: the drift
  is strongest in exactly the debt-heavy names such a screen would remove, so a
  compliant implementation would likely be weaker than what is measured here.

## Data

- **Earnings dates**: SEC EDGAR 8-K Item 2.02 filing dates per stock, cached at
  `engine/data_store/earnings_calendar/{SYM}.json` by
  `scripts/build_earnings_calendar.py`. Source decision: FMP's historical earnings
  calendar was the first choice but `FMP_API_KEY` exists only in the Vercel
  deployment environment (not in engine/.env, no local vercel CLI); Yahoo chart
  `events=earnings` returns zero historical events and quoteSummary/calendarEvents
  (api/events.js) is forward-looking only, as is Finnhub's free calendar. EDGAR is
  free, key-less, and the filing of record for the earnings release.
- **Coverage (honest)**: 33 names, 1,148 events in the 2016–2024 iteration window
  (~3.9/stock/yr — expected ~4). Full-history spans per name in
  `_summary.csv`: most names 2004→2026; late listings shorter (PLTR from 2020-11,
  UBER from 2019-05, AVGO from 2018-06). Validation: event windows show 2.36x the
  mean |2-day return| of non-event windows (4.61% vs 1.95%), every name ratio
  >= 1.3 — dates are real and aligned to the price data.
- **Known approximations**: filing date ≈ release date (same-day for large caps,
  occasionally +1 day); NO BMO/AMC flag and NO analyst EPS estimates — hence the
  price-based surprise proxy below, NOT SUE.
- **Prices**: Yahoo daily parquets in engine/data_store (7 names newly cached:
  MRK PEP QCOM TXN AVGO MU AMAT). Iteration window strictly < 2025-01-01; the
  2025+ holdout is never touched.

## Strategy (engine/apex_quant/strategies/pead.py)

- Event: filing date D -> T0 = first trading day >= D, T1 = next trading day.
- Surprise proxy: **ann_ret = close(T1)/close(T0-1) − 1** (2-day close-to-close
  window spanning both BMO and AMC reactions without needing the flag).
  Positive surprise := ann_ret >= **+2%**. (~41% of events qualify on 2016-2024.)
- Entry: LONG signal emitted at T1's close, filled at the next bar's open —
  ~1 bar after the surprise; all inputs known at decision time.
- Exit: **fixed horizon** — exactly `holding_horizon` trading days, implemented
  as `exit_mode="barrier"` with a catastrophic-only signal barrier pair
  (−30%/+200%) that should essentially never bind within 20 days for these names.
  Verified in the gate's exit-reason tally (expect ~100% "time"). The managed TMS
  stack is deliberately NOT used: the PEAD premium is the N-day drift, and
  trailing/breakeven exits would change the bet.
- Sizing: standard RiskManager path (fractional-Kelly on an honest p ~ 0.53–0.60
  and reward_risk 1.5, capped at max_risk_per_trade 2%), config risk caps binding
  (max_portfolio_risk 6.5%, gross 3x, correlation cluster caps, regime scale).
  Audit note: IF certified, deployment would be at HALF risk-budget — a
  deployment decision, not a gate input.
- Costs: config equity bps model (spread 2.0 bps + slippage 1.0 bps per side,
  ~4 bps round trip) — the v5 mechanics as configured, unchanged.

## Configurations (the full selection set: exactly 6 trials)

| # | name          | hold | filter                              |
|---|---------------|------|-------------------------------------|
| 1 | pead_h10      | 10   | none (HEADLINE)                     |
| 2 | pead_h05      | 5    | none                                |
| 3 | pead_h20      | 20   | none                                |
| 4 | pead_h10_vol  | 10   | gap-day volume > trailing-63d median|
| 5 | pead_h20_vol  | 20   | gap-day volume > trailing-63d median|
| 6 | pead_h10_madj | 10   | ann_ret − SPY matched 2d return >= 2%|

Volume filter definition: max(vol(T0), vol(T1)) > median(volume, 63 bars ending
the day before T0). Market-adjustment uses SPY's own bars bracketing T0/T1.

## Gates (identical machinery/thresholds as every prior book gate)

- DSR > 0.95, deflated by the FULL TrialLedger count (205 after recording).
- PBO < 0.5 across the 6 configs (the whole pre-registered selection set).
- CPCV (15 paths, purge = the config's holding horizon): median OOS Sharpe > 0
  AND > 50% of paths positive.
- Annualization 252 (equities). Warmup 70 bars (63-bar volume median + margin;
  documented deviation from the 250-bar trend-gate warmup — an event-driven
  sleeve needs no long lookback).
- Report per config: full-window Sharpe/PF/maxDD/expectancy/trades-per-week
  (expect sparsity), per-stock contribution (concentration check), exit-reason
  tally, constraint log. Determinism check on the headline (run twice, identical).

## Honesty rules

Iteration window only (strictly < 2025-01-01). Trials recorded BEFORE running.
No config beyond the 6 above will be tried against these gates; if all 6 reject,
the verdict is REJECT, not another sweep.
