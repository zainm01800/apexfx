# W1 GATE — ORDER-INVARIANT RISK ALLOCATION: **REJECTED as a certified change (C1/C2 HOLD — the mechanism works exactly; C3 fails on the gates leg: DSR 0.946 < 0.95, PBO 0.5395 ≥ 0.5)**

**Pre-registration:** `engine/data_store/order_invariant_prereg.md` (written BEFORE any
challenger run; the 2 trials were recorded before execution). **Results:**
`engine/data_store/validation/order_invariant_gate_2026-07-25.json`. **Script:**
`engine/scripts/run_portfolio_gate_order_invariant.py`. **Window:** ITERATION only, strictly
< 2025-01-01; certified anchor (mrpt 0.01, EQUITY_CORE panel order, gap-aware engine)
reproduced EXACT by the control (Sharpe 0.86284, 1637 trades, equity 292,551.34).
**Ledger:** 271 → **273**; every DSR deflated by 273.

## What was tested

The 2026-07-22 ordering audit measured that the certified Sharpe is partly an artifact of
panel iteration order: same-bar candidates are evaluated in dict-insertion order and the
scarce resources (10-slot swing bucket, 12-position cap, 6.5% portfolio-risk budget) are
consumed first-come-first-served. The challenger replaces that with (a) EV-ranked
selection (`p·b−(1−p)`, tie-break name) for the hard count caps, and (b) a
**simultaneous-γ** portfolio-risk cap: every candidate sized independently, then one
factor `γ = budget / implied_total` applied uniformly to every position's open risk
(candidates scaled, open positions trimmed at the next open, stops/targets untouched).
Both flags default OFF; certified behaviour byte-identical (anchor re-verified).

## The pre-registered scoreboard

| | control (sequential) | challenger (simultaneous-γ) |
|---|---|---|
| Sharpe — certified order | 0.86284 (anchor, exact) | **0.72859** |
| Sharpe — shuffle 101/202/303 | 0.54870 / 0.59143 / 0.50496 | **0.72859 / 0.72859 / 0.72859** |
| Sharpe spread across 4 orderings | **0.3579** (the artifact) | **0.0000** (invariant) |
| Trades across 4 orderings | 1637/1683/1670/1652 (4 distinct) | 1694 (1 distinct) |
| Final equity across 4 orderings | 292,551 / 175,180 / 173,569 / 160,632 | 222,846 (all four) |
| Profit factor | 1.3245 | 1.2345 |
| Win rate | 55.77% | 55.08% |
| Max drawdown | 16.32% | 18.06% |
| Worst day / worst trade | −5.09% / −3,294 | −4.91% / −3,215 |
| Expectancy / trade | +120.44 (+1.022%) | +73.83 (+1.263%) |
| Avg monthly P&L | +1,783 | +1,137 |
| γ binds / trims | — | 62 / 274 |
| DSR @ n=273 | 0.981 ✓ | **0.946 ✗ (< 0.95)** |
| CPCV median / frac positive | +0.048 / 15-of-15 ✓ | +0.044 / 15-of-15 ✓ |
| PBO (2-config set) | — | **0.5395 ✗ (≥ 0.5)** |

**Pre-registered rule:** C1 (challenger order-invariant: |ΔSharpe| ≤ 1e-9, trades exact,
|Δequity| ≤ 1e-9 across certified + 3 shuffles) **HOLDS — spread exactly 0.0**.
C2 (control order-dependent on the same shuffles) **HOLDS — spread 0.358 Sharpe, 45%
equity, 4 distinct trade counts**. C3 (Sharpe/PF vs control shuffle-median within noise
AND all three gates pass): Sharpe 0.729 ≥ 0.549−0.05 ✓ and PF 1.235 ≥ 1.163−0.10 ✓ both
hold, but the gates leg **FAILS** (DSR 0.946, PBO 0.5395). **Verdict: REJECT.**

## The honest reading

The primary claim is **proven, not supported**: on identical data, seed, and code, the
challenger's full metric set is *exactly* invariant to panel order (zero spread), while
the certified rule swings 0.505 ↔ 0.863 — the defect is removed, not damped. The
mechanism is real in the book: γ bound on 62 decision bars and 274 open-position trims
executed.

On performance the right comparison is the honest centre of the sequential distribution,
not its lucky top (pre-registered in §4): the challenger **beats every one** of the
control's three shuffles (0.729 vs 0.505/0.549/0.591) and the shuffle median by +0.18
Sharpe, with a better worst day and comparable worst trade. It trails only the certified
ordering itself (0.863) — the ordering the audit showed was the best of seven tried,
i.e. the number that is itself selection luck. The per-trade picture is consistent with
the design: slightly more trades (1694 vs 1637), higher per-trade expectancy in %
(1.263% vs 1.022%), lower absolute expectancy (smaller average size — the trim ratchet
costs ~£650/month against the lucky ordering but *gains* ~£500/month against the shuffle
centre).

What the gates certify: both configs pass DSR-adjacent evidence (CPCV 15-of-15 positive,
overlapping medians +0.048/+0.044) but the challenger's deflated Sharpe lands at 0.946 —
0.004 under the 0.95 bar at 273 trials — and the 2-config PBO reads 0.5395 (marginal fail;
the pre-registered caveat applies: ~100% universe overlap limits PBO's discrimination by
construction). The pre-committed rule makes no exception for "invariance proven and the
gates missed by a hair", so the mode is **REJECTED as a certified change — recorded, not
adopted. The certified sequential book remains the book of record.**

**Note for the owner:** the certified 0.86284 now stands measured as the top of an
order-dependent distribution whose median is ~0.55. The order-invariant mode delivers
0.729 with *zero* order risk and 15-of-15 positive CPCV paths — adopting it as a
*robustness policy* (as the 0.75% risk-per-trade was adopted un-gated on 2026-07-23) is a
defensible owner call on this evidence, but it is a policy call, not a gate-certified
improvement, and is flagged as such here.

## Pre-registered caveats, restated where they bound the result

- γ events are rare (62 bars); the dominant order-dependent cap is the 10-slot swing
  bucket, addressed by EV ranking. The two effects cannot be separated within the
  2-config budget (prereg §6) — separation is a new pre-registration.
- Trims pay spread+slippage like any fill and never re-lever: the trim ratchet is the
  honest cost of invariance, measured in the numbers above.
- Gross-exposure and correlation caps remain sequential in EV-ranked order; neither
  binds in the certified book (prereg §2b).
- Shuffle runs are the same 2 configs on permuted inputs — property measurement, no
  ledger charge (prereg §4).

## Determinism

Full gate executed twice (seed 42): metrics payload **byte-identical** modulo
`generated_at` and the ledger bookkeeping line (first pass 271 → 273, second pass dedups
273 → 273); control reproduced the certified anchor exactly in both passes, and the
challenger's four orderings were exactly equal in both. Unit tests:
`tests/test_order_invariant.py` (certified defaults preserved; defer switch skips the
sequential cap; proportional 0.8125 split vs sequential clamp on synthetic twins;
order-invariance on a scenario where the sequential path is order-dependent; trim
accounting) + full suite green.

## Ledger

- **n before this gate: 271**
- **+2** (`book_h_gold_252_seq`, `book_h_gold_252_simul`, kind `order_invariant_gate`,
  recorded before the first run) → **273**. Rerun deduped (273 → 273).
