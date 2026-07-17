# Book E Gate — Frozen TrendBook Config on a WIDE (77-Instrument) Universe — 2026-07-17

**Pre-registration:** `engine/data_store/book_e_prereg_2026-07-17.md` (written before the runs; hypothesis, frozen params, universe, drop rule, compute fallback).
**Window:** ITERATION only, strictly < 2025-01-01 (daily bars; window 2016-01-01 → 2024-12-31; per-instrument history starts at listing — PLTR 2020, SOL 2021, ARB/SUI 2023, DOT 2020-08). No `--final` run; the 2025+ holdout was not touched in any way.
**Configuration:** the frozen TrendBook stack, identical to Books C/D except the universe — `RegimeGatedMomentum` + `MultiTimeframeMomentum` (1w×50 HTF gate) per instrument, vol 63, hold 21, rr 1.5, rule_based regime, managed exits, vol-scaled sizing, config caps binding (2% per trade, 3× gross, 1.5× corr-cluster, 6.5% portfolio risk, swing bucket 10 concurrent, drawdown breakers 10%/20%), v5 per-asset-class costs (equity 2.0 bps/side, crypto 1.25 bps/side, FX per-pair pips). `book_e_252` = Book D's exact config; `book_e_126` = Book C's exact config — so each E book differs from its letter-book twin ONLY by the universe.
**Universe:** 77 = the existing 42 (24 equities/ETFs + 11 crypto + 7 FX majors) + 35 new (6 broad/size/intl ETFs, 4 rates/credit, 3 commodity, 1 real-estate, 8 sectors, 11 mega-caps, 2 crypto). Full list + rationale in the pre-reg. New data fetched 2026-07-17 via the normal store path (`ParquetStore.get_or_fetch` + `YahooAdapter`, 2016-01-01 → present; session-normalized, forming-bar trimmed). **Drop rule applied:** no instrument fell under 300 in-window bars (new equity/ETFs 2264 each; LTC/USD 3288; DOT/USD 1595); `MATIC/USD` still has no cached 1d data and skipped as in every prior run → resolved universe exactly 77 ({'equity': 57, 'crypto': 13, 'forex': 7}).
**Gate:** identical to the multiasset gate — DSR > 0.95 **and** PBO < 0.5 **and** CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive. Exactly 2 trials recorded BEFORE the runs; **ledger n: 150 → 152**; both DSRs deflated by **152**.
**Script:** `engine/scripts/run_portfolio_gate_book_e.py` (thin orchestration over `run_portfolio_gate.py` + `run_portfolio_gate_multiasset.py`, machinery unchanged). Machine-readable output: `engine/data_store/validation/portfolio_gate_book_e_2026-07-17.json`.

## Verdicts (vs the Book C/D clean-data twins)

| Gate | Book C clean (126, 42 inst) | **Book E-126 (77 inst)** | Book D clean (252, 42 inst) | **Book E-252 (77 inst)** |
|---|---|---|---|---|
| DSR (> 0.95) | 0.682 ✗ (n=150) | **0.962 ✓ (n=152)** | 0.934 ✗ (n=150) | **0.712 ✗ (n=152)** |
| PBO (< 0.5) | 0.056 ✓ | **0.2055 ✓** | 0.056 ✓ | **0.2055 ✓** |
| CPCV median OOS | +0.069 ✓ | **+0.058 ✓** | +0.050 ✓ | **+0.061 ✓** |
| CPCV frac positive | 15/15 ✓ | **15/15 ✓** | 14/15 ✓ | **14/15 ✓** |
| **Verdict** | REJECT | **PASS** | REJECT | **REJECT** |

**`book_e_126` passes all three gates at the honest 152-trial deflation — the first config in this campaign to do so on clean data.** `book_e_252` fails DSR decisively (0.712); its PBO/CPCV pass. PBO is computed across the two E books as the pre-registered selection set: the IS-better book (E-126, ann. Sharpe 1.15) stays better OOS across the 16 splits.

CPCV paths (per-period Sharpe):
- E-126: `[0.076, 0.083, 0.082, 0.063, 0.086, 0.039, 0.048, 0.016, 0.035, 0.057, 0.046, 0.057, 0.058, 0.079, 0.078]` — all positive.
- E-252: `[0.061, 0.070, 0.096, 0.031, 0.066, 0.050, 0.073, 0.050, 0.088, 0.067, −0.003, 0.046, 0.033, 0.082, 0.032]` — 14/15.

DSR mechanics note: the sr0 hurdle is estimated from the selection set's own Sharpe dispersion — E's two books (ann. 1.15 vs 0.81) raise it to 0.0410/period from 0.0345 in the C/D run. E-252's DSR is punished both by its own lower Sharpe and by being the weaker sibling of E-126; that is the deflation working as designed, stated plainly.

## Full-window metrics (iteration window, caps binding)

| Metric | C clean (126, 42) | **E-126 (77)** | D clean (252, 42) | **E-252 (77)** |
|---|---|---|---|---|
| Total return (~9.0y) | +181.8% | **+648.6%** | +438.5% | **+247.8%** |
| Sharpe (ann., 252) | 0.68 | **1.15** | 0.97 | **0.81** |
| Max drawdown | 19.8% | **16.8%** | 19.1% | **20.2%** |
| Trades | 1590 | **1537** | 1516 | **1178** |
| Entries/week (same 3287d span) | 3.39 | **3.27** | 3.23 | **2.51** |
| Trades/year | 176.8 | **170.8** | 168.5 | **130.9** |
| Win rate | 54.7% | **55.7%** | 55.9% | **54.3%** |
| Profit factor | 1.26 | **1.50** | 1.41 | **1.31** |
| Expectancy / trade | +108.91 (+0.957%) | **+384.79 (+0.978%)** | +273.13 (+1.109%) | **+197.29 (+0.742%)** |
| Net per trade | +116.46 | **+428.10** | +299.13 | **+210.33** |
| Max gross leverage | ~3.02× | **~3.00×** | ~2.84× | **~3.03×** |
| Instruments net positive | 27/42 | **34/77** | 31/42 | **25/77** |

## The breadth hypothesis, tested directly: entries did NOT increase

The pre-registered prediction was ~1.7–2× entries from 1.83× instruments with Sharpe preserved.
**The frequency leg is rejected; the edge-preservation leg is confirmed.**

- Entries/week went **DOWN**, not up: E-126 3.27/wk vs C-126 3.39/wk (−3.4%); E-252 2.51/wk vs D-252 3.23/wk (−22%). Trade counts: 1537 vs 1590; 1178 vs 1516.
- **The book was already breadth-saturated at 42 instruments.** The 10-slot swing bucket and the 6.5% portfolio-risk cap — not signal flow — set the entry rate. Adding 35 candidates only increased the overflow of vetoed signals:

| Constraint family (events) | C (126, 42) | **E-126 (77)** | D (252, 42) | **E-252 (77)** |
|---|---|---|---|---|
| timeframe_bucket_full (veto: 10 swing slots full) | 19,029 | **41,376** | 15,300 | **29,777** |
| max_portfolio_risk_exceeded (veto: 6.5% open-risk cap hit) | 5,729 | **18,614** | 7,710 | **12,137** |
| portfolio_risk_cap (size clipped to remaining headroom) | 464 | **630** | 572 | **430** |
| max_correlated_exposure (1.5× cluster cap clipped) | 11 | **10** | 12 | **15** |
| max_total_exposure (3× gross clipped) | 1 | **5** | 0 | **1** |
| drawdown_breaker (HARD HALT, DD ≥ 20%) | 0 | **0** | 0 | **17,247** |
| drawdown_reducing_scale / max_risk_per_trade / regime_scale / vol_target (scalings) | 15,781 | **41,322** | 19,407 | **27,458** |
| **Total vetoes** | **24,759** | **59,994** | **23,012** | **59,163** |

- The new sleeve barely traded and lost money on the tiny sample it got: **E-126: 48 of 1537 trades (3.1%) from 35 new instruments, net −13,551** (2/35 net positive; best IYR +2,723 on 1 trade; worst DOT/USD −10,543 on 7). **E-252: 37 of 1178, net −6,462** (3/35 net positive). First-come-first-served slot allocation in instrument-list order plus the always-full bucket meant the legacy sleeve kept the slots; many new names (DIA, VTI, EFA, EEM, BA, GS, HD…) took 0–1 trades in 9 years. ~1.4 trades per new instrument over 9 years is not a sample — the new sleeve was never given one.
- Where the performance came from instead: the legacy-42 sleeve inside E-126 made **+671,544 on 1489 trades** vs C-126's whole-book +181,835 on 1590 — same signals, different cap-mediated selection (which signals get vetoed when 77 candidates compete for 10 slots and 6.5% risk). The wide universe's value showed up as a *selector*, not as added bets. Whether that selection generalizes is exactly what the OOS gates test — and for E-126 they pass (15/15 CPCV paths positive, PBO 0.2055, DSR 0.962 at n=152).
- **E-252's collapse is a risk-system event, not a signal failure:** it hit the 20% drawdown breaker on **2023-03-13** and was halted for the remaining **660 timeline bars (~21 months, through 2024-12-31; max DD 20.2%)** — 17,247 halt vetoes, zero trades in 2024, 42 in 2023 (all pre-halt). Book D on 42 instruments never tripped the breaker (max DD 19.1%). This is the grid run's l189-notch lesson repeating: at some configurations the book's left tail is defined by the breaker, and an extra ~2 years of frozen-in-drawdown is what that looks like.

## Per-asset-class P&L

| Class | C clean: trades / net | **E-126: trades / net** | D clean: trades / net | **E-252: trades / net** |
|---|---|---|---|---|
| Equity | 1345 / +153,944 (24) | **1281 / +570,421 (57)** | 1298 / +395,543 (24) | **1028 / +233,886 (57)** |
| Crypto | 206 / +38,637 (11) | **220 / +76,148 (13)** | 190 / +54,339 (11) | **128 / +9,353 (13)** |
| Forex | 39 / −7,416 (7) | **36 / +11,424 (7)** | 28 / +3,598 (7) | **22 / +4,525 (7)** |

## Sanity checks (pre-registered)

- **Determinism (seed 42): PASS.** Both full-window runs repeated after the gate: book_e_252 ret 2.4776419572 / 1178 trades / final equity 347764.195722, book_e_126 ret 6.4858891225 / 1537 trades / 748588.912251 — byte-identical to the gate run. PBO seeded at `cfg.seed` (42); no RNG elsewhere.
- **No lookahead: structural + verified on new instruments.** Signals are `PointInTimeAccessor` windows (≤ t only); entries execute at the next bar's open (pending queue, `portfolio.py` steps 2/4). Fill check on 8 NEW instruments (JPM, SLV, LTC/USD, VTI, HYG, GSG, USO, DOT/USD): **876/876 trades** fill at exactly bar-open × (1 ± side cost) (equity 2.0 bps/side, crypto 1.25 bps/side; worst relative deviation 1.1e-7 = the 6-decimal trade-record rounding).
- **History floor:** all 77 instruments ≥ 300 in-window bars; no drops beyond the standing MATIC/USD cache absence.
- Wiring: new tickers classify via `asset_class_of` (no slash → equity; LTC/DOT via `CRYPTO_BASES` → crypto, 365-day vol annualization, crypto mean-reversion disabled); Yahoo mapping is the pass-through/generic-crypto branch, exercised by the fetch + fill checks above.

## Honest commentary

- **Verdict per the pre-registered bar: `book_e_126` PASS, `book_e_252` REJECT.** E-126 is the first config in this whole campaign to clear DSR > 0.95 at the honest ledger count on clean data — and it did so at n=152 with a 15/15-positive CPCV distribution and a passing PBO. Per the standing rule, a PASS at the iteration gate is the precondition for even considering a `--final` holdout look. **No `--final` was run** (explicitly out of scope here); whether to spend the one holdout look on E-126 is now the user's decision, and it should be weighed against the caveats below, not just the green row.
- **The breadth hypothesis as stated is half-dead: frequency is capped, not expanded.** Entries/week fell slightly (126) and materially (252). The book was already slot-saturated at 42 instruments (C-126: 19k bucket-full vetoes); at 77, vetoes more than doubled (41k) while entries fell. Any future attempt to raise trade frequency through breadth must raise the caps (bucket size, portfolio-risk budget) — a new pre-registration — not the universe.
- **The improvement is real but its mechanism deserves suspicion.** E-126's +648.6% / Sharpe 1.15 comes almost entirely from the legacy sleeve being cap-selected differently (+671k vs +182k on the same signals), not from the new instruments (48 trades, net −13.6k). The gates are designed to test exactly this kind of in-window selection luck, and E-126 passes them — 15/15 purged OOS paths positive, PBO 0.2055, DSR 0.962 at n=152 — but a skeptic's reading is available and honest: adding 35 instruments gave the veto system 1.83× more chances to dodge bad trades in-window, and some of that dodge ability may not persist.
- **The lookback ordering inverted.** On 42 instruments: 252 (Sharpe 0.97) ≫ 126 (0.68). On 77: 126 (1.15) ≫ 252 (0.81). Combined with the grid's l189 notch, the lookback parameter is not stable across universe widths; what IS stable is the book's positive OOS character (29/30 positive CPCV paths on 42, 29/30 on 77, all four configs). E-252's specific failure mode — a 21-month breaker halt from Mar-2023 — is a left-tail/risk-budget fact, not evidence the 252 signal went bad.
- **New-sleeve cost note (pre-registered):** more round trips at unchanged costs did not degrade per-trade economics for E-126 (expectancy +0.978%/trade vs C's +0.957%; PF 1.50 vs 1.26) — but the new instruments themselves contributed net-negative P&L on a negligible sample. If E-126 ever goes forward, the new sleeve's role is diversification/selection, not standalone expectancy.
- **Concentration caveat, updated:** the book is still overwhelmingly long-biased US equity trend + crypto beta over 2016–2024; E-126's equity sleeve is 83% of trades and 86% of net P&L. The new defensive names (XLU, XLP, JNJ, PG, KO…) almost never got a slot while tech momentum occupied the book — in a different regime that allocation order could matter, and it is an artifact of first-come-first-served slotting, not a ranking.

## Ledger (cumulative)

- **n_trials before: 150**
- **n_trials after: 152** (+2: `book_e_252`, `book_e_126` — recorded before the runs; DSR deflated by 152 for both)

## Compute notes

- Full 77-instrument universe, both variants ran (the 25-min fallback was not triggered): 2 full-window runs + PBO + 2×15 CPCV paths ≈ **8.4 min** on .venv-mac (CPCV 154s/160s per book). Determinism re-runs: 152s + 42s.
- 35 new daily caches written to `engine/data_store/` (2016-01-01 → 2026-07-16, forming-bar trimmed); gate used only < 2025-01-01.
- Results JSON: `engine/data_store/validation/portfolio_gate_book_e_2026-07-17.json`.

---

# HOLDOUT RESULT (one look, user-approved) — 2026-07-17

**What this is:** after `book_e_126` passed the iteration gate above, the user approved spending the project's **ONE holdout look** on it (logged as look #1 in `engine/data_store/validation/holdout_looks.log`). The SAME frozen config (77 instruments, lookback 126, all else identical) was run on the holdout window **2025-01-01 → 2026-07-17** (562 days, ~18.5 months) — data that was never touched during any iteration. Script: `engine/scripts/run_book_e_holdout.py`; machine-readable output: `engine/data_store/validation/book_e_126_holdout_2026-07-17.json`. Run once; no re-checking.
**Pre-fixed verdict criteria (set by the parent BEFORE the run, unmoved):** CONFIRMED = holdout Sharpe > 0.5 AND positive expectancy/trade · DEAD = Sharpe ≤ 0 OR negative expectancy · MARGINAL = in between → unproven, forward paper evidence decides.
**Run mechanics:** PIT accessors carry full history (signals at any holdout bar are fully warmed with legitimate past data, as live trading sees); the backtester runs from 2024-01-01 so its ATR/vol arrays are warm at the boundary; metrics are computed on the ≥ 2025-01-01 slice only (10 carryover crypto trades entered Dec-2024 and closed by 2025-01-13 are in the equity MTM but excluded from trade stats). No instrument had insufficient holdout data (all 77 ≥ 60 bars in-window; none excluded). **Determinism: two full executions byte-identical.**

## Verdict: MARGINAL

| Metric | In-window E-126 (2016→2024) | **Holdout (2025-01→2026-07)** |
|---|---|---|
| Total / annualized return | +648.6% / — | **+7.53% / +3.30%** |
| Sharpe (ann., 252) | 1.15 | **0.345** |
| Max drawdown | 16.8% | **15.5%** |
| Trades | 1537 (469.6 wk) | **293 (80.3 wk)** |
| Entries/week · trades/yr | 3.27 · 170.8 | **3.65 · 190.4** |
| Win rate | 55.7% | **52.2%** |
| Profit factor | 1.50 | **1.09** |
| Expectancy / trade | +384.79 (+0.978%) | **+22.00 (+0.773%)** |
| Max gross leverage | ~3.00× | **~1.56×** |
| Instruments traded / net positive | 45/77 · 34/77 | **32/77 · 20/32** |

- **Sharpe 0.345 vs the 0.5 bar → not CONFIRMED. Sharpe > 0 and expectancy +22.00 > 0 → not DEAD. By the pre-fixed rule: MARGINAL — unproven; forward paper evidence decides.** Sharpe degraded ~70% from the in-window 1.15 (worse than the ~50% base-rate degradation the criteria were calibrated to); per-trade expectancy held up much better (−21%).
- **Per-class P&L flipped:** crypto carried the holdout (65 trades, **+10,776** across 13 instruments) while equities were net-negative (228 trades, **−2,281** across 19 instruments) — the reverse of the in-window book where equities were 86% of P&L. **Forex took zero trades** (vs 36 in-window; consistent with its in-window ~4/yr rate). Biggest losers: META −4,990, AMZN −4,821, TSLA −3,912 — US mega-cap long-trend had no 2025–2026 wind in its sails; biggest winners: TSM +5,433, AMD +5,422, AVAX/USD +5,321.
- **Holdout-window CPCV (thin folds, interpret with care):** median −0.007, **6/15 (40%) paths positive** — on ~18 months of data with 21-bar purges each path is ~70 bars, so this is noisy; it would NOT have passed the iteration CPCV gate (median > 0, >50% positive).

## Curve shape: not one lucky month, but three lucky trades

- 12/19 months positive. Best: **Oct-2025 +6.65%**; worst: **Feb-2025 −6.48%**. The curve ground through a rough Feb–May-2025 stretch (−6.5, −2.3, −4.2, −3.1%), then strung together Jun–Oct-2025 gains (+4.3, +5.8, +0.4, +4.0, +6.7%), went flat winter, and gave back Apr-2026 (−5.8%) and Jun–Jul-2026 (−2.2, −2.3%).
- **Concentration is the honest worry: the top-3 trades contribute 82.0% of the window's net P&L** — TSM long 2025-11-24→12-10 +2,642; ETH/USD short 2026-02-01→02-04 +2,241; TSM long 2025-12-16→2026-01-02 +2,081. Strip them and the remaining 290 trades net ≈ +1,530 over 18.5 months (≈ flat). The expectancy is positive but the distribution is doing the lifting through a very small number of large managed-exit winners.
- Leverage averaged far below the caps (~1.56× vs ~3.0× in-window): the book was rarely at full strength — timeframe_bucket_full ×9,718 and max_portfolio_risk_exceeded ×3,169 over the run (incl. the 2024 warmup year), drawdown-amber scaling ×674.

## Honest commentary

- **The one look is spent; the answer is "unproven, not dead."** The pre-registered discipline held: criteria were fixed before the run and applied as written. MARGINAL means exactly what the parent defined: no deployment decision on this evidence — forward paper trading is the next evidentiary layer, and it costs no holdout blindness (there is none left to spend: the 2025+ window is now burned for this book family).
- Two readings, both honest: (1) the edge survived contact with unseen data — positive return, positive expectancy per trade at ~79% of its in-window size, crypto sleeve strongly positive, 3.65 entries/wk slightly ABOVE the in-window rate; (2) the risk-adjusted stream is weak (Sharpe 0.35, PF 1.09), equity-sleeve negative, and 82% of the profit rides on three trades — luck-of-the-path cannot be ruled out on 18.5 months.
- The lookback-126 config was chosen by the iteration gate; E-252 (rejected in-window) was not run on the holdout and never will be from this project state — that would be a second look.
- Constraint profile stayed dominated by the same two vetoes (bucket-full, portfolio-risk) — the breadth-throttling finding from the iteration window also holds out-of-window: only 45 of 77 instruments ever traded in-window and only 32 of 77 in the holdout; the remaining members of the universe are decoration under the current caps.
- Ledger unchanged by this look (no new trial — a holdout evaluation of an already-recorded config; n stays 152).
