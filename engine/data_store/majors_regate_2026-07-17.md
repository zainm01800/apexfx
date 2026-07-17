# Majors Re-Gate — 2026-07-17 (fixed engine, legitimacy re-run)

**Question:** do any of the 7 FX majors earn re-entry now that the engine and data are
rebuilt (session-calendar fix, per-pair v5 costs, managed exits, per-TF annualization,
regime eps alignment, BB cache fix, time-stop parity)? This was a legitimacy re-run of
the *same* configs on repaired plumbing — **not** new tuning.

**Answer: NO. 0 of 7 majors pass the full single-instrument gate. The EUR/USD
carry-filter variant also fails. Nothing was un-vetoed.
`high_frequency_optimized_configs.json` is untouched. The active book stays
AUD/USD + NZD/USD only.**

---

## Setup and honesty controls

- Gate: `engine/scripts/run_candidate_check.py`, ITERATION mode only — the 2025+
  holdout was never touched (it is burned for the trend family anyway).
- Window: **2016-01-04 → 2024-12-31 strict** (2,333 daily bars/pair; store migrated
  today to single 00:00-UTC day-bar convention; phantom weekend bars already removed).
- Factory: `default` = `RegimeGatedMomentum`; EUR/USD variant: `carry_trend`
  = `CarryTrendFilter`.
- Grid (per pair, the `baseline-mom126` sweep from `scripts/run_backtests.py::_sweep`):
  headline `{momentum_lookback 126, vol_window 126, holding_horizon 10, reward_risk 1.5,
  regime_method rule_based}`, mates `{63/63 rule_based}` and `{126/126 hmm}`.
  7 pairs × 3 configs + EUR/USD carry × 3 configs = 24 configs submitted to the ledger.
- Exits: managed (TradeManager), warmup 250, seed 42.
- Costs: config v5 per-pair round-trip (EUR/USD & USD/CAD class default ~1 pip RT;
  GBP/USD 2.4; USD/JPY 0.6 via pair×TF override; USD/CHF 0.8; AUD/USD 3.3;
  NZD/USD 2.1 via pair×TF override).
- TrialLedger: **n = 152 → 170** (18 new distinct trials; 6 of the 24 were already
  recorded from earlier GBP/USD / single-config sweeps and deduped). All 24 configs
  were recorded **before any run**, so every DSR below is deflated by the same final
  n = 170 — not by this run's 3-config grid.
- Determinism spot-check: EUR/USD gate run twice; `dsr`, `pbo`, `cpcv`, `verdict`
  payloads bit-identical (seed 42 holds end-to-end).
- Per-run artifacts: local caches `data_store/validation/{regime_gated_momentum,carry_trend_filter}__*.json`;
  Supabase `post_backtest` accepted every row (labels `majors-regate-2026-07-17`,
  `majors-regate-carry-2026-07-17`).

## Gate results (thresholds: DSR > 0.95, PBO < 0.5, CPCV median OOS Sharpe > 0 AND > 50% of 15 paths positive)

| Pair | DSR | PBO | CPCV med OOS | CPCV frac +ve | Verdict |
|---|---|---|---|---|---|
| EUR/USD | 0.578 ✗ | 0.790 ✗ | −0.009 | 33% ✗ | **REJECT** |
| GBP/USD | 0.417 ✗ | 0.789 ✗ | +0.020 | 60% ✓ | **REJECT** |
| USD/JPY | 0.002 ✗ | 0.328 ✓ | −0.007 | 13% ✗ | **REJECT** |
| USD/CHF | 0.000 ✗ | 0.760 ✗ | −0.030 | 0% ✗ | **REJECT** |
| AUD/USD | 0.036 ✗ | 0.655 ✗ | +0.004 | 67% ✓ | **REJECT** |
| USD/CAD | 0.001 ✗ | 0.837 ✗ | −0.034 | 27% ✗ | **REJECT** |
| NZD/USD | 0.047 ✗ | 0.982 ✗ | −0.019 | 27% ✗ | **REJECT** |
| EUR/USD (carry filter) | 0.197 ✗ | 0.791 ✗ | +0.005 | 53% ✓ | **REJECT** |

**Pairs passing the full gate: none.**

DSR is the universal killer: no pair gets above 0.58 against a 0.95 bar once deflated
by the honest n = 170 trial count. PBO ≈ 0.79 on 5 of 8 rows says even the in-sample
config *selection* is overfit-prone. CPCV passes on 3 rows (GBP/USD, AUD/USD, EUR/USD
carry) are near-zero medians (+0.004…+0.020), not edges.

## Full-window stats, headline config (2016-01-04 → 2024-12-31, managed exits, v5 costs)

| Pair | Trades | Entries/mo | Expectancy ($/trade) | Profit factor | Sharpe | Max DD |
|---|---|---|---|---|---|---|
| EUR/USD | 124 | 1.15 | +29.83 | 1.12 | 0.19 | 6.3% |
| GBP/USD | 130 | 1.20 | +45.51 | 1.18 | 0.27 | 6.1% |
| USD/JPY | 127 | 1.18 | −57.22 | 0.80 | −0.28 | 8.5% |
| USD/CHF | 116 | 1.08 | −124.43 | 0.48 | −0.81 | 15.6% |
| AUD/USD | 125 | 1.16 | −14.26 | 0.95 | −0.05 | 10.1% |
| USD/CAD | 110 | 1.02 | −98.37 | 0.61 | −0.58 | 12.8% |
| NZD/USD | 118 | 1.09 | −77.45 | 0.77 | −0.32 | 11.8% |
| EUR/USD (carry) | 58 | 0.54 | −31.79 | 0.87 | −0.07 | 4.1% |

## Honest expectation-setting

- **Even the "good" rows are thin, and the gate is right to reject them.** EUR/USD and
  GBP/USD show positive full-window expectancy (+$30/+$46 per trade, PF 1.12/1.18,
  Sharpe 0.19/0.27). But ~$40/trade on a 10-day hold is a fraction of the round-trip
  cost envelope; with 170 trials on the clock, DSR says results this good arise by
  chance ~42–58% of the way to the bar, and PBO ~0.79 says the winning config is
  usually an in-sample artifact. Thin edge + multiple testing = indistinguishable
  from luck. That is the machinery working as designed.
- **Frequency is low regardless.** The headline config fires ~1.0–1.2 entries/month
  per pair. Had all 7 majors passed (they did not), the whole 7-pair trend book would
  add only ≈ 7.9 entries/month ≈ **1.8 trades/week** — before correlation overlap
  (EUR/GBP/CHF vs USD are heavily cross-correlated; realized independent bets would be
  fewer). The carry variant halves EUR/USD frequency (0.54/mo) and still loses money.
- **Resulting active book: AUD/USD + NZD/USD only** (their live status rests on their
  own prior validations and configs, not on this mom126-headline re-gate; this run's
  scope was un-veto only, and their config entries were not modified). On the tested
  headline config those two pairs would together yield ≈ 2.25 entries/month ≈
  **0.5 trades/week** — that is the realistic order flow to expect from the majors
  sleeve. Anything beyond that has to come from crosses/other families, not from
  forcing the majors back in.

## What changed on disk

- `data_store/validation/trial_ledger.json`: n 152 → 170 (24 configs recorded pre-run, 18 net new).
- `data_store/validation/regime_gated_momentum__{EUR,GBP,AUD,NZD}_USD.json`,
  `...__USD_{JPY,CHF,CAD}.json`, `carry_trend_filter__EUR_USD.json`: refreshed with
  post-fix gate reports (labels above; also posted to Supabase).
- `scratch/harvest_majors_regate_metrics.py` + `scratch/majors_regate_metrics.json`:
  full-window stats harvest (no new trials — same recorded configs).
- `data_store/high_frequency_optimized_configs.json`: **unchanged** (no passes → no un-vetoes).
- `engine/config.yaml`, `run_live_paper_trading.py`: untouched. No post-2024 data used anywhere.
