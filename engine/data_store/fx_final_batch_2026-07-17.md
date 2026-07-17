# FX Final Batch — Pre-Registration + Verdicts (2026-07-17)

**Pre-registration written BEFORE any run in this batch** (ledger n=145 at write
time). Verdict sections are filled in after the runs, below the fold.

Two pre-registered questions, both on the `carry_trend_filter` factory
(`CarryTrendFilter` = RegimeGatedMomentum + negative-carry veto), the best FX
result of the research program to date:

- **Q1 — cost sensitivity.** Does carry-filtered trend on EUR/USD or USD/CHF daily
  pass the full gate at RAW-ACCOUNT costs (0.6 pip round-trip all-in, e.g. OANDA
  core pricing / IBKR-style), vs the current config v5 retail costs (EUR/USD:
  class default 1.0 pip spread + 0.5 bps slippage; USD/CHF: measured override
  0.8 pip RT)?
- **Q2 — vol-managed overlay.** Does a thin Barroso & Santa-Clara (2015, JFE) style
  volatility-management wrapper (fixed vol target + hard stand-down in vol spikes,
  per the defensible version in research report #3; Daniel & Moskowitz 2016 crash
  clustering) push EUR/USD over the full gate — at current costs and at raw costs?

## Honesty rules (binding, unchanged)

- Iteration window strictly < 2025-01-01. The 2025+ holdout is never loaded. No
  `--final`. Seed 42 (config.yaml).
- Gate (unchanged, `apex_quant/validation/report.py`): DSR > 0.95 (deflated by the
  shared ledger's FULL count), PBO < 0.5, CPCV median OOS Sharpe > 0 with > 50% of
  15 paths positive. No changes to gate math; no parameter tuning to sneak a pass.
- Every config is recorded in the shared TrialLedger
  (`data_store/validation/trial_ledger.json`) BEFORE its run — the entire batch
  plan below is recorded up front, and every run in this batch is deflated by the
  FINAL count **n = 150** (145 + 5 new), not a running subtotal.
- Budget: **≤ 6 new trials**. Planned: **5 new** (dedup by canonical JSON; the
  ledger key is `{instrument, timeframe, factory, params}` — cost scenario is an
  evaluation environment, not a new strategy configuration, exactly as recorded by
  `run_candidate_check.py` all day). 1 spare, unused.
- Costs are overridden **in memory only** by the runner
  (`cfg.asset_classes.forex.pair_rt_cost_pips[pair] = 0.6` on a deep copy of the
  config — the override IS the full RT cost, applied half per fill, slippage 0.0,
  per `AppConfig.forex_cost_components`). `config.yaml` is NOT edited.

## Q1 — cost sensitivity (factory `carry_trend`, 1d)

Grid per pair (headline first) — the headline 126 plus its two existing grid mates:

1. `{momentum_lookback:126, vol_window:63, holding_horizon:21, reward_risk:1.5, regime_method:"rule_based", timeframe:"1d"}` — headline
2. `{...same, reward_risk:2.0}` — mate
3. `{...same, holding_horizon:10, reward_risk:2.0}` — mate

Runs: EUR/USD @ 0.6 pip RT; USD/CHF @ 0.6 pip RT.

- EUR/USD: all 3 configs already ledgered (this morning's grid) → **0 new trials**.
- USD/CHF: headline already ledgered (this afternoon); the 2 mates are **new → +2**.

Side-by-side baselines at current costs (already ledgered, no re-run needed):

| Pair | Cost basis | DSR (n) | PBO | CPCV med | paths +ve |
|---|---|---|---|---|---|
| EUR/USD | 1.0 pip + 0.5 bps (class default) | 0.416 (104) | 0.987 | +0.024 | 87% |
| USD/CHF | 0.8 pip RT (config v5 override) | 0.639 (137) | n/a* | +0.016 | 73% |

\* USD/CHF was only ever run headline-only at current costs (PBO fails closed with
<2 configs); the 0.6-pip run is its first full-grid gate.

Note on the often-quoted "EUR/USD DSR 0.869": that figure came from the
headline-only rerun, where a 1-config grid gives `sr_std=0` ⇒ deflation benchmark
`sr0=0` — i.e. effectively NO multiple-testing deflation. The honest grid-deflated
EUR/USD figure at current costs is **0.416** (and will be lower still at n=150).
The 0.6-pip runs below are gated against the honest benchmark.

## Q2 — vol-managed overlay (factory `vol_managed`, EUR/USD 1d only)

New file `apex_quant/strategies/vol_managed_overlay.py` — `VolManagedCarryTrend`,
a thin wrapper around `CarryTrendFilter`:

- **Proxy (exact construction).** The wrapped strategy's own hypothetical daily
  returns are tracked by a unit-exposure shadow position: a non-FLAT base signal
  at bar `s` puts the shadow in that direction for bars `s+1 … s+holding_horizon`
  (time-stop-only approximation of the managed exits; a fresh non-FLAT signal
  replaces the shadow early; FLAT signals leave it to expire). Shadow daily return
  = shadow direction × close-to-close instrument return. The vol proxy at bar `t`
  is the annualised (×√252) sample std of the last **21** shadow daily returns
  with dates **strictly before `t`**. One-time replay pre-warm at the first
  `generate` call: the shadow is replayed over the trailing
  252+21+`holding_horizon`+buffer bars before `t₀` (each replayed signal is
  `base.generate(pit, s)` at `s < t₀` — strictly point-in-time; replayed signals
  are only accepted when the replayed shadow is flat, mirroring live sequencing).
- **Scale.** `f = min(1, target_vol / proxy)`, `target_vol` defaulting to the
  proxy's own trailing **252-day median** (self-calibrating, no tuned constant).
  Signals carry no size in this engine — the only size lever is `probability` via
  fractional Kelly — so the overlay remaps `p → p'` with
  `p' = (f·full_kelly(p,b)·b + 1)/(b+1)`, which yields EXACTLY `f ×` the Kelly
  risk fraction at unchanged stop/target geometry (risk layer stays supreme;
  downstream caps can still bind). Overlay is inert (pass-through) until 252
  valid daily proxy values exist or while proxy/median ≤ 0.
- **Stand-down.** When `stand_down` is on and `proxy > 1.5 × 252-day median`, the
  signal is forced FLAT (hard veto, Daniel & Moskowitz crash-clustering logic).

Grid (headline first), all `{momentum_lookback:126, vol_window:63, holding_horizon:21, regime_method:"rule_based", timeframe:"1d"}` plus:

1. `{reward_risk:1.5, stand_down:true}` — headline (damp + stand-down)
2. `{reward_risk:1.5, stand_down:false}` — damp only (isolates the stand-down)
3. `{reward_risk:2.0, stand_down:true}` — grid mate

Runs: EUR/USD @ current costs (1.0 pip + 0.5 bps) AND @ 0.6 pip RT (same ledger
keys; second cost pass adds 0). **New trials: +3.**

## Trial budget summary

| Run | New trials | Deduped |
|---|---|---|
| Q1 EUR/USD @ 0.6 | 0 | 3 |
| Q1 USD/CHF @ 0.6 | 2 | 1 |
| Q2 EUR/USD overlay @ 1.0 | 3 | 0 |
| Q2 EUR/USD overlay @ 0.6 | 0 | 3 |
| **Total** | **5 ≤ 6** | |

Ledger: **n=145 before → n=150 after** (planned). Every DSR in this batch deflated
by n=150.

## Commitment

If a configuration passes the FULL gate (all three legs, at n=150 deflation), it
earns a user-approved single `--final` holdout look — stated explicitly in the
verdicts. If nothing passes: FX directional iteration at retail stops here.

---

# VERDICTS (filled in after the runs)

**Ledger: n=145 before → n=150 after (+5 new trials, exactly the pre-registered
set; budget ≤ 6 respected, 1 spare unused). Every DSR below deflated by n=150.**
Seed 42; determinism verified — the diagnostic headline backtest of each slice was
run twice in-process, equity series identical in all 4 slices. Overlay sanity suite
(`scratch/sanity_vol_managed.py`): Kelly-remap identity exact (max err 8e-17 over
2000 random p/b/f), inert pass-through exact, no-lookahead verified both against
future bars and against the current bar, carry vetoes intact inside the wrapper.

## Q1 — cost sensitivity: does either pair pass the full gate at 0.6 pip? NO.

| Pair | Costs | DSR (n) | PBO | CPCV med | paths +ve | Verdict |
|---|---|---|---|---|---|---|
| EUR/USD | 1.0 pip + 0.5 bps (class default) | 0.416 (104) | 0.987 | +0.024 | 87% | REJECT |
| EUR/USD | **0.6 pip RT (raw)** | **0.337 (150)** | **0.997** | **+0.025** | **87%** | **REJECT** |
| USD/CHF | 0.8 pip RT (config v5 override) | 0.639* (137) | n/a* | +0.016 | 73% | REJECT |
| USD/CHF | **0.6 pip RT (raw)** | **0.288 (150)** | **0.945** | **−0.002** | **47%** | **REJECT** |

\* USD/CHF at 0.8 pip was run headline-only: its DSR 0.639 has deflation benchmark
sr0=0 (1-config grid ⇒ sr_std=0) and PBO fails closed — it is NOT comparable to the
honest 3-config numbers. The 0.6-pip run is its first full-grid gate.

Headline backtests (managed exits, warmup 250, window 2014-01-01 → 2024-12-31):

| Pair @ cost | trades | Sharpe | total ret | maxDD | PF | expectancy |
|---|---|---|---|---|---|---|
| EUR/USD @ 1.0+0.5bps | 92 | −0.07 | −2.2% | 10.9% | 0.92 | −23.7 |
| EUR/USD @ 0.6 RT | 77 | +0.33 | +7.2% | 4.1% | 1.42 | +87.1 |
| USD/CHF @ 0.8 RT | 85 | −0.33 | −8.4% | 11.5% | 0.67 | −99.3 |
| USD/CHF @ 0.6 RT | 87 | −0.08 | −2.2% | 8.5% | 0.91 | −25.3 |

Read: raw costs transform the HEADLINE economics (EUR/USD flips positive,
USD/CHF improves ~4x) but move the GATE barely at all. The gate's blockers were
never the per-trade cost drag: they are multiple-testing deflation (DSR at n=150
needs ann. Sharpe ≫ 0.33 to survive) and in-sample selection overfit (PBO ≈ 1.0 —
within its own 3-config grid, the IS-best config is at/below the OOS median in
~100% of CSCV splits). Cheaper fills cannot fix either. EUR/USD's CPCV leg passes
at both cost levels; USD/CHF fails all three legs even at raw costs.

## Q2 — vol-managed overlay: does it push EUR/USD over the full gate? NO.

| Config | Costs | DSR (150) | PBO | CPCV med | paths +ve | Verdict |
|---|---|---|---|---|---|---|
| carry-only (Q1 ref) | 1.0 + 0.5 bps | 0.416 (104) | 0.987 | +0.024 | 87% | REJECT |
| carry-only (Q1 ref) | 0.6 RT | 0.337 | 0.997 | +0.025 | 87% | REJECT |
| **vol-managed** | 1.0 + 0.5 bps | **0.804** | **0.825** | **+0.033** | **100%** | **REJECT** |
| **vol-managed** | 0.6 RT | **0.815** | **0.825** | **+0.033** | **100%** | **REJECT** |

Headline overlay backtest (55 trades both cost levels): Sharpe **+0.52/+0.53**,
total +9.9%/+10.1%, maxDD **2.4%**, PF 2.02/2.05, win 65%. Overlay mechanics on
the full window: 216 non-FLAT base signals → **161 stand-downs** (74%), 11 damped
entries, 55 trades. The stand-down is the dominant mechanism; because the strategy
is part-time, its own-vol series is bimodal (zero when flat, instrument vol when
in-market), so the 1.5× median rule fires on most re-entries after active
stretches — exactly the Daniel & Moskowitz post-spike rebound zone. Note the
overlay is nearly cost-INSENSITIVE (≈5 RT/yr): 0.6 vs 1.0 pip changes Sharpe by
~0.01.

Read: the overlay does what the literature says — it works on the economics where
it matters (Sharpe −0.07 → +0.52 at retail costs, maxDD 10.9% → 2.4%, CPCV
positive paths 87% → 100%, median OOS up) and it moves the gate in the right
direction on every leg (DSR ~0.4 → 0.80–0.82, PBO 0.99 → 0.83). It still fails:
DSR 0.82 < 0.95 under honest n=150 deflation, and PBO 0.83 says the headline
remains the lucky pick of its own small grid more often than not. This is the
closest anything in the FX program has come to the bar, and it is still short.

## Final answer

**Is there ANY certifiable FX configuration at retail costs? NO. At raw-account
(0.6 pip RT) costs? Also NO.** Nothing in this batch — or anywhere in the
145→150-trial ledger — passes the full gate (DSR > 0.95 with honest deflation,
PBO < 0.5, CPCV majority-positive). No configuration earns a `--final` holdout
look; the holdout stays blind.

**FX directional iteration at retail stops here.** The evidence is consistent
across every angle tried today (7 majors × carry-filtered trend, carry tilt book,
XS momentum book, combined stack, raw-cost sensitivity, vol management): there is
a small, real-looking OOS edge in EUR/USD carry-filtered slow trend (best with the
vol overlay: 15/15 positive CPCV paths, Sharpe ~0.5, maxDD 2.4%) but it is too
small to survive honest multiple-testing deflation and selection-overfit control
at ANY cost model we can plausibly get as retail. The vol-management layer is the
one mechanism that demonstrably improved risk-adjusted economics — it belongs on
non-FX sleeves where the underlying edge is larger, not as a reason to keep
digging in FX.

## Artifacts

- This file: `data_store/fx_final_batch_2026-07-17.md` (pre-reg above the fold,
  written before any run)
- New strategy: `apex_quant/strategies/vol_managed_overlay.py` (`VolManagedCarryTrend`)
- Runner: `scripts/run_fx_final_batch.py` (cost override in memory only;
  config.yaml untouched; ledger recorded before runs; local JSON only, no
  Supabase posts in this batch)
- Raw results: `data_store/validation/fx_final_batch_2026-07-17.json`
- Sanity suite: `scratch/sanity_vol_managed.py` (all checks passed)
- Ledger: `data_store/validation/trial_ledger.json` n=150
- Holdout: untouched — `holdout_looks.log` gains no entries from this batch


---

# CLEAN-DATA RE-RUN (2026-07-17, later same day)

Re-run of the **Q2 vol-managed overlay gate only**, after the data-layer rebuild
(phantom weekend/Sunday-stub bars removed) and the engine fixes
(regime/annualization/BB cache/time-stops/BE buffer). Same pre-registered 3-config
grid (`vol_managed` on EUR/USD 1d: headline 126/63/21/1.5 + stand-down off +
rr 2.0), both cost levels. **No new ledger entries** (identical configs dedup;
ledger n=150 before AND after; DSR deflated by n=150). Gate-math files
(`validation/report.py`, `metrics.py`, `cpcv.py`) verified untouched by the engine
fixes. Iteration window strictly < 2025-01-01; no holdout contact; seed 42.

**Data-integrity note (found during the re-run):** `_load_history`'s adapter
gap-fill fetches the full 2014→2025 range whenever the store cache does not cover
the start, and it **re-injects phantom Sunday bars** (they are distinct calendar
dates, so the date-dedup cannot drop them) — 567 of them in the merged EUR/USD
frame, MORE than the original morning run had (the rebuilt store no longer masks
them). A first re-run pass on that frame (3,326 bars) gave DSR 0.492 / PBO 0.314 /
CPCV +0.022 / 67% and was **discarded**. The runner now filters weekend bars from
the merged frame to match the rebuilt store's convention (2,759 bars, 0 weekend
bars: 414 adapter-prefix bars 2014–2015 + 2,333 rebuilt-store bars 2016→2024,
plus 12 boundary/gap-fill bars). Every number below is on that frame. The earlier
gate batches (this morning's included) all ran on adapter-filled frames containing
Sunday stubs — that caveat now attaches to every pre-rebuild result, including the
"15/15 positive paths" headline below.

## Side-by-side: morning (dirty data, pre-fix engine) vs clean data + fixed engine

| Costs | Run | DSR (n=150) | PBO | CPCV med | paths +ve | Verdict |
|---|---|---|---|---|---|---|
| 1.0 pip + 0.5 bps | morning | 0.804 | 0.825 | +0.033 | 100% (15/15) | REJECT |
| 1.0 pip + 0.5 bps | **clean** | **0.258** | **0.948** | **+0.006** | **67% (10/15)** | **REJECT** |
| 0.6 pip RT | morning | 0.815 | 0.825 | +0.033 | 100% (15/15) | REJECT |
| 0.6 pip RT | **clean** | **0.284** | **0.945** | **+0.007** | **67% (10/15)** | **REJECT** |

Headline backtest (managed exits, warmup 250):

| Costs | Run | trades | Sharpe | total ret | maxDD | PF |
|---|---|---|---|---|---|---|
| 1.0 + 0.5 bps | morning | 55 | +0.52 | +9.9% | 2.4% | 2.02 |
| 1.0 + 0.5 bps | **clean** | 52 | **−0.16** | **−3.1%** | **4.2%** | **0.80** |
| 0.6 RT | morning | 55 | +0.53 | +10.1% | 2.4% | 2.05 |
| 0.6 RT | **clean** | 52 | **−0.15** | **−3.0%** | **4.1%** | **0.81** |

Clean-run overlay mechanics: 132 non-FLAT base signals → 80 stand-downs (61%),
17 damped, 52 trades; determinism re-verified (identical equity across two
in-process runs in both slices).

## Answers

- **Does the 15/15-positive-paths result survive clean data + the fixed engine?
  NO.** It collapses to 10/15 (67%) at both cost levels, the median OOS Sharpe
  shrinks ~5× (+0.033 → +0.006/+0.007), and the headline economics invert
  (Sharpe +0.52 → −0.16, PF 2.02 → 0.80). The strong morning result was
  materially an artifact of phantom Sunday bars and/or the pre-fix engine — the
  kind of thing the CPCV/DSR machinery exists to catch, and it just did.
- **Does the gate verdict change? NO.** REJECT before, REJECT now — and weaker on
  every leg: DSR 0.26–0.28 (vs 0.80–0.82), PBO back to ≈ 0.95 (vs 0.83), headline
  Sharpe negative. The CPCV leg still technically passes (median > 0, 67% > 50%),
  which only restates what the morning already showed: a tiny positive-leaning
  OOS tendency, nowhere near a certifiable edge.

**Final answer stands, and is now stronger:** there is no certifiable FX
configuration at retail costs or at raw-account costs. FX directional iteration
at retail stops here. Nothing earns a `--final` holdout look.

Artifacts of this re-run: slices `q2_overlay_current__clean_2026-07-17` and
`q2_overlay_raw__clean_2026-07-17` in
`data_store/validation/fx_final_batch_2026-07-17.json`; runner
`scripts/run_fx_final_batch.py` gained a `--tag` option and the weekend-bar
filter described above (original morning slices preserved untouched).
