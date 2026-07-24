# GATE — EARLIER FIRST PARTIAL: **ADOPT NOTHING — the certified +1R ladder stands**

**Pre-registration:** `engine/data_store/early_partial_prereg.md` (written BEFORE any run;
the 3 trials were recorded before execution). **Results:**
`engine/data_store/validation/early_partial_gate_2026-07-24.json` (+ determinism twin
`..._run2.json`). **Script:** `engine/scripts/run_portfolio_gate_early_partial.py`.
**Window:** ITERATION only, strictly < 2025-01-01; certified anchor (mrpt 0.01, certified
panel insertion order). **Ledger:** 266 → **269**; every DSR deflated by 269.

**Certified-anchor reproduction: EXACT** — the p1_r=1.0 baseline reproduced
book_h_gapaware_2026-07-22.json to the digit (Sharpe 0.86284, 1637 trades, final equity
292,551.34) before any challenger number was believed.

## The £/month cost of the safety (the table the experiment was run for)

In-window average monthly profit on the £100k certified book, vs baseline:

| Config | Avg £/month | Cost vs baseline | Median £/month | Worst month |
|---|---|---|---|---|
| `book_h_gold_252` (p1 at +1.0R, certified) | **£1,782.88** | — | £123.89 | −£19,672.76 |
| `..._p1_075` (p1 at +0.75R) | £1,061.12 | **−£721.76/mo (−40.5%)** | £294.88 | −£16,038.12 |
| `..._p1_050` (p1 at +0.50R) | £796.79 | **−£986.09/mo (−55.3%)** | £656.43 | −£14,393.21 |

**The safety costs 4–5.5× the pre-registered 10% budget.** That alone fails both
challengers on the owner-trade rule — and the drawdown story below fails them again.

## The pre-registered owner-trade rule (prereg §4, binding)

Adopt the EARLIEST config with (1) monthly-profit cost ≤ 10%, (2) win rate ≥ +2pts,
(3) max drawdown not worse:

| Challenger | Cost ≤ 10%? | Win rate ≥ +2pts? | MaxDD not worse? | Qualifies? |
|---|---|---|---|---|
| `p1_050` | **NO — 55.3%** | yes (+12.60pts) | **NO (18.21% vs 16.32%)** | **NO** |
| `p1_075` | **NO — 40.5%** | yes (+4.59pts) | **NO (18.36% vs 16.32%)** | **NO** |

**DECISION: ADOPT NOTHING — the certified +1R first partial stands.** (Exit code 1 by
design; the adoption rule, not the gates, is the binding criterion for this owner-trade
experiment.)

## Full scoreboard (3 configs × gate metrics)

| | p1 at +1.0R (certified) | p1 at +0.75R | p1 at +0.50R |
|---|---|---|---|
| Sharpe (ann.) | **0.86284** | 0.69586 | 0.60758 |
| Profit factor | **1.3245** | 1.2726 | 1.2147 |
| Win rate | 55.77% | 60.36% (+4.59pts) | 68.37% (+12.60pts) |
| Max drawdown | **16.32%** | 18.36% (worse) | 18.21% (worse) |
| Worst day (ret) | **−5.09%** (−£8,527) | −5.47% (−£6,928) | −5.12% (−£6,709) |
| Expectancy / trade | **£120.44 (1.022%)** | £66.56 (0.673%) | £42.22 (0.656%) |
| Trades | 1637 | 1781 | 2109 |
| Total return (~9y) | **+192.6%** | +114.6% | +86.1% |
| Final equity | **£292,551** | £214,601 | £186,053 |
| % positive months | 50.9% | 55.6% | 57.4% |
| DSR @ n=269 | **0.958 ✓** | 0.872 ✗ | 0.798 ✗ |
| CPCV median / frac pos | **+0.048 / 15-of-15 ✓** | +0.036 / 13-of-15 | +0.043 / 13-of-15 |
| PBO (3-config set) | 0.1555 ✓ | — | — |
| Gate verdict (informational) | **PASS** | REJECT (DSR) | REJECT (DSR) |

## The honest reading

The mechanism works exactly as hypothesised — earlier banking converts would-be −1R
reversals into small wins, and win rate climbs hard (55.8% → 60.4% → 68.4%). **But the
safety the owner wanted is not what earlier partials deliver:**

1. **The profit engine is the right tail, and the early breakeven amputates it.** The
   average month collapses (−40%/−55%) while the *median* month actually improves and
   positive months rise to 57.4%: the book makes its money in a minority of big
   trend-capture months, and a breakeven stop set at +0.5R/+0.75R scratches those trades
   out in noise before they reach +1.5R. Expectancy per trade falls −45%/−65%.
2. **Drawdown gets WORSE, not better (16.3% → 18.4%/18.2%).** Scratched positions free
   slots that recycle into more trades (1637 → 1781 → 2109), and the thinner per-trade
   edge leaves the equity curve *more* exposed in losing regimes, not less. The worst
   day in return terms is flat-to-worse (−5.09% → −5.47%/−5.12%); the £ improvement in
   the worst day/month is mostly the smaller equity base, not protection.
3. **Out-of-sample it degrades too:** both challengers fail DSR at the full ledger count
   (0.872, 0.798 < 0.95) and show 2 negative CPCV paths each (baseline: none). PBO
   0.1555 across the set passes (with the standing overlapping-family caveat, prereg §4).

If the owner wants a smoother curve, the honest answer from this gate is that the first
partial is the wrong knob: what it buys is *feel-good* win rate at a proven cost of
~£700–£1,000/month on £100k and *deeper* drawdowns. Vol-targeting / risk-throttle
overlays (W-series) address smoothness at the sizing layer, where the cost calculus is
different. The certified ladder stands.

## Determinism

Full gate executed twice (seed 42): outputs **byte-identical** modulo `generated_at` and
the ledger pre-state (`n_trials_before`; the rerun dedups 269 → 269) — verified by
`engine/scratch/compare_early_partial_determinism.py`. Unit tests:
`tests/test_early_partial.py` (7 tests: certified default unchanged at p1_r=1.0; early
trigger banks 50% + moves breakeven at 0.75R/0.5R; the reversal-conversion mechanism;
hard stop untouched below the trigger; P2/0.5R-lock ladder intact after early P1; fixed
target still caps first) + full suite **613 passed**.

## Ledger

- **n before this gate: 266** (per earnings-blackout gate, 2026-07-24).
- **+3** (`book_h_gold_252`, `book_h_gold_252_p1_075`, `book_h_gold_252_p1_050`, kind
  `early_partial_gate`, mrpt 0.01, p1_r in params) recorded before the first run →
  **269**. Rerun deduped (269 → 269).

## Caveats (pre-registered and observed)

- £ figures are account-currency units on the £100k certified anchor; no FX conversion,
  same as every prior report.
- PBO across 3 configs sharing ~100% of their universe has limited discriminative power
  by construction — reported as computed (0.1555).
- The frozen paper test (workflow, state.json, engine/config.yaml live sections) was not
  touched; the current live-risk setting (0.75%) is unaffected — this gate priced the
  exit ladder against the certified 1% anchor only.
