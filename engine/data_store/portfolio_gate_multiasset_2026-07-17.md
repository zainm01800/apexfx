# Portfolio-Level Gate — Diversified MULTI-ASSET Trend Book (126 vs 252) — 2026-07-17

**Window:** ITERATION only, strictly < 2025-01-01 (daily bars; store-limited span 2016-01-01 → 2024-12-31; per-instrument history starts at listing — PLTR 2020, UBER 2019, ETH 2020, SOL 2021, ARB/SUI 2023, etc.). No `--final` run; the 2025+ holdout was not touched in any way.
**Costs:** per-asset-class, unchanged engine mechanics — forex per-pair v5 pips (majors 0.6–3.3 RT), equities 2.0 bps/side (0.5×2.0 spread + 1.0 slippage), crypto 1.25 bps/side (0.5×1.5 + 0.5).
**Gate:** identical to the FX-only gate (`validation/portfolio_report.py` thresholds): DSR > 0.95 **and** PBO < 0.5 **and** CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive. DSR deflated by the shared TrialLedger's full updated count: **n_trials = 108** for both books.
**Hypothesis (pre-registered):** the FX-only diversified book was rejected at this gate today (`portfolio_gate_2026-07-17.md`: 0/15 CPCV paths positive — 22 FX pairs ≈ 8 correlated currency factors at 1–10 pip RT costs). The literature the trend claim rests on (`docs/research/2026-07-17_fx_edges_evidence.md`: Hurst/Ooi/Pedersen; Moskowitz/Ooi/Pedersen) is explicit that the edge lives in a book diversified **across asset classes** — equities, bonds, commodities proxies, currencies (the AQR century study runs 67 markets). This run gives that claim its one pre-registered shot with the universes the engine already carries.
**Script:** `engine/scripts/run_portfolio_gate_multiasset.py` — thin orchestration reusing `run_portfolio_gate.py`'s machinery (`TrendBook` adapter, `_gate`, helpers) unchanged; per-asset-class mechanics are exercised through `PortfolioBacktester`'s existing `cfg.mechanics_for()` path. Machine-readable output: `engine/data_store/validation/portfolio_gate_multiasset_2026-07-17.json`.

---

## Pre-registered configurations (the full selection set: exactly 2 trials)

Both books: **24 equities/ETFs + 12 crypto + the 7 FX majors** (EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD — the cheapest v5 costs; bond/gold/sector exposure rides via TLT/GLD/XLE/XLF), one shared equity curve (`PortfolioBacktester`, managed exits, warmup 250), signal per instrument = `RegimeGatedMomentum` wrapped in `MultiTimeframeMomentum` (htf_rule="1w", htf_ma_window=50 — the live 1d stack) with `instrument` passed explicitly (per-instrument Bollinger cache + per-class regime eps: equity 1.5×, crypto 5×, forex 1×; crypto mean-reversion disabled), vol-scaled sizing via `RiskManager`, config risk caps binding (`max_total_exposure 3.0`, `max_correlated_exposure 1.5`, `max_portfolio_risk 0.065`, `max_risk_per_trade 0.02`, drawdown breakers 0.10/0.20), CPCV purge = holding horizon 21.

| | Book C — `book_c_multiasset_126` | Book D — `book_d_multiasset_252` |
|---|---|---|
| momentum_lookback | 126 | 252 |
| vol_window / holding_horizon / reward_risk | 63 / 21 / 1.5 | 63 / 21 / 1.5 |
| regime_method | rule_based | rule_based |

Universe resolved to **42 instruments** (24 equity, 11 crypto, 7 forex): MATIC/USD has no cached 1d data and dropped out via the standard MIN_BARS skip. No universe reduction was needed for compute.

## Verdicts

| Book | DSR | PBO | CPCV med OOS | frac +ve | Verdict |
|---|---|---|---|---|---|
| C — multi-asset trend 126 | **0.995 ✓** | **0.8115 ✗** | +0.047 ✓ | 87% of 15 ✓ | **REJECT** |
| D — multi-asset trend 252 | **0.996 ✓** | **0.8115 ✗** | +0.052 ✓ | **100%** of 15 ✓ | **REJECT** |

**Both books fail the gate on PBO alone** — a verdict of a completely different kind from the FX-only rejection (which failed all three gates with 0% positive paths). Here the DSR — deflated by the honest 108-trial count — clears 0.95 decisively, and the out-of-sample distribution is uniformly positive (Book D: 15/15 CPCV paths > 0; Book C: 13/15). What fails is the *selection* step: across the 16-split × 4000-combo PBO, the in-sample-better book (D, ann. Sharpe 0.795 vs C's 0.770) does not stay better out-of-sample — 0.81 is well worse than the 0.5 coin-flip threshold. With exactly 2 pre-registered configs that are near-identical (126 vs 252 lookback, same everything), PBO is coarse by construction — but the pre-registered rule is all three gates, and it is applied as written.

CPCV paths (per-period Sharpe):
- C: `[0.063, 0.076, 0.065, 0.047, 0.072, 0.033, 0.035, −0.006, 0.039, 0.081, 0.038, 0.062, 0.012, 0.052, −0.004]`
- D: `[0.060, 0.075, 0.072, 0.054, 0.071, 0.048, 0.028, 0.018, 0.034, 0.052, 0.040, 0.087, 0.042, 0.076, 0.045]`

## Full-window run (iteration window, caps binding)

| Metric | Book C (126) | Book D (252) |
|---|---|---|
| Trades | 2337 | 2355 |
| Total return (~9.0y; CAGR ≈) | +252.1% (~15.0%/yr) | +259.4% (~15.3%/yr) |
| Sharpe (ann., 252) | 0.77 | 0.80 |
| Max drawdown | 18.7% | 19.3% |
| Win rate | 51.7% | 50.7% |
| Profit factor | 1.25 | 1.23 |
| Expectancy (`expectancy_pnl`, engine metric) | +96.92 pnl/trade (+0.551%/trade) | +94.25 pnl/trade (+0.500%/trade) |
| Net per trade (net_pnl / n_trades) | +110.43 | +112.37 |
| Max gross leverage (approx, from trade list) | ~3.44× | ~3.47× |
| Instruments net positive | 30/42 | 29/42 |
| Caps bound (top families) | timeframe_bucket_full ×17102, max_risk_per_trade ×9232, regime_scale ×9232, max_portfolio_risk_exceeded ×6887, drawdown_reducing_scale ×1189, portfolio_risk_cap ×1138, vol_target ×47, max_correlated_exposure ×9 | timeframe_bucket_full ×17063, max_risk_per_trade ×7213, regime_scale ×7213, max_portfolio_risk_exceeded ×4835, drawdown_reducing_scale ×1344, portfolio_risk_cap ×968, vol_target ×54, max_correlated_exposure ×15, max_total_exposure ×11 |

### Per-asset-class P&L (is FX still the bleeder?)

| Class | Book C: trades / net / mean per trade / insts +ve | Book D: trades / net / mean per trade / insts +ve |
|---|---|---|
| Equity (24) | 1862 / **+215,600** / +115.79 / 18 of 24 | 1885 / **+239,023** / +126.80 / 19 of 24 |
| Crypto (11) | 299 / **+40,359** / +134.98 / 9 of 11 | 293 / **+27,658** / +94.40 / 6 of 11 |
| Forex (7) | 176 / +2,108 / +11.98 / 3 of 7 | 177 / **−2,050** / −11.58 / 4 of 7 |

FX is no longer the book's killer — it is simply dead weight (≈ flat in C, mildly negative in D, consistent with the FX-only finding that there is no FX trend edge at these costs). **Equities carry the book** (83–90% of net P&L), crypto contributes positively in both books. Top contributors C: NVDA +37.7k, MSFT +33.5k, GOOGL +29.4k, META +27.0k, TSM +20.7k. Top contributors D: MSFT +44.8k, META +34.4k, NFLX +31.9k, GOOGL +29.7k, BTC/USD +25.2k. Worst: XLE −8.6k, ARKK −6.7k (C); USD/JPY −11.8k, BNB/USD −7.7k (D). Note the shape of the edge: long-biased US mega-cap/semis trend + crypto beta over a historic 2016–2024 bull — the classic diversified-trend return stream, and exactly what the literature says the premium looks like.

## Wiring verification (done before the full run)

- **Instrument pass-through:** `TrendBook` passes `instrument=` to both `RegimeGatedMomentum` and `MultiTimeframeMomentum` for all 43 ids. Verified on a 3-instrument smoke (SPY + BTC/USD + USD/JPY): per-class regime eps = 1.5×/5.0×/1.0× base, crypto mean-reversion disabled, equity/forex enabled; Bollinger cache keys are `(instrument, timeframe, t)`; Book D's lookback=252 honored.
- **Cost math exercised per class:** `_fill` assertions — SPY buy 100.0 → 100.02 (2.0 bps), BTC/USD buy → 100.0125 (1.25 bps), USD/JPY 1d pair_tf override 0.6 pips RT → 150.0 → 150.003. **Real-trade check:** from an actual 4-instrument backtest, entry fills match bar-open × (1 ± side cost) exactly for SPY long, QQQ short, BTC/USD long and short. Crypto vol annualizes at 365, equity/forex at 252 (`mechanics_for`).
- **Smoke end-to-end:** 3-instrument `--no-ledger` run reproduced identical numbers across repeated executions (determinism; PBO seeded at `cfg.seed`=42).

## Engine bug fixed during this run (minimal, behavior-preserving)

The first full-gate attempt completed both full-window runs + PBO, then crashed inside CPCV: `PortfolioBacktester.run` sliced each instrument to the CPCV test window and late-listing instruments (PLTR, SOL, ARB, SUI, …) have **zero bars** in early windows → `atr_series` IndexError. The FX-only gate never hit this (all 22 pairs trade back to 2016). Fix in `engine/apex_quant/backtest/portfolio.py`: skip instruments whose `[start, end]` slice is empty and exclude them from that run's instrument list — semantically correct (an instrument doesn't exist before it lists) and behavior-preserving everywhere a window is non-empty (the previously crashing case is the only changed path). Verified: the post-fix 3-instrument smoke reproduces the pre-fix smoke byte-for-byte, and the two full-window 42-instrument runs are identical across the crashed and completed executions (252.1%/259.4%, same trade counts, same per-class P&L). **Side finding:** `tests/test_portfolio.py::test_single_instrument_parity` fails (20 vs 18 trades) both with and without this fix — it pre-dates it and tracks the earlier uncommitted v5 per-pair cost wiring in `PortfolioBacktester._fill`; not addressed here.

## Honest commentary

- **The verdict is REJECT, and it is not close in the way that matters for the rule:** the pre-registered gate demands all three of DSR/PBO/CPCV, and PBO fails at 0.81 for both books. By the discipline this project runs on, neither book is validated for deployment, and per the standing rule a `--final` holdout look is only warranted for a config that PASSES the iteration gate — running one on a rejected book burns holdout blindness for nothing. That rule stands unless the user explicitly overrides it.
- **But this is a categorically different reject from every reject so far.** The FX-only book lost money on 15/15 OOS paths; this book makes money on 13/15 and 15/15, with deflated DSR ≈ 0.995 at the honest 108-trial count, ~0.8 ann. Sharpe net of per-class costs, and caps binding throughout (portfolio-risk cap ×1138/×968, gross leverage pinned at ~3.4×). Moving from 22 correlated FX crosses to a genuinely multi-asset book is precisely the change the literature prescribed, and the result moved from "uniform bleed" to "uniform gain, selection unresolved". The remaining failure is about **choosing between C and D**, not about whether the book is positive OOS.
- **What PBO = 0.81 does and doesn't say:** with n_configs=2 it only asks "does the IS-better book stay better OOS?" — and for two books whose returns are near-identical (126 vs 252 lookback), the answer is noisy by construction. It does **not** say the OOS gains are luck; CPCV and the deflated DSR address that directly and pass. It says this 2-config selection procedure cannot certify a winner. Treating that as "no edge" would be as wrong as treating it as "validated edge".
- **Concentration caveat:** ~85% of net P&L is US mega-cap equity trend in a historic bull, plus crypto beta. The 2016–2024 window flatters long-risk premia; CPCV mitigates (each path is a distinct OOS block, all positive for D) but the window is what it is. MaxDD ~19% sits exactly at the drawdown-breaker neighborhood — the risk system, not the signal, defines the left tail here.
- **Annualization caveat:** the mixed book's equity curve is marked on the union calendar (crypto weekends included) while metrics annualize at 252 — same convention as the FX-only gate, slightly flattering Sharpe vs a pure-252 book.
- **Options from here (in order of honesty):** (1) accept the reject and stay on demo — the rule was pre-registered precisely to be binding on days like today; (2) if the user judges the 2-config PBO to be the wrong instrument for a C-vs-D choice this correlated, the defensible move is **not** a quiet `--final` peek — it is a new pre-registration (new ledger trials) that either tests one book only, or widens the selection set so PBO has something to discriminate among; (3) cost reduction remains the one clearly-tradable lever, as in the FX case; (4) FX adds nothing to this book — any future variant could drop the 7 majors at zero cost to expectancy, but that variant would itself be a new pre-registered trial.
- **No `--final` was run.** The 2025+ holdout remains untouched.

## Ledger

- **n_trials before: 106**
- **n_trials after: 108** (+2: `book_c_multiasset_126`, `book_d_multiasset_252` — recorded before the first run; the post-fix re-run deduped against the same canonical keys, so the count stayed 108; DSR deflated by 108 for both books)

## Compute notes

- Full 42-instrument universe; **no crypto cut was needed** (the documented fallback was not triggered): 2 full-window runs (67s + 16s) + PBO + 2×15 CPCV paths (78s + 65s) ≈ 4 min on .venv-mac.
- Determinism: full-window runs byte-identical across repeated executions; PBO uses `cfg.seed` (42); no RNG elsewhere. Strategy/feature caches are keyed per point-in-time object (+ instrument/eps); all reads are `PointInTimeAccessor` windows (≤ t only).
- Results JSON: `engine/data_store/validation/portfolio_gate_multiasset_2026-07-17.json`.

---

# Follow-up (same day, second pre-registration): the 12-config selection grid

**Context:** the 2-config run above REJECTED both books on PBO alone (0.8115) while DSR (0.995/0.996 at n=108) and CPCV (13/15, 15/15 positive paths) passed. With 2 near-identical configs PBO is coarse by construction, so the pre-committed honest next step was to re-run the SAME gate (same universe, same thresholds, same machinery) over a PBO-meaningful selection set. This section is that run; the 2-config record above is untouched (it is the pre-registration trail).
**Script:** `engine/scripts/run_portfolio_gate_multiasset_grid.py`. Machine-readable output: `engine/data_store/validation/portfolio_gate_multiasset_grid_2026-07-17.json`.
**Ledger:** **n_trials 108 → 120** — all 12 configs recorded BEFORE the runs; every DSR below is deflated by **120**.

## Pre-registered selection set (exactly 12 trials)

`momentum_lookback ∈ {63, 126, 189, 252} × holding_horizon ∈ {10, 15, 21}`; reward_risk 1.5, vol_window 63, rule_based regime, HTF 1w×50 gate, managed exits, vol-scaled sizing, config caps binding, CPCV purge = each config's own holding horizon. Universe unchanged: 24 equities + 12 crypto + 7 FX majors (MATIC/USD skipped → 42 instruments). Ordered lookback-major, horizon inner; the headline (126, 21) — Book C's parameters — is grid #6 (1-based).

## Headline finding: the horizon axis is DEGENERATE under managed exits

Within each lookback, h=10/15/21 produce **byte-identical results** — same trades, same P&L, same equity curve, same CPCV paths (verified: exactly 1 distinct signature per lookback across the three horizons). Under `exit_mode="managed"`, `holding_horizon` is never consulted: exits are decided by the `TradeManager` (chandelier trail / squeeze / scale-outs), and the horizon only feeds `max_hold` in `exit_mode="barrier"`. **The 12-config grid is effectively 4 distinct books × 3 exact duplicates.** The ledger honestly keeps all 12 (all 12 parameter sets were evaluated; the count is the conservative denominator), and the grid columns fed to PBO include the duplicates — both facts make the gates *harder*, not easier, and are reported as-is.

Consequence for comparability: `ma_grid_l126_h21` reproduced Book C **exactly** (ret 252.1%, 2337 trades, same Sharpe) and `ma_grid_l252_h21` reproduced Book D exactly (ret 259.4%, 2355 trades) — the determinism check.

## Verdicts (DSR deflated by 120; PBO computed across the 12-config set)

**PBO across 12 configs = 0.649 ≥ 0.5 → FAIL** (16 splits, 4000 combos, seed 42).

| Config | Total ret | Sharpe (ann.) | maxDD | Trades | Win | PF | Expectancy | Lev~ | DSR (n=120) | CPCV med | frac +ve | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| l63_h10/15/21 | +281.0% | 0.82 | 18.8% | 2366 | 50.4% | 1.25 | +102.52 (0.53%) | 3.41 | **0.815 ✗** | +0.053 ✓ | 100% ✓ | **REJECT** |
| l126_h10/15/21 | +252.1% | 0.77 | 18.7% | 2337 | 51.7% | 1.25 | +96.92 (0.55%) | 3.44 | **0.761 ✗** | +0.047 ✓ | 87% ✓ | **REJECT** |
| l189_h10/15/21 | +30.8% | 0.31 | 20.0% | 1818 | 50.8% | 1.13 | +15.46 (0.37%) | 3.47 | **0.183 ✗** | +0.049 ✓ | 87% ✓ | **REJECT** |
| l252_h10/15/21 | +259.4% | 0.80 | 19.3% | 2355 | 50.7% | 1.23 | +94.25 (0.50%) | 3.47 | **0.787 ✗** | +0.052 ✓ | 100% ✓ | **REJECT** |

(Each row is three grid cells; they are identical, per the degeneracy finding.)

**Does the headline (126, 21) pass all three gates over the real selection set? NO.** It passes CPCV only. DSR fails at **0.761 ≤ 0.95** and PBO fails at **0.649 ≥ 0.5**. The 2-config run's DSR pass (0.995) was an artifact of the small denominator: at the honest 120-trial count the benchmark maximum-Sharpe hurdle (sr0) rises from 0.003 to 0.036 per-period and every config's DSR falls below 0.95 — including the grid-best (l63, 0.815). **No config passes; the grid-best in-sample (l63, ann. Sharpe 0.823) is not the headline (ranks 9/12 IS), and it fails the same two gates.**

## Why PBO still fails (and what the path-level detail shows)

- **The selection set is effectively 4 columns, not 12** — the horizon axis contributes zero variance, and the 4 distinct books are highly correlated (same universe, same signal family, lookbacks 63→252). PBO asks one question: does the in-sample winner (l63) stay ahead out-of-sample across the 16 splits? 0.649 says it does so materially less than half the time. Even the coarse 2-config version of this test failed (0.8115); widening the set moved the number but not the verdict.
- **The OOS weakness is not one bad block hitting all configs.** Negative CPCV paths are lookback-specific: l126 → paths 8 & 15; l189 → paths 6 & 15 (path 15 shared); l63 and l252 → none. So the book is broadly positive OOS everywhere (all 12 CPCV gates pass: medians 0.047–0.053, 87–100% positive), but no lookback is *reliably best* — the IS ranking (63 > 252 > 126 ≫ 189) does not survive splitting.
- **The l189 notch is the loudest warning.** IS performance is non-monotonic in lookback: 0.82 (63) → 0.77 (126) → **0.31** (189) → 0.80 (252). The l189 run was strangled by the drawdown breaker (×11418 halts; maxDD pinned at exactly 20.0%). A smooth, real premium does not vanish at one interior lookback and return at the next; this sensitivity is exactly what PBO and DSR exist to punish, and they did.
- **DSR vs CPCV tension, stated plainly:** every config is positive on ≥87% of purged OOS paths (CPCV pass), yet none clears the multiple-testing-deflated significance bar at n=120. Both are true at once: the multi-asset trend book is real and positive in-window, but ~0.8 ann. Sharpe net of retail costs over 9 years is not large enough to survive an honest 120-trial deflation at the 0.95 confidence level.

## Honest commentary

- **The pre-registered discipline has now spoken twice, and the answer is consistent: REJECT.** The 2-config run failed selection (PBO); the 12-config run fails selection (PBO 0.649) *and* significance (DSR ≤ 0.815 everywhere at n=120). The earlier "DSR passes decisively" did not survive the honest denominator — this is precisely the mechanism the TrialLedger exists for, and it worked as designed.
- **What is true:** the diversified multi-asset trend book is positive OOS across every tested configuration (all 12 CPCV gates pass; contrast the FX-only book's 0/15). What is **not** established: that any configuration clears the validation bar for deployment after honest multiple-testing correction, or that a chosen lookback is robust (the l189 notch). By the standing rule, **no `--final` holdout look is warranted** — it is reserved for configs that PASS the iteration gate, and running one here would burn holdout blindness on a rejected family. The 2025+ holdout remains untouched.
- **The horizon grid dimension was spent on a no-op** (managed exits ignore it) — noted for future pre-registrations: vary something the exit engine actually reads (atr_stop_mult, reward_risk, exit_mode) or accept that `holding_horizon` only matters under `exit_mode="barrier"`. The 12 ledger charges stand regardless; that is the cost of finding out.
- **Options from here, unchanged in kind but sharper in focus:** (1) accept the reject and stay on demo — two pre-registered attempts at this family have failed the gate; (2) if trend stays on the roadmap, the strongest remaining facts are the uniform CPCV positivity and the cost sensitivity — a lower-cost execution assumption or a lower-turnover variant (fewer, slower trades) would be *new* pre-registrations charged to the ledger, not tweaks of this one; (3) dropping the FX sleeve costs the book nothing (≈ flat in every config) but is likewise a new trial.

## Ledger (cumulative)

- **n_trials before this follow-up: 108**
- **n_trials after: 120** (+12: `ma_grid_l{63,126,189,252}_h{10,15,21}` — recorded before the runs; DSR deflated by 120 for all configs)

## Compute notes (follow-up)

- Full 42-instrument universe; no reductions. 12 full-window runs + 12×15 CPCV paths + 12-config PBO ≈ 17 min on .venv-mac (full runs 7–67s each; CPCV 63–79s per config).
- Determinism: `ma_grid_l126_h21`/`ma_grid_l252_h21` reproduce the 2-config Books C/D byte-for-byte (metrics, trade counts, per-class P&L); within-lookback horizons are exactly degenerate (1 distinct signature per lookback); PBO seeded at `cfg.seed` (42).
- Instrument pass-through and per-class cost/annualization paths unchanged and verified in the 2-config run (same `TrendBook` construction); equity 2.0 bps/side, crypto 1.25 bps/side, forex per-pair v5 pips; crypto vol annualized 365.
- Results JSON: `engine/data_store/validation/portfolio_gate_multiasset_grid_2026-07-17.json`.

---

# Clean-data re-run (2026-07-17, later same day): Books C & D on the rebuilt data layer + fixed engine

**Context:** after the two runs above, the data layer was rebuilt — **11,957 Sunday + 880 Saturday phantom bars removed** from all daily caches, session convention fixed, 1h backfilled — and the engine received correctness fixes (regime eps aligned with live, per-TF annualization, BB cache fix, time-stop parity, BE buffer 3 pips, book-risk-after-partials). This section re-runs the SAME two pre-registered books (Book C `book_c_multiasset_126`, Book D `book_d_multiasset_252`) through the SAME gate (`run_portfolio_gate_multiasset.py`, unchanged), same seed (42), iteration window strictly < 2025-01-01, on the trustworthy data. The earlier sections are the contaminated-data record and are left untouched.
**Ledger:** **no new trials** — identical canonical configs deduped against the existing entries (150 → 150). The ledger grew 120 → 150 through the engine-fix campaign's own validation work; **DSR below is deflated by the current honest count, n = 150**. The contaminated-run JSONs are preserved as `..._contaminated-data.json` alongside the new `portfolio_gate_multiasset_2026-07-17.json`.
**Data sanity (verified before the run):** EUR/USD 2599 → 2333 bars, USD/JPY 2629 → 2334 (phantoms gone; zero weekend bars in FX/equities); equities unchanged (AAPL 2264); crypto correctly keeps weekends (BTC/USD 3141).

## Gate results, side-by-side (contaminated → clean)

| Gate | Book C (126) contaminated | Book C clean | Book D (252) contaminated | Book D clean |
|---|---|---|---|---|
| DSR (> 0.95) | 0.995 ✓ (n=108) | **0.682 ✗ (n=150)** | 0.996 ✓ (n=108) | **0.934 ✗ (n=150)** |
| PBO (< 0.5) | 0.8115 ✗ | **0.056 ✓** | 0.8115 ✗ | **0.056 ✓** |
| CPCV median OOS | +0.047 ✓ | **+0.069 ✓** | +0.052 ✓ | **+0.050 ✓** |
| CPCV frac positive | 87% (13/15) ✓ | **100% (15/15) ✓** | 100% (15/15) ✓ | **93% (14/15) ✓** |
| **Verdict** | REJECT | **REJECT** | REJECT | **REJECT** |

## Full-window metrics, side-by-side (contaminated → clean)

| Metric | Book C: contaminated → clean | Book D: contaminated → clean |
|---|---|---|
| Total return (~9y) | +252.1% → **+181.8%** | +259.4% → **+438.5%** |
| Sharpe (ann.) | 0.77 → **0.68** | 0.80 → **0.97** |
| Max drawdown | 18.7% → 19.8% | 19.3% → 19.1% |
| Trades | 2337 → **1590** (−32%) | 2355 → **1516** (−36%) |
| Win rate | 51.7% → 54.7% | 50.7% → 55.9% |
| Profit factor | 1.25 → 1.26 | 1.23 → **1.41** |
| Expectancy / trade | +96.92 (+0.551%) → **+108.91 (+0.957%)** | +94.25 (+0.500%) → **+273.13 (+1.109%)** |
| Net per trade | +110.43 → +116.46 | +112.37 → +299.13 |
| Max gross leverage | ~3.44× → ~3.02× | ~3.47× → ~2.84× |
| Instruments net positive | 30/42 → 27/42 | 29/42 → 31/42 |

## Per-asset-class P&L (contaminated → clean)

| Class | Book C | Book D |
|---|---|---|
| Equity (24) | 1862 tr +215,600 → **1345 tr +153,944** | 1885 tr +239,023 → **1298 tr +395,543** |
| Crypto (11) | 299 tr +40,359 → **206 tr +38,637** | 293 tr +27,658 → **190 tr +54,339** |
| Forex (7) | 176 tr +2,108 → **39 tr −7,416** | 177 tr −2,050 → **28 tr +3,598** |

The single largest mechanical change: **FX trade count collapsed ~80%** (176→39, 177→28). The phantom Sunday/Saturday bars were generating phantom FX signals on stale weekend prints; with sessions fixed, the FX sleeve barely trades. Equities still carry both books; crypto is additive in both.

CPCV paths (clean): C `[0.071, 0.087, 0.083, 0.067, 0.088, 0.021, 0.049, 0.011, 0.043, 0.078, 0.058, 0.089, 0.055, 0.075, 0.069]` — all positive. D `[0.055, 0.083, 0.070, 0.051, 0.088, 0.062, 0.049, 0.012, 0.028, 0.050, 0.008, 0.025, 0.035, 0.052, −0.022]` — 14/15.

## Plain answers

- **Does the OOS character survive trustworthy data? YES.** 29 of 30 CPCV paths positive across the two books (C: 15/15, D: 14/15), medians +0.069/+0.050 — the positive out-of-sample character of the diversified multi-asset trend book was **not** a phantom-bar artifact. If anything it firmed up (Book C went 13/15 → 15/15).
- **Does any verdict change? NO at the top level — both books remain REJECT — but the failure mode changed completely.** On contaminated data the books failed PBO (selection) and, in the 12-config follow-up, DSR at n=120. On clean data **PBO flips to a decisive PASS (0.056)**: the IS-better book (D) now stays better out-of-sample across splits. What remains failing is only **DSR at the current ledger count of 150**: C 0.682, **D 0.934 — 0.016 below the 0.95 bar**. CPCV passes for both.

## Honest commentary

- **The clean data + fixed engine made the book *better*, not worse** — fewer, better trades (expectancy ~1.7–2.9× higher, win rate +3–5 pts, PF up, leverage down), and Book D's full-window Sharpe rose 0.80 → 0.97 (+438.5% over ~9y). Part of the contaminated-run PBO failure was evidently data noise scrambling the C-vs-D ordering; with trustworthy sessions the ordering is stable (PBO 0.056).
- **The DSR comparison across runs is not apples-to-apples, and that must be stated plainly:** the contaminated run's 0.995/0.996 was deflated by 108, this run's 0.682/0.934 by 150. Both the numerator (Sharpe, engine behavior) and the denominator (ledger growth) moved. The honest current statement is: **at n=150, Book D falls just short of the 0.95 bar and Book C is well short.** No denominator-shopping: the ledger count is what it is, and re-running identical configs does not change it (dedup, not addition).
- **Trade-mechanics note:** the engine fixes (time-stop parity among them) changed exit behavior — trade counts fell ~32–36% while per-trade expectancy roughly doubled, so the earlier grid finding that `holding_horizon` is inert under managed exits **may no longer hold** on this engine build; that degeneracy result was engine-version-specific and should not be cited against the fixed engine without a re-test.
- **Standing rule, unchanged:** both books REJECT, so no `--final` holdout look is warranted. Book D is now one DSR notch (0.016) from passing all three gates on trustworthy data — that is a fact, not a pass. If the user wants to know whether D clears the bar, the honest routes are (a) leave it — REJECT stands, or (b) a **new pre-registration** (charged to the ledger) on the fixed engine — not a reinterpretation of this run.
- **Determinism on the fixed engine: verified** — Book D full-window run repeated twice, byte-identical (ret 438.4575%, 1516 trades, Sharpe 0.97356, final equity 538,457.47), matching the gate output. PBO seeded at `cfg.seed` (42); no RNG elsewhere.

## Ledger (cumulative)

- **n_trials before this re-run: 150** (120 after the grid + 30 from the engine-fix campaign's own trials)
- **n_trials after: 150** (+0 — identical configs deduped; DSR deflated by 150)

## Compute notes (clean-data re-run)

- Full 42-instrument universe (MATIC/USD still absent from cache); 2 full-window runs (61s + 15s) + PBO + 2×15 CPCV paths (73s + 65s) ≈ 4 min on .venv-mac.
- Results JSON: `engine/data_store/validation/portfolio_gate_multiasset_2026-07-17.json` (contaminated-run JSON preserved as `portfolio_gate_multiasset_2026-07-17_contaminated-data.json`; grid-era JSON likewise preserved).
