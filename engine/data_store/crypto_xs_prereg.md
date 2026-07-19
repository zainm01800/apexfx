# Pre-registration — crypto cross-sectional momentum sleeve (Sleeve E)

**Date:** 2026-07-19 · **Status:** registered BEFORE any gate run · **Runner:** `engine/scripts/run_crypto_xs_gate.py`
**Evidence base:** `docs/research/2026-07-18_beating_sharpe_1_2.md` (§1 cross-sectional crypto momentum; §7/§8 sleeve E) · **Strategy:** `engine/apex_quant/strategies/crypto_xs_momentum.py`

---

## Hypothesis

**Weekly-rebalanced cross-sectional momentum over the top liquid coins, long-only top
bucket vs cash, earns a real net edge at retail costs.** The research note documents:
short-horizon (1–4 week) cross-sectional momentum is statistically significant on
large/liquid coins (Liu et al. 2022 *J. Finance*; Jia et al. 2022; Dobrynskaya 2023);
the conflicting "weekly reversal" findings come from many-small-coin universes
(bid-ask-bounce/illiquidity artefacts — Zaremba et al. 2021 review) and do not apply
to a top-11 liquid universe; long-horizon (6–12m) momentum is not significant
(Grobys & Sapkota 2019). Post-2021 honesty (Springer, RQFA 2025): crypto momentum is
**episodic** — performance comes in moments and needs vol management to be viable at
all. Honest expected range: **0.4–0.8 net, regime-dependent**. This gate tests whether
that documented edge survives this engine's costs, risk caps and validation gauntlet.

## Data (verified before registration)

- Universe: the config crypto list (`BTC/USD, ETH/USD, SOL/USD, BNB/USD, XRP/USD,
  ADA/USD, AVAX/USD, DOGE/USD, MATIC/USD, LINK/USD, ARB/USD, SUI/USD`) — Yahoo daily
  parquets via the normal `ParquetStore` path. **MATIC/USD has no cached 1d data and
  drops out via the standard skip → 11 instruments.**
- Iteration window **strictly < 2025-01-01** (the 2025+ holdout is never loaded).
  Pre-2025 bar counts (verified 2026-07-19): BTC 3141 (from 2016), ETH 1734,
  SOL 1219 (2021-08), BNB/XRP/ADA/LINK 1827 (2020-01), AVAX 1469 (2020-12),
  DOGE 1307 (2021-06), ARB 650 (2023-03), SUI 599 (2023-05) — all ≥ the 300-bar
  `MIN_BARS` floor.
- **Consequence of the 300-bar min-history rule (registered, not discovered):** a
  name becomes rankable only 300 bars after listing, and ≥4 eligible names are
  required for a cross-section, so the sleeve is necessarily flat before
  ~2020-11 (BTC-only era) and the effective sample is **~2020-11 → 2024-12**
  (~4.1 years, ~1,500 bars). ARB/SUI join the cross-section only in early 2024.
- Costs: config v5 crypto mechanics — 1.5bps spread + 0.5bps slippage per side
  (≈2.5bps round-trip), `cost_model: bps`, applied by the standard
  `PortfolioBacktester._fill` path. Annualization **365** (crypto trades every
  calendar day; `asset_classes.crypto.annualization`), used for Sharpe/DSR/CPCV
  alike — not the 252 mixed-book compromise of the multi-asset gate.

## Strategy & grid (6 configs, fixed in advance)

`CryptoXsMomentum` (new, style-consistent with `strategies/cross_sectional.py`):
vol-scaled momentum score (`lookback`-bar return / 63d realised vol, backward-only),
**weekly rebalance** — signals emitted only on the last bar of each ISO week on the
union index (detected from the index, gap-safe; crypto weeks are complete 7-day
spans), filled next bar open. **Top-3 long-only** (no short leg: spot-only retail
access; the documented edge is the top bucket vs stablecoin). `min_universe=4`,
`min_history=300`, `reward_risk=1.5`, `holding_horizon=7` (the weekly time-stop),
managed exits (TradeManager trail/BE/partials/time-stop — the deployable sleeve),
standard RiskManager vol-scaled sizing with config risk caps binding.

| # | lookback | regime_filter | rationale |
|---|---|---|---|
| 1 (headline) | **21** | **on** | ≈3 weeks — centre of the documented 1–4-week effect + BTC-63d crash filter |
| 2 | 14 | on | fast end of the documented effect |
| 3 | 42 | on | slow end |
| 4 | 21 | off | filter ablation — is the crash protection load-bearing? |
| 5 | 14 | off | fast, unfiltered |
| 6 | 42 | off | slow, unfiltered |

Regime filter: hold only while `BTC/USD` 63-bar return > 0 (the asset class's own
trend as common-factor proxy; fails closed to flat when BTC has no valid reading).
Lookback and the filter are the only swept parameters; DSR/PBO treat the 6-config
grid as the whole selection set, deflated by the FULL ledger count. **The headline
(config #1) is the gated config; grid mates feed PBO/DSR and the ablation read.**

## Trial accounting (honesty rules)

- **6 trials**, recorded in the shared TrialLedger
  (`data_store/validation/trial_ledger.json`) **BEFORE any validation runs** —
  the script records, saves, then runs; this run's own trials count toward the
  deflation denominator (canonical-JSON dedup inside TrialLedger).
- Ledger state at registration: **n = 182** → expected **n = 188** after recording
  (verified in the run log).
- Every DSR is deflated by the **final** ledger n, not this run's grid.
- Determinism: `seed: 42` (config.yaml) drives PBO's combinatorial splits; the
  strategy and backtester are single-threaded deterministic pandas/numpy; no
  network. Full results JSON lands in
  `data_store/validation/crypto_xs_gate_2026-07-19.json`.
- Supabase posting skipped (research sweep).

## Gate criteria (unchanged system rules)

PASS requires ALL of: **DSR > 0.95** (deflated by final n=188), **PBO < 0.5** across
the 6-config selection set, **CPCV median OOS Sharpe > 0 with > 50% of 15 paths
positive** (purge = holding_horizon = 7 bars). Anything else is REJECT. Per-config
verdicts are computed for all six; the sleeve verdict is the headline's.

## Reporting commitments (whatever the outcome)

Per config: full-window Sharpe, profit factor, max drawdown, expectancy,
trades-per-week (turnover), per-instrument net-P&L contribution, DSR / PBO / CPCV
median & frac-positive / PASS-REJECT. **Post-2021 sub-period (2021-01-01 →
2024-12-31, inside the iteration window) reported separately for every config** —
crypto momentum decayed post-2021; the note says when it works, not just whether.
Deliverable: `engine/data_store/crypto_xs_gate.md` with the honest read on whether
the documented 0.4–0.8 net is actually there, and when.

## Decision rules (pre-committed)

- **Headline PASS** ⇒ Sleeve E earns a forward-paper discussion (Coinbase/Kraken
  execution — IBKR Paxos is too narrow for the 11-name universe — sizing, and
  correlation vs the multi-asset trend book).
- **Headline REJECT but a grid mate passes** ⇒ report it plainly as a
  selection-effect candidate (PBO exists to discount exactly this); no deployment
  claim; at most a re-registration with the surviving config as the new headline.
- **No PASS anywhere** ⇒ close Sleeve E at retail costs: the documented gross edge
  does not survive this engine's net gauntlet in the iteration window. No further
  crypto cross-sectional grids without genuinely new data or a new hypothesis class.

## Known caveats (registered, not discovered)

- Yahoo daily crypto = 365 bars/yr with exchange/venue quirks (weekend bars, USDT≈USD
  unhedged); 2021–2024 contains two full cycles plus the 2022 crash — a fair but
  short sample for an episodic edge.
- Managed exits mean the TradeManager shapes trades (winners can run past the 7-bar
  time-stop; losers can be cut before it) — this tests the deployable sleeve, not the
  academic Monday-to-Monday fixed-horizon bet.
- Top-3-of-11 with min_universe=4 means early-sample books are concentrated
  (top-3 of 4–6 names in 2020–2021); per-instrument contributions will show it.
- Execution would be Coinbase/Kraken taker (Binance closed to new UK retail); the
  config's 1.5bps spread assumption is optimistic vs Coinbase Advanced taker fees —
  the gate tells us the edge size; fee-tier sensitivity is a post-gate question,
  flagged honestly in the deliverable.
