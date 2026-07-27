# PRE-REGISTRATION — Prop-condition rate frontier on the trend-ensemble book (2026-07-27)

**Status: pre-registered BEFORE any sweep run.** This document fixes the question, the
configuration grid, the metrics, the Monte Carlo design (firm rules, path counts, seeds),
the reporting framing, and the ledger plan before execution. Changing anything after the
run requires a new pre-registration and new ledger charges.

**Base book:** the ADOPTED multi-horizon trend ensemble — `momentum_lookbacks = [63, 126,
252]` on `book_h_gold_39` (trend_ensemble_gate_2026-07-27: Sharpe 0.92377, worst day
−3.45%, maxDD 15.92% at the certified risk anchor `max_risk_per_trade = 0.01`,
`max_portfolio_risk = 0.065`). Everything else certified: universe and panel insertion
order (EQUITY_CORE first — ordering-sensitive), carry_filter off, lookback machinery, vol
63, hold 21, rr 1.5, rule_based regime, HTF 1w×50 gate, managed exits, vol-scaled sizing,
per-class v5 costs, `max_total_exposure 3.0`, `max_correlated_exposure 1.5`, drawdown
breakers 0.10/0.20, swing bucket 10, global 12. Iteration window strictly < 2025-01-01,
seed 42, warmup 250. The 2025+ holdout is not touched.

**This is a MEASUREMENT study, not an adoption gate.** No certified default changes here.
The output is an efficient frontier — rate vs funded-rule survival — so the future funded
runner (config.prop.yaml) can be placed at an informed operating point. There is no
"winner" to promote and no gate verdict; the only pre-committed selection is the
*recommendation rule* in §6.

---

## 1. Question

What is the highest honest monthly rate the validated book can run at while still
surviving FUNDED-ACCOUNT conditions — (a) the daily-loss rule at 3% of equity (FTMO
1-step profile) and at 5% (The5ers Pro Growth profile), (b) the account floor (10%
EOD-trailing / 5% static), and (c) per-trade risk sane enough that a single evaluation
attempt passes with ~70%+ probability?

The two risk knobs that move both the rate and the daily-loss distribution are
`risk.max_risk_per_trade` (per-trade stop sizing) and `risk.max_portfolio_risk`
(aggregate open-risk cap — the binding constraint on how much can be lost in one day when
positions cluster). Both are real engine knobs (RiskManager), and both are exactly what
config.prop.yaml sets for the funded runner.

## 2. The bounded sweep (the full selection set: exactly 12)

Ensemble `[63, 126, 252]` base, certified everything else; ONLY the two risk knobs vary:

| knob | values |
|---|---|
| `max_risk_per_trade` | 0.0075, 0.01, 0.0125, 0.015 |
| `max_portfolio_risk` | 0.025, 0.035, 0.045 |

4 × 3 = **12 configs**, named `prop_r075_cap025` … `prop_r150_cap045`. The cap range
brackets config.prop.yaml's 0.040; the per-trade range brackets the certified 0.01 anchor
and stops at 1.5% (the account owner's noted ceiling — beyond it cap collisions destroy
rate, config.yaml risk note 2026-07-23). No other grid points, no post-hoc extensions;
anything else is a new pre-registration.

**Reference point (no new trial):** the certified 252-only anchor at mrpt 0.01 / cap
0.065 (book_h_gapaware_2026-07-22: Sharpe 0.86284, worst day −5.09%, maxDD 16.32%, avg
+£1,783/mo) and the already-measured ensemble control at the same certified caps (Sharpe
0.92377, worst day −3.45%, avg +£2,002/mo). These bound the comparison "ensemble vs
252-only" from existing ledger entries; the sweep itself is ensemble-only.

## 3. Backtest metrics per config (full window, iteration only)

Per config, one full-window run on the certified panel (same orchestration as
run_portfolio_gate_trend_ensemble.py; `cfg.risk.max_risk_per_trade` and
`cfg.risk.max_portfolio_risk` overridden, nothing else):

- Sharpe, Sortino, profit factor, win rate, trades, expectancy (£ and %), max drawdown,
  total/annualized return, final equity (the standard `compute_metrics` set).
- Monthly-tail set (same helper as prior gates): n months, avg/median/worst month £,
  worst daily return, worst daily £, worst trade £.
- **Daily-loss distribution** from the run's daily equity returns: min, p1, p5, the 5
  worst days, and the fraction of trading days at or below −2.5%, −3%, and −5% (the
  daily-rule thresholds the firms use, plus the declared 2.5% engine stop level).
- **Trade-outcome distribution for the MC** (§4): per trade, `r_i = pnl_i /
  equity_at_entry_i` (equity curve value as-of the entry date), plus the empirical
  distribution of trade closures per trading day (including zero-closure days).

## 4. Monte Carlo per config (pre-registered design)

Per config, per firm profile, **20,000 paths**, seeded deterministically from seed 42
(`np.random.SeedSequence([42, config_index, firm_index])`, config/firm indices in
declaration order — independent streams per cell, reproducible on rerun).

**Resampling (honest to the config's own distribution):** each simulated day draws a
closure count K from the config's empirical closures-per-day distribution, then K trade
returns i.i.d. with replacement from that config's empirical `{r_i}` pool; the day return
is their sum, compounded into equity (`eq *= 1 + day_ret`). This replaces the old
hand-built 3-outcome R-multiple toy (scratch/prop_risk_sweep.py) with the actual book.
Declared approximations: closures and trade outcomes are drawn independently (same-day
cross-instrument loss clustering is preserved only through the empirical closure-count
distribution); EOD marking only — no intraday path exists in a daily-bar backtest, so
"daily loss" is close-to-close, which is the conservative-but-standard reading of an
equity daily-loss rule.

**Firm profiles (fixed here, treated as given):**

| profile | target | daily-loss rule | floor |
|---|---|---|---|
| FTMO 1-step | +10% | day loss ≤ −3% of day-start equity ⇒ FAIL | EOD equity ≤ 90% of peak EOD equity (trailing, ratchets up) ⇒ FAIL |
| The5ers Pro Growth | +6% | day loss ≤ −5% of day-start equity ⇒ FAIL | EOD equity ≤ 95% of START balance (static) ⇒ FAIL |

Attempt mechanics: normalized start balance 1.0; up to 252 trading days (12 months) per
attempt; checks evaluated on EOD equity in this order — daily-rule breach, floor breach,
then target (a breach on the pass day still fails the path). Paths neither passed nor
failed by day 252 are **timeouts** (counted as not-passed). Recorded per cell: pass
probability, median months to pass (21 trading days/month, passers only), timeout
fraction, fail-by-daily fraction, fail-by-floor fraction, and the per-day daily-rule
breach rate across all active path-days.

**Funded 12-month survival:** a fresh 252-day funded sim per cell (same resampling, same
rules, balance reset to 1.0, trailing peak reset): survival = no daily-rule breach and no
floor breach over 12 months. Also record the median monthly % among survivors.

## 5. Deliverables

- This prereg; study script `engine/scripts/run_prop_condition_frontier.py`.
- Validation JSON `engine/data_store/validation/prop_condition_frontier_2026-07-27.json`
  (backtest metrics + daily-loss distribution + MC cells for all 12 configs; ledger
  before/after counts).
- Report `engine/data_store/prop_condition_frontier.md`: the frontier table (config ×
  rate × worst-day × FTMO pass%/months/survival × ProGrowth pass%/months/survival) and
  the recommended operating point per firm with reasoning.

## 6. Pre-committed reporting / recommendation rule

Report the full 12-point frontier — NOT a single "winner". The recommendation, per firm
profile, is: **the highest avg-£/month config whose MC pass probability is ≥ 70%** (the
sane-survival bar), with its daily-fail, floor-fail, and funded-survival numbers stated
alongside; if several configs tie within noise, prefer the lower per-trade risk. If NO
config clears 70% for a firm, say so and recommend the max-pass config instead — the
frontier is reported either way.

## 7. Ledger plan

`TrialLedger` at **n = 282** at writing. Exactly **12 new trials** (the 12 configs, kind
`prop_condition_frontier`) are recorded BEFORE the first run → **n = 294**. The 252-only
reference uses existing ledger entries (no charge). No other configs will be evaluated.

## 8. Determinism

Backtests are deterministic (no RNG; seed 42 fixed in config). The full study is run
twice (`--out` twin): the results payload must be byte-identical modulo `generated_at`
and the ledger bookkeeping line (first pass 282 → 294, twin dedups 294 → 294). MC streams
derive from SeedSequence([42, …]) — identical on rerun. py_compile + full pytest suite
green; per-commit pushes.

## 9. Known limitations

- **EOD-only daily-loss measurement.** A daily-bar backtest cannot see intraday equity
  excursions; a real firm's equity daily-loss rule marks intraday. The MC's daily-loss
  numbers are therefore a lower bound on true breach frequency; the report must say so.
- **Backtest-to-live haircut is not modeled.** The MC resamples the *backtest* trade
  distribution (as tasked); live slippage/liquidity can degrade expectancy. The certified
  book's costs are already per-class v5, but the pass probabilities read as
  backtest-honest, not live-guaranteed.
- **Independence approximation** (§4): closure counts and trade returns are drawn
  independently per day; portfolio-level loss clustering enters only through the
  empirical closure-count distribution, not through correlated same-day outcomes.
- **Firm rules as stated in §4 are the study's fixed profiles**, simplified from the
  firms' full rulebooks (no profit-split, consistency, or news-trading clauses modeled).
- **Timeouts count as not-passed**; a firm with no time limit would convert some
  timeouts into later passes, so pass% is mildly conservative.
