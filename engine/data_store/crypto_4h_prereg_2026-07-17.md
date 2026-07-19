# Pre-registration — 4h crypto trend sleeve (BTC/USD, ETH/USD)

**Date:** 2026-07-17 · **Status:** registered BEFORE any gate run · **Runner:** `engine/scripts/run_crypto_4h_gate.py`
**Evidence base:** `docs/research/2026-07-17_subdaily_edges_post_cost.md` (§5 cost/bar rule) · **Prior test:** `engine/data_store/intraday_candidates_2026-07-17.md`

---

## Hypothesis

**4h trend survives costs where 15m/1h cannot, per the cost/bar rule.** The research note's
viability rule: a sub-daily edge has a chance only if round-trip cost ≤ ~2–5bps and ≤ ~5–10%
of the traded bar's range. At the config-v5 crypto cost (≈2.5bps RT), cost/bar is ~17% on FX
15m, ~8% on FX 1h — both dead — but only **~1–5% of bar range on crypto 1h/4h**, the one
intraday configuration whose arithmetic survives at retail. Today's 1h US-close momentum test
(a *timing* edge, 3–10bps gross) was microscopic and rejected (DSR 0.031). This is a
**different, untested hypothesis**: slow time-series *trend* on 4h bars — ~1-week lookbacks,
multi-day holds, ~1–2 trades/week — where per-trade P&L is measured in hundreds of bps of
range, not single-digit bps, so the documented 3–10bps intraday breakeven should not bind.
Anchor literature: Liu & Tsyvinski (2021, RFS) crypto TSMOM; Hudson & Urquhart (2021);
Gerritsen et al. (2020) — all daily/weekly; 4h is the finest frequency whose cost/bar clears
the rule.

## Data (built before registration, verified)

- `engine/data_store/BINANCE_BTC_USD_4h.parquet`, `BINANCE_ETH_USD_4h.parquet`, built by
  `engine/scripts/build_binance_4h.py` from the existing 1h klines cache
  (`fetch_binance_1h.py`; Binance public API, USDT≈USD documented).
- **15,290 bars per instrument, 2018-01-01 00:00 → 2024-12-31 20:00 UTC** — iteration window
  strictly < 2025-01-01 (asserted; the 2025+ holdout is never loaded and is BURNED for the
  trend family besides).
- Bars aligned to 00:00 UTC day boundaries (6/day: 00/04/08/12/16/20), **open-time labels**
  matching the store's 1h convention (bar labeled T covers [T, T+4h)).
- Aggregation: open=first, high=max, low=min, close=last, volume=sum; hand-verified against
  the 1h source. Incomplete bins (exchange outages) **dropped, not filled**: 36 partial +
  16 empty bins dropped, matching the 27 documented outage gaps; bar accounting closes
  exactly (61,246 contributing 1h bars).

## Strategy & grid (3 configs, fixed in advance)

`RegimeGatedMomentum` (the baseline factory, `default_factory`) on 4h bars — the same
strategy family as the daily book, so a PASS ports directly. Managed exits (TradeManager),
`rule_based` regime. All configs carry `timeframe="4h"`.

| # | momentum_lookback | vol_window | holding_horizon | reward_risk | rationale |
|---|---|---|---|---|---|
| 1 (headline) | **42** | 42 | 10 | 1.5 | ≈1 week of 4h bars |
| 2 | 21 | 21 | 10 | 1.5 | ≈3.5 days |
| 3 | 84 | 84 | 10 | 1.5 | ≈2 weeks |

vol_window tracks momentum_lookback, mirroring the engine's own `default_param_grid`
convention ({63,63},{21,21},{126,126}). Lookbacks are the only swept parameter; DSR/PBO
treat the 3-config grid as the multiple-testing set, deflated by the FULL ledger count.

## Cost levels (2, fixed in advance)

- **rt2.5_config** — config v5 crypto model as-is: 1.5bps spread + 0.5bps slippage per side
  ⇒ **≈2.5bps round-trip**.
- **rt10_stress** — stressed: 8bps spread + 1bps slippage per side ⇒ **10bps round-trip**
  (the intraday research says edges are fee-fragile; a trend sleeve that needs <10bps RT to
  exist is not a retail candidate).

## Trial accounting (honesty rules)

- **12 trials = 2 instruments × 3 configs × 2 cost levels**, recorded in the shared
  TrialLedger (`data_store/validation/trial_ledger.json`) **BEFORE any validation runs** —
  cost levels are recorded as distinct trials here (stricter than the 1h campaign, where
  they shared keys), because each cost level is a distinct evaluated configuration.
- Ledger state at registration: **n = 170** → expected **n = 182** after recording
  (verified in the run log; 0 pre-existing 4h keys).
- Every DSR is deflated by the **final** ledger n, not this run's grid.
- Determinism: `seed: 42` (config.yaml), single-threaded pandas/numpy path; no network.
- Supabase posting skipped (research sweep); full per-run JSON records land in
  `data_store/validation/crypto_4h_2026-07-17/`.

## Gate criteria (unchanged system rules, per instrument × cost level)

PASS requires ALL of: **DSR > 0.95** (deflated by final n=182), **PBO < 0.5**, **CPCV median
OOS Sharpe > 0 with > 50% of paths positive**. Anything else is REJECT. Headline config
(lookback 42) is the gated config; grid mates feed PBO/DSR only.

## Reporting commitments (whatever the outcome)

Per instrument × cost level: DSR, PBO, CPCV median/frac-positive, PASS/REJECT, full-window
expectancy / profit factor / trades-per-week, and **net bps/trade vs the documented 3–10bps
intraday breakeven** (research note §1, Shen et al. breakevens). Deliverable:
`engine/data_store/crypto_4h_gate_2026-07-17.md`.

## Decision rules (pre-committed)

- **Any PASS at rt2.5_config** ⇒ candidate earns a forward-paper sleeve discussion (sizing,
  correlation vs the daily book, venue fee tier). PASS required at rt2.5 specifically;
  rt10_stress is the fragility probe, not the gate for deployment.
- **No PASS anywhere** ⇒ close the intraday line for this system at retail costs, plainly:
  15m/1h dead by cost arithmetic (research note), 1h close-momentum dead by measurement
  (today's first campaign), 4h trend dead by this gate. No further intraday grids without
  genuinely new data or a genuinely new hypothesis class.

## Known caveats (registered, not discovered)

- `bars_per_year("BTC/USD","4h")` = 6 × 365 = **2,190** — per-TF annualization handles 4h
  cleanly (audit E5 machinery; Sharpe/ann_return correct at 4h).
- `regime_config_for("4h", …)` has **no dedicated tf_scale** — 4h falls through to the daily
  value 1.0 (nearest handling: 1h uses 0.15, 1d uses 1.0). With the crypto ×5 multiplier the
  slope eps is the daily-crypto setting; if anything this makes the regime gate *stricter*
  on 4h, not looser. Not tuned post hoc either way.
- CPCV/DSR operate on per-bar equity returns with the risk layer sizing positions; per-trade
  bps are equal-weighted across trades. Both views reported.
- Binance-only venue, USDT≈USD unhedged; depeg episodes (e.g. 2023-03) inside the sample.
- Managed exits mean the TradeManager (trail/breakeven/partials/time-stop at
  holding_horizon) shapes trades — this tests the *deployable sleeve*, not the academic
  fixed-horizon bet (that was the 1h campaign's barrier-mode design, and it failed).
