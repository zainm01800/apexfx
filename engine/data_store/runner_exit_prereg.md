# PRE-REGISTRATION — Runner exit on Book H + gold (2026-07-22)

**Status: pre-registered BEFORE any gate run.** The mechanism is built (default OFF, so the
certified book is byte-identical — 445 tests green) but NOT yet gated and NO trials are
charged. Charges happen at gate-run time. Changing anything after the run needs a new
pre-registration and new ledger charges.

**Base book:** `book_h_gold_252` (certified). This experiment changes **the exit only** —
signal, universe, sizing, regime, HTF gate, costs, caps, window (< 2025-01-01), seed 42, and
gate machinery are byte-identical. Any delta is attributable to the exit change alone.

## 1. Hypothesis (falsifiable, stated before the run)

The certified book enters on **252-day momentum** — a signal built to catch large multi-month
trends — then caps every winner at a fixed **1.5R** target. The Chandelier trail exists but is
neutered: it only activates after the 1R partial and the 1.5R cap closes the position before it
can ever carry a winner further. Best-case trade ≈ 1.25R blended. Trend-following profit is
supposed to come from the rare 5–20R tail winner, which this structure makes impossible.

**H-runner:** removing the 1.5R cap on the post-P1 remainder and letting it ride the existing
2×ATR Chandelier trail (uncapped) will improve the book's **risk-adjusted** metrics, because the
signal is a trend signal and the exit currently amputates trends.

**The honest counter-hypothesis, pre-registered:** "let winners run" also means giving back open
profit on every reversal, a **lower win rate**, and **fatter drawdowns**. The runner may well
LOSE to the capped book on DSR/Sharpe even if its gross return is higher. That is exactly why
this is gated, not adopted because it sounds right. **Adopt nothing unless it clears the gate.**

## 2. The change (one variable)

The mechanism is `TradeManager(runner_mode=True)`. When ON, after Partial 1 (50% off at 1R +
stop to breakeven — UNCHANGED):
- the fixed 1.5R target close is **skipped**, and
- Partial 2 (the 25% trim at 1.5R) is **skipped**,

so the whole 50% remainder rides the 2×ATR Chandelier trail until the trail (or the breakeven
stop, or the time-stop) takes it out. The **downside is untouched** — the hard stop and the
breakeven-after-1R still protect exactly as before (unit-tested: `test_runner_exit.py`).

Deliberately held FIXED (not swept, to avoid fitting a knob): the trail multiplier stays at the
book's existing **2.0×ATR**. P1 fraction (50%), 1R trigger, breakeven, and time-stop are
unchanged. **One variable moves: capped vs uncapped remainder.**

## 3. Configs — exactly 2 (the full selection set)

| Config | Exit | Ledger |
|---|---|---|
| `book_h_gold_252` (baseline) | fixed 1.5R target (certified) | dedup — already at n=208 |
| `book_h_gold_runner_252` | post-P1 remainder rides 2×ATR trail, uncapped | **1 NEW charge** |

**Why only 2 (the Book I lesson, applied):** the Book I gate REJECTED all four configs because
a 4-way near-identical overlapping set made set-level **PBO rank-unstable (0.602)**. A 2-config
set is the minimum that still computes PBO and is the most rank-stable possible. We change one
thing and test it against the incumbent — nothing more. Expected ledger: 208 → 209.

## 4. Gates (identical machinery + thresholds as every prior gate)

1. **DSR > 0.95**, deflated by the full ledger count at run time (expected n = 209).
2. **PBO < 0.5** across the 2-config set.
3. **CPCV 15 paths**: median OOS Sharpe > 0 and > 50% of paths positive.

**Decision rule (binding):** adopt the runner ONLY if it passes all three gates AND its DSR
exceeds the baseline's on the same snapshot. Otherwise the certified capped book stands. A
"higher total return but lower/failing DSR" runner is a REJECT — risk-adjusted quality is the
bar, not gross return.

## 5. Pre-registered caveats
1. **Snapshot dependence** (see `book_i_gate.md`): the baseline reproduces at ~1.03 Sharpe on
   current parquets, not the certified 1.086. Both configs run on the SAME snapshot so the
   comparison is internally valid; the runner's verdict is *relative to the baseline on that
   snapshot*, never an absolute claim.
2. **Gap fills still optimistic:** stop/target fills assume the level is available intrabar; on
   a gap the real fill is worse. This affects both configs equally, but the runner holds winners
   longer so it is *more* exposed to reversal gaps — noted, not corrected here (a separate
   honesty fix). If the runner passes, gap-aware fills must be applied before any live use.
3. **Determinism:** seed 42, gate run twice, JSONs identical modulo `generated_at`.
4. **Live parity:** the IBKR mirror does not replicate partials at all today, so neither the
   capped nor the runner exit is live-accurate yet. This gate decides the *simulation* question;
   live adoption additionally requires the mirror to manage exits.
5. Holdout (2025+) untouched. Iteration window ends 2024-12-31.

## 6. Deliverables
`scripts/run_portfolio_gate_runner.py` (thin sibling of the Book H gate, injects
`TradeManager(runner_mode=True)` via the backtester's new `trade_manager=` seam),
`data_store/validation/runner_gate_<date>.json` (+ determinism twin), `data_store/runner_gate.md`
(honest report, verdict in the first sentence), and this prereg. Exit code 0 only if the runner
passes and beats the baseline DSR.
