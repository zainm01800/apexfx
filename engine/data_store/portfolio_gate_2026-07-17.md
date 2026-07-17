# Portfolio-Level Gate — Diversified Daily Trend Book ± Carry Filter — 2026-07-17

**Window:** ITERATION only, strictly < 2025-01-01 (daily bars, 2016-01-03 → 2024-12-31 store-limited; crosses end 2024-12-30). No `--final` run; the 2025+ holdout was not touched in any way.
**Costs:** per-pair realized costs, config v5 (majors ~1 pip RT, crosses up to ~10 pips RT).
**Gate:** `run_portfolio_cpcv` + `deflated_sharpe_ratio` + `probability_of_backtest_overfitting`, thresholds identical to the single-instrument gate (`validation/portfolio_report.py`): DSR > 0.95 **and** PBO < 0.5 **and** CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive. DSR deflated by the shared TrialLedger's full updated count: **n_trials = 106** for both books.
**Hypothesis (pre-registered):** the academically defensible trend claim (`docs/research/2026-07-17_fx_edges_evidence.md`: Hurst/Ooi/Pedersen; Moskowitz/Ooi/Pedersen) is about the **diversified vol-scaled book across markets**, not single pairs — so the book, not any pair, is the unit that could pass. Follow-up to `candidate_sweep_2026-07-17.md`, where nothing passed at single-pair level.
**Script:** `engine/scripts/run_portfolio_gate.py` (thin orchestration over existing machinery; the `TrendBook` adapter plays the role `EnsembleVote` plays for `validate_ensemble.py`). Machine-readable output: `engine/data_store/validation/portfolio_gate_2026-07-17.json`.

---

## Pre-registered configurations (the full selection set: exactly 2 trials)

Both books: all 22 config forex pairs, one shared equity curve (`PortfolioBacktester`, managed exits, warmup 250), vol-scaled sizing via `RiskManager`, config risk caps binding (`max_total_exposure 3.0`, `max_correlated_exposure 1.5`, `max_portfolio_risk 0.065`, `max_risk_per_trade 0.02`, drawdown breakers 0.10/0.20), CPCV purge = holding horizon 21.

| | Book A — `book_a_plain_trend` | Book B — `book_b_carry_filtered` |
|---|---|---|
| Signal per pair | `RegimeGatedMomentum` wrapped in `MultiTimeframeMomentum` (the live 1d stack, see `run_live_paper_trading.py`) | identical, but the base signal is wrapped in `CarryTrendFilter` (veto when the trade direction earns negative carry; point-in-time policy rates) |
| momentum_lookback | 126 | 126 |
| vol_window | 63 | 63 |
| holding_horizon | 21 | 21 |
| reward_risk | 1.5 | 1.5 |
| regime_method | rule_based | rule_based |
| HTF gate | htf_rule="1w", htf_ma_window=50 | htf_rule="1w", htf_ma_window=50 |

## Verdicts

| Book | DSR | PBO | CPCV med OOS | frac +ve | Verdict |
|---|---|---|---|---|---|
| A — plain diversified trend (22 pairs) | 0.003 | 0.663 | −0.044 | **0%** of 15 | **REJECT** |
| B — carry-filtered diversified trend (22 pairs) | 0.000 | 0.663 | −0.018 | **0%** of 15 | **REJECT** |

**Both books fail all three gates.** Unlike the single-pair sweep (where EUR/USD rows failed only on DSR), the diversified book fails everywhere: the out-of-sample bleed is uniform — every one of the 15 CPCV paths is negative for both books. This is a systematic drain, not variance or selection luck.

CPCV paths (per-period Sharpe):
- A: `[−0.047, −0.044, −0.044, −0.044, −0.044, −0.072, −0.072, −0.072, −0.072, −0.030, −0.023, −0.019, −0.052, −0.047, −0.028]`
- B: `[−0.025, −0.017, −0.017, −0.017, −0.017, −0.070, −0.069, −0.070, −0.069, −0.003, −0.006, −0.025, −0.003, −0.018, −0.038]`

## Full-window run (iteration window, caps binding)

| Metric | Book A | Book B |
|---|---|---|
| Trades | 2162 | 1788 (−17%) |
| Total return | −19.6% | −19.4% |
| Ann. return / vol | −2.1% / 3.4% | −2.0% / 2.8% |
| Sharpe (ann.) | −0.60 | −0.71 |
| Max drawdown | 20.0% | 20.0% |
| Win rate | 29.0% | 44.1% |
| Profit factor | 0.47 | 0.42 |
| Expectancy (`expectancy_pnl`, engine metric)* | −25.45 pnl/trade (−0.518%/trade) | −14.11 pnl/trade (−0.203%/trade) |
| Net per trade (net_pnl / n_trades; mean trade return) | −9.04 pnl (−0.150%) | −10.83 pnl (−0.113%) |
| Max gross leverage (approx, from trade list) | ~3.4× | ~3.0× |
| Caps bound (top families) | timeframe_bucket_full ×3547, max_risk_per_trade ×2259, regime_scale ×2259, drawdown_reducing_scale ×2128, drawdown_breaker ×1832, max_portfolio_risk_exceeded ×85, portfolio_risk_cap ×27, max_total_exposure ×23 | max_risk_per_trade ×1828, regime_scale ×1828, drawdown_reducing_scale ×1755, timeframe_bucket_full ×514, max_portfolio_risk_exceeded ×32, portfolio_risk_cap ×14, max_total_exposure ×8 |
| Pairs net positive | 3/22 (NZD/JPY +914, EUR/JPY +239, EUR/USD +206) | 3/22 (EUR/JPY +884, USD/CAD +257, CHF/JPY +224) |
| Worst pairs | AUD/USD −3888, USD/JPY −2440, GBP/NZD −2250, GBP/JPY −2141 | GBP/JPY −3585, USD/JPY −2578, GBP/CAD −2237, EUR/GBP −1810 |

\* `compute_metrics.expectancy_pnl` counts scratch (break-even, |pnl| rounds to 0) trades in its `loss_rate`, which over-weights losses — with ~750 such trades in Book A it reads ~2.8× more negative than the plain mean. Both numbers are reported; the mean is the fair per-trade figure.

## What the carry filter did (mechanically, as designed)

Fewer trades (2162 → 1788), higher win rate (29% → 44%), better mean trade (−0.150% → −0.113%), better CPCV median (−0.044 → −0.018) — and the book still loses on 15/15 paths. Same conclusion as the single-pair sweep: the veto removes some negative-carry losers but **cannot create edge where the underlying trend signal has none**.

## Bug found and fixed during this run (affects interpretation of the sweep)

The smoke test exposed impossible exits (e.g. a GBP/USD short closed at a "target" of 111.12 — a USD/JPY-scale price; one trade at −84.9× return). Root cause: `CarryTrendFilter` built its internal `RegimeGatedMomentum` **without `instrument`**, so (a) the class-level Bollinger cache in `baseline.py` was keyed `("", "1d", t)` — shared across every pair in the process, serving the first pair's band midline as another pair's mean-reversion target — and (b) the asset class fell back to "equity", applying a 1.5× regime slope eps and quietly changing signal frequency. Fixed by adding an optional `instrument` pass-through to `CarryTrendFilter` (default `None` = old behavior, so existing callers are unaffected); `run_portfolio_gate.py` passes it for every pair. Post-fix, Book B's base signal is byte-for-byte the same construction as Book A's — the carry veto is the only difference.

**Implication for `candidate_sweep_2026-07-17.md`:** `run_candidate_check.py` never passes `instrument` either, so all RegimeGatedMomentum/CarryTrendFilter rows in that sweep ran with the equity regime eps, and pairs validated after the first in each process could read cross-contaminated Bollinger targets on mean-reversion trades. EUR/USD (validated first) was unaffected; the other five pairs' carry/trend rows should be treated as approximate. Every one of those rows was a REJECT anyway, and this portfolio-level result supersedes them — but if a single-pair number is ever load-bearing, re-run it with the fixed wrapper.

## Honest commentary

- **The diversified-book hypothesis is rejected for this engine, full stop.** The one framing the literature actually supports — a vol-scaled trend *book*, not single pairs — was given its cleanest possible shot: the live baseline stack, pre-registered parameters, real per-pair costs, book-level risk caps, and the same three gates everything else faces. It lost money on 15/15 out-of-sample paths, with and without the carry filter.
- **Why the literature doesn't rescue it:** the documented diversified-trend edge (Hurst/Ooi/Pedersen; MOP) trades dozens of largely uncorrelated futures markets — equity index, bonds, commodities, FX — at institutional costs. This book is 22 FX crosses that share a handful of currency factors (the correlation-cluster and portfolio-risk caps bound constantly: `max_portfolio_risk_exceeded` ×85/×32, `portfolio_risk_cap` ×27/×14), so the effective diversification is far below the pair count, and v5 retail costs (crosses up to ~10 pips RT) sit on every one of ~2,000 round trips. A few-tenths-of-a-Sharpe institutional edge does not survive that translation.
- **The risk system did its job.** maxDD reads exactly 20.0% in both books because the drawdown breaker (0.20) fired and halted new entries (×1832 in A); the trough is the cap, not the market. Leverage stayed at the 3× gross cap (`max_total_exposure` bound ×23/×8). The book failed slowly and safely — the failure is in the signal, not the plumbing.
- **No zero-cost control was run** (that would be another trial charged to the ledger): the gate judges the net-of-cost result, which is the only thing that is tradable. Decomposing how much of the bleed is costs vs adverse selection is a research question, not a validation one.
- **PBO caveat:** with exactly 2 pre-registered configs, PBO only asks "does the in-sample-better book stay better out-of-sample?" — 0.663 says worse than a coin flip. It is coarse by construction (n_configs=2), and moot here: both books independently fail DSR and CPCV.
- **Next step:** none on this hypothesis. A `--final` holdout look is only warranted for a config that PASSES the iteration gate; running one on a rejected book would burn holdout blindness for nothing. If trend is to remain on the roadmap, the honest levers are (1) cost reduction — the measured cost drag is the one thing that is clearly fixable — or (2) a different signal family, pre-registered as a new hypothesis and charged to the ledger.

## Ledger

- **n_trials before: 104**
- **n_trials after: 106** (+2: `book_a_plain_trend`, `book_b_carry_filtered` — recorded before the runs; DSR deflated by 106 for both books)

## Compute notes

- Full 22-pair universe used; **no pair reduction was needed** (the deliberate fallback of dropping EUR/NZD, CHF/JPY, GBP/NZD was not triggered): the complete gate — 2 full-window runs + PBO + 2×15 CPCV paths — ran in ~92 s on .venv-mac.
- Determinism: Book A's 3-pair smoke subset produced identical numbers across repeated runs; PBO uses `cfg.seed` (42); no RNG elsewhere.
- The class-level regime/HTF/Bollinger caches are keyed per point-in-time data object (+ instrument/eps), so the two books share caches safely; all strategy/feature reads are `PointInTimeAccessor` windows (≤ t only). The central-bank rates provider is effective-dated; the 2024-12 row is the last one any strict < 2025-01-01 lookup can see.
- Results JSON: `engine/data_store/validation/portfolio_gate_2026-07-17.json`.
