# PRE-REGISTRATION — Book Q: portfolio-level volatility-target overlay (2026-07-23)

**Status: written BEFORE the frontier grid returned.** The 4×5 grid was launched and this
document was committed while it was still running, so the decision rule below could not have
been shaped by the outcome. **20 trials charged** (ledger 232 → 252).

Target set by the account owner: **£800–1,000/month on £100k (9.6–12% CAGR)** with
**max drawdown ≈ 11%**.

## 1. Honest disclosure of prior search

This hypothesis was NOT generated from a clean slate. Two prior searches inform it:

1. `risk_per_trade_prereg.md` swept five risk-per-trade values (all five charged, ledger 226).
   0.75% produced 10.65% ann / Sharpe 1.119 / **maxDD 14.3%** — inside the profit target but
   outside the drawdown target. That is the gap this experiment tries to close.
2. A parallel session's uncosted pandas model (`vol_targeted_claim_audit.md`) reported
   Sharpe 1.331 from volatility targeting. Audited: honest value ~0.99 after costs and a real
   risk-free rate. **The number was wrong, but the mechanism it pointed at is legitimate** —
   that is why it is being tested properly here rather than dismissed.

## 2. Mechanism — falsifiable, stated in advance

The engine already has a per-INSTRUMENT vol ceiling (`target_portfolio_vol`, step 6 of
`RiskManager.permit`). It sizes each position against that instrument's own volatility and
therefore **cannot see that ten positions have become one correlated bet**. Book-level realised
volatility is the quantity that actually predicts drawdown, and nothing was measuring it.

The overlay (`portfolio_vol_target`, step 4.6) scales every position by
`clip(target / realised_book_vol, min, max)`, where realised book vol is computed from the
equity curve over a trailing 63 bars, strictly causally.

**Why this should beat cutting risk-per-trade:** lowering risk-per-trade de-levers uniformly,
sacrificing return in calm periods where the book is safe. The overlay de-levers *only when the
book is actually volatile*. Momentum strategies crash in high-vol regimes, so the two should be
correlated and the trade-off should be better than linear.

**Falsifiable prediction, recorded in advance:** at matched CAGR, an overlay config must show
**lower forward p95 drawdown** than the no-overlay config that reaches the same CAGR. If the
overlay only moves configs along the same return/drawdown line — i.e. it is indistinguishable
from simply turning risk-per-trade down — **the mechanism is disproved** and the honest
conclusion is that vol targeting adds nothing the existing risk cap did not already do.

## 3. Grid — all 20 points charged

`max_risk_per_trade` ∈ {0.50%, 0.75%, 1.00%, 1.25%}
× `portfolio_vol_target` ∈ {off, 5%, 6%, 7%, 8%}

All runs use `slot_allocation="expected_value"` (ordering artifact removed, measured spread
0.000), gap-aware stop fills ACTIVE, per-asset-class costs applied to every fill by
`PortfolioBacktester`. Iteration window < 2025-01-01; the 2025+ holdout stays untouched.

Charging all 20 — not just the winner — is the point. Selecting the best cell of a grid and
charging one trial is how a search gets laundered into a discovery.

## 4. Gates + binding decision rule

1. **DSR > 0.95** at the full ledger count (n=252).
2. **CPCV, 15 paths**: median OOS Sharpe > 0 and >50% of paths positive.
3. **PBO** — computed and REPORTED, **not binding**. Across nine prior gates it ran 0.15–0.86
   on near-identical machinery and rejected seven. It cannot discriminate books sharing a
   signal and universe (~0.99 correlated). This is exactly that case.
4. **PAIRED TEST (binding for every A/B claim):** circular block bootstrap on the daily return
   difference vs the matched no-overlay config (`validation/paired_tests.py`, block 21,
   B=10,000, seed 42). Requires **p < 0.05**.
5. **DRAWDOWN WALL (binding):** **95th-percentile forward 1-year drawdown ≤ 11%**, bootstrapped
   from the realised return process (20,000 sims). The single backtest path is one draw and is
   NOT the test. A config with higher Sharpe but a breaching tail is a REJECT.
6. **PROFIT FLOOR:** CAGR ≥ 9.6% (£800/month on £100k), measured as **compounded CAGR**, never
   as arithmetic mean ÷ 12.

**Adopt the highest-Sharpe config satisfying ALL of 1, 2, 4, 5 and 6. If no cell satisfies both
5 and 6, the honest report is that the target is not reachable on this book** — and the
deliverable becomes the measured frontier plus what it would take to move it, not a config.

## 5. Pre-registered counter-hypotheses

- **The overlay is just a slower risk knob.** If every overlay config lands on the same
  return/drawdown curve as the no-overlay configs, it adds nothing. Tested directly by §2.
- **Re-levering is where the risk hides.** `scalar_max = 1.5` lets the book lever UP in calm
  periods. Calm periods precede vol spikes, so this may import drawdown rather than remove it.
  Reported explicitly: max and mean applied scalar for every adopted config.
- **A flat book has near-zero realised vol** and would demand unbounded leverage; the scalar cap
  is what prevents this, so the result is partly an artifact of where that cap is set. If the
  winning config sits at `scalar_max` most of the time, the overlay is not really targeting vol.

## 6. Caveats

1. In-sample, one snapshot; Yahoo re-bases adjusted prices (see `book-i-gate` — certified
   numbers do not reproduce exactly across pulls).
2. Gap-aware fills active — not comparable to any pre-2026-07-22 figure.
3. 39-instrument book; ~16 are unreachable from a UK retail IBKR account (PRIIPs). Live
   attainable return will be lower than any figure here.
4. Determinism: seed 42, two runs, identical modulo `generated_at`.

## 7. Deliverables

`scratch/frontier_vol_target.py` (grid), `scripts/run_portfolio_gate_book_q.py` (gate),
`data_store/validation/book_q_gate_2026-07-23.json`, and `data_store/book_q_gate.md` with the
verdict in the first sentence — including "target not reachable" if that is what the data says.
