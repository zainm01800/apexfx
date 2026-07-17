# FX Majors Stack — Pre-Registration (2026-07-17)

Written BEFORE any run in this batch. Purpose: gate the evidence-backed FX sleeves
(docs/research/2026-07-17_fx_edges_evidence.md) on the **7 majors only**
(EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD — the cheapest-cost
pairs under config v5: ~1 pip RT class defaults, vs 2–10 pips on crosses that
already killed the 22-pair book), then gate the combined book.

## Honesty rules (binding)

- Iteration window strictly < 2025-01-01. The 2025+ holdout is never loaded. No `--final`.
- Every config below is recorded in the shared TrialLedger
  (`data_store/validation/trial_ledger.json`, n=136 before this batch) BEFORE its
  run; DSR is deflated by the ledger's full count at run time.
- Budget for this batch: **≤ 10 new trials** (dedup by canonical JSON; identical
  configs already ledgered do not re-count). Planned: 9 new (3 + 2 + 2 + 2), 1 spare.
- Gate (unchanged): DSR > 0.95, PBO < 0.5, CPCV median OOS Sharpe > 0 with
  > 50% of paths positive. No parameter tuning to sneak a pass.
- Seed: 42 (config.yaml). Determinism spot-checked by re-running one config.

## Sleeve A — carry-filtered slow trend (per-instrument gate)

Machinery: `scripts/run_candidate_check.py --factory carry_trend`, one run over the
7 majors, timeframe 1d.

Headline config (only config — see budget note below):
`{momentum_lookback: 126, vol_window: 63, holding_horizon: 21, reward_risk: 1.5,
regime_method: rule_based, timeframe: 1d}` — identical to the headline already
ledgered+run today for EUR/USD, GBP/USD, USD/JPY, AUD/USD (those 4 dedup, 0 new
trials; their gates re-run for uniform reporting). **New trials: USD/CHF, USD/CAD,
NZD/USD = 3.**

Budget note: the single-instrument PBO needs ≥2 configs in the run's grid or it
fails closed (n/a). The 3-config grids for the 4 previously-gated majors are already
in the ledger from this morning (all REJECT, best EUR/USD DSR 0.416); re-spending
9 new trials to replicate that grid on the 3 remaining majors would consume the
entire batch budget. So Sleeve A per-instrument verdicts for the 3 new majors are
headline-only: DSR/CPCV reported, per-instrument PBO n/a by construction. The
book-level carry-filtered trend test (which is where the academically defensible
claim lives anyway — diversified book, not single pairs) is covered by this
morning's `run_portfolio_gate.py` books and by the combined stack below.

## Sleeve B — carry tilt (book-level gate)

Machinery: `run_portfolio_validation` (DSR/PBO/CPCV, PortfolioBacktester, managed
exits, config risk caps binding, per-pair v5 costs) via a new thin script
`scripts/run_fx_majors_stack_gate.py`. Model: `CrossSectionalCarry` with
`CSVRateProvider` (point-in-time policy rates, monthly 2013→2024).

Turnover finding (pre-registered, from `backtest/portfolio.py` mechanics):
CrossSectionalCarry re-ranks daily but the book holds **one position per
instrument** and only evaluates a new entry when flat, so it is NOT
daily-rebalanced churn. Position lifetime is exit-driven (managed mode: time-stop
only for losers after ~7 daily bars, winners trail); on exit the pair re-enters
immediately if still in a carry bucket. Policy-rate ranks move ~monthly at most, so
effective rotation is monthly-ish; realized round-trips/year will be measured and
reported. The cost-sensitivity variant below stretches the forced rotation to
quarterly-ish regardless.

Grid (headline first):
1. `{long_frac: 0.30, short_frac: 0.30, holding_horizon: 21, reward_risk: 1.5}` — headline.
2. `{long_frac: 0.30, short_frac: 0.30, holding_horizon: 63, reward_risk: 1.5}` — quarterly-rotation cost-sensitivity variant.

**New trials: 2** (recorded as book `FX7_PORTFOLIO`, factory `cross_sectional_carry`).

## Sleeve C — XS momentum, majors-only (book-level gate)

Machinery: `scripts/validate_currency_momentum.py` (rewritten today for the shared
ledger/gate), minimally extended with `--instruments` and `--grid` CLI args.
Model: `CurrencyCrossSectionalMomentum` (currency-leg decomposition, k strong vs
k weak currencies). The prior failure was the 22-pair book at cross costs; this is
the genuinely different majors-only test at ~1 pip.

Grid (headline first; cut from the script's 5-config default to respect the budget):
1. `{lookback: 63, k: 2, holding_horizon: 21}` — headline (3-mo formation, monthly rotation).
2. `{lookback: 126, k: 2, holding_horizon: 21}` — 6-mo formation variant.

**New trials: 2** (recorded as `FX7_PORTFOLIO`, factory `currency_xs_momentum`).

## Combined stack — A+B+C as one book (book-level gate)

Per-instrument combination (the honest option — PortfolioBacktester keys one
strategy per instrument, so "concatenating books" is not literal): a thin
`StackedSignal` adapter aggregates the three sleeves' per-instrument signals —
Sleeve A `CarryTrendFilter` (headline params above), Sleeve B `CrossSectionalCarry`
(headline), Sleeve C `CurrencyCrossSectionalMomentum` (headline) — by majority
vote: direction needs ≥ min_votes of the 3 sleeves agreeing, else FLAT;
probability = mean of the agreeing sleeves; all three share the one
RiskManager/caps via the normal PortfolioBacktester path. Approximation documented:
sleeve positions are not separately sized — the combined signal is one position per
instrument per direction.

Grid (headline first):
1. `{min_votes: 2}` — headline (2-of-3 agreement).
2. `{min_votes: 3}` — unanimity variant (lower turnover, stricter).

**New trials: 2** (recorded as book `FX7_STACK`, factory `fx_majors_stack`).

## Trial budget summary

| Sleeve | New trials | Deduped (already ledgered) |
|---|---|---|
| A carry-filtered trend (7× headline) | 3 | 4 (EUR/USD, GBP/USD, USD/JPY, AUD/USD) |
| B carry tilt book | 2 | 0 |
| C XS momentum majors book | 2 | 0 |
| Combined stack | 2 | 0 |
| **Total** | **9 ≤ 10** | |

Verdicts will be written to `data_store/fx_majors_stack_gate_2026-07-17.md` with
per-sleeve + combined DSR/PBO/CPCV/expectancy/PF/maxDD and per-pair P&L. If nothing
passes, the report says so plainly; any PASS earns one user-approved `--final`
holdout look, not automatic deployment.
