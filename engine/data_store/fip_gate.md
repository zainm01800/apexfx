# GATE — FIP / INFORMATION-DISCRETENESS ENTRY GATE: **REJECTED — certified ungated entries stand**

**Pre-registration:** `engine/data_store/fip_prereg.md` (written BEFORE any run; the 2
trials were recorded before execution). **Results:**
`engine/data_store/validation/fip_gate_2026-07-28.json` (+ determinism twin
`..._twin.json`). **Script:** `engine/scripts/run_portfolio_gate_fip.py`.
**Window:** ITERATION only, strictly < 2025-01-01; certified anchor (mrpt 0.01, certified
panel insertion order). **Ledger:** 296 → **299** (a concurrent commission-measurement
trial landed 296 → 297 before this gate's recording, which took 297 → 299; prereg §6
amendment); every DSR deflated by 299.

**Certified-anchor reproduction: EXACT** — the control reproduced
book_h_gapaware_2026-07-22.json to the digit (Sharpe 0.86284, 1637 trades, final equity
292,551.34) before any challenger number was believed.

## The mechanism works at the trade level — and the gate still fails

The pre-registered mechanism diagnostic (control trades split by their ID half at entry)
**confirms the Da–Gurun–Warachka effect inside this book**:

| Cohort at entry | Trades | Expectancy | Win rate | Net pnl |
|---|---|---|---|---|
| Continuous (ID ≤ median — kept) | 1,065 | **£139.50** | **57.28%** | £148,564 |
| Discrete (ID > median — vetoed) | 572 | £84.97 | 52.97% | £48,601 |

Continuous-information entries really are the better trades (+£54.5/trade, +4.3pts win
rate). **But the gate that keeps only those entries loses money systemically**, because
this book is slot-constrained, not signal-constrained: `timeframe_bucket_full` binds
~18k times, so 10,199 vetoes do not remove 10,199 entries — the freed slots recycle into
*other* entries (trade count: 1,637 → 1,632, essentially unchanged), and the recycled
entries are far worse than the vetoed ones. Expectancy per trade falls −41%
(£120.44 → £71.33); the average month drops −£709 (−40%).

## The pre-registered adoption rule (prereg §5, binding)

Adopt iff ALL four legs hold: per-path expectancy improvement on ≥12/15 CPCV paths,
DSR > 0.95 at n=299, PBO < 0.5, full-window Sharpe drop ≤ 0.05:

| Leg | Required | Observed | Pass? |
|---|---|---|---|
| CPCV expectancy paths improved | ≥ 12/15 | **5/15** | **NO** |
| DSR (challenger, n=299) | > 0.95 | **0.853** | **NO** |
| PBO (2-config set) | < 0.5 | 0.25125 | yes |
| Sharpe drop | ≤ 0.05 | **0.185** | **NO** |

**DECISION: ADOPT NOTHING — REJECTED.** Three of four legs fail; the kill rule engages.
(Control gate verdict: PASS — DSR 0.955 > 0.95, CPCV median +0.048, 15/15 paths positive.)

## Full scoreboard (baseline vs challenger)

| | `fip_control_252` (certified) | `fip_gate_252` (challenger) |
|---|---|---|
| Sharpe (ann.) | **0.86284** | 0.67767 (−0.185) |
| Profit factor | **1.3245** | 1.2650 |
| Win rate | **55.77%** | 55.02% |
| Expectancy / trade | **£120.44 (1.022%)** | £71.33 (0.708%) |
| Max drawdown | **16.32%** | 17.86% (worse) |
| Worst day (ret) | −5.09% (−£8,527) | **−4.90% (−£6,297)** |
| Worst month | **−£19,673** | −£22,425 (worse) |
| £-per-month (avg) | **£1,782.88** | £1,074.19 (−40%) |
| Trades | 1,637 | 1,632 (10,199 vetoes — slots recycle) |
| Total return (~9y) | **+192.6%** | +116.0% |
| Final equity | **£292,551** | £216,013 |
| CPCV median / frac pos | +0.048 / 15-of-15 | +0.049 / 14-of-15 |
| DSR @ n=299 | **0.955 ✓** | 0.853 ✗ |
| PBO (2-config set) | 0.25125 ✓ | — |

## The honest reading

1. **The FIP mechanism is real in this book** (trade-level split: £139.50 vs £84.97) —
   the paper's effect replicates on a multi-asset trend book's entries.
2. **A signal-level filter is the wrong instrument for it here.** The certified book's
   binding constraint is the 10-slot swing bucket, not signal supply: refusing an entry
   does not leave capital idle, it hands the slot to the next candidate. The vetoed
   discrete-ID trades (£84.97 expectancy) were replaced by recycled entries that are on
   average much worse — the system's marginal candidate is worse than the marginal
   vetoed trade, so filtering *hurts* expectancy (−£49/trade).
3. **Where FIP could still help is ranking, not filtering** — allocating scarce slots to
   continuous-ID candidates first (an EV/ordering overlay) instead of vetoing. That is a
   different experiment (slot-allocation layer, not entry gate) and would need its own
   pre-registration and ledger charge.
4. OOS the gate is merely flat, not disastrous (CPCV median +0.049 vs +0.048, one
   negative path) — the damage is the Sharpe/expectancy dilution through recycling, and
   it fails DSR at the full ledger count.

## Determinism

Full gate executed twice (seed 42): outputs **byte-identical** modulo `generated_at`
(the rerun dedups the ledger 299 → 299; `n_trials_before` identical). Unit tests:
`tests/test_entry_gates.py` (11 tests: FIP sign convention continuous-vs-jumpy, undefined
formation, median split keep/veto, determinism of the mask builder, factor sleeve
membership, comomentum z blocking, wrapper veto/MR-bypass/FLAT pass-through, certified
TrendBook default wraps nothing) + full suite green.

## Ledger

- **n before this gate: 296** (per universe-expansion gate, 2026-07-28) → 297 (concurrent
  `book_h_gold_252_commission109` measurement trial, unrelated campaign) → **299**
  (+2: `fip_control_252`, `fip_gate_252`, kind `fip_gate`, mrpt 0.01), recorded before
  the first run. Rerun deduped (299 → 299).

## Caveats (pre-registered and observed)

- £ figures are account-currency units on the £100k certified anchor; no FX conversion,
  same as every prior report.
- PBO across 2 configs sharing ~100% of their universe has limited discriminative power
  by construction — reported as computed (0.25125).
- The ID cross-section mixes asset classes (stocks, UCITS, gold, crypto, FX) — crypto's
  7-day calendar and jumpier returns sit disproportionately in the discrete half;
  pre-registered as the book the gate would trade, reported, not verdict-binding.
- The frozen paper test (workflow, state.json, engine/config.yaml live sections) was not
  touched; `entry_gate=None` remains the certified default — this experiment changed no
  certified behaviour.
