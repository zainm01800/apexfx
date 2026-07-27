# EXIT DECOMPOSITION — certified Book H gold trade set (MEASUREMENT, informational — NOT a gate)

**Question:** on the certified book's *actual* 1,637 trades, would quicker closes — fixed-day
exits, or lower flat-R full closes — have beaten the certified managed-exit ladder, using the
SAME entries, SAME fills and SAME daily bars?

**Answer: NO — every quicker-close variant loses to the ladder, most of them badly.**
On raw total P&L the decomposition does *not* leave the ladder unchallenged in the other
direction either: the three slower/fuller-exposure variants (fixed-day-10, trail-only,
stop-only) print above baseline — the same direction as the **REJECTED** runner gate. That
finding is informational only; the runner family already failed its pre-registered gate on
PBO, and the per-trade lens used here flatters long-hold variants (see "Caveats"). No
strategy change is proposed, gated, or implied.

- **Anchor:** `engine/data_store/validation/book_h_gapaware_2026-07-22.json` (gold) — Sharpe
  0.86284, 1,637 trades, final equity 292,551.34; mrpt 0.01, certified panel insertion order
  (EQUITY_CORE first), exit_mode=managed, warmup 250, seed 42, ITERATION window strictly
  < 2025-01-01.
- **Script:** `engine/scripts/run_exit_decomposition.py`. **Results:**
  `engine/data_store/validation/exit_decomposition_2026-07-25.json` (+ determinism twin
  `..._twin.json` — two runs byte-identical apart from the timestamp).
- **Ledger:** 275 → **276** — exactly ONE trial, `kind: exit_decomposition`, recorded BEFORE
  the run (dedup-safe; the twin re-run added nothing). **This is a measurement, not a gate:
  no DSR/PBO verdict was computed and none is implied.**
- **Anchor reproduction: EXACT** (Sharpe 0.86284, 1,637 trades, equity 292,551.34 to the
  digit) before any variant number was believed. **Control parity:** the certified ladder,
  replayed trade-by-trade through the real `TradeManager`, matched every recorded trade P&L
  with max |diff| = 0.000000; replay total £197,164.45 = recorded total £197,164.45 (the
  certified metrics' 197,164.63 differs by £0.18 — `compute_metrics` sums the 2dp-rounded
  `Trade.pnl` values). 10 positions still open at the window end are excluded (no recorded
  trade).

## Method (one paragraph)

Entry state (filled entry price, entry bar index, initial stop, target, units) was captured
by spying on `PortfolioBacktester._enter`/`._record` — the engine code path is untouched,
so the trade set is the certified one byte-for-byte. Counterfactuals replay each trade from
the bar AFTER the entry bar (engine management timing) against the same bar arrays, ATR and
squeeze series the engine computes, with the engine's own fills (`_fill`/`_pip`) and one
commission per close transaction. Variant rules: `fixed_day_N` = pure time exit at the close
of day N (entry bar, filled at its open, counts as day 1; no stop, no target);
`flat_r_R` = 100% close on the first touch of +R (stop checked FIRST, gap-aware — the
engine's conservative order — else the level), else initial stop, else a hard 21-bar cap at
the close; `stop_only` = initial stop (gap-aware) or 21-bar cap; `trail_only_1R` = no
partials and no fixed target, breakeven stop on the +1R touch, then the certified 2×ATR
chandelier + 1×ATR squeeze tighten + manager time-stop (>21 bars and < +0.25R).
`ladder_control` = the certified TradeManager itself. £/month = variant total ÷ 108 months
on the certified equity curve (all variants share the denominator; cross-check: control
£1,825.60/mo vs the gates' £1,782.88/mo, which is a month-end-equity basis that includes the
−£4.6k open-position mark at the window end). R multiples are per-trade P&L ÷ initial risk
(units × |entry − stop|).

## The matrix (1,637 certified trades, £100k book)

| Variant | Net P&L | £/mo vs base | Win rate | Exp/trade | Exp (R) | PF | Avg hold | Worst trade |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ladder_control (certified)** | **£197,164** | — (£1,825.60) | 55.8% | £120.44 | 0.131 | 1.325 | 12.9d | −£3,294 |
| fixed_day_1 | −£10,416 | −£1,922.05 | 49.1% | −£6.36 | −0.006 | 0.933 | 1.0d | −£2,023 |
| fixed_day_2 | £19,687 | −£1,643.31 | 50.2% | £12.03 | 0.008 | 1.084 | 2.0d | −£3,284 |
| fixed_day_3 | £52,464 | −£1,339.82 | 52.4% | £32.05 | 0.027 | 1.188 | 3.0d | −£3,702 |
| fixed_day_5 | £111,606 | −£792.21 | 54.2% | £68.18 | 0.071 | 1.311 | 5.0d | −£4,299 |
| fixed_day_10 | £258,775 | **+£570.47** | 54.4% | £158.08 | 0.154 | 1.523 | 10.0d | −£5,617 |
| flat_r_0.50 | −£2,436 | −£1,848.15 | 66.9% | −£1.49 | 0.003 | 0.995 | 7.2d | −£3,294 |
| flat_r_0.75 | £81,451 | −£1,071.43 | 60.3% | £49.76 | 0.051 | 1.146 | 9.3d | −£3,294 |
| stop_only | £412,948 | **+£1,997.99** | 48.4% | £252.26 | 0.241 | 1.598 | 16.8d | −£3,294 |
| trail_only_1R | £277,597 | **+£744.75** | 52.9% | £169.58 | 0.177 | 1.451 | 15.1d | −£3,294 |

Exit-reason colour: control = 930 stop / 508 target / 199 time. flat_r_0.50 hits its +0.5R
target on 1,075 trades (66%) yet still LOSES money — 0.5R wins cannot pay for 1R losses plus
costs at a 67% hit rate; flat_r_0.75 needs ~57% and gets 60%, barely positive. stop_only
rides 970 of 1,637 trades to the 21-bar cap at full size (only 665 stopped) — that is where
its extra P&L lives. trail_only records 1,435 stops, most of them breakeven scratches after
the +1R touch (win rate falls to 52.9% despite bigger winners). 3 trades in the two
uncapped variants reach the window end still open (closed at the last close).

## The honest read

**1. Quicker closes: the ladder wins everywhere, and the gradient is the story.** Total P&L
rises monotonically with hold length — day 1 is outright negative-EV (−£10.4k, the entries
are literally worthless without the hold), day 5 still forfeits 43% of the book's P&L, and
even day 10's *apparent* win is a fuller-exposure effect (caveat 3), not a quick exit.
Closing everything at +0.5R is net-negative (PF 0.995) despite a 66.9% win rate; +0.75R
forfeits £1,071/mo (−59%). This is the third independent confirmation of the same fact,
after two pre-registered gates: moving only the *partial* to +0.75R/+0.50R cost
−£721.76/mo (−40.5%) / −£986.09/mo (−55.3%) (`early_partial_gate.md`, REJECTED), and
vol-conditioning the trigger still failed on drawdown (`vol_adaptive_partial_gate.md`,
REJECTED). The certified ladder's edge is in the hold, not in faster banking.

**2. The other direction is NOT new information — it is the runner result restated.**
Stop-only (+£1,998/mo), trail-only (+£745/mo) and fixed-day-10 (+£570/mo) all beat the
ladder's total on this trade set by keeping full size through winners instead of banking
50% at +1R and capping at +1.5R. The runner gate measured exactly this family (uncapped
trail after the +1R partial): it beat the baseline on return, Sharpe, drawdown, PF, win
rate and expectancy — and was **REJECTED on PBO 0.711** (`runner_gate.md`). The cost of the
ladder's insurance is visible here (expectancy 0.131R vs 0.241R stop-only), and so is what
the insurance buys: win rate 55.8% vs 48.4%, and no free-falling round trips.

**3. Caveats that flatter the long-hold variants (why this stays informational).**
(a) Per-trade decomposition ignores slot contention: the certified book is slot-bound
(`timeframe_bucket_full` ×18,155 on a 10-slot swing bucket), and variants holding 15–17
days vs the ladder's 12.9 would crowd out entries in a real portfolio re-run — their
portfolio-level totals would be lower than printed; symmetrically the quick variants free
slots, but they lose too much per trade for that to matter. (b) No caps, correlations or
equity path are re-simulated; £/month is a closed-trade sum ÷ 108, not an equity curve.
(c) fixed_day_10 carries no stop: worst trade −£5,617 (≈1.7R) vs −£3,294 for every
stopped variant — gap risk the certified book does not take. (d) One decomposition on one
trade set has no multiple-testing protection and asks for none.

**Verdict: on the question asked — do quicker closes beat the certified ladder on this exact
trade set — the answer is an unambiguous NO (7 of 7 quicker variants forfeit £792–£1,922 per
month). The decomposition confirms the ladder against early exits. It does not prove the
ladder optimal against slower/fuller exits, and it was not designed or gated to: that
challenger family remains REJECTED on overfitting grounds (PBO), not on P&L. Informational
only — no config touched, no adoption proposed.**

## Reproduction

    cd engine
    .venv-mac/bin/python scripts/run_exit_decomposition.py            # certified run + ledger
    .venv-mac/bin/python scripts/run_exit_decomposition.py \
        --no-ledger --out data_store/validation/exit_decomposition_2026-07-25_twin.json

Determinism: both runs byte-identical (minus `generated_at`). Certified-anchor reproduction
hard-fails the script if the gold metrics drift. `pytest` green at the commit of this report.
