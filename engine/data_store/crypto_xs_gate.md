# Crypto cross-sectional momentum (Sleeve E) — gate results

**Date:** 2026-07-19 · **Runner:** `engine/scripts/run_crypto_xs_gate.py` · **Pre-registration:** `engine/data_store/crypto_xs_prereg.md` (written before any run) · **Evidence base:** `docs/research/2026-07-18_beating_sharpe_1_2.md` (§1, sleeve E)

**Bottom line: REJECTED — 0 PASS, 6 REJECT.** The documented 0.4–0.8 net edge *is visible in-sample* (full-window net Sharpe 0.61–0.84 at config costs; post-2021 sub-period 0.63–1.07), and CPCV passes everywhere (medians +0.025…+0.049 per-bar, 60–80% of 15 paths positive) — but **PBO 0.668 (≥ 0.5) fails every config**, and 5 of 6 configs also fail DSR after deflation by the full 190-trial ledger. The one DSR survivor (`xs_l021_reg_off`, 0.957) is exactly the kind of near-miss PBO exists to discount. The pre-registered conjunctive gate admits no exceptions. **Per the pre-registered decision rule (no PASS anywhere): Sleeve E is closed at retail costs — no deployment claim, no forward-paper sleeve.**

---

## Protocol (as pre-registered; enforced by the runner)

- Iteration window **strictly < 2025-01-01**; the 2025+ holdout never loaded.
- **6 trials recorded in the shared TrialLedger BEFORE any validation ran**: pre-run read-back showed the ledger had already moved **182 → 184** since registration (two trials appended by another process against the shared, live ledger — same caveat as the 4h campaign); this run recorded its 6 → **n = 190**, and every DSR below is deflated by the full **190**. The direction is conservative: a larger denominator only harshens DSR.
- Gates unchanged: **DSR > 0.95**, **PBO < 0.5** across the whole 6-config selection set, **CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive** (C(6,2)=15, purge = holding_horizon = 7 bars, 1% embargo; seed 42; PBO n_splits=16 → 4,000 combos).
- Strategy: `CryptoXsMomentum` (new, `strategies/crypto_xs_momentum.py`, pattern of `cross_sectional.py`): vol-scaled momentum score (lookback return / 63d vol), **weekly rebalance** (signals only on the last bar of each ISO week on the union index, filled next bar open), **top-3 long-only**, `min_universe=4`, `min_history=300`, `reward_risk=1.5`, `holding_horizon=7`, managed exits, standard RiskManager vol-scaled sizing, config risk caps binding. Grid: lookback {21 (headline), 14, 42} × BTC-63d regime filter {on, off}.
- Costs: config v5 crypto (1.5bps spread + 0.5bps slippage/side ≈ **2.5bps RT**). Annualization **365** everywhere (crypto trades every calendar day).
- Determinism: headline full-window run executed twice → **identical equity curve**. Full records: `engine/data_store/validation/crypto_xs_gate_2026-07-19.json`.

## Data and effective sample (registered consequence, confirmed)

11 instruments (MATIC/USD dropped by the standard no-data skip): BTC, ETH, SOL, BNB, XRP, ADA, AVAX, DOGE, LINK, ARB, SUI vs USD, Yahoo daily, 2016-01-01 → 2024-12-31. With `min_history=300` and `min_universe=4`, the sleeve is structurally flat before ~2020-11 (BTC-only era) — the **effective sample is ~2020-11 → 2024-12 (~4.1y)**; ARB/SUI join the cross-section only in early 2024. This also means **3 of the 15 CPCV paths have both test blocks entirely pre-eligibility and are exactly 0.0**, so the frac-positive ceiling is 12/15 = 80% — the gate counts those flat paths as non-positive (conservative).

## Gate results (headline: `xs_l021_reg_on`)

| Config | DSR (>0.95) | PBO (<0.5) | CPCV med OOS | frac +paths | Verdict |
|---|---|---|---|---|---|
| **l021 reg_on (headline)** | 0.918 ✗ | **0.668 ✗** | +0.040 ✓ | 60% ✓ | **REJECT** (DSR, PBO) |
| l021 reg_off | **0.957 ✓** | **0.668 ✗** | +0.049 ✓ | **80% ✓** | **REJECT** (PBO) |
| l014 reg_on | 0.843 ✗ | **0.668 ✗** | +0.032 ✓ | 60% ✓ | **REJECT** (DSR, PBO) |
| l014 reg_off | 0.935 ✗ | **0.668 ✗** | +0.042 ✓ | **80% ✓** | **REJECT** (DSR, PBO) |
| l042 reg_on | 0.866 ✗ | **0.668 ✗** | +0.025 ✓ | 60% ✓ | **REJECT** (DSR, PBO) |
| l042 reg_off | 0.865 ✗ | **0.668 ✗** | +0.034 ✓ | 60% ✓ | **REJECT** (DSR, PBO) |

DSR detail: per-bar Sharpe 0.0318–0.0441 vs deflated benchmark sr0 = 0.0137 at n=190, T = 3,140 bars. Headline returns are fat-tailed (skew +0.72, kurtosis 30.8) — the episodic edge shows up in the moment structure, which is exactly what inflates the DSR benchmark.

## Full-window economics (net of config costs, managed exits, ~4.1y effective)

| Config | Total ret | Ann ret | Ann vol | Sharpe (365) | Sortino | MaxDD | Trades | Trades/wk | Win | PF | Expectancy/trade | Peak gross lev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **l021 reg_on** | +35.0% | 3.6% | 4.9% | **0.73** | 0.54 | −13.9% | 268 | 0.60 | 53% | 1.46 | +128 (2.68%) | 0.61x |
| l021 reg_off | +58.0% | 5.5% | 6.6% | **0.84** | 0.80 | −11.5% | 437 | 0.97 | 51% | 1.46 | +131 (1.79%) | 0.75x |
| l014 reg_on | +31.6% | 3.2% | 5.5% | **0.61** | 0.42 | −12.9% | 279 | 0.62 | 53% | 1.38 | +113 (2.55%) | 0.64x |
| l014 reg_off | +54.2% | 5.2% | 6.7% | **0.78** | 0.72 | −10.7% | 448 | 1.00 | 53% | 1.40 | +121 (1.97%) | 0.66x |
| l042 reg_on | +29.0% | 3.0% | 4.9% | **0.63** | 0.49 | −14.5% | 260 | 0.58 | 53% | 1.41 | +111 (2.91%) | 0.40x |
| l042 reg_off | +34.9% | 3.5% | 5.7% | **0.64** | 0.58 | −16.4% | 410 | 0.91 | 52% | 1.34 | +85 (2.06%) | 0.42x |

Risk caps bind on essentially every entry (`max_risk_per_trade` ×260–452; drawdown-brake and vol-target also active): this is the engine's low-risk deployable sizing (ann vol 4.9–6.7%, peak gross ≤0.75x), not a juiced backtest. Expectancy 1.8–2.9%/trade against ~2.5bps round-trip costs — **fees are not the binding constraint (same finding as the 4h trend gate); statistical certification is.**

## Post-2021 sub-period (2021-01-01 → 2024-12-31, inside the iteration window)

| Config | Total ret | Sharpe (365) | MaxDD | Trades | Trades/wk | PF | Exp %/trade |
|---|---|---|---|---|---|---|---|
| **l021 reg_on** | +25.1% | **0.85** | −13.9% | 250 | 1.20 | 1.39 | 2.64 |
| l021 reg_off | +46.4% | **1.07** | −11.5% | 419 | 2.01 | 1.42 | 1.73 |
| l014 reg_on | +20.1% | 0.63 | −12.9% | 261 | 1.25 | 1.29 | 2.40 |
| l014 reg_off | +40.7% | 0.94 | −10.7% | 430 | 2.06 | 1.35 | 1.85 |
| l042 reg_on | +18.5% | 0.67 | −14.5% | 243 | 1.16 | 1.31 | 2.81 |
| l042 reg_off | +23.8% | 0.71 | −16.4% | 393 | 1.88 | 1.27 | 1.97 |

**No post-2021 decay in this sample** — the sub-period is as good or better than the full window at every config (the full window adds only ~2 pre-2021 months, so this is near-identity, not a discovery). The Springer-2025 episodicity shows up differently: CPCV path Sharpes spread from −0.046 to +0.082 — the edge arrives in episodes, and two of the six reg_on path-pairs are negative where reg_off stays positive.

## Per-instrument contribution (headline `xs_l021_reg_on`, net P&L / trades)

| Instrument | Net P&L | Trades | | Instrument | Net P&L | Trades |
|---|---|---|---|---|---|---|
| ADA/USD | +13,704 | 31 | | LINK/USD | +752 | 30 |
| BTC/USD | +9,624 | 44 | | BNB/USD | +669 | 37 |
| XRP/USD | +9,083 | 28 | | AVAX/USD | −1,702 | 14 |
| ETH/USD | +4,315 | 36 | | SOL/USD | −1,760 | 21 |
| DOGE/USD | +2,917 | 18 | | SUI/USD | −4,217 | 7 |
| ARB/USD | +1,014 | 2 | | | | |

Consistent with the literature the prereg cited: the P&L concentrates in the most liquid large-caps (BTC/ETH/ADA/XRP/LINK/DOGE all positive), while the youngest, thinnest names bleed (SUI −4.2k on 7 trades, SOL −1.8k, AVAX −1.7k; SUI is negative at every config it participates in, −4.2k to −6.4k). The large-coin/small-coin split documented by Zaremba et al. (2021) reproduces inside our own 11-name universe.

## Commentary — is the 0.4–0.8 net there, and when?

- **In-sample, yes — squarely in the documented band.** Full-window net Sharpe 0.61–0.84 at honest costs across all six configs, post-2021 0.63–1.07, PF 1.27–1.46, and every tradeable CPCV path of the unfiltered 21d config positive (12/12). The research note's "0.4–0.8 net, regime-dependent" is an accurate description of what this engine measures in the iteration window.
- **Out-of-sample, not certifiably.** PBO 0.668 says that picking the best of these six configs on in-sample rank is more likely than not a selection artefact; DSR says that after 190 trials of deflation, 5 of 6 cells cannot be distinguished from the campaign's noise floor. `xs_l021_reg_off` passing 2 of 3 gates (DSR 0.957, CPCV 80%) is the near-miss the conjunctive gate is designed to reject — reported plainly, per the pre-registered decision rule it earns no re-registration by itself.
- **When it works:** the upside episodes (2020-11→2021 alt season, 2023–2024 recovery) drive essentially all the P&L; the 2022 crash and choppy 2022-H2/2023-H1 transitions are flat-to-negative. That is the documented episodicity — the edge is real when the asset class trends, absent when it chops.
- **The regime filter hurt here.** Contrary to the crash-protection rationale, reg_off beats reg_on at all three lookbacks (0.84/0.78/0.64 vs 0.73/0.61/0.63) with equal or smaller drawdown at 14/21d: the engine's managed exits + vol-scaled sizing already cut the 2022-style crashes, while the BTC-63d filter's flat stretches missed the sharp V-shaped recoveries that are exactly crypto momentum's best moments. One window, one observation — but the ablation was pre-registered precisely to measure this, and the answer in this window is clear.
- **Costs don't bind; leverage isn't the story either.** Expectancy ≥1.7%/trade vs 2.5bps RT costs means even a 10× fee stress (~Coinbase Advanced taker tiers) removes ~0.05%/trade of a 1.8–2.9% expectancy. What kills the sleeve is certification, not frictions — and certification is the gate that matters for a 190-trial campaign.

## Decision (executing the pre-registered rule)

- No PASS anywhere ⇒ **Sleeve E is closed at retail costs**: no forward-paper discussion, no Coinbase/Kraken execution work, no deployment claim. The one-config DSR pass is a selection-effect candidate only (PBO 0.668), explicitly not actionable per the pre-registered rule for grid-mate survivors.
- Honest residue, for the record: if a future campaign revisits crypto cross-sectional momentum, the evidence points at **unfiltered ~21d lookback on the large-liquid names only** (BTC/ETH/ADA/XRP/LINK/DOGE carried the P&L; SUI/SOL/AVAX subtracted), with crash handling left to the exit layer rather than a trend filter — and it must come with genuinely new data (the burned 2025+ holdout is unavailable to this family) or a genuinely new hypothesis class, not another lookback grid.

## Caveats (read before quoting any number)

- Effective sample is only ~4.1 years (2020-11 → 2024-12) by construction (300-bar min-history + 4-name minimum cross-section); 3/15 CPCV paths are structurally flat and count as non-positive.
- Sub-period trade stats use the exit-date convention (trades opened before 2021-01-01 and closed after it count in the sub-period with full P&L); equity slices are mark-to-market throughout.
- Yahoo daily crypto venue quirks (weekend bars, USDT≈USD unhedged); managed-exit results are engine-TradeManager-specific.
- The TrialLedger is shared and live: it moved 182 → 184 between registration and this run (external appends), then 184 → 190 with this run's 6 trials. All DSRs use n=190; any future growth only harshens them — no verdict here can flip to PASS.

## Files

- New strategy: `engine/apex_quant/strategies/crypto_xs_momentum.py` (+ export in `engine/apex_quant/strategies/__init__.py`)
- New tests: `engine/tests/test_crypto_xs_momentum.py` (8 tests, all passing: weekly gating, top-N long-only, min-universe/min-history, regime filter, leakage safety, bounded probability, PortfolioBacktester integration)
- Pre-registration: `engine/data_store/crypto_xs_prereg.md` (before any run)
- New runner: `engine/scripts/run_crypto_xs_gate.py`
- Run records: `engine/data_store/validation/crypto_xs_gate_2026-07-19.json`
- Ledger: `engine/data_store/validation/trial_ledger.json` (**184 → 190**, 6 pre-registered trials; 182 → 184 by external appends between registration and run)
- Not touched: `engine/config.yaml`, `run_live_paper_trading.py`, the shared store write path, the 2025+ holdout, the live daemon.
