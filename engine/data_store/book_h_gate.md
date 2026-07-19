# Portfolio-Level Gate — BOOK H: the halal re-platformed multi-asset trend book — 2026-07-19

**Pre-registration:** `engine/data_store/book_h_prereg.md` (written BEFORE any run; substitution
table, screen methodology, ≤3 configs, ledger plan, FX riba quantification, limitations — this
report changes nothing that was pre-registered).
**Window:** ITERATION only, strictly < 2025-01-01 (daily bars, store-limited span 2016-01-01 →
2024-12-31; per-instrument history starts at listing — PLTR 2020, UBER 2019, SPSK 2019-12,
SOL 2021, ARB/SUI 2023, etc.). **No `--final` run; the 2025+ holdout was not touched in any way.**
**Base book:** Book D (`book_d_multiasset_252`) verbatim — lookback 252, vol 63, hold 21, rr 1.5,
`rule_based` regime, HTF 1w×50 gate, managed exits, vol-scaled sizing, config risk caps binding,
per-asset-class v5 costs (equity 2.0 bps spread + 1.0 slippage per side, crypto 1.25 bps/side,
forex per-pair pips), warmup 250, CPCV purge 21, seed 42. **Book H changes the universe only.**
**Script:** `engine/scripts/run_portfolio_gate_book_h.py` — thin orchestration reusing
`run_portfolio_gate.py` (`TrendBook`, `_gate`, helpers) and `run_portfolio_gate_multiasset.py`
(`FX_MAJORS_7`, `_class_breakdown`) unchanged; each book runs on its own universe panel, which
is the point of the re-platforming. Machine-readable output:
`engine/data_store/validation/book_h_gate_2026-07-19.json`.
**Ledger:** 3 trials recorded at pre-registration, **n_trials 190 → 193**; the gate run itself
re-recorded the same canonical keys and deduped (193 → 193). Every DSR below is deflated by **193**.

---

## Universe H (per prereg §3)

- **Core (38):** 12 screened stocks (AAPL MSFT NVDA META AMZN GOOGL TSLA AMD PLTR TSM NFLX UBER)
  + ISWD.L / ISDU.L / ISDE.L (iShares MSCI World / USA / EM **Islamic** UCITS ETFs)
  + XLK XLE XBI SMH SOXX (kept sector ETFs; XLK's Visa/Mastercard borderline call documented in
  the prereg) + 11 crypto + 7 FX majors.
- **+gold (39):** core + SGLD.L (Invesco Physical Gold ETC, allocated LBMA bars, USD line).
- **+sukuk (39):** core + SPSK (SP Funds Dow Jones Global Sukuk ETF; the audit's first choice,
  HSBC Global Sukuk HSKD.L, has zero in-window history — documented in prereg §2 row 5).
- **Dropped from Book D:** SPY QQQ IWM (→ certified Islamic UCITS), XLF (banks), ARKK (holds
  COIN/HOOD/Block financials), GLD (→ allocated ETC in the +gold config), TLT (→ sukuk in the
  +sukuk config). MATIC/USD still absent from cache, skips as in Book D.

## Verdicts (DSR deflated by 193; PBO across the full 3-config selection set)

**PBO across 3 configs = 0.27225 < 0.5 → PASS** (16 splits, 4000 combos, seed 42).

| Config | DSR (> 0.95) | PBO | CPCV med OOS | frac +ve | Verdict |
|---|---|---|---|---|---|
| `book_h_core_252` | **0.966 ✓** | **0.272 ✓** | **+0.053 ✓** | **93% (14/15) ✓** | **PASS** |
| `book_h_gold_252` | **0.996 ✓** | **0.272 ✓** | **+0.065 ✓** | **93% (14/15) ✓** | **PASS** |
| `book_h_sukuk_252` | **0.987 ✓** | **0.272 ✓** | **+0.053 ✓** | **93% (14/15) ✓** | **PASS** |

CPCV paths (per-period Sharpe):
- core: `[0.053, 0.082, 0.084, 0.050, 0.082, 0.051, 0.044, 0.015, 0.031, 0.091, 0.050, 0.093, 0.061, 0.095, −0.026]`
- gold: `[0.073, 0.074, 0.099, 0.056, 0.097, 0.056, 0.061, 0.023, 0.052, 0.065, 0.032, 0.067, 0.065, 0.088, −0.024]`
- sukuk: `[0.053, 0.082, 0.084, 0.053, 0.105, 0.051, 0.044, 0.010, 0.052, 0.091, 0.047, 0.079, 0.057, 0.103, −0.026]`

All three configs share one mildly negative path (#15, the same final OOS block that was Book D's
only negative path at −0.022).

## Full-window side-by-side: Book H vs Book D (clean re-run anchor)

| Metric | Book D clean | H core | H + gold | H + sukuk |
|---|---|---|---|---|
| Universe | 42 | 38 | 39 | 39 |
| Total return (~9y) | +438.5% | +291.9% | **+510.1%** | +375.0% |
| Sharpe (ann., 252) | 0.97 | 0.85 | **1.09** | 0.96 |
| Ann. vol | 14.7% | 13.7% | 13.9% | 13.7% |
| Max drawdown | 19.1% | 19.4% | 19.3% | 19.4% |
| Trades | 1516 | 1587 | 1557 | 1581 |
| Win rate | 55.9% | 54.9% | 55.7% | 55.6% |
| Profit factor | 1.41 | 1.31 | 1.42 | 1.38 |
| Expectancy / trade | +273.13 (+1.109%) | +176.25 (+1.069%) | +310.08 (+1.110%) | +220.38 (+1.155%) |
| Net P&L / net per trade | +453,479 / +299.13 | +296,174 / +186.63 | +519,735 / +333.81 | +374,394 / +236.81 |
| Max gross leverage | ~2.84× | ~3.24× | ~3.24× | ~3.24× |
| DSR (own n) | 0.934 ✗ (n=150) | 0.966 ✓ (n=193) | 0.996 ✓ (n=193) | 0.987 ✓ (n=193) |
| PBO | 0.056 ✓ | 0.272 ✓ (3-config set) | 〃 | 〃 |
| CPCV med / frac | +0.050 / 14-15 | +0.053 / 14-15 | +0.065 / 14-15 | +0.053 / 14-15 |
| **Verdict** | **REJECT** | **PASS** | **PASS** | **PASS** |

Per-asset-class P&L (trades / net):

| Class | Book D clean | H core | H + gold | H + sukuk |
|---|---|---|---|---|
| Equity | 1298 / +395,543 (24) | 1350 / +272,298 (20) | 1350 / +457,011 (21) | 1339 / +337,794 (21) |
| Crypto | 190 / +54,339 (11) | 196 / +31,235 (11) | 178 / +69,892 (11) | 202 / +38,415 (11) |
| Forex | 28 / +3,598 (7) | 41 / −7,358 (7) | 29 / −7,168 (7) | 40 / −1,816 (7) |

## The halal-constraint cost, quantified

Measured as Δ full-window Sharpe vs Book D clean (0.97), the pre-registered H2 metric:

- **Equity re-platforming alone (core): −0.12 Sharpe** (0.85 vs 0.97; PF 1.31 vs 1.41). This is
  the honest headline cost: swapping SPY/QQQ/IWM (+22.8k combined in Book D) for the Islamic
  UCITS trio (net +0.7k combined, see below) and deleting XLF/ARKK/GLD/TLT without their
  halal replacements costs about an eighth of a Sharpe point in-window.
- **Re-platforming + allocated gold leg: +0.12 Sharpe — a BENEFIT, not a cost** (1.09 vs 0.97;
  PF 1.42 ≈ 1.41; +510.1% vs +438.5%). SGLD.L alone contributed +21.7k in 17 trades vs GLD's
  +15.7k in 28 — and the portfolio-level reshuffle around it was net-positive.
- **Re-platforming + sukuk leg: −0.01 Sharpe — neutral** (0.96 vs 0.97). SPSK barely traded
  (4 trades, +1.0k — short history, low vol, few 252-lookback triggers), functionally replacing
  TLT's +1.7k at par.

**Bottom line: in-window, full halal compliance of the equity/defensive sleeve costs between
−0.12 and +0.12 annualized Sharpe depending on which defensive leg is carried — i.e. the
constraint is approximately free, and with the allocated-gold leg it is accretive.** The
pre-registered hypothesis H1 (positive-OOS character survives) holds on all three configs;
H2 (cost small relative to compliance benefit) holds with margin.

## Where the numbers come from — attribution and caveats

- **The flagship substitution LOST money as a trend leg.** ISWD.L is net-negative in all three
  configs (−13.2k core, −8.1k gold, −11.3k sukuk, worst-or-second-worst instrument each time),
  while ISDU.L (+3.6 to +7.3k) and ISDE.L (+9.0 to +14.1k) are positive. The cause is structural,
  not compliance-related: **ISWD.L is the GBp (pence) LSE line**, so its daily return stream
  embeds cable — buy-hold in-window ISWD.L +120.3% vs SPY +191.6%, with GBP/USD −15.0% over the
  same span (and in-window daily-return corr vs SPY only 0.385). The 252-lookback momentum
  signal on the pence line is trading a GBP-diluted world-equity stream. Economically, a
  USD-based investor holding ISWD gets world-Islamic-equity returns in USD terms; the backtest
  cannot see that (the engine has no quote-currency conversion — the same approximation Book D
  already runs on). **Action item this surfaces:** if a USD-priced Islamic world-equity line
  with adequate history can be sourced (none validated on Yahoo LSE today — probed ISWF/WDIA/
  WDSL/1WIS, all absent), it should replace ISWD.L in a future pre-registration; until then
  ISWD.L's negative P&L overstates the true cost of the flagship substitution.
- **SGLD.L did the gold leg's job better than GLD did.** +21.7k (17 trades) vs +15.7k (28
  trades) — fewer, better trend entries; the two track the same metal (in-window daily corr
  0.804, buy-hold +138.4% vs +135.3%).
- **The +gold uplift is NOT pure gold attribution.** SGLD.L's own net is +21.7k, yet total net
  rises +224k core→gold and per-instrument P&L reshuffles book-wide (crypto +31.2k→+69.9k,
  XBI −2.4k→+14.9k, AMZN into the top-3, ISWD's loss shrinking). One shared equity curve with
  binding caps is a coupled system: adding one instrument changes vol-scaling and cap binding
  for everyone (the same sensitivity Book D's grid showed). The gate evaluates books as whole
  systems — reported as such, not as "gold adds 0.24 Sharpe".
- **FX stayed dead weight** in every Book H config (−7.4k / −7.2k / −1.8k on 29–41 trades),
  replicating Book D's clean-data finding (+3.6k, 28 trades ≈ flat). The prereg's FX riba
  quantification for the user's scholar stands: the sleeve is < 1% of net P&L either way, so
  the tom-next ruling at momentum horizons is economically low-stakes in both directions.
- **PBO caveat, pre-registered and repeated here:** the 3 configs share ~95% of their universe,
  so PBO's discriminative power is limited by construction; 0.272 passes, and it is reported as
  computed.

## Why Book H passes DSR where Book D failed — the honest mechanics

Book D clean failed DSR at 0.934 (n=150); all three Book H configs pass at a *harder* count
(n=193). Both facts, stated plainly:

1. **The Sharpe numerators moved both ways.** H gold (1.09) exceeds Book D (0.97); H core (0.85)
  is below it. Numerator alone does not explain the verdict flip.
2. **The hurdle (sr0) is set by the selection set's internal Sharpe dispersion, not only by
   the ledger count** (`deflated_sharpe_ratio`: sr0 = expected-max-Sharpe of N trials with std
   = std of the configs' per-period Sharpes). Book D's 2-config set was widely dispersed
   (C 0.68 vs D 0.97 ann. → sr_std 0.0131 → sr0 0.0345); Book H's 3-config set is tight
   (0.85/0.96/1.09 → sr_std 0.0075 → sr0 0.0207). The tighter the pre-registered family, the
   lower the expected-max hurdle — this is the DSR formula working as written, not a
   denominator trick: n=193 *raises* sr0 vs n=150 (0.0201→0.0207) within the same dispersion.
3. **Sensitivity to ledger growth:** after the gate completed, an unrelated concurrent campaign
   (st_reversal × 6, pead_book × 6) grew the shared ledger to 205. Recomputing Book H's DSRs at
   n=205: core 0.9657, gold 0.9961, sukuk 0.9866 — **all still PASS** (Book D at n=205 would be
   0.9233, still REJECT). The verdicts are not marginal to the denominator.

## Is Book H "certified" where Book D was "promising, not certified"?

- **In the only sense this project's discipline can certify: YES, in-window.** Book H is the
  first trend book to pass all three pre-registered gates — DSR > 0.95 at the honest full
  ledger count, PBO < 0.5 across the selection set, CPCV 14/15 positive paths with median
  +0.05 to +0.07 — on the rebuilt clean data layer, on the fixed engine, with config caps
  binding throughout. Book D never did this (REJECT on DSR in the clean re-run).
- **What this certification is NOT:** (a) it is not a holdout result — 2025+ is untouched; per
  the standing rule a PASS now *warrants* a `--final` holdout look, but running one burns
  holdout blindness and is the user's call, not made here; (b) it is not a fatwa — the screens
  are AAOIFI-style activity screens plus *referenced* (not point-in-time recomputed) debt-ratio
  checks, the MSCI Islamic 33.33% vs AAOIFI 30% divergence is documented in the prereg, XLK's
  Visa/Mastercard and NFLX/META's DJ-Islamic "entertainment" exclusion are flagged for the
  scholar, and the FX tom-next question is quantified but deliberately unanswered
  (prereg §7); (c) it is not TER-adjusted — the engine models spread/slippage, not fund fees.
- **Honest residual concerns:** the ISWD.L pence-line artifact (above) means the core book's
  −0.12 cost likely *overstates* the true substitution cost; conversely the +0.12 gold benefit
  is partly a coupled-system reshuffle, not a guaranteed property of the gold leg. Both cut
  toward "the halal constraint is approximately free," which is the robust in-window conclusion.

## Ledger

- **n_trials before pre-registration: 190**
- **n_trials after pre-registration: 193** (+3: `book_h_core_252`, `book_h_gold_252`,
  `book_h_sukuk_252` — recorded before the first run; the gate re-recorded identical canonical
  keys and deduped, so the run itself added 0)
- **n_trials at report time: 205** (+12 from an unrelated concurrent st_reversal/pead campaign
  that landed after the gate completed; DSR sensitivity at n=205 shown above — verdicts
  unchanged)

## Compute & verification notes

- Full universes, no reductions: 3 full-window runs (47s + 51s + 50s) + PBO + 3×15 CPCV paths
  (111s + 115s + 113s) ≈ 8 min on .venv-mac.
- **Determinism: verified.** `book_h_gold_252` full-window re-run standalone reproduced the
  gate output byte-for-byte (ret 510.0603%, 1557 trades, Sharpe 1.08620, PF 1.42054, final
  equity 610,060.29). PBO seeded at `cfg.seed` (42); no RNG elsewhere.
- **Smoke test before the full run:** 5-instrument `--no-ledger` subset exercised the whole
  path including the `.L` tickers (classified equity → bps costs, 252 annualization, Mon–Fri
  calendar) end-to-end; ledger untouched in smoke mode.
- **Data for the 5 new tickers** was fetched through the store path (`ParquetStore.get_or_fetch`
  + `YahooAdapter`), inheriting the rebuilt data layer's guarantees: session normalization,
  off-calendar rejection (verified zero weekend bars), atomic writes, file locking. In-window
  bars: ISWD.L/ISDU.L/ISDE.L/SGLD.L 2273 each (2016-01→2024-12, max gap 5 calendar days =
  holiday weekends), SPSK 1259 (2019-12→2024-12). Yahoo probe metadata recorded in the prereg
  (currencies: ISWD.L GBp; ISDU.L/ISDE.L/SGLD.L/SPSK USD).
- Results JSON: `engine/data_store/validation/book_h_gate_2026-07-19.json`.

## Files created/modified in this campaign

- `engine/data_store/book_h_prereg.md` — the pre-registration (substitution table, screens,
  configs, ledger plan, FX note, limitations). **New.**
- `engine/scripts/run_portfolio_gate_book_h.py` — the gate script (thin orchestration over the
  existing machinery; no engine-code changes). **New.**
- `engine/data_store/validation/book_h_gate_2026-07-19.json` — machine-readable gate output.
  **New.**
- `engine/data_store/book_h_gate.md` — this report. **New.**
- `engine/data_store/ISWD.L_1d.parquet`, `ISDU.L_1d.parquet`, `ISDE.L_1d.parquet`,
  `SGLD.L_1d.parquet`, `SPSK_1d.parquet` — new cached daily data via the store path. **New.**
- `engine/data_store/validation/trial_ledger.json` — +3 pre-registered Book H trials
  (190 → 193). **Modified** (subsequent growth to 205 is a separate concurrent campaign).
- No engine source files, configs, or existing data were modified. `engine/config.yaml` and
  `scripts/run_live_paper_trading.py` untouched; the live daemon was not disturbed.
