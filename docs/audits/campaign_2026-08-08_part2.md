# ApexFX Campaign 2 — Portfolio Construction, Hidden Costs, Concentration & Governance
**Date:** 2026-08-08 · **Scope:** the areas Campaign 1 didn't touch — capital allocation, short-side cost completeness, crypto calendar mechanics, factor concentration, pyramiding evidence, validation governance · **Rule in force:** frozen proof untouched throughout.

---

## 1. Pyramiding (research) — admissible, hostile priors, DEFER

- Ledger: **never tested** (333 trials, zero pyramid entries). Engine doesn't support multi-lot positions (one position per instrument; `portfolio.py:311,416,532`).
- Literature: no robust Sharpe improvement — pyramiding converts Sharpe into skew (Zarattini/Concretum 40-futures study: trade P&L ~doubles, vol more than doubles, maxDD 25.7%→48.7%; Quantica 2,750 trades: aggregate distributions barely differ; Carver: "extra positive skew, you lose too much in Sharpe").
- In-house priors hostile: the early-partial gate (REJECTED) already killed the BE-at-+0.5R stop trajectory a +0.5R add would create; the runner gate (REJECTED on PBO) covered "fuller exposure in winners."
- **Verdict: DEFER.** A +1.0R add variant is the only defensible cell; full pre-reg spec written in the research report if ever wanted. Expected value: low.

## 2. Hidden short-side costs (audit) — dividends missing everywhere

- **Dividends are not modeled at all.** Price series are split-adjusted but dividend-unadjusted; no dividend path exists in any P&L/cost function. Effect: **shorts flattered** (ex-date drops booked as short P&L, dividend never charged — missing leg ≈ £0.8–2.0k over the certified window, 3–4× the entire borrow leg) and **longs understated** (never receive the cash). Book-level the two partly offset — certified headline is mildly *conservative* — but per-sleeve attribution is corrupted both ways. Worst-case Sharpe impact −0.04 to −0.06: no verdict flip against DSR 0.998 headroom, but the borrow-fee report's "the drag is signal, not financing" conclusion is wrong by ~3–4×.
- **Borrow fee (50bps) was measurement-only** — never wired into the certified anchor; the anchor embeds zero-cost short financing.
- **Append-only cache never restates on splits** — a future split creates a fake discontinuity bar in ATR/momentum/correlation inputs; the ARB dead feed (25 pinned bars) is the live instance of this failure class. Fix: overlap-window restatement check in `_top_up` + a dead-feed quality guard.
- **Fix queued:** dividend sidecar (`events=div,split` from the vendor) or a pre-registered dividend-drag sensitivity run, W2-borrow-protocol style.

## 3. Crypto calendar mechanics (audit) — mostly right, one CRITICAL live-path bug

- **Union-calendar handling is correct:** crypto positions mark/manage on weekend bars, equities freeze, equity curve appends one point per day — no double-count, no skipped days. The nightly Action runs 7×/week; **no weekend exit-delay problem exists.**
- **CRITICAL (live path):** the live loop's correlation cap computes **price-level correlation** (co-trending assets read ≈+1 regardless of return co-movement) with a 30-calendar-day ffill+bfill frame — vs the certified backtest's 63-union-bar pairwise log-return correlation. The live cap binds essentially at random. Feeds real order gating when the live loop runs. Fix: log-returns, drop bfill, align window.
- **MODERATE:** correlation cap fails *open* on missing/poisoned data (NaN → 0.0 = "uncorrelated", bypasses the cluster cap); crypto cost model 1.25bps/side vs Binance-real 7.5–10bps (≈£1.5–2k drag over the window — no flip, but marginal crypto names go further negative); paper/backtest borrow-accrual parity gap (inert at 0.0 default).

## 4. Portfolio cap sensitivity (measurement, anchor EXACT, twin-verified) — 6.5% is right; 4.5% is the prop answer

| cap | Sharpe | maxDD | worst day | net £ | £/mo | cap vetoes |
|---|---|---|---|---|---|---|
| 0.045 | **0.9214** | 17.01% | **−3.77%** | 172,595 | 1,598 | 2,557 |
| **0.065 (certified)** | 0.8628 | **16.32%** | −5.09% | **197,165** | **1,826** | 184 |
| 0.080 | 0.7703 | 17.98% | −5.23% | 160,553 | 1,487 | 9 |
| 0.10–0.15 | 0.7747 | 17.98% | −5.23% | 162,584 | 1,505 | 0 |

- Loosening the cap **loses** money (vetoed entries are adversely-selected late adds onto a loaded book) — the cap earns its keep. 
- Sharpe-optimal is 4.5%; net-P&L/Calmar-optimal is the certified 6.5%. **Prop reading: firms with a 4–5% daily-loss rule need the 4.5% cap** (worst day −3.77% contained) — the certified 6.5% has a −5.09% worst day in-sample. Direct input for the funded-runner config.
- Artifact: `validation/cap_sensitivity_2026-08-08.json(+twin)`, `cap_sensitivity_2026-08-08.md`.

## 5. Factor concentration (measurement, anchor EXACT, integrity-checked) — the hidden tail

- **~87% of net P&L is tech/AI trend.** Top-5 instruments (TSLA, TSM, MSFT, META, NVDA) = 55.7% of £197k net. HHI 0.0486 → effective N ≈ 20.6 of 39 names.
- Return streams look diversified (avg pairwise |corr| 0.014) — but that's trade-*timing* diversification, not factor diversification.
- Exposure tails: max net long **238% of equity**; net long >50% of equity on 42% of days; max net tech long **168% of equity**.
- **Gap stress (overnight upper bound):** a −25% tech-basket gap at max exposure = **−43% of equity in a day** (p05 −29.9%); a routine −10% megacap session at max tech exposure = −16.8% — the entire 16.3% maxDD envelope in one session.
- **Verdict:** concentration is the book's real tail risk. Slow reversals are survivable (stops + time exits bleed exposure); a single-session theme gap at a high-exposure day is not. The current live book (long AMD/TSM/AAPL + tech shorts + stacked long-USD) is exactly this pattern.
- **Mitigations for the funded build (queue):** per-theme exposure cap (tech ≤ ~60–80% of equity), per-currency-factor cap (USD netting), both as funded-runner risk overlays — they don't need alpha gates, they're risk policy, but they DO need a measurement pass to set thresholds (queue at graduation).
- Artifact: `validation/factor_concentration_2026-08-08.json`, `factor_concentration_2026-08-08.md`.

## 6. Validation governance (research) — the holdout has already been touched

- **Formal look #1 exists** (book_e_126, 2026-07-17, user-approved, MARGINAL, logged in `holdout_looks.log`) — burned for the wide trend family by convention. **Book H gold (the funded candidate) has never had a holdout look — still blind for the config that matters.**
- **Informal violation found:** untracked `engine/scratch/run_10yr_blind_backtest.py` ran through 2026-07-22 — straight through the holdout — unlogged, unledgered. Needs deletion or logging + a hard assert that any ≥2025-01-01 window appends to `holdout_looks.log`.
- **September protocol (pre-registered recommendation):** ONE confirmation look, only if the paper proof graduates (≥60d, ≥40 trades, Sharpe>0, cost ratio<1.5×), on the exact funded-runner config (with only iteration-gated amendments), evaluation only — no fitting, no sweeps, no retry. MARGINAL → extend paper clock; DEAD → family burned; CONFIRMED → buy the challenge.
- **Multiplicity assessment:** the ledger+DSR+Bonferroni-K structure is stronger than most professional practice, but: (a) DSR's dispersion is estimated from in-run config mates — flatters 2-config gates (N=333 barely bites); fix by estimating dispersion from a fixed reference set and recording per-trial Sharpes (all 333 ledger entries currently have `sharpe=None`); (b) `family` set on 6/333 trials; (c) scratch-layer prospecting escapes the count — formalize: bulk-charge scratch sessions or scope them measurement-only.

---

## Consolidated ADOPT / REJECT / DEFER (Campaign 2)

| # | Item | Verdict | Lands |
|---|---|---|---|
| 1 | Pyramiding gate | **DEFER** (hostile priors; spec ready) | optional, post-graduation |
| 2 | Dividend modeling (charge shorts, credit longs) + restatement/dead-feed guards | **ADOPT (honesty)** | funded-runner build |
| 3 | Live correlation-cap statistic fix (log-returns, 63-bar, no bfill) | **ADOPT (correctness)** | before any live loop restart |
| 4 | Crypto fee realism (fee_bps mechanism) | **ADOPT at funded mapping** (no verdict flip) | funded-runner build |
| 5 | Portfolio cap: keep 6.5% certified; **use 4.5% for daily-loss-rule firms** | **ADOPT as funded config input** | graduation |
| 6 | Theme + currency-factor exposure caps | **ADOPT as risk policy** (thresholds via measurement at graduation) | funded-runner build |
| 7 | Holdout protocol: one confirmation look post-graduation; delete/log the blind-backtest violation; log-assert | **ADOPT (governance)** | now |
| 8 | DSR dispersion fix + per-trial Sharpe recording + scratch charging rule | **ADOPT (governance)** | next gate cycle |
