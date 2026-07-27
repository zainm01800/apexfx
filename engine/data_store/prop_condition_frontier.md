# PROP-CONDITION RATE FRONTIER — measurement study (2026-07-27)

**Pre-registration:** `engine/data_store/prop_condition_prereg.md` (written BEFORE any
sweep run; the 12 trials were recorded before execution). **Results:**
`engine/data_store/validation/prop_condition_frontier_2026-07-27.json` (+ determinism
twin `_twin.json`). **Script:** `engine/scripts/run_prop_condition_frontier.py`.
**Window:** ITERATION only, strictly < 2025-01-01; certified panel insertion order
(EQUITY_CORE first); seed 42. **Base:** adopted ensemble `momentum_lookbacks
[63,126,252]` on `book_h_gold_39`, certified machinery/costs; only
`max_risk_per_trade` and `max_portfolio_risk` vary. **Ledger:** 282 → **294** (12 trials,
kind `prop_condition_frontier`, recorded before the first run; twin rerun deduped
294 → 294).

**This is a measurement study, not an adoption gate.** Nothing here changes a certified
default; the frontier exists to place the future funded runner (config.prop.yaml) at an
informed operating point.

## What was measured

Per config: one full-window backtest (standard metrics + monthly tail + daily-loss
distribution), then a 20,000-path Monte Carlo per firm profile resampling **that
config's own backtest trade-return pool** (`pnl / equity-at-entry`) with **its own
empirical closures-per-day distribution**, EOD-only, seeded
`SeedSequence([42, config, firm])`. Attempt cap 252 trading days (12 months; timeouts
count as not-passed — prereg §4, conservative for firms with no time limit). Funded
12-month survival simulated per cell from a fresh balance. Firm profiles as fixed in the
prereg: **FTMO 1-step** +10% target / −3%-of-day-start-equity daily rule / 10%
EOD-trailing floor; **The5ers Pro Growth** +6% target / −5% daily / 5% static floor.

## The frontier — backtest side (100k book, iteration window 2016→2024)

| config | risk | cap | Sharpe | ann | avg £/mo (%/mo) | med £/mo | worst month | maxDD | worst day | p1 day | days ≤−2.5% | days ≤−3% | trades | win |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prop_r075_cap025 | 0.75% | 2.5% | 0.804 | 4.6% | +716 (0.72%) | +253 | −7,301 | 9.6% | −2.16% | −1.20% | 0.00% | 0.00% | 1540 | 53.1% |
| prop_r075_cap035 | 0.75% | 3.5% | 0.900 | 6.2% | +1,074 (1.07%) | +256 | −12,391 | 12.7% | −2.69% | −1.37% | 0.06% | 0.00% | 1597 | 55.7% |
| prop_r075_cap045 | 0.75% | 4.5% | 0.803 | 6.1% | +1,046 (1.05%) | +168 | −12,705 | 13.0% | −2.60% | −1.60% | 0.06% | 0.00% | 1646 | 54.4% |
| prop_r100_cap025 | 1.00% | 2.5% | 0.838 | 5.1% | +831 (0.83%) | +198 | −8,745 | 11.7% | −2.73% | −1.21% | 0.03% | 0.00% | 1420 | 52.3% |
| prop_r100_cap035 | 1.00% | 3.5% | 0.897 | 7.0% | +1,258 (1.26%) | +262 | −14,039 | 15.0% | −3.33% | −1.58% | 0.16% | 0.06% | 1550 | 54.3% |
| prop_r100_cap045 | 1.00% | 4.5% | 0.828 | 7.3% | +1,346 (1.35%) | +394 | −17,546 | 17.4% | −3.42% | −1.90% | 0.50% | 0.09% | 1618 | 54.8% |
| prop_r125_cap025 | 1.25% | 2.5% | 0.841 | 5.4% | +879 (0.88%) | +258 | −10,034 | 12.3% | −3.28% | −1.23% | 0.03% | 0.03% | 1411 | 51.2% |
| prop_r125_cap035 | 1.25% | 3.5% | 0.883 | 7.4% | +1,392 (1.39%) | +362 | −13,549 | 16.5% | −3.56% | −1.66% | 0.25% | 0.09% | 1454 | 55.7% |
| prop_r125_cap045 | 1.25% | 4.5% | 0.907 | 8.7% | +1,750 (1.75%) | +794 | −19,271 | 17.5% | −4.73% | −1.96% | 0.47% | 0.19% | 1584 | 55.7% |
| prop_r150_cap025 | 1.50% | 2.5% | 0.878 | 5.9% | +1,003 (1.00%) | +509 | −7,956 | 12.1% | −3.67% | −1.30% | 0.06% | 0.03% | 1311 | 51.3% |
| prop_r150_cap035 | 1.50% | 3.5% | 0.893 | 7.6% | +1,438 (1.44%) | +226 | −14,388 | 16.5% | −2.86% | −1.72% | 0.25% | 0.00% | 1419 | 54.3% |
| **prop_r150_cap045** | **1.50%** | **4.5%** | **1.026** | **10.8%** | **+2,497 (2.50%)** | **+1,577** | **−25,207** | **13.9%** | **−3.61%** | **−1.97%** | **0.56%** | **0.28%** | **1484** | **55.6%** |

Reference points (existing ledger entries, certified caps 1.0%/6.5% — wider than any swept
cap): certified 252-only anchor +£1,783/mo, Sharpe 0.863, maxDD 16.3%, worst day **−5.09%**
(would breach even a 5% daily rule on an EOD basis); ensemble at the same caps
+£2,002/mo, Sharpe 0.924, maxDD 15.9%, worst day −3.45%. The ensemble base clears the
5% daily bar that the 252-only book fails — one more reason the funded runner belongs on
the ensemble.

## The frontier — Monte Carlo side (20k paths/cell, seed 42)

Eval: pass% / median months-to-pass (passers only) / timeout% / fail-by-daily% /
fail-by-floor%. Funded: 12-month survival% (funded daily% / floor% fail) and median
monthly % among survivors.

| config | FTMO pass | mo | TO | fD | fF | FTMO surv12mo (fD/fF) | 5ers pass | mo | TO | fD | fF | 5ers surv12mo (fD/fF) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prop_r075_cap025 | 13.1% | 9.8 | 86.9% | 0.04% | 0.0% | 100.0% (0.04/0.0) | 46.8% | 7.9 | 51.5% | 0.00% | 1.7% | 98.4% (0.00/1.7) |
| prop_r075_cap035 | 28.2% | 9.2 | 71.7% | 0.06% | 0.1% | 99.8% (0.11/0.1) | 63.3% | 6.9 | 34.3% | 0.00% | 2.4% | 97.9% (0.00/2.1) |
| prop_r075_cap045 | 30.8% | 8.9 | 68.9% | 0.16% | 0.2% | 99.5% (0.21/0.3) | 63.8% | 6.6 | 31.8% | 0.00% | 4.4% | 95.9% (0.00/4.1) |
| prop_r100_cap025 | 18.6% | 9.5 | 81.2% | 0.14% | 0.1% | 99.8% (0.15/0.1) | 54.1% | 7.3 | 43.6% | 0.00% | 2.3% | 97.5% (0.00/2.5) |
| prop_r100_cap035 | 38.0% | 8.6 | 59.9% | 1.81% | 0.3% | 97.8% (1.97/0.3) | 69.4% | 6.1 | 26.0% | 0.01% | 4.6% | 95.3% (0.01/4.7) |
| prop_r100_cap045 | 42.7% | 8.2 | 55.6% | 1.12% | 0.6% | 98.1% (1.21/0.7) | 71.5% | 5.8 | 22.6% | 0.00% | 5.9% | 94.0% (0.01/6.0) |
| prop_r125_cap025 | 24.1% | 9.1 | 70.3% | 5.41% | 0.1% | 94.2% (5.62/0.2) | 57.5% | 6.9 | 37.9% | 0.00% | 4.6% | 95.2% (0.00/4.8) |
| prop_r125_cap035 | 43.0% | 8.1 | 51.4% | 5.07% | 0.6% | 93.1% (6.27/0.6) | 71.7% | 5.7 | 22.0% | 0.01% | 6.2% | 93.8% (0.03/6.2) |
| prop_r125_cap045 | 52.4% | 7.4 | 37.1% | 9.50% | 0.9% | 87.4% (11.58/1.1) | 76.7% | 5.1 | 14.5% | 0.06% | 8.7% | 91.2% (0.13/8.6) |
| prop_r150_cap025 | 29.8% | 8.7 | 63.2% | 6.74% | 0.2% | 92.4% (7.31/0.3) | 61.7% | 6.5 | 32.4% | 0.04% | 5.8% | 94.3% (0.04/5.6) |
| prop_r150_cap035 | 46.0% | 7.9 | 52.1% | 0.87% | 1.1% | 97.8% (1.18/1.0) | 71.9% | 5.6 | 20.6% | 0.00% | 7.5% | 92.5% (0.00/7.5) |
| **prop_r150_cap045** | **62.6%** | **6.9** | **29.9%** | **6.41%** | **1.1%** | **90.1% (8.81/1.1)** | **82.6%** | **4.5** | **9.7%** | **0.08%** | **7.6%** | **92.0% (0.24/7.8)** |

## How the frontier reads

- **FTMO 1-step pass rates are timeout-dominated, not breach-dominated.** At every grid
  point the largest non-pass bucket is "still trading at the 12-month cap" (30–87%),
  while floor fails stay ≤1.1% and daily fails reach double digits only at 1.25–1.5%
  risk. A +10% target inside 252 trading days demands ~0.85%/month net; only the
  highest-rate cell sustains that drift with margin. With an unlimited time window (the
  real 1-step rulebook) a large share of those timeouts convert to passes at the same
  drift — the reported pass% is the conservative reading, per the prereg.
- **The 3% daily rule is the FTMO binding risk constraint once per-trade risk ≥1.25%.**
  Funded 12-month daily-breach rates: 5.6% (r125_cap025) → 11.6% (r125_cap045) → 8.8%
  (r150_cap045). The backtest worst day at 1.5%/4.5% (−3.61%, 2018-02-05) exceeds 3% on
  an EOD basis; 0.28% of all days close ≤ −3%. The mitigation already declared for the
  funded runner is the engine-level 2.5% daily stop + flatten (config.prop.yaml,
  implemented 2026-07-23 in the RiskManager/live loop, NOT active in these backtests):
  with it enforced, EOD losses cap near −2.5% and the 3% rule effectively cannot bind.
- **The5ers' 5% static floor is that profile's binding constraint**, not its 5% daily
  rule (daily fails ≤0.24% everywhere): funded 12-month floor-fail runs 1.7–7.8% and
  scales with both knobs. 5% of start balance is ~0.5× this book's annual vol at the
  swept sizes — the floor, not the day, is what a Pro Growth account must respect.
- **Rate scales with both knobs, and the cap interacts non-monotonically** (cap 2.5%
  truncates hard at every risk level — the portfolio_risk_cap binds 658–973 times per
  2.5%-cap config vs 103–728 at the wider caps; Sharpe peaks at r150_cap045, 1.026). Neighbouring Sharpes within ~0.05 of each other
  are single-full-window measurements, not gated differences — this study does not run
  CPCV and makes no adoption claim; the validated object remains the ensemble base.
- **The best cell beats the certified-caps ensemble on both rate and drawdown**:
  +£2,497/mo vs +£2,002/mo, maxDD 13.9% vs 15.9% — at the price of a fatter left tail
  (worst month −£25.2k vs −£20.6k, worst day −3.61% vs −3.45%, more daily-rule exposure).

## Recommended operating points (pre-committed rule, prereg §6)

**The5ers Pro Growth → `prop_r150_cap045` (1.5% risk, 4.5% portfolio cap).**
It is the highest-£/month config clearing the ≥70% pass bar: **82.6% pass per attempt,
median 4.5 months**, +£2,497/mo (≈2.5%/mo on 100k), worst day −3.61% (1.4pt under the
5% daily rule), funded 12-month survival 92.0%. The residual risk to price in is the
static floor: 7.8% of funded paths touch −5% within 12 months — the account is lost
~1 time in 13 funded-years at this size. If the owner prefers to halve that, the next
frontier point down is r100_cap045 (71.5% pass, floor-fail 6.0%, +£1,346/mo) — a 46%
rate cut for a 1.8pt floor-fail reduction; not worth it. Clear choice.

**FTMO 1-step → no config clears 70% pass; the max-pass point is also
`prop_r150_cap045` (62.6%, median 6.9 months) — recommended WITH the daily-rule
caveat, and only with the 2.5% engine daily stop enforced.**
Per the prereg, when nothing clears the bar the report says so and recommends the
max-pass config. 62.6% is that point, and it is timeout-dominated (29.9% still trading
at the 12-month cap; only 7.5% actually breach anything). The honest caveats: (a) its
funded 12-month daily-breach rate at the 3% rule is 8.8% WITHOUT an engine daily stop —
enforcing the declared 2.5% stop + flatten is mandatory before real money on a 3%-daily
firm; (b) its backtest worst day −3.61% already exceeds 3% EOD. **The conservative
alternative for a 3%-daily firm is `prop_r150_cap035`** (same 1.5% risk, 3.5% cap):
pass 46.0%, but the only high-rate cell whose worst EOD day stays under 3% (−2.86%),
funded daily-fail just 1.2%, funded survival 97.8%, at +£1,438/mo — the pick when
account longevity outranks first-attempt pass rate and no engine daily stop is wired in.
Both points are on the reported frontier; the choice between them is the owner's risk
posture, not this study's verdict.

**What the study answers:** the highest honest monthly rate that survives funded
conditions on this book is **≈2.5%/month (+£2,497/mo per 100k) at 1.5% risk / 4.5% cap
on the ensemble base** — achievable with 82.6% single-attempt pass probability under a
6%-target/5%-daily/5%-static-floor profile (The5ers Pro Growth), and 62.6% under the
10%-target/3%-daily/10%-trailing profile (FTMO 1-step, timeout-capped; higher in
practice with no time limit). Above this grid point the study does not go: per the
account owner's standing note and the prereg, >1.5% per trade was not swept.

## Determinism and ledger

Full study executed twice (seed 42): results payload **byte-identical** modulo
`generated_at` and the ledger bookkeeping line (first pass 282 → 294, twin dedups
294 → 294). MC streams derive from `SeedSequence([42, config_index, firm_index])` —
identical on rerun. Ledger: **n before 282, +12 (`prop_r075_cap025` … `prop_r150_cap045`,
kind `prop_condition_frontier`, recorded BEFORE the first run) → 294.** py_compile clean;
full pytest suite green; per-commit pushes.

## Caveats that bound these numbers (prereg §9, restated where they bite)

- **EOD-only.** A daily-bar backtest cannot see intraday equity excursions; a real daily
  rule marks intraday, so every daily-breach number above is a lower bound. The 2.5%
  engine daily stop exists precisely to convert this from a measurement into an
  enforceable limit — mandatory on a 3%-daily firm.
- **No backtest→live haircut.** Pass probabilities assume live expectancy equals the
  backtest pool's; a 50% expectancy haircut would lower every pass% (the pre-study toy MC
  with a hand-built haircut profile showed the same direction).
- **Independence approximation.** Same-day cross-instrument loss clustering enters only
  through the empirical closure-count distribution, not correlated outcomes; crisis days
  like 2018-02-05 (the worst day at the recommended cell, and in the worst-5 at
  r150_cap035) are in the pools, but joint tails may be fatter live.
- **Firm profiles are the prereg's fixed simplifications** — no profit-split,
  consistency, minimum-days, or news clauses modeled.
- **Timeouts count as not-passed** — conservative for firms with unlimited time.
