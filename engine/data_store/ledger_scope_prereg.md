# PRE-REGISTRATION — Trial Ledger Scoping Rules (2026-07-22)

**Status: pre-registered BEFORE any new sleeve is tested.**
**Decision: HYBRID — per-family ledgers with a family-selection penalty.**

---

## 1. The problem

The deflated Sharpe ratio (DSR) requires an honest count N of independent trials.
At N=226 the current global ledger contains:

| Factory                    | Trials | Description                                |
|----------------------------|--------|--------------------------------------------|
| `trend_book_mtf`           | ~178   | Trend-following: universes, lookbacks, exits |
| `trend_book_ev_slots`      | 3      | EV slot allocation variants                |
| `trend_book_ev_rpt`        | 5      | Risk-per-trade sweep                       |
| `st_reversal`              | 6      | Short-term reversal sleeve                 |
| `pead_book`                | 6      | Post-earnings drift sleeve                 |
| `cot_reversal_book`        | 4      | COT positioning sleeve                     |
| *Other (crypto XS, etc.)*  | ~24    | Various failed candidates                  |

Two distinct problems bind simultaneously:

**Problem A — over-penalising genuinely new work.** A mean-reversion strategy on
15-minute FX bars shares zero parameters, zero signal logic, and zero return
correlation with trend-following on daily equities. Charging it N=226 for searches
conducted entirely within trend-following is statistically wrong: those 178 trend
trials explored a different region of strategy space and do not inflate the expected
maximum Sharpe of mean-reversion candidates.

**Problem B — rationalising away discipline.** "This is a new family, start fresh" is
exactly the move a researcher makes when a candidate barely fails. The freedom to
declare a new family *after seeing results* is the same degree of freedom DSR exists
to control. Every research programme that went wrong started with a reasonable-
sounding exception.

## 2. Decision: hybrid ledger with family-selection penalty

### 2.1 Per-family ledgers

Each strategy family maintains its own trial count N_f, starting from 1 on its first
registered trial. The family's DSR is deflated by N_f, not by the global count.

### 2.2 Family-selection penalty

Testing K families is itself a multiple-testing act: you are selecting the best family
from K candidates. To correct for this, the **binding DSR threshold is raised** from
0.95 to:

$$\text{threshold}(K) = 1 - \frac{1 - 0.95}{K} = 1 - \frac{0.05}{K}$$

This is a Bonferroni correction on the family-selection step. It is conservative
(Bonferroni assumes independence, which over-corrects when families are correlated),
but conservative is the right direction when the alternative is rationalising away
the penalty entirely.

| Families tested (K) | DSR threshold |
|----------------------|---------------|
| 1                    | 0.950         |
| 2                    | 0.975         |
| 3                    | 0.983         |
| 4                    | 0.988         |
| 5                    | 0.990         |
| 10                   | 0.995         |

### 2.3 What counts as a "family"

A family is defined by the conjunction of:

1. **Signal generation mechanism** — the core logic that produces entry signals.
   Trend-following (momentum + regime gate) is one family. Mean-reversion on
   Bollinger Bands is another. Cross-sectional ranking is another.
2. **Timeframe class** — daily/weekly signals are one class; intraday (≤1h) is
   another. The same signal logic on a different timeframe class is a new family.

A family is NOT defined by:
- Universe (adding instruments to the same signal is not a new family)
- Risk parameters (risk-per-trade, slot counts — these are tuning within a family)
- Exit mechanics (barrier vs managed — tuning within a family)
- Lookback window (126 vs 252 — tuning within a family)

### 2.4 Current family roster (K)

Families already tested (each increments K whether they passed or failed).
Factory prefixes map to the `factory` field in `trial_ledger.json`:

| K  | Family name                | Factory prefix(es)                      | N_f | Status  |
|----|----------------------------|-----------------------------------------|-----|---------|
| 1  | Daily trend-following      | `default`, `trend_book_mtf`, `trend_book_ev_slots`, `trend_book_ev_rpt`, `regime_gated_momentum`, `fx_majors_stack` | 148 | Active  |
| 2  | Carry / yield differential | `carry_trend`, `cross_sectional_carry`  | 28  | FAILED (riba) |
| 3  | Intraday close momentum    | `intraday_close_momentum`               | 8   | FAILED  |
| 4  | Fix flow reversal          | `fix_flow_reversal`                     | 8   | FAILED  |
| 5  | Currency XS momentum       | `currency_xs_momentum`                  | 7   | FAILED  |
| 6  | Meta-labeling              | `meta`                                  | 6   | FAILED  |
| 7  | Crypto XS momentum         | `crypto_xs_momentum`                    | 6   | FAILED  |
| 8  | Short-term reversal        | `st_reversal`                           | 6   | FAILED  |
| 9  | Post-earnings drift        | `pead_book`                             | 6   | FAILED  |
| 10 | COT positioning            | `cot_reversal_book`                     | 4   | FAILED  |
| 11 | Vol-managed overlay        | `vol_managed`, `vol_target_overlay_trend_book` | 5 | FAILED  |

**Current K = 11.** The DSR threshold for the next family (K=12) is:
$$1 - \frac{0.05}{12} = 0.9958$$

This is a high bar. It should be. Ten families have failed. Launching a twelfth must
reflect the accumulated search.

### 2.5 Recording rules — how this cannot be revised

1. **Family declaration is irrevocable.** Once a family name and factory prefix are
   written to this document and a trial is recorded under it, the family exists.
   Splitting a family retroactively to lower N_f is forbidden.

2. **K is monotonically non-decreasing.** A new family increments K. A failed family
   cannot be un-counted. K cannot be lowered by declaring that a prior family "wasn't
   really independent."

3. **The family-selection penalty applies to ALL families equally.** The trend book's
   DSR threshold is also raised to threshold(K). If K=10, even the incumbent must
   clear 0.995 to be re-validated. (At Sharpe 0.922 and N_f=186, the incumbent's
   DSR is 0.921 — it already fails. This is honest: the incumbent has not cleared
   the bar, and raising the bar does not change that fact.)

4. **This document is versioned in git.** The commit hash at the time of writing is
   the immutable record. Any amendment must be a new commit with a changelog entry
   explaining why the rules changed, written BEFORE seeing any results under the
   new rules.

5. **Ledger storage.** Each family's trials are stored in the global
   `trial_ledger.json` with a `family` field added to each trial's config dict.
   The global file remains the single source of truth; per-family N_f is computed
   by filtering on `family`. The `family` field is set at recording time and is
   immutable.

## 3. Practical implications

### 3.1 For the next sleeve candidate

Before running any backtest for a new sleeve:
1. Write a prereg document declaring the family name, factory prefix, and configs.
2. Increment K in this document (append to the roster table).
3. Record all trial configs in `trial_ledger.json` with `family: "<name>"`.
4. The DSR threshold for this candidate is `1 - 0.05/K`.
5. The N_f for DSR deflation is the count of trials in this family only.

### 3.2 For re-validating the incumbent

The trend-following book's DSR must also clear `threshold(K)`. At K=11, this is 0.9955.
At N_f=148 and Sharpe 0.922, the incumbent DSR is ~0.93 — it does not clear.

This is the correct outcome: the trend book has been searched extensively (148 trials)
and its honest Sharpe (0.922) is not high enough to survive that search at any
reasonable significance level. The book is *operationally useful* (positive expected
return, inside the drawdown wall) but not *statistically validated* by DSR.

The path forward is either:
- A genuinely better trend signal (higher Sharpe) that clears DSR at N_f=148+
- A genuinely independent sleeve that diversifies the portfolio Sharpe above 1.0

### 3.3 Why the global ledger alternative was rejected

A pure global ledger (N=226 for everything) was considered and rejected because:

1. It conflates unrelated searches. The 178 trend trials explored {lookback, universe,
   exit, risk} in a trend-following context. They tell you nothing about whether a
   mean-reversion candidate's Sharpe is inflated.
2. It creates a perverse incentive to stop exploring. Each failed family raises the
   bar for all future work, even work in completely different regions of strategy
   space. This penalises breadth, which is the opposite of what Grinold's law says
   you need.
3. The control test (synthetic series) confirms that DSR's discriminatory power comes
   primarily from return distribution shape, not N. A true 1.5 Sharpe clears at
   N=500; a true 0.5 fails at N=1. The family-selection Bonferroni correction
   addresses the multiple-families problem more precisely than inflating N.

### 3.4 Why a pure per-family ledger was rejected

A pure per-family ledger (restart N=1 for each family, no penalty) was rejected
because:

1. It allows unlimited family declarations at zero cost. A researcher who tests 50
   families, each with 3 configs, pays N_f=3 per family and clears DSR easily —
   while the true search involved 50 × 3 = 150 trials.
2. The Bonferroni penalty on K is the minimum honest correction for the family-
   selection step.

## 4. Changelog

| Date       | Change                                    | Commit |
|------------|-------------------------------------------|--------|
| 2026-07-22 | Initial pre-registration of ledger rules  | HEAD   |
