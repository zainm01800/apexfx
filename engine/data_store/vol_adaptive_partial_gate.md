# GATE — VOL-ADAPTIVE FIRST PARTIAL: **ADOPT NOTHING — the certified +1R ladder stands**

**Pre-registration:** `engine/data_store/vol_adaptive_partial_prereg.md` (written BEFORE any
run; the 2 trials were recorded before execution). **Results:**
`engine/data_store/validation/vol_adaptive_partial_gate_2026-07-25.json` (+ determinism twin
`..._run2.json`). **Script:** `engine/scripts/run_portfolio_gate_vol_adaptive_partial.py`.
**Window:** ITERATION only, strictly < 2025-01-01; certified anchor (mrpt 0.01, certified
panel insertion order). **Ledger:** 269 → **271**; every DSR deflated by 271.

**Certified-anchor reproduction: EXACT** — the p1_r=1.0 baseline reproduced
book_h_gapaware_2026-07-22.json to the digit (Sharpe 0.86284, 1637 trades, final equity
292,551.34) before any challenger number was believed.

## The £/month cost of the safety (the table the experiment was run for)

In-window average monthly profit on the £100k certified book, vs baseline:

| Config | Avg £/month | Cost vs baseline | Median £/month | Worst month |
|---|---|---|---|---|
| `book_h_gold_252` (p1 at +1.0R, certified) | **£1,782.88** | — | £123.89 | −£19,672.76 |
| `..._p1_voladapt` (0.75R HIGH / 1.0R LOW vol) | £1,742.07 | **−£40.81/mo (−2.3%)** | £457.71 | −£17,000.98 |

For contrast, the REJECTED flat variants (early_partial_gate.md): −£721.76/mo (−40.5%) for
0.75R-everywhere, −£986.09/mo (−55.3%) for 0.50R-everywhere. **Vol-conditioning removed
~94% of the expectancy tax** — and still fails the binding rule, on drawdown.

## The pre-registered owner-trade rule (prereg §5, binding)

Adopt the challenger ONLY IF (1) monthly-profit cost ≤ 5%, (2) max drawdown IMPROVES
(lower), (3) win rate does not fall:

| Challenger | Cost ≤ 5%? | MaxDD improves? | Win rate not falls? | Qualifies? |
|---|---|---|---|---|
| `p1_voladapt` | yes (**2.3%**) | **NO (17.52% vs 16.32%)** | yes (+2.98pts) | **NO** |

**DECISION: ADOPT NOTHING — the certified +1R first partial stands.** (Exit code 1 by
design; the adoption rule, not the gates, is the binding criterion for this owner-trade
experiment.)

## The vol-classification split (prereg §2 rule, mechanical)

Median of the book's own annualised vol-63 series per instrument over the in-window data;
universe median **0.2944**; HIGH = strictly above. 19 HIGH / 20 LOW:

- **HIGH-vol → p1 at +0.75R (19):** NVDA 0.449, META 0.316, TSLA 0.518, AMD 0.520,
  PLTR 0.654, NFLX 0.379, UBER 0.449, XBI 0.295, and ALL 11 crypto names
  (BTC 0.630 … SUI 1.057).
- **LOW-vol → p1 at +1.0R, certified (20):** AAPL 0.237, MSFT 0.220, AMZN 0.281,
  GOOGL 0.267, TSM 0.294, the 3 Islamic UCITS (0.126–0.172), XLK/XLE/SMH/SOXX
  (0.189–0.275), SGLD.L 0.130, and ALL 7 FX majors (0.066–0.091).

No hand-picked assignments: the split is a pure function of the in-window data, computed
once before any run and held fixed for the full-window run and every CPCV fold.

## Full scoreboard (2 configs × gate metrics)

| | p1 at +1.0R (certified) | p1 vol-adaptive (0.75R HIGH) |
|---|---|---|
| Sharpe (ann.) | 0.86284 | **0.89560** |
| Profit factor | 1.3245 | **1.3836** |
| Win rate | 55.77% | **58.75% (+2.98pts)** |
| Max drawdown | **16.32%** | 17.52% (+1.20pts, WORSE) |
| Worst day (ret) | −5.09% (−£8,527) | **−4.33%** (−£9,548 £-worse) |
| Expectancy / trade | **£120.44 (1.022%)** | £113.85 (0.857%) |
| Trades | 1637 | 1702 (+65) |
| Total return (~9y) | **+192.6%** | +188.1% |
| Final equity | **£292,551** | £288,143 |
| Avg £/month | **£1,782.88** | £1,742.07 (−2.3%) |
| Median £/month / % pos months | £123.89 / 50.9% | **£457.71 / 54.6%** |
| DSR @ n=271 | 0.997 ✓ | 0.998 ✓ |
| CPCV median / frac pos | 0.048 / **15-of-15 ✓** | 0.056 / 14-of-15 |
| PBO (2-config set) | 0.773 ✗ | — |
| Gate verdict (informational) | REJECT (PBO) | REJECT (PBO) |

## The honest reading

The mechanism was right about the COST and wrong about the SAFETY:

1. **Vol-conditioning fixed the expectancy tax.** Conditioning the early trigger on
   instrument vol cut the monthly-profit cost from −40.5% (flat 0.75R) to −2.3%, INSIDE
   the pre-registered 5% budget. Win rate rose +2.98pts, Sharpe and PF actually improved
   (0.86 → 0.90, 1.32 → 1.38), the median month nearly quadrupled (£124 → £458) and
   positive months rose to 54.6%. Per-instrument P&L confirms the design: the HIGH-vol
   sleeve, where the trigger moved, gave back −£13.3k (TSLA −£7.5k, XBI −£5.9k, NVDA
   −£5.6k — trend legs scratched at +0.75R breakeven), while the book-level recycling of
   scratched positions REDISTRIBUTED +£10.6k into LOW-vol names whose triggers never
   changed (SGLD.L +£6.9k, GOOGL +£4.8k, AAPL +£4.5k — portfolio-interference effects
   under shared slot/risk caps, not trigger effects).
2. **But the drawdown still deepened (16.32% → 17.52%).** This is the second gate in a
   row where an earlier breakeven RAISED every smoothness proxy except the one that was
   made binding. Scratched high-vol positions free slots that recycle into more trades
   (1637 → 1702), and the thinner per-trade edge on the book's biggest movers leaves the
   equity curve MORE exposed in losing regimes. The worst DAY improved in return terms
   (−5.09% → −4.33%) but not in £ (−£8,527 → −£9,548, larger equity base at that point).
3. **The prereg anticipated exactly this** (§1 counter-hypothesis: "high-vol names are
   exactly where a breakeven stop at +0.75R sits inside normal noise") and made maxDD
   improvement a binding leg. The leg fails. ADOPT NOTHING.
4. **Out-of-sample it is a wash:** DSR passes for both (0.997 / 0.998 @ n=271); CPCV
   median rose (0.048 → 0.056) but with one negative path (baseline: none); PBO 0.773
   across the 2-config set fails the informational < 0.5 bar (near-degenerate by
   construction with 2 configs sharing ~100% of their universe — reported as computed,
   same caveat as every prior overlapping-family PBO; it rejects the BASELINE too, which
   is certified, so its discriminative content here is nil).

**This closes the first-partial line of inquiry.** Flat earlier triggers: REJECTED
(−40%/−55% profit, deeper drawdown). Vol-conditioned earlier trigger: cost fixed, drawdown
still deeper, REJECTED under the binding rule. The certified +1.0R first partial with
simultaneous breakeven stands as designed; if the owner wants a smoother curve, the
evidence now twice says the first partial is the wrong knob (see the W-series sizing-layer
overlays, where the cost calculus differs).

## Determinism

Full gate executed twice (seed 42): outputs **byte-identical** modulo `generated_at` and
the ledger pre-state (`n_trials_before`; the rerun dedups 271 → 271) — verified by
`engine/scratch/compare_vol_adaptive_partial_determinism.py`. Unit tests:
`tests/test_vol_adaptive_partial.py` (7 tests: certified default unchanged with no map;
mapped symbol banks 50% + moves breakeven at 0.75R; unmapped symbol keeps the certified
1R; symbol-keyed lookup for shorts; hard stop untouched below the trigger; P2/0.5R-lock
ladder intact after an adaptive P1; fixed target still caps first) + full suite
**620 passed**.

## Ledger

- **n before this gate: 269** (per early-partial gate, 2026-07-24).
- **+2** (`book_h_gold_252`, `book_h_gold_252_p1_voladapt`, kind
  `vol_adaptive_partial_gate`, mrpt 0.01, the trigger rule and resolved per-instrument
  map in params) recorded before the first run → **271**. Rerun deduped (271 → 271).

## Caveats (pre-registered and observed)

- £ figures are account-currency units on the £100k certified anchor; no FX conversion,
  same as every prior report.
- The vol class is a long-run full-window median regime per name (mechanical honesty
  trade, prereg §8); a time-varying rule would be a different experiment with its own
  prereg and ledger charges.
- LOW-vol per-instrument P&L deltas are portfolio-interference effects (shared slot/risk
  caps + compounding), not trigger effects — their triggers are byte-identical.
- PBO across 2 configs sharing ~100% of their universe has no useful discriminative power
  by construction — reported as computed (0.773).
- The frozen paper test (workflow, state.json, engine/config.yaml live sections) was not
  touched; the current live-risk setting (0.75%) is unaffected — this gate priced the
  exit ladder against the certified 1% anchor only. The `p1_r_by_instrument` parameter
  defaults to `None` (certified behaviour); nothing live references it.
