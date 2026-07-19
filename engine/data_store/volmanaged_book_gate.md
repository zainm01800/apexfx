# Gate Report — Vol-Managed Trend Super-Sleeve (Sleeve A) vs Plain Book D — 2026-07-19

**Pre-registered in:** `engine/data_store/volmanaged_book_prereg.md` (written before the run; hypothesis, exact configs, ledger commitment, PASS criteria).
**Script:** `engine/scripts/run_volmanaged_book_gate.py`. **Overlay:** `engine/apex_quant/strategies/vol_target_overlay.py` (new, documented there).
**Machine-readable output:** `engine/data_store/validation/volmanaged_book_gate_2026-07-19.json`.
**Window:** ITERATION only, strictly < 2025-01-01 (42 instruments: 24 equity/ETF + 11 crypto + 7 FX majors). Costs/caps/exits/seed unchanged (v5 per-class costs, managed exits, warmup 250, seed 42). The 2025+ holdout was not touched in any way.
**Research basis:** `docs/research/2026-07-18_beating_sharpe_1_2.md` (Barroso & Santa-Clara 2015; Daniel & Moskowitz 2016; Bongaerts et al. 2020 — documented uplift +0.1–0.3 Sharpe plain, more state-conditional).

## Bottom line

- **Gate verdict: REJECT** for `book_a_vm_252` (and for the plain baseline re-run). Both pass DSR (0.9995 / 0.9987 at n=184) and CPCV (13/15, 14/15 positive); **both fail PBO** — 0.906 across the 3 evaluated configs, 0.5088 on the 2-way plain-vs-vm headline (bar: < 0.5).
- **The uplift question: NOT the documented effect on this book.** Full-window Sharpe 0.974 → 1.059 = **+0.086**, below the pre-registered +0.1–0.3 band. Ann. vol falls 14.65% → 13.73%, but **maxDD does not improve (19.13% → 19.50%)**, profit factor is flat-to-down (1.410 → 1.397), turnover is NOT lower (14.8 → 15.0 ×/yr), and CPCV is mixed (median 0.050 → 0.060 better; positive paths 14/15 → 13/15 worse).
- **The stand-down alone does NOT help drawdown** (ablation: maxDD 19.47%, Sharpe +0.057). This book's left tail is set by the risk system's drawdown breakers, not by the signal — the redundancy hypothesis (H0) is largely confirmed, see below.

## Selection set (2 NEW trials + 1 re-run; recorded BEFORE running)

| | book_a_plain_252 | book_a_vm_252 | book_a_vm_252_standdown_only |
|---|---|---|---|
| role | Book D exact re-run (dedupes vs `book_d_multiasset_252`; NOT a new trial) | vol-target overlay + panic stand-down (NEW) | ablation: stand-down only, `vol_scale=False` (NEW; full-window diagnostic, no CPCV — as pre-registered) |
| overlay params | — | target_vol 0.10, proxy 21d (own signal-vol shadow), inst-vol median 126d, stand 1.5×, panic ret 21d | same, scaling off |

**Determinism/baseline check:** the plain re-run reproduces the 2026-07-17 clean-data gate EXACTLY (Sharpe 0.97356, 1516 trades, +438.4575%, same CPCV paths) — engine and data unchanged since that run, so this is the exact comparison baseline.

## Side-by-side (iteration window, caps binding)

| Metric | plain (Book D) | vol-managed | stand-down only |
|---|---|---|---|
| Total return (~9y) | +438.5% | +467.5% | +476.4% |
| Ann. return / ann. vol | 14.09% / 14.65% | 14.56% / 13.73% | 14.70% / 14.30% |
| **Sharpe (ann., 252)** | **0.9736** | **1.0594 (+0.086)** | **1.0310 (+0.057)** |
| Sortino / Calmar | 1.099 / 0.737 | 1.170 / 0.747 | 1.181 / 0.755 |
| **Max drawdown** | **19.13%** | **19.50%** | **19.47%** |
| Trades / win rate | 1516 / 55.9% | 1561 / 55.3% | 1537 / 55.1% |
| Profit factor | 1.4096 | 1.3973 | 1.4141 |
| Expectancy (engine) | +273.13 (1.109%/tr) | +285.39 (1.157%/tr) | +289.97 (1.089%/tr) |
| Net per trade | +299.13 | +307.05 | +313.75 |
| Max gross leverage ~ | 2.84× | 3.02× | 2.84× |
| Turnover ~ (1-way entry notional / mean equity / yr) | 14.78× | 15.02× | 15.47× |
| Instruments net positive | 31/42 | 29/42 | — |
| **DSR (n=184)** | 0.9987 ✓ | 0.9995 ✓ | (diagnostic) |
| **PBO** | 0.906 (3-way) ✗ / 0.5088 (2-way) ✗ | shared ✗ | shared ✗ |
| **CPCV median / frac +ve** | +0.050 / 14/15 ✓ | +0.060 / **13/15** ✓ | not run (pre-registered) |
| **Verdict** | REJECT | **REJECT** | — |

CPCV paths: plain `[0.055, 0.083, 0.070, 0.051, 0.088, 0.062, 0.049, 0.012, 0.028, 0.050, 0.008, 0.025, 0.035, 0.052, −0.022]`; vm `[0.071, 0.084, 0.081, 0.048, 0.089, 0.000, 0.008, −0.004, 0.002, 0.079, 0.052, 0.079, 0.060, 0.083, −0.018]` — the vm median is higher but it turns one of plain's weak-positive paths (−0.022 stays negative; +0.012/+0.008 region erodes to −0.004/+0.002).

## Overlay firing rates (full-window run)

- **Stand-down: 2090 / 24,493 non-FLAT signal evaluations = 8.5%** (signal evaluations, not trades — the book requests a signal every flat bar; 2090 blocked entry bars map to only modest trade-count changes via re-evaluation on later bars). Most stood-down: BTC/USD (118), ARKK (113), XBI (113), META (92), GBP/USD (88), SMH (86) — the high-vol growth/crypto sleeve, as the D&M construction intends.
- **Vol-target damping: 16,496 / 24,493 = 67.3%** of non-FLAT signals damped below 1× (target 0.10 vs typical signal-vol proxy; most damped: SMH, SOXX, ARKK, XBI, XLK — semis/growth, whose trend signals run hottest).
- Ablation fires the same stand-down rate (2084/24,518, 8.5%) with zero damping, as designed.

Per-asset-class P&L (plain → vm): equity 1298 tr +395.5k → 1298 tr **+378.2k**; crypto 190 tr +54.3k → 196 tr **+90.9k**; forex 28 tr +3.6k → 67 tr **+10.1k**. The Sharpe gain is mostly a crypto-sleeve effect (damping overheated BTC/sol-trend signals improved their mix); equities — the book's engine — are mildly WORSE net. FX trade count up 28→67 (stand-downs re-time entries), still immaterial to the book.

## Why the documented uplift doesn't show up here (the honest read)

1. **The book's sizing is already vol-managed at the risk layer.** Regime scaling touches every trade (regime_scale ×9238), the RiskManager's own vol-target cap binds (×27–28), drawdown breakers throttle the left tail (drawdown_reducing_scale ×903), and gross leverage pins at ~2.8–3.0× against `max_total_exposure`. Barroso & Santa-Clara's +0.4 was measured on RAW momentum with fixed sizing; this book's raw signal is already conditioned before it becomes a position. The signal-level overlay is largely **redundant plumbing on an already-governed pipe** — exactly the H0 the pre-registration named.
2. **The left tail is owned by the breakers, not the signal.** Plain maxDD 19.13% sits at the 0.20 hard-breaker neighborhood; both vm variants land at 19.5% — the stand-down (8.5% of signal bars) cannot tighten a floor the risk system sets elsewhere, and slightly higher leverage under damping (2.84→3.02×) offsets what little the overlay removes. D&M's crash-avoidance needs an unmanaged left tail to bite on; this book doesn't offer one.
3. **What the overlay does do:** cut realized vol ~0.9pt (14.65→13.73%) at +0.5pt ann. return — a modestly better mix (+0.086 Sharpe, Sortino 1.10→1.17), but with 1561 vs 1516 trades (more, not the Bongaerts lower-turnover prediction), slightly lower PF, and 2 fewer instruments net positive. At this size it is a re-mix (crypto up, equity down), not a premium.
4. **PBO says the choice itself is uncertifiable.** 0.906 (3-way) / 0.5088 (2-way): the IS-better config does not stay better across splits. As in the C/D gate, the candidates are near-identical correlated return streams, so PBO is coarse — but the rule was pre-registered as binding, and it fails. The plain book remains one gate short (PBO) with DSR now passing at n=184; the vm book is in the same place with a different mix and no drawdown benefit.

**DSR caveat, stated plainly (as in prior gates):** the DSR pass here is NOT comparable to Book D's 0.934 fail at n=150 on 2026-07-17. `sr0 = std(trial_sharpes) × E[max of n]`: this selection set's three Sharpes are tightly clustered (0.974/1.031/1.059 ann.), so the dispersion term fell ~4.6× (sr0 0.036 → 0.0076) while the count rose 150 → 184. Both books' DSRs here are computed exactly as the machinery prescribes at the honest n=184 — but the cross-run movement is a denominator-and-dispersion artifact, not new evidence about the book.

## Consequences for the max-Sharpe stack

- Sleeve A as specified does **not** earn a place over plain Book D: the gate rejects it, the uplift is below the documented band, and the drawdown mechanism is inert on this book. **Keep Book D plain as the trend sleeve.**
- If vol conditioning is re-attempted, the evidence here says the signal level is the wrong layer for THIS book — the risk layer already does it. A different lever (costs, universe, exit geometry) would be a new pre-registration.
- Standing rule unchanged: REJECT ⇒ no `--final` holdout look. The 2025+ window remains untouched.

## Ledger

- **n before: 182** → **n after: 184** (+2: `book_a_vm_252`, `book_a_vm_252_standdown_only`, recorded before the runs; `book_a_plain_252` deduped against the existing `book_d_multiasset_252` entry). All DSRs deflated by 184.

## Compute notes

- Full 42-instrument universe; 3 full-window runs (~1–2 min each with overlay replay pre-warm) + 2×15 CPCV paths + 2 PBOs ≈ 5 min on .venv-mac.
- Determinism: plain re-run byte-identical to the 2026-07-17 clean-data Book D (metrics, trade count, CPCV paths); overlay adds no RNG; PBO seeded at `cfg.seed` (42).
- Overlay point-in-time discipline: shadow/proxy/median series use only bars strictly before `t`; pre-warm replay calls the base at `s < t0` on the same fitted-per-fold construction as the trial-matrix backtest; fresh instances per CPCV fold.
- Smoke (3-instrument, `--no-ledger`) verified wiring before the recorded run: stand-down and damping fire, ablation scaling-off honored, CPCV path clean.
