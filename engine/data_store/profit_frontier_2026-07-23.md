# RESULT — can this book make £800–1,000/month at ~11% drawdown?

**Short answer: no. Not at 11% drawdown, and not at any drawdown.** The whole measured
frontier tops out around **£640/month**, and that point already carries a ~13.6% forward
drawdown. Inside an 11% wall the best is about **£515/month**.

This is the honest output of the pre-registered programme in `vol_target_overlay_prereg.md`.
Everything below runs through `PortfolioBacktester` — per-asset-class costs on every fill,
real stops, gap-aware fills, EV slot allocation. Iteration window < 2025-01-01.

## 1. First, a correction to an earlier figure

I previously quoted **"0.75% risk → 10.65% annual → £887/month."** That came from the §1
disclosure table in `risk_per_trade_prereg.md`, which was a **pre-fix diagnostic sweep** run
before EV slot allocation and gap-aware fills existed.

The gated, corrected number for the same config is **7.05% CAGR = £587/month**. It matches
`book_p_gate_2026-07-22.json` exactly. The pre-fix table overstated returns by ~50%.

## 2. The measured frontier (39 instruments, fully costed)

`scratch/frontier_vol_target.py` → `validation/frontier_vol_target.json`

| risk/trade | vol overlay | CAGR | £/month | Sharpe | fwd p95 DD | trades | cap hits |
|---|---|---|---|---|---|---|---|
| 0.50% | off | 4.95% | £413 | **0.922** | **8.2%** | 1,694 | 0 |
| 0.50% | 8% | 5.49% | £458 | 0.857 | 10.0% | 1,694 | 0 |
| 0.75% | 6% | 5.87% | £490 | 0.858 | 10.6% | 1,693 | 14 |
| 1.00% | 5% | 6.17% | **£514** | 0.855 | **11.2%** | 1,694 | 20 |
| 0.75% | off | 7.05% | £587 | 0.893 | 12.0% | 1,694 | 3 |
| 1.00% | 8% | **7.71%** | **£642** | 0.869 | 13.6% | 1,681 | 234 |
| 1.00% | off | 4.96% | £413 | 0.586 | 14.8% | 1,694 | 163 |
| 1.25% | off | 3.57% | £298 | 0.423 | 16.3% | 1,696 | 635 |

**Nothing reaches £800/month anywhere in the grid.** Max is £642/month at 13.6% drawdown.

## 3. Why — the arithmetic is decisive

Return ≈ Sharpe × volatility, and forward p95 drawdown runs ≈1.5× volatility on this return
distribution (skew −0.14, kurtosis 9.7 — fat tails).

The book's Sharpe is **~0.9**. So:

- £800/month = 9.6% CAGR. At Sharpe 0.9 that needs **10.7% vol** → **~16% drawdown**.
- 9.6% CAGR *at 11% drawdown* needs vol ≈7.3% and **Sharpe ≈1.33**.

**The target requires a ~50% Sharpe improvement (0.9 → 1.33).** No sizing configuration
produces that, because sizing cannot change Sharpe — it only slides you along the line.
Every knob tested (risk-per-trade, vol overlay, portfolio cap) is a sizing knob.

## 4. The vol-target overlay: built, tested, and DISPROVED on this book

A genuine engine feature was added — `portfolio_vol_target` (RiskManager step 4.6), which
scales the whole book by `clip(target / realised_book_vol, min, max)` from the equity curve,
strictly causally. This is the mechanism the parallel session's Sharpe-1.331 pandas model was
actually exploiting, so it deserved a real test rather than dismissal.

The prereg (§2) committed in advance to this falsification test: *at matched CAGR the overlay
must show lower forward drawdown than the no-overlay config reaching the same CAGR.*

**Result: it fails at 11 of 16 points — and at every single point below 13.5% drawdown**,
which is the entire region of interest.

| overlay config | fwd p95 DD | CAGR | no-overlay at same DD | |
|---|---|---|---|---|
| 0.50% + vt5% | 8.0% | 4.39% | 4.95% | worse |
| 0.50% + vt8% | 10.0% | 5.49% | 5.95% | worse |
| 0.75% + vt6% | 10.6% | 5.87% | 6.30% | worse |
| 1.00% + vt5% | 11.2% | 6.17% | 6.61% | worse |
| 1.00% + vt8% | 13.6% | 7.71% | 5.82% | "better" |

Best Sharpe: **0.922 without the overlay vs 0.869 with it.** The overlay costs ~0.06 Sharpe.

Its apparent wins all sit above 13.5%, where the no-overlay comparison is itself broken — at
1.00%/1.25% with the overlay off, the 6.5% portfolio cap binds 163 and 635 times and truncates
positions to whatever budget is left, collapsing Sharpe to 0.586 and 0.423. **The overlay is
not adding edge there; it is repairing damage that lower risk-per-trade also repairs, and more
cheaply.** Pre-registered counter-hypothesis #1 ("just a slower risk knob") is confirmed.

The feature is kept, defaulted **off** (`portfolio_vol_target: 0.0`), with tests. It is
correct code and a real capability — it simply does not help this particular book.

## 5. The runner exit is also dead

Previously reported as beating baseline on every metric (Sharpe 1.088) and rejected only on
PBO — which `risk_per_trade_prereg.md` §4 established cannot discriminate near-twin books.
Re-tested with the paired block bootstrap that prereg prescribed (block 21, B=10,000, seed 42):

```
sharpe_base   0.893      sharpe_new    0.870
sharpe_delta  -0.023     p_value       0.5683
95% CI        [-0.274, +0.228]
```

**Not significant, and the point estimate is negative.** The old Sharpe-1.088 result did not
survive EV slot allocation and gap-aware fills. That open question is now closed honestly —
the runner exit was never an improvement, and PBO rejecting it was accidentally correct.

## 5b. The portfolio risk cap is not the ceiling either

`scratch/frontier_portfolio_cap.py` → `validation/frontier_portfolio_cap.json`

The high-risk cells were being strangled by the 6.5% `max_portfolio_risk` cap (163 hits at
1.00%, 635 at 1.25%), so the return ceiling might have been the cap rather than the book.
Tested directly: risk-per-trade 1.00/1.50/2.00% × cap 6.5/12/20% × overlay off/6%.

| rpt | cap | overlay | CAGR | £/month | Sharpe | fwd p95 DD | cap hits |
|---|---|---|---|---|---|---|---|
| 1.00% | 6.5% | off | 4.96% | £413 | 0.586 | 14.8% | 163 |
| 1.00% | 12% | off | 6.70% | £559 | 0.743 | 14.6% | 0 |
| 1.50% | 12% | 6% | 7.30% | £608 | 0.787 | 14.7% | 0 |
| 2.00% | 6.5% | 6% | **8.44%** | **£703** | 0.824 | **16.0%** | 749 |
| 2.00% | 12% | off | 3.56% | £297 | 0.474 | 14.4% | 23 |

Relieving the cap does help the broken cells (1.00% off: 4.96% → 6.70% CAGR), confirming the
cap *was* truncating. But **Sharpe never recovers to the 0.922 of the plain 0.50% config**, and
raising the cap past 12% changes nothing at all — it stops binding there.

**Configs reaching £800/month across the entire 18-cell cap grid: zero.** Absolute maximum
found anywhere in ~40 tested configurations is **£703/month at 16.0% forward drawdown**.

## 6. What you can actually choose

| you want | config | £/month | forward p95 DD | P(breach 11% in 1yr) |
|---|---|---|---|---|
| safest / best Sharpe | 0.50%, overlay off | £413 | 8.2% | 0.8% |
| **~11% wall** | **1.00% + vt 5%** | **£514** | **11.2%** | ~5% |
| more money | 1.00% + vt 8% | £642 | 13.6% | ~13% |
| absolute max found | 2.00% + vt 6%, cap 6.5% | £703 | 16.0% | ~25% |

For a funded account the honest recommendation stays **0.50% with the overlay off**: the
highest Sharpe (0.922), zero cap truncation, and a 0.8% chance of an 11% breach in a year.

## 5c. BREADTH — the gap in the sizing frontier, and the session's most important result

`scratch/frontier_breadth_slots.py` → `validation/frontier_breadth_slots.json`

Every config above ran at `max_concurrent_trades=12`, `max_swing_slots=10`. The constraint log
shows **`timeframe_bucket_full` firing 16,921 times** — the book is refusing entries it wanted
to take, constantly. Grinold says IR ≈ IC·√breadth, so more slots should mean more Sharpe.

**The opposite happens.**

| risk/trade | slots | CAGR | Sharpe | fwd p95 DD | trades |
|---|---|---|---|---|---|
| 0.50% | **12/10 (today)** | 4.95% | **0.922** | **8.2%** | 1,694 |
| 0.50% | 20/18 | 5.45% | 0.704 | 12.8% | 2,901 |
| 0.50% | 30/28 | 3.61% | 0.460 | 14.8% | 3,671 |
| 0.50% | 39/39 | 3.78% | 0.476 | 14.9% | 3,716 |
| 0.75% | 12/10 | 7.05% | 0.893 | 12.0% | 1,694 |
| 0.75% | 39/39 | 1.36% | 0.200 | 16.3% | 3,606 |

**The extra trades unlocked by extra slots have negative edge.** Grinold's formula assumes IC
is constant across bets; here it is not. The edge is concentrated in the top-ranked candidates
and is *gone* — worse than gone — below that. The 12-slot cap has been acting as an accidental
quality filter, and the EV slot allocator is what makes it work: it fills those 12 slots with
the highest-expected-value candidates.

This is the cleanest evidence yet that **the ceiling belongs to the signal, not the plumbing** —
and it rules out "add more instruments / more positions" as a route to the target.

## 6b. A like-for-like comparison I initially got wrong

I dismissed the pandas toy's Sharpe as inflated by `rf=0` and zero costs. The `rf=0` half of
that was **not** a valid criticism of the *comparison*: `compute_metrics` also uses
`rets.mean()/rets.std()`, i.e. **the engine's Sharpe is an rf=0 number too**. On the same
convention:

| | Sharpe (rf=0) | costs |
|---|---|---|
| pandas toy | **1.269** | 2 bps per unit turnover |
| engine, best config | 0.922 | real per-asset-class fills |

The engine's cost model is genuinely applied — equity `(0.5×2.0 + 1.0)/1e4` ≈ **2 bps per
fill, ~4 bps round trip**; crypto ~9 bps RT; forex on the per-pair v5 pips model. And the toy
trades vastly more (2,218%/yr vs ~132 trades/yr). **So the toy pays far more in costs and
still shows a higher Sharpe.**

The 1.331 headline was still wrong, and the "£97,044 floor" still inverted. But the *portfolio
construction* it used — continuous inverse-vol weights on rank-selected momentum, no stops,
periodic rebalance — appears genuinely better than the engine's discrete entry/ATR-stop/target
structure. That is a **structural** difference, and it is the one family this frontier never
tested.

**Mechanism worth noting:** Sharpe scales with √(fraction of time invested). The engine caps
concurrent positions at 12 out of 39 instruments, so it is idle or partly idle much of the
time, which depresses Sharpe by construction independently of signal quality.

## 7. What would actually move the needle

Sizing is exhausted. Only these change Sharpe:

1. **A genuinely uncorrelated return stream.** The three-sleeve test measured sleeve
   correlations of ~0.20 (trend↔TOM 0.207, trend↔crypto 0.200) — that is real diversification —
   but the added sleeves were too weak to lift the total (best £374/month, DSR 0.0005). The
   diversification maths is right; the sleeves need real edge, not better weighting.
2. **Lower costs / fewer trades at equal signal.** 1,694 trades over 12.8 years is not
   turnover-bound, so this is a small lever here.
3. **A better primary signal.** This is the actual bottleneck and has been since the
   meta-labeling work. Everything since has been risk plumbing on a Sharpe-0.9 signal.

## 8. Caveats

1. In-sample, one snapshot; Yahoo re-bases adjusted prices — quote figures with this date.
2. ~16 of 39 instruments are unreachable from a UK retail IBKR account (PRIIPs). **Live
   attainable return is lower than every figure here.**
3. The 2025+ holdout remains untouched.
4. Ledger stands at 232; the 20-cell grid is charged when `run_portfolio_gate_book_q.py` runs.
