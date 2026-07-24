# W4 GATE — EARNINGS BLACKOUT ±1d: **REJECTED (it costs trend entries and does NOT cut the tail)**

**Pre-registration:** `engine/data_store/earnings_blackout_prereg.md` (written BEFORE any
blackout run; the 2 trials were recorded before execution). **Results:**
`engine/data_store/validation/earnings_blackout_gate_2026-07-24.json`. **Script:**
`engine/scripts/run_portfolio_gate_earnings_blackout.py`. **Window:** ITERATION only, strictly
< 2025-01-01; certified anchor (mrpt 0.01, equity-sleeve panel in certified insertion order).
**Ledger:** 264 → **266**; every DSR deflated by 266.

## The pre-registered scoreboard (Book H gold EQUITY SLEEVE, 21 instruments)

| | control (no blackout) | challenger (±1d blackout) |
|---|---|---|
| Sharpe | **0.9955** | 0.8522 (**−0.143**) |
| Profit factor | **1.3463** | 1.3221 (−0.024) |
| Total return (~9y) | **+191.6%** | +150.0% (−41.5pt) |
| Trades | 1546 | 1520 (−26) |
| Expectancy / trade | **+123.65 (+0.836%)** | +99.41 (+0.707%) |
| Win rate | 55.8% | 55.3% |
| Max drawdown | 17.03% | 17.38% (worse) |
| **Worst daily loss** | **−4.49%** | **−5.67% (worse)** |
| DSR @ n=266 | 0.982 ✓ | 0.952 ✓ (marginal) |
| CPCV median / frac positive | +0.064 / 15-of-15 ✓ | +0.056 / 15-of-15 ✓ |
| PBO (2-config set) | — | 0.188 ✓ |

**Pre-registered rule:** H1 (tail improvement) **FAILS** — the worst day got *worse*, not 10%
smaller. H2 (no lost trend entries) **FAILS** — ΔSharpe −0.143 breaches the pre-committed −0.10
floor. Challenger passes the three gates (DSR marginally). **Verdict: REJECTED.**

## The honest reading

The hypothesis was that earnings-window entries carry gap-through-stop risk that the ±1d block
removes for free. Measured: the block removed 26 net entries and **£41.5 points of total return**
— those entries were worth more than their gap risk cost. This is the PEAD (post-earnings-
announcement drift) effect from the inside: a 252-lookback trend book's entries near earnings
are disproportionately *good* entries (earnings are a trend catalyst), not just gap risk. The
tail did not improve either (worst day −4.49% → −5.67%; the remaining gaps are not
earnings-clustered enough for an entry block to matter — the block suppresses ENTRIES, it
cannot touch positions already open over a release).

Coverage: 11 of 12 stocks (SEC EDGAR 8-K dates; TSM, an ADR, unblocked — prereg §7), ~36
in-window events per stock, 110 blocked bars per stock. The challenger still passes the gates
on its own (DSR 0.952 @ 266 is marginal) — the rejection is on the pre-registered
falsification rule, which is exactly what the rule is for.

## Determinism

Full gate executed twice (seed 42): outputs **byte-identical** modulo `generated_at` and the
ledger pre-state (`n_trials_before`; the rerun dedups 266 → 266). Unit tests:
`tests/test_earnings_blackout.py` (±1-day calendar math incl. weekend-landing and edge
clipping, FLAT-only-on-blocked-bars, attribute proxying, selective wrapping) + full suite green.

## Ledger

- **n before this gate: 264** (258 + 4 W2 + 2 W3)
- **+2** (`book_h_gold_equity`, `book_h_gold_equity_blackout1d`, kind `earnings_blackout_gate`,
  recorded before the first run) → **266**. Rerun deduped (266 → 266).
