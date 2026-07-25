# PRE-REGISTRATION — Vol-adaptive first partial on the certified trend book (2026-07-25)

**Status: pre-registered BEFORE any run.** This document fixes the hypothesis, the exact
mechanical volatility-classification rule, the configuration set, the success criteria, and
the ledger plan before execution. The mechanism (a per-instrument first-partial trigger
threaded through the existing `trade_manager=` seam) does NOT exist yet — it is added as a
new `TradeManager` constructor parameter defaulting to certified behaviour — and NO trials
are charged until gate-run time. Charges happen at gate-run time, BEFORE execution. Changing
anything after the run requires a new pre-registration and new ledger charges.

**Base book:** `book_h_gold_252` — the certified gap-aware anchor
(`engine/data_store/validation/book_h_gapaware_2026-07-22.json`: Sharpe **0.86284**, 1637
trades, final equity 292,551.34 on £100k at `max_risk_per_trade = 0.01`). This experiment
changes **the first-partial trigger only, per instrument** — signal, universe (certified
panel insertion order: EQUITY_CORE first, then SGLD.L, crypto, FX majors), sizing, regime,
HTF gate, costs, caps, window (< 2025-01-01), seed 42, and gate machinery are byte-identical.
Any delta is attributable to the trigger change alone.

**Prior art (why this is the only remaining variant):** the FLAT earlier triggers were
gated 2026-07-24 (`early_partial_gate.md`) and REJECTED: +0.75R for all names cost −40.5%
of monthly profit and WORSENED max drawdown (16.3% → 18.4%); +0.5R for all names cost
−55.3% and also worsened drawdown. The flat experiment paid the expectancy tax on every
instrument. This experiment conditions the trigger on instrument volatility so the tax is
paid only where the mechanism says the safety is real.

---

## 1. Hypothesis (falsifiable, stated before the run)

High-volatility instruments revert faster: a trade up +0.75R in a high-vol name is more
likely to give the move back, so banking the first 50% earlier converts would-be −1R
reversals into small wins where reversals are common. Low-volatility instruments trend
persistently: the same early breakeven stop scratches out slow trend legs in noise before
they reach +1.5R, amputating the right tail that pays for the book.

**H:** a per-instrument first-partial trigger — **p1_r = 0.75R for HIGH-vol instruments,
p1_r = 1.0R (certified) for LOW-vol instruments** — captures the reversal-protection on the
fast reverters WITHOUT paying the expectancy tax on the slow trenders, so the monthly-profit
cost vs the certified baseline is small (≤ 5%) while max drawdown improves and win rate
does not fall.

**The honest counter-hypothesis, pre-registered:** the flat-partial gate showed the
0.75R trigger deepens drawdown even while raising win rate (scratched positions recycle
into more trades with thinner edge). If that effect dominates on the high-vol half of the
book too — high-vol names are exactly where a breakeven stop at +0.75R sits inside normal
noise — the challenger fails the same way the flat variant did, just on half the universe.
That is exactly why this is gated and priced, not adopted because the story is plausible.

**This is an OWNER-TRADE experiment, not a performance claim** (same category as the
early-partial gate and the 2026-07-23 0.75%-risk override): the intent is more safety,
priced honestly in £/month, decided by the binding rule in §4.

## 2. The mechanical volatility-classification rule (fixed BEFORE the run)

No hand-picked per-name assignments. The rule is:

1. For each instrument in the certified panel, take the SAME cleaned in-window daily bars
   the backtest loads (`ParquetStore` → `clean` → strictly < 2025-01-01).
2. Compute the book's EXISTING vol-63 measure — identical to what the portfolio
   backtester itself computes per instrument (`_vol_series` in
   `apex_quant/backtest/portfolio.py`): the 63-day rolling std (ddof=1) of log
   close-to-close returns, annualised by √ann where ann is the instrument class's daily
   annualisation from `cfg.mechanics_for(inst).annualization` (252 equities/FX, 365 crypto).
3. Reduce to one scalar per instrument: the **median of that annualised vol-63 series**
   over the full in-window history (NaN warmup values excluded).
4. Universe split: compute the **median of those per-instrument scalars across the panel**.
   An instrument is **HIGH-vol if its scalar is STRICTLY GREATER than the universe
   median**, LOW-vol otherwise (≤ median). With the 39-name panel this puts 19 names in
   HIGH and 20 in LOW, bar ties at the median (which fall LOW by the strict inequality).

The classification is computed **once**, from the full in-window panel, at gate-run time
before any backtest, is printed and stored in the results JSON, and is held FIXED for the
full-window run and for every CPCV fold (the challenger TradeManager carrying the map is
forwarded into every fold, exactly as the early-partial gate forwarded its fixed p1_r).
The resulting split is reported in the gate report.

## 3. The change (one variable)

`TradeManager(p1_r_by_instrument={HIGH: 0.75, LOW: 1.0})` — a new constructor parameter,
default `None`, which maps instrument symbol → first-partial trigger. When `None`
(default), behaviour is byte-identical to the certified book (`p1_r = 1.0` for everything);
the backtester position dict already carries `"symbol"`, so the lookup is mechanical.
Everything else in the ladder is UNCHANGED: 50% partial fraction, stop → breakeven at the
moment Partial 1 fires (it travels with the earlier trigger on HIGH-vol names — that is the
point of the experiment), Partial 2 (25% at +1.5R + lock 0.5R), 2×ATR Chandelier trail,
squeeze tightening, 21-bar time exit, gap-aware fills. Downside before the trigger is
untouched — the hard initial stop still protects exactly as before (unit-tested:
`tests/test_vol_adaptive_partial.py`).

## 4. Configs — exactly 2 (the full selection set)

| Config | first-partial trigger | Role |
|---|---|---|
| `book_h_gold_252` | 1.00R for every instrument (certified) | baseline / anchor hard-check |
| `book_h_gold_252_p1_voladapt` | 0.75R for HIGH-vol, 1.00R for LOW-vol (§2 rule) | challenger |

Ledger: exactly **2 NEW trials** (kind `vol_adaptive_partial_gate`, `max_risk_per_trade`
0.01, the trigger rule and the resolved per-instrument map in the params so the keys cannot
dedup against any existing entry), recorded BEFORE the first run. Ledger 269 → **271**; the
full updated count deflates every DSR. Re-runs dedup (271 → 271).

## 5. Success criteria — the OWNER-TRADE rule (binding)

Adopt the challenger ONLY IF ALL THREE hold vs the certified baseline:

1. **Cost ≤ 5%:** average in-window monthly profit (£/month on the £100k book) is at most
   5% below the baseline's;
2. **Drawdown improves:** max drawdown is LOWER than the baseline's (16.3153%);
3. **Win rate does not fall:** win rate is ≥ the baseline's (55.7728%).

If any leg fails: **ADOPT NOTHING — the certified +1R ladder stands**, reported as the
honest answer. DSR / PBO / CPCV are run with identical machinery and thresholds as every
prior gate (DSR > 0.95 at the full ledger count, PBO < 0.5 across the 2-config set, CPCV 15
paths median > 0 with > 50% positive) and are **recorded for information** — the adoption
criterion is the owner's stated comfort-for-cost trade above, not a gate pass.
(Pre-registered caveat: with 2 configs sharing ~100% of their universe, PBO's
discriminative power is limited by construction — reported as computed, same caveat as
every prior overlapping-family PBO.)

## 6. Measurements

Per config: Sharpe (annualised), profit factor, win rate, max drawdown, **worst daily
return** and worst daily P&L (£), expectancy per trade (£ and %), trade count, total
return, final equity, **average monthly profit (£/month on £100k)** = mean of
month-on-month equity differences over the in-window curve, CPCV 15-path OOS Sharpe
distribution, DSR at n=271, PBO across the 2-config set. The £/month comparison vs
baseline is the FIRST table of the gate report, followed by the vol-classification split
(which instruments landed HIGH vs LOW, with their vol-63 medians).

## 7. Honesty rules (identical to every prior gate)

- ITERATION window only: data strictly < 2025-01-01. The 2025+ holdout is never touched.
- **Certified-anchor reproduction:** the baseline config (p1_r = 1.0 everywhere, mrpt 0.01)
  MUST reproduce book_h_gapaware_2026-07-22.json exactly (Sharpe 0.862838, 1637 trades,
  final equity 292,551.34, PF 1.324524, win rate 0.557728, maxDD 0.163153, expectancy
  120.442657) — the run hard-fails otherwise. Panel insertion order is the certified one
  (EQUITY_CORE first; alphabetical order is a known ~0.33-Sharpe artifact).
- Seed 42. **Determinism:** the full gate is run twice; outputs byte-identical modulo
  `generated_at` and the ledger pre-state (`n_trials_before`; the rerun dedups 271 → 271).
- Frozen paper test (workflow, state.json, engine/config.yaml live sections) untouched.
- If the challenger surprises and RAISES profit, that is the headline — but the adoption
  rule in §5 still governs (it is a comfort-for-cost decision, not a profit claim).

## 8. Known limitations

- The classification uses full-window in-window data (2007→2024), so a name's vol class is
  its long-run average regime, not a time-varying one; a name that was low-vol for a decade
  and high-vol recently is classified by its median. This is the mechanical honesty trade:
  any time-varying rule would add a second experiment's worth of knobs. Stated here so the
  simplification is on the record BEFORE the run.
- The breakeven stop at +0.75R sits inside normal daily noise for high-vol names; scratch
  frequency on exactly those names is the mechanism being measured, not a modelling error.
- £ figures are the book's account-currency units on the £100k certified anchor
  (initial_equity 100000); no FX conversion is applied, same as every prior report.
- CPCV for the challenger forwards the SAME `TradeManager(p1_r_by_instrument=...)` into
  every fold — measuring the baseline exit OOS while reporting the challenger would be an
  invalid gate (the runner-exit/early-partial seam exists precisely for this).
