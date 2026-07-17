# Pre-Registration — Forward Paper Test of the Multi-Asset Trend Book — 2026-07-17

**Status:** ACTIVE from 2026-07-17 (seed bar 2026-07-16). Engine-simulated paper — no broker, no MT4, no real money. **Time is the out-of-sample.** The book is FROZEN: no parameter changes. Any change restarts the experiment clock and must be recorded in this file (see §6).

**Why this exists:** the 2026-07-17 multi-asset portfolio gate (`engine/data_store/portfolio_gate_multiasset_2026-07-17.md`) found the diversified trend book positive on 13/15–15/15 CPCV paths with full-window Sharpe ~0.77–0.80, maxDD ~19%, PF 1.25 — but REJECTED it at the deployment gate (12-config grid: DSR 0.76–0.82 vs the 0.95 bar, PBO 0.649 vs 0.5). Verdict: **promising, not certified**. The user's decision: forward paper-test the book as-is. This document is the binding contract for that experiment. Nothing here re-validates the strategy; it tests whether the validated-in-window behavior *persists* out of window.

---

## 1. Frozen configuration (as amended 2026-07-17 — Book D, `book_d_multiasset_252`; see §6 change log #1)

- **Signal per instrument:** `RegimeGatedMomentum` wrapped in `MultiTimeframeMomentum` (the live 1d stack), constructed by the `TrendBook` adapter in `engine/scripts/run_portfolio_gate.py` with `instrument=` passed explicitly (per-instrument Bollinger cache; per-class regime eps: equity 1.5×, crypto 5×, forex 1×; crypto mean-reversion disabled).
- **Parameters** (`COMMON_PARAMS` + amendment, `carry_filter=False`): `momentum_lookback=252`, `vol_window=63`, `holding_horizon=21`, `reward_risk=1.5`, `regime_method="rule_based"`, `timeframe="1d"`, `htf_rule="1w"`, `htf_ma_window=50`.
- **Exits:** `TradeManager` managed exits (`exit_mode="managed"`): partial 50% at 1R + breakeven, partial 25% at 1.5R + lock 0.5R, ATR-chandelier trail (2×ATR, 22-bar window), squeeze tighten (1×ATR), time stop (7 daily bars if < 0.25R). Under managed exits `holding_horizon` is never consulted (gate degeneracy finding).
- **Sizing:** vol-scaled via `RiskManager.permit` — fractional-Kelly edge gate (`kelly_fraction 0.20`), ATR stop (`atr_window 14`, `atr_stop_mult 2.5`), vol-target ceiling (`target_portfolio_vol 0.10`).
- **Caps (config v5, binding):** `max_risk_per_trade 0.02`, `max_total_exposure 3.0`, `max_correlated_exposure 1.5` (|corr| ≥ 0.60 clusters, 63-day window), `max_portfolio_risk 0.065`, swing bucket 10 slots / global 12, drawdown breakers: reduce from 0.10, halt new entries at 0.20.
- **Costs (v5 per-asset-class, applied to every simulated fill):** equities 2.0 bps/side (0.5×2.0 spread + 1.0 slippage), crypto 1.25 bps/side (0.5×1.5 + 0.5), forex per-pair round-trip pips (this universe: EUR/USD class default 1.0+0.5, GBP/USD 2.4, USD/JPY 0.6 [1d pair-tf], USD/CHF 0.8, AUD/USD 3.3, USD/CAD class default, NZD/USD 2.1 [1d pair-tf]). Commission 0.
- **Universe (42):** 24 equities/ETFs (AAPL MSFT NVDA META AMZN GOOGL TSLA AMD PLTR TSM NFLX UBER SPY QQQ IWM GLD TLT XLK XLE XLF ARKK SMH SOXX XBI) + 11 crypto (BTC ETH SOL BNB XRP ADA AVAX DOGE LINK ARB SUI /USD) + 7 FX majors (EUR/USD GBP/USD USD/JPY USD/CHF AUD/USD USD/CAD NZD/USD). **MATIC/USD is excluded** (no cached 1d data at gate time; excluded explicitly in the stepper so a data fix cannot silently change the book).
- **Start equity:** £100,000 paper (engine account currency). **Start date:** 2026-07-17; seed bar 2026-07-16 (decisions computed on the most recent closed bar, marked PENDING-ENTRY for the next bar; **no backfilled history**).

## 2. Machinery and the parity guarantee

- **Stepper:** `engine/scripts/run_paper_portfolio.py` (daily CLI) driving `apex_quant/backtest/paper.py::PaperPortfolio`, a `PortfolioBacktester` subclass whose `step()` is a 1:1 port of `run()`'s loop body (exits → pending fills at next bar open → mark → sequential risk-gated decisions with provisional book).
- **Shared components (paper ≡ backtest by construction):** the gate's own `TrendBook`/`COMMON_PARAMS`/`WARMUP=250`/`MIN_BARS=300` (imported, not copied); the same `RiskManager(cfg.risk)`, `TradeManager`, `RuleBasedRegime`; the same cost mechanics (`_fill`/`_pip`/`cfg.forex_cost_components` → v5 pair costs; equity/crypto bps); the same `PointInTimeAccessor` data path (`ParquetStore` + `clean`).
- **Proof:** `engine/tests/test_paper_portfolio.py::test_stepper_matches_backtester` — day-by-day stepping reproduces `run()`'s equity curve, trades, per-instrument accounting and constraint log exactly on a 600-bar mixed-calendar synthetic panel. Seed determinism verified live (identical re-seed 2026-07-17).
- **Execution convention (engine-simulated):** decisions on bar *t*'s close fill at bar *t+1*'s open ± modelled costs — identical to the backtester. Only bars strictly before today 00:00 UTC are processed, so a same-day re-run is a strict no-op (idempotent). Weekend runs advance only crypto (365-day calendar); equity/FX Friday bars are processed Saturday.
- **Data of record:** the parquet cache topped up daily by the Yahoo adapter (tail-only fetch; frozen history is not restated; a possibly-partial last cached bar is refetched once and settled). CI runners rebuild/extend the same cache via `actions/cache`.
- **State:** local JSON `engine/data_store/paper_portfolio/state.json` (authoritative locally) + Supabase mirror `apex_paper_positions` / `apex_paper_daily` (authoritative in CI; restore path when no local file). Every decision is logged to `engine/data_store/paper_portfolio/decisions.log`.

## 3. Evaluation protocol

**(a) Weekly auto-metrics** — computed every run from the paper equity curve + closed trades via the backtester's own `compute_metrics`, stored on the latest `apex_paper_daily.metrics`: Sharpe-to-date (ann. 252), profit factor, max drawdown, expectancy/trade, win rate, plus embedded cost total. *Cost drag vs model:* fills are engine-simulated with the model's own costs, so realized-vs-model cost ratio is 1.0 by construction; the tracked quantities are cumulative embedded costs (`cost_total`) and cost as a share of gross P&L — a real-venue cost ratio only becomes measurable if/when a real venue is attached.

**(b) HALT rule:** equity drawdown ≥ **15%** from peak → the stepper sets `halted=true` (new entries blocked, exits still managed), notes the event on the daily row, and STOPS for review. Resume only via `run_paper_portfolio.py --clear-halt` after a written review note is added to this file. (This is stricter than, and layered over, the book's own 10%/20% drawdown breakers.)

**(c) REVIEW dates:** after **30** and **60** processed bars (union-calendar days — crypto weekends count, so ≈ 2026-08-15 and ≈ 2026-09-14). Review contents: weekly metrics trend, per-asset-class P&L vs the gate's shape (equities carried ~85–90% of net; FX ≈ flat), cap-binding profile vs the gate's constraint log, data/top-up health.

**(d) Graduate criteria (ALL):** ≥ 60 processed union-calendar days **and** ≥ 40 closed trades **and** realized Sharpe > 0 **and** realized-vs-model cost ratio < 1.5× (1.0 by construction while engine-simulated; binds if a real venue is attached) → eligible for *real-venue discussion only*. Graduation from paper is not validation; the gate REJECT verdict stands on the record.

**(e) Kill criteria (ANY):** realized Sharpe < −0.5 after 60 processed days; or cost ratio > 2×; or 3 consecutive weeks with zero trades AND zero signals (pipeline dead — investigate data/top-up first, then declare dead).

**(f) Config changes:** **NONE allowed.** Any change to parameters, universe, costs, sizing, caps, exits, or this protocol restarts the experiment clock from the change date and must be recorded in §6 with a reason. (Bug fixes to the *stepper plumbing* that do not change trading behavior are allowed but must be noted, with the parity test re-run.)

## 4. What would make this experiment uninterpretable

- Editing the book "just once" mid-run (see §3f) — the clock restarts, no exceptions.
- Silent data-source drift: if Yahoo top-up starts disagreeing with the cached history materially (check at reviews), note it; do not quietly switch providers.
- Filling pending entries by hand, or nudging the state file. The state file is append-only evidence.

## 5. Seed record (2026-07-17)

- Seed run processed exactly one bar (**2026-07-16**): equity £100,000.00, 0 open, **8 PENDING-ENTRY** for the next bar: AAPL long ~14.7k, MSFT short ~9.9k, META long ~8.8k, AMD long ~5.1k, PLTR short ~8.8k, TSM long ~7.4k, NFLX short ~6.5k, SPY long ~5.7k (notionals). 26 non-flat signals: 8 permitted, 18 vetoed by `max_portfolio_risk_exceeded` — the portfolio-risk cap binds from day one, consistent with the gate's constraint log.
- Second same-day run: strict no-op (idempotency). Re-seed from scratch: byte-identical state (determinism).
- Supabase mirror: **APPLIED 2026-07-17 ~15:00 BST** by the user; first daily row (2026-07-16) verified in `apex_paper_daily`.

## 6. Change log (mandatory)

| Date | Change | Reason | Clock restarted? |
|---|---|---|---|
| 2026-07-17 | Experiment seeded (bar 2026-07-16) | — | n/a (start) |
| 2026-07-17 | **#1: Book C (lookback 126) → Book D (lookback 252)** | Original book choice was made on weekend-contaminated data (12,837 phantom bars since removed). Clean-data re-run: D dominates C (Sharpe 0.97 vs 0.68, PF 1.41 vs 1.26, CPCV 14-15/15 both, expectancy ~2.5×). One-time day-1 amendment before any fill occurred; selection explicitly acknowledged (deflation ledger unchanged — no new trials). | **YES — clock restarts at the D re-seed** |
| 2026-07-17 | **#2: IBKR paper mirror attached (parallel execution-realism record; see §7)** | §3a/§3d need a realized-vs-model cost ratio, which is 1.0 by construction while engine-simulated; a real venue makes it measurable. Mirror only OBSERVES the state file and replicates fills on an IBKR **paper** account — no parameter, universe, cost, sizing, cap or exit change; engine-sim remains the experiment of record. | **NO — observation only; the book and the experiment clock are untouched** |

## 7. IBKR paper mirror (change log #2, 2026-07-17) — execution-realism record

**What it is.** A parallel, read-only mirror of this experiment onto a real venue's paper account: IBKR paper `DUQ278370` (~$1M; hard account allowlist in `engine/apex_quant/execution/ibkr_executor.py` — the executor raises unless the gateway reports exactly that account). After each daily step, `engine/scripts/run_ibkr_mirror.py` reads `state.json` and replicates **that bar's fills** — entries with `entry_time == last_processed_date`, exits with `exit_time == last_processed_date` — as MARKET DAY orders (equities queue for the next session open; crypto/FX fill immediately), waits for fills, and writes `engine/data_store/ibkr_mirror/YYYY-MM-DD.json`: per-order IBKR avg fill vs engine-sim fill, divergence bps (signed + direction-adjusted cost), commissions, and a mean/max |divergence| summary by asset class. Idempotent per bar; no Supabase writes.

**What it is NOT.** It does not alter the frozen experiment in any way: the book, parameters, universe, costs, state file and Supabase tables are untouched, and the **engine-simulated paper portfolio remains the experiment of record** for §3 graduation/kill criteria. The mirror feeds exactly one protocol quantity: the realized-vs-model cost ratio (§3a, §3d), which is 1.0 by construction until this produces data.

**Deliberate simplifications (v1):** (i) the mirror runs after the step, so its fills land ~1 bar after the model's at-open fills — recorded divergence therefore bundles execution lag + real fill quality, which is precisely the quantity being measured; (ii) stops/targets are recorded but NOT attached as venue brackets (the book's exits are TradeManager-managed daily — partials, breakeven, ATR trail — which no static bracket can follow); exits are mirrored when the engine exits; (iii) engine partial exits shrink engine units intraday and are not traded — the size drift is reported in each record's `post_run_position_check`, not hidden; (iv) IBKR crypto (Paxos) is long-only — short crypto entries are skipped and recorded as venue-unsupported divergences; (v) engine equity is GBP paper vs the USD account — units pass through 1:1 (shares/coins/base units), no FX conversion; the mirror measures fill divergence, not currency effects.
