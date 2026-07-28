# GATE — CROSS-ASSET FACTOR CONFIRMATION: **FAMILY REJECTED — certified ungated entries stand**

**Pre-registration:** `engine/data_store/factor_confirmation_prereg.md` (written BEFORE any
run; the 3 trials were recorded before execution). **Results:**
`engine/data_store/validation/factor_confirmation_gate_2026-07-28.json` (+ determinism twin
`..._twin.json`). **Script:** `engine/scripts/run_portfolio_gate_factor_confirm.py`.
**Window:** ITERATION only, strictly < 2025-01-01; certified anchor (mrpt 0.01, certified
panel insertion order). **Ledger:** 299 → **302**; every DSR deflated by 302.

**Certified-anchor reproduction: EXACT** — the control reproduced
book_h_gapaware_2026-07-22.json to the digit (Sharpe 0.86284, 1637 trades, final equity
292,551.34) before any challenger number was believed.

## The two sleeves tell opposite stories — and the binding rule rejects both

The pre-registered mechanism diagnostic (control trades split by factor agreement at
entry) **refutes the hypothesis for equities and confirms it for alt-crypto**:

| Sleeve | Cohort | Trades | Expectancy | Win rate |
|---|---|---|---|---|
| Equity | factor agrees | 951 | £119.17 | 55.94% |
| Equity | factor **disagrees** (vetoed) | 439 | **£152.21** | **58.54%** |
| Alt-crypto | factor agrees | 98 | **£156.53** | **53.06%** |
| Alt-crypto | factor **disagrees** (vetoed) | 25 | **−£253.30** | **36.00%** |

Gating equity entries on ISWD.L's 63d trend vetoes the *better* half of the book's equity
trades — the challenger's full-window result is catastrophic (total return −0.4% over ~9y,
Sharpe 0.03, 3,955 vetoes). The equity-sleeve expectancy leg loses **0 of 15** CPCV paths.

The alt-crypto challenger is the mirror image: vetoes only 437 entries, keeps the good
cohort, and lands Sharpe **0.905 vs 0.863** (+0.042), 209.0% vs 192.6% total return,
15/15 positive CPCV paths. It still fails the pre-registered bar: sleeve expectancy
improves on **7/15** paths (< 12 required — crypto's late listing leaves several early
paths with zero sleeve trades, counted as not-improved by rule), DSR 0.032 < 0.95, and the
family PBO is 0.622 ≥ 0.5. **A one-sleeve pass is not a partial adoption (prereg §5).**

## The pre-registered adoption rule (prereg §5, binding)

Family adoption requires ALL legs for BOTH challengers:

| Leg | `fac_equity_iswd_63` | `fac_crypto_btc_63` |
|---|---|---|
| Sleeve expectancy ≥ 12/15 paths | **0/15 — FAIL** | **7/15 — FAIL** |
| DSR > 0.95 @ n=302 | **0.000 — FAIL** | **0.032 — FAIL** |
| Sharpe drop ≤ 0.05 | **0.836 — FAIL** | −0.042 (improves) — pass |
| Family PBO < 0.5 | **0.622 — FAIL** | (shared leg) |

**DECISION: FAMILY REJECTED — certified ungated entries stand.** Kill rule engaged on
both challengers.

**DSR caveat (machinery working as designed):** the DSR deflates each config against the
Sharpes of the whole 3-config selection set. This set contains a near-zero-Sharpe dud
(the equity challenger), which drags the null distribution and crushes every config's
DSR — including the control's (0.023 here, vs 0.955 in the FIP gate's 2-config set where
it PASSED). The control's certified quality is unchanged; the number measures the
selection set, and it is reported as computed.

## Full scoreboard (control vs the two sub-sleeve configs)

| | `fac_control_252` (certified) | `fac_equity_iswd_63` | `fac_crypto_btc_63` |
|---|---|---|---|
| Sharpe (ann.) | **0.86284** | 0.02706 | **0.90527** (+0.042) |
| Profit factor | 1.3245 | 0.9992 | **1.3632** |
| Win rate | 55.77% | 51.97% | **56.06%** |
| Expectancy / trade | £120.44 (1.022%) | **−£0.11** | **£133.43 (1.093%)** |
| Max drawdown | **16.32%** | 19.67% | **16.02%** |
| Worst day (ret) | **−5.09% (−£8,527)** | −4.70% (−£5,254) | −5.09% (−£11,418) |
| Worst month | **−£19,673** | −£8,276 | −£23,919 |
| £-per-month (avg) | £1,782.88 | −£3.63 | **£1,935.37** |
| Trades | 1,637 | 1,653 (3,955 vetoes) | 1,609 (437 vetoes) |
| Total return (~9y) | +192.6% | **−0.4%** | **+209.0%** |
| Final equity | £292,551 | £99,608 | **£309,020** |
| CPCV median / frac pos | +0.048 / 15-of-15 | +0.020 / 10-of-15 | **+0.053 / 15-of-15** |
| DSR @ n=302 | 0.023 ✗ | 0.000 ✗ | 0.032 ✗ |
| PBO (3-config set) | 0.622 ✗ | — | — |

## The honest reading

1. **Equity factor confirmation is backwards in this book.** The equity sleeve is
   index-heavy and the 12 single stocks trend hardest in exactly the periods when the
   broad index chops (idiosyncratic trends); requiring ISWD.L agreement vetoes those
   trades and keeps the index-churn whipsaws. The challenger doesn't lose a little — it
   loses *everything* (expectancy −£0.11/trade).
2. **BTC confirmation on alt-crypto is the one leg with real signal** (agree £156.53 vs
   disagree −£253.30; challenger Sharpe 0.905, +£153/month, 15/15 positive CPCV paths).
   It still fails the pre-registered bar: the expectancy leg needs 12/15 and crypto's
   2020+ listing leaves early paths with zero sleeve trades (not-improved by rule);
   DSR at the full ledger count and the family PBO fail too. If the owner wants this
   explored as a single-sleeve gate (alt-crypto only, its own selection set, its own
   PBO), that is a **new pre-registration**, not a reinterpretation of this one.
3. **PBO 0.622 across the family** says the in-sample best config of this 3-config set
   is more likely than not an overfit pick — with the standing caveat that the configs
   share ~100% of their universe, so PBO's discriminative power is limited by
   construction (pre-registered, reported as computed).

## Determinism

Full gate executed twice (seed 42): outputs **byte-identical** modulo `generated_at`
(the rerun dedups the ledger 302 → 302). Unit tests: `tests/test_entry_gates.py`
(11 tests, incl. factor sleeve membership — gold/BTC/FX ungated — trend-sign blocking,
undefined-factor pass-through) + full suite green.

## Ledger

- **n before this gate: 299** (per the FIP gate) → **302** (+3: `fac_control_252`,
  `fac_equity_iswd_63`, `fac_crypto_btc_63`, kind `factor_confirmation_gate`, mrpt 0.01),
  recorded before the first run. Rerun deduped (302 → 302).

## Caveats (pre-registered and observed)

- £ figures are account-currency units on the £100k certified anchor; no FX conversion,
  same as every prior report.
- Alt-crypto history is short (several names list 2020+): early CPCV paths have zero
  sleeve trades and count as not-improved by the pre-registered rule — the 7/15 figure
  is computed exactly as registered.
- The frozen paper test (workflow, state.json, engine/config.yaml live sections) was not
  touched; `entry_gate=None` remains the certified default.
