# Pre-registration — US large-cap short-term reversal sleeve (long-only, halal-screened)

**Date:** 2026-07-19 · **Status:** registered BEFORE any gate run · **Runner:** `engine/scripts/run_st_reversal_gate.py`
**Evidence base:** research-audit Task B rank #1 (brief of 2026-07-19): Nagel (2012, *RFS*, "Evaporating Liquidity"); de Groot, Huij & Zhou (2012, *JBF*, "Another look at trading costs and short-term reversal profits"); Lehmann (1990); Jegadeesh (1990) · **Strategy:** `engine/apex_quant/strategies/st_reversal.py`

---

## Hypothesis

**Weekly-rebalanced cross-sectional short-term reversal over screened US large
caps — buy the worst 5-day losers, hold one week, long-only — earns a real net
edge whose returns are crisis-alpha liquidity-provision returns, negatively
correlated with the trend book.** Nagel (2012) shows short-term reversal profits
ARE liquidity-provision returns: they spike when aggregate volatility is high
and liquidity demanders pay most for immediacy — i.e. exactly when the trend
book suffers. de Groot, Huij & Zhou (2012) show the raw weekly reversal is
cost-fragile, but a cost-aware construction (fewer, more liquid names; trade
only on statistically significant moves) keeps **30–50 bps/week net** on the
large-liquid US universe. Honest long-only retail expectation: **net Sharpe
0.3–0.5, crisis-concentrated, expected ρ ≈ −0.1 to −0.3 vs trend** — the
diversification is the claim, not the standalone Sharpe. This gate tests whether
that documented edge survives this engine's costs, risk caps and validation
gauntlet, and whether the negative-ρ texture actually shows up.

## Data (verified before registration)

- Universe: **32 screened liquid US large caps** — the 12 already in the store
  (`AAPL, MSFT, NVDA, META, AMZN, GOOGL, TSLA, AMD, PLTR, TSM, NFLX, UBER`) plus
  20 fetched via the normal `ParquetStore.get_or_fetch` Yahoo path on 2026-07-19
  (`XOM, JNJ, WMT, PG, KO, V, MA, HD, BA, CAT, INTC, CSCO, ORCL, CRM, ADBE,
  PFE, ABBV, NKE, MCD, COST`). Plus `SPY`, loaded **only** as the vol-state
  instrument, never traded by the sleeve.
- **Halal screen (registered):** no banks, insurers or diversified financials
  (JPM & co. excluded); no alcohol/tobacco/gambling names. `V`/`MA` are payment
  networks whose revenue is transaction fees, not interest-based lending
  (commonly pass AAOIFI-style debt-ratio screens) — included per the task
  brief; a stricter screen drops them with a one-line universe change. `BA` has
  a defense segment — included per the brief's explicit list. `SPY` holds
  financials at index weight and is therefore used **only** as the vol-state
  reference, never traded.
- Iteration window **strictly < 2025-01-01** (the 2025+ holdout is never loaded).
  Pre-2025 bar counts (verified 2026-07-19): 2,264 for all 30 long-listed names
  (from 2016-01-04); `UBER` 1,421 (2019-05), `PLTR` 1,070 (2020-09) — all ≥ the
  300-bar `MIN_BARS` floor; `min_history=300` keeps late listings out of the
  cross-section until they have a real history.
- Costs: config v5 equity mechanics — `cost_model: bps`, 2.0 bps spread +
  1.0 bps slippage per side ≈ **4 bps round-trip**, zero commission, applied by
  the standard `PortfolioBacktester._fill` path. Annualization **252** (cash
  equities; `asset_classes.equity.annualization`) for Sharpe/DSR/CPCV alike.
- Yahoo daily closes are split-adjusted but not dividend-adjusted: a long-only
  book's returns are modestly UNDERSTATED (this universe yields ~1.5–2%/yr) —
  the bias runs against the sleeve, i.e. conservative. Survivorship: the
  universe is today's large caps; delisted names are absent — registered, not
  discovered.

## Strategy & grid (6 configs, fixed in advance)

`ShortTermReversal` (new, style-consistent with `strategies/cross_sectional.py`
and `crypto_xs_momentum.py`): score = raw trailing `formation`-bar return,
backward-only; **weekly rebalance** — signals only on the last bar of each ISO
week on the union index (index-detected, gap-safe), filled next bar open.
**Long-only bottom bucket** (biggest losers). `min_universe=10`,
`min_history=300`, `reward_risk=1.5`, `holding_horizon=5` (the weekly
time-stop), managed exits (TradeManager trail/BE/partials/time-stop — the
deployable sleeve, not the academic Monday-to-Monday bet), standard RiskManager
vol-scaled sizing with config risk caps binding.

Filter modes: `plain` = bottom-3, no gates. `cost` (de Groot et al.) = bottom-2,
and a name is eligible only if |formation return| > **1.5 × its 20d realised
daily vol scaled to the formation horizon** (√formation; a 1.5σ move filter)
AND it sits in the liquid half of the universe (20d median dollar volume ≥
cross-sectional median at t). `vol_state` (Nagel) = bottom-3, but stand down
entirely when **SPY 21d realised vol < its 126d rolling median** (the edge is
vol-state-conditional; fails closed to flat when SPY has no valid reading).

| # | config | formation | filter_mode | bottom_n |
|---|---|---|---|---|
| 1 (headline) | `rev_f5_plain` | 5 | plain | 3 |
| 2 | `rev_f5_cost` | 5 | cost | 2 |
| 3 | `rev_f5_volstate` | 5 | vol_state | 3 |
| 4 | `rev_f10_plain` | 10 | plain | 3 |
| 5 | `rev_f10_cost` | 10 | cost | 2 |
| 6 | `rev_f10_volstate` | 10 | vol_state | 3 |

Formation {5, 10} × filter {plain, cost, vol_state} is the only swept axis;
DSR/PBO treat the 6-config grid as the whole selection set, deflated by the
FULL ledger count. **The headline (config #1) is the gated config; grid mates
feed PBO/DSR and the cost-/vol-state ablation reads.**

## Trial accounting (honesty rules)

- **6 trials**, recorded in the shared TrialLedger
  (`data_store/validation/trial_ledger.json`) **BEFORE any validation runs** —
  the script records, saves, then runs; this run's own trials count toward the
  deflation denominator (canonical-JSON dedup inside TrialLedger).
- Ledger state at registration: **n = 190** (verified 2026-07-19). The ledger is
  shared and live — concurrent gate runs from other research threads may raise
  it before this run executes; the script records this run's 6 trials BEFORE
  running and deflates by the **final** ledger n at run time, whatever it is
  (actuals in the run log and results JSON).
- Every DSR is deflated by the **final** ledger n, not this run's grid.
- Determinism: `seed: 42` (config.yaml) drives PBO's combinatorial splits; the
  strategy and backtester are single-threaded deterministic pandas/numpy; no
  network. The headline full-window run is executed twice and the equity curves
  must be identical. Full results JSON lands in
  `data_store/validation/st_reversal_gate_2026-07-19.json`.
- Supabase posting skipped (research sweep).

## Gate criteria (unchanged system rules)

PASS requires ALL of: **DSR > 0.95** (deflated by the final ledger n — 190 at
registration plus this run's 6 and any concurrent trials), **PBO < 0.5**
across the 6-config selection set, **CPCV median OOS Sharpe > 0 with > 50% of
15 paths positive** (purge = holding_horizon = 5 bars). Anything else is
REJECT. Per-config verdicts are computed for all six; the sleeve verdict is the
headline's.

## Reporting commitments (whatever the outcome)

Per config: full-window Sharpe, profit factor, max drawdown, expectancy,
trades-per-week, **annualized turnover and the realized weekly cost estimate**
(one-way entry notional / mean equity / yr, and the ≈4 bps round-trip drag),
per-instrument net-P&L contribution, DSR / PBO / CPCV median & frac-positive /
PASS-REJECT. Plus the two texture checks the mechanism requires:

- **Vol-regime breakdown:** the sleeve's daily returns split by the SPY-21d-vol
  state (above/below its 126d median) — per-regime Sharpe, mean daily return,
  and share of total P&L. If the Nagel mechanism is real, the high-vol half
  must carry the P&L. Crisis episodes inside the iteration window (Feb-2018
  Volmageddon, Q4-2018, COVID Feb–Apr 2020, the 2022 bear) are inspected
  individually.
- **Correlation vs trend:** ρ of the sleeve's daily returns against the
  **book_d multi-asset trend book** (lookback 252). The 2026-07-17 gate JSON
  (`portfolio_gate_multiasset_2026-07-17.json`) stores metrics only — no equity
  series — so book_d's curve is **reconstructed by re-running its exact
  pre-registered config on the iteration window** (already in the ledger; no
  new trial). ρ vs SPY reported alongside as reference. Expected sign: negative.

Deliverable: `engine/data_store/st_reversal_gate.md` with the honest read on
whether the crisis-alpha texture is real enough to earn a sleeve slot (even at
0.3–0.5 net), and whether ρ actually comes out negative vs trend.

## Decision rules (pre-committed)

- **Headline PASS** ⇒ the sleeve earns a forward-paper discussion (sizing as a
  trend-book diversifier, execution, and the kill criterion below).
- **Headline REJECT but a grid mate passes** ⇒ report it plainly as a
  selection-effect candidate (PBO exists to discount exactly this); no
  deployment claim; at most a re-registration with the survivor as headline.
- **No PASS anywhere** ⇒ close the sleeve at retail costs: the documented gross
  edge does not survive this engine's net gauntlet in the iteration window. No
  further reversal grids without genuinely new data or a new hypothesis class.

**Forward kill criterion (pre-registered, applies if a PASS ever reaches paper
trading):** kill the sleeve on net expectancy ≤ 0 over the trailing 126 trading
days, OR if crisis alpha fails to materialise in the first VIX > 30 episode.

## Known caveats (registered, not discovered)

- Managed exits mean the TradeManager shapes trades (winners can run past the
  5-bar time-stop; losers can be cut before it) — this tests the deployable
  sleeve, not the academic fixed-horizon bet.
- The vol-state variant is the mechanism test; if `plain` makes its money in
  LOW-vol states, the Nagel story is wrong for this construction and the ρ
  claim dies with it — the regime breakdown is reported either way.
- 2016–2024 contains one liquidity crisis (COVID), one vol shock (Feb 2018),
  one hiking-cycle bear (2022) and long low-vol grinds — a fair but short
  sample for an episodic edge; the crisis episodes are few.
- Survivorship and dividend notes above; both registered before running.
