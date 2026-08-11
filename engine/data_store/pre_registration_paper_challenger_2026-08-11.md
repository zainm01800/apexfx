# Pre-Registration — Forward Paper CHALLENGER (Book B): 252 + spill50 vs Book D — 2026-08-11

**Status:** ACTIVE from 2026-08-11 (seed bar: the most recent closed bar at first run — see §5). Engine-simulated paper — no broker, no real money. **Time is the out-of-sample.** The challenger is FROZEN: no parameter changes. Any change restarts the experiment clock and must be recorded in §6.

**Why this exists:** the 2026-08-08 momentum-spillover gate (`engine/data_store/momentum_spillover_gate_prereg.md`, results `engine/data_store/validation/momentum_spillover_gate_2026-08-08.json`) tested conditioning crypto/FX entries on SPY's trailing-return sign on top of the certified Book H gold 252 config. Verdict: **CONFIRMED (adoptable)** — best challenger `book_h_gold_252_spill50` (in-window, strictly < 2025-01-01: Sharpe 0.9385 vs 0.8628 anchor, PF 1.404, 1598 trades, maxDD 17.0%; all four gate legs passed). A backtest CONFIRMED is still only in-window evidence. This document pre-registers the **forward** test: the certified config + spill50 stepped bar-by-bar against the live frozen proof (Book D, `pre_registration_paper_trend_2026-07-17.md`) as a real-time A/B. Nothing here re-validates anything; it tests whether the gated behavior *persists* out of window.

---

## 1. Frozen configuration (Book B, `book_h_gold_252_spill50`)

Identical to the certified gate run (`scripts/run_momentum_spillover_gate.py`, `BOOKS["book_h_gold_252_spill50"]`):

- **Signal per instrument:** `RegimeGatedMomentum` wrapped in `MultiTimeframeMomentum` via the gate's `TrendBook` adapter — the same construction as Book D.
- **Parameters:** `momentum_lookback=252`, `vol_window=63`, `holding_horizon=21`, `reward_risk=1.5`, `regime_method="rule_based"`, `timeframe="1d"`, `htf_rule="1w"`, `htf_ma_window=50`, `carry_filter=False`. (Byte-identical to Book D's `BOOK_PARAMS`.)
- **Spillover gate (the only signal difference vs Book D):** `SpilloverGate` (`engine/apex_quant/strategies/spillover_gate.py`, imported by both the gate script and the stepper). On crypto/FX instruments only: LONG entries permitted only when SPY's trailing **50**-day return > 0 at the SPY bar at-or-before the decision bar (risk-on), SHORT entries only when < 0. Vetoes return FLAT with rationale `spillover regime veto`. Equity/ETF/metals sleeves untouched. SPY itself is **not traded** — it is the gate's reference series only.
- **Universe (39):** Book H gold — 12 screened stocks (AAPL MSFT NVDA META AMZN GOOGL TSLA AMD PLTR TSM NFLX UBER) + 3 Islamic UCITS (ISWD.L ISDU.L ISDE.L) + 5 kept sector ETFs (XLK XLE XBI SMH SOXX) + SGLD.L (allocated gold ETC) + 11 crypto (BTC ETH SOL BNB XRP ADA AVAX DOGE LINK ARB SUI /USD) + 7 FX majors (EUR/USD GBP/USD USD/JPY USD/CHF AUD/USD USD/CAD NZD/USD). **MATIC/USD is excluded** (no cached 1d data; excluded explicitly so a data fix cannot silently change the book). The 18 crypto+FX instruments are the gated set.
- **Sizing:** vol-scaled via `RiskManager.permit`, **`max_risk_per_trade` pinned at 0.01** — the gate's `CERTIFIED_MRPT`, NOT the live config value (0.0075 since 2026-07-23). All other caps follow the live `config.yaml`, the same convention Book D runs under; any mid-run config edit lands in both books' conventions and must be noted in §6. The mrpt asymmetry vs Book D is deliberate (this is the certified config) and is recorded here so the A/B readout interprets sizing correctly.
- **Exits / costs:** `TradeManager` managed exits; v5 per-asset-class costs — identical machinery to Book D.
- **Start equity:** £100,000 paper. **Start date:** 2026-08-11 (first run); seed bar = the most recent closed bar at that run (see §5).

## 2. Machinery and isolation from the frozen proof

- **Stepper:** `engine/scripts/run_paper_portfolio_challenger.py`, driving the same `apex_quant/backtest/paper.py::PaperPortfolio` as Book D (parity proof: `tests/test_paper_portfolio.py`). Everything behavioral — `_top_up`, Supabase row builders, decision logger, halt rule, seed equity — is **imported** from `scripts/run_paper_portfolio.py`, which is not modified; run with no new args its behavior is byte-identical to before this experiment.
- **Gate series:** SPY daily bars are topped up with the same tail-only Yahoo path as the traded universe, trimmed to strictly-closed bars, and mapped to each gated instrument's calendar via `risk_on_map()` (the gate script's exact logic, shared module).
- **State:** local JSON `engine/data_store/paper_portfolio_b/state.json` + own `decisions.log`; Supabase mirror `apex_paper_b_positions` / `apex_paper_b_daily` (same schema/RLS as the A pair; `paper_store.py`'s table names are parameterized, defaulting to the A tables). **The A book's state file and tables are never read or written by this stepper.**
- **Nightly schedule:** second step in `.github/workflows/paper-portfolio.yml`, after the proof step, `continue-on-error: true` — a challenger failure can never fail the frozen proof's run.
- **Start-clock honesty (verified 2026-08-11 against `paper.py`):** the stepper does **NOT** backfill. On a fresh state, `seed_watermark()` marks every closed union-calendar bar up to the penultimate one as already processed, so the first `advance()` steps over exactly ONE bar — the most recent closed — whose decisions become PENDING-ENTRY for the next bar. There are no same-period fills; the forward clock starts clean at the seed bar. Corollary for the A/B: Book D has been running since 2026-07-16, so the books' curves must be compared **over the shared window only** (Book D's equity re-based to its 2026-08-10 close), never on full history.

## 3. Evaluation protocol

Same conventions as the frozen proof (`pre_registration_paper_trend_2026-07-17.md` §3), with the A/B as the primary readout:

**(a) Auto-metrics** — every run: Sharpe-to-date (ann. 252), profit factor, max drawdown, expectancy/trade, win rate, embedded cost total, stored on the latest `apex_paper_b_daily.metrics`.

**(b) HALT rule:** equity drawdown ≥ **15%** from peak → stepper sets `halted=true`, notes it on the daily row, stops for review. Resume only via `--clear-halt` after a written note in §6.

**(c) REVIEW dates:** after **30** and **60** processed union-calendar days from the seed bar (≈ 2026-09-09 and ≈ 2026-10-09 — crypto weekends count). Review contents: metrics trend, per-class P&L, gate veto rate (share of crypto/FX signals flipped FLAT by the spillover gate), cap-binding profile, data health — **and the head-to-head vs Book D over the shared window** (re-based): cumulative return, Sharpe, maxDD.

**(d) Graduate criteria (ALL):** ≥ 60 processed union-calendar days **and** ≥ 40 closed trades **and** realized Sharpe > 0 **and** challenger shared-window Sharpe ≥ Book D's → eligible for *adoption discussion only* (replacing or complementing the proof book is a separate, explicitly argued decision; the spillover gate's in-window CONFIRMED verdict stands on the record either way).

**(e) Kill criteria (ANY):** realized Sharpe < −0.5 after 60 processed days; or 3 consecutive weeks with zero trades AND zero signals; or the gate plumbing is found to diverge from the certified config (fix = restart clock, noted in §6).

**(f) Config changes:** **NONE allowed.** Any change to parameters, universe, gate (including L=50), costs, sizing, caps, exits, or this protocol restarts the experiment clock from the change date and must be recorded in §6. Bug fixes to the *stepper plumbing* that do not change trading behavior are allowed but must be noted, with the parity test re-run.

## 4. What would make this experiment uninterpretable

- Touching the frozen proof in the name of the challenger (shared code edits that alter Book D's behavior, writes to the A tables/state) — isolation is the whole design.
- Comparing full-history curves (Book D has a month's head start) instead of the shared window — see §2.
- Editing the book "just once" mid-run; silent data-source drift; hand-nudging state. The state file is append-only evidence.

## 5. Seed record (2026-08-11)

- Seed run 2026-08-11 ~13:49 UTC processed exactly one bar (**2026-08-10**): equity £100,000.00, 0 open, **4 PENDING-ENTRY** for the next bar — META short ~6.1k, NFLX short ~8.5k, UBER short ~2.1k, XBI long ~7.9k (notionals; all ungated equity/ETF sleeve — no crypto/FX signal that bar, so no spillover vetoes yet). Daily-row notes: `entries 0, exits 0, signals 4 permitted/0 vetoed/27 flat`.
- Second same-day run: strict no-op (idempotency confirmed, state NOT rewritten).
- Supabase mirror: first daily row (2026-08-10, equity 100000.0, n_open 0) verified in `apex_paper_b_daily`; `apex_paper_b_positions` empty. **Isolation verified:** Book A's latest `apex_paper_daily` row (2026-08-10, equity 96600.86, n_open 11) and `apex_paper_positions` unchanged across the seed run; A's local `state.json` sha256 unchanged; A's stepper re-run clean against a scratch state copy (`--no-supabase`), exit 0.

## 6. Change log (mandatory)

| Date | Change | Reason | Clock restarted? |
|---|---|---|---|
| 2026-08-11 | Experiment seeded (bar: see §5) | — | n/a (start) |
