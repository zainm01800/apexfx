# Runner exit gate — REJECTED on PBO, despite beating the baseline on every other measure (2026-07-22)

**Verdict: adopt nothing. The capped 1.5R target stands.** The runner passed DSR, passed
CPCV, and beat the certified baseline on return, Sharpe, drawdown, profit factor, win rate and
cost churn — then failed the PBO leg (0.711 ≥ 0.5), which both books share. Per
`runner_exit_prereg.md` §4 the rule is pass ALL THREE and beat the baseline DSR. It did not
pass all three. **Rejected.**

| | Baseline (capped 1.5R) | Runner (uncapped trail) |
|---|---|---|
| Total return | 265.9% | **266.4%** |
| Sharpe | 1.032 | **1.088** |
| Max drawdown | 15.8% | **15.3%** |
| Profit factor | 1.396 | **1.509** |
| Win rate | 55.8% | **57.0%** |
| Expectancy / trade | £165.19 | **£200.64** |
| Trades | 1,639 | **1,357** |
| DSR (n=213) | 0.9994 ✓ | **0.9997 ✓** |
| CPCV positive | 15/15 ✓ | **15/15 ✓** |
| **PBO** | **0.711 ✗** | **0.711 ✗** |
| Verdict | REJECT | **REJECT** |

## What it changed
One variable: after the 50% partial at 1R + breakeven (unchanged), the remaining half is not
capped at the fixed 1.5R target and is not trimmed by Partial 2 — it rides the existing 2×ATR
Chandelier trail uncapped. Downside untouched (hard stop and breakeven still protect).

The mechanism behaved exactly as the prereg predicted: **282 fewer trades for the same
return** — winners running instead of being cut at 1.5R and re-entered — lifting expectancy
per trade from £165 to £201 and profit factor from 1.40 to 1.51. The pre-registered
counter-hypothesis (lower win rate, fatter drawdowns from giving back open profit) did **not**
materialise; both improved.

## A verification trap found while building the gate
`run_portfolio_cpcv` constructed its own `PortfolioBacktester` with no way to pass a
TradeManager. Left alone, the full-window run would have used the runner exit while **CPCV
silently measured the BASELINE exit and reported it as the challenger's** — an invalid gate
that looks entirely normal. `trade_manager` is now threaded through the CPCV path and the
smoke test confirms the two books produce different CPCV paths.

## The PBO problem — recorded honestly, and raised BEFORE this result
PBO across today's six gates, identical machinery:

| Gate | Configs | PBO |
|---|---|---|
| Book I | 4 | 0.602 |
| Book J | 2 | 0.384 |
| Book K | 2 | 0.710 |
| Book L | 2 | 0.810 |
| Book M | 2 | 0.154 |
| Runner | 2 | 0.711 |

PBO asks: *"if I pick the in-sample winner, does it stay a winner out-of-sample?"* When two
books share the same signal and universe and differ by one exit rule, their returns correlate
~0.99 and their ranking is close to a coin flip — so PBO returns ≈0.5 plus noise. That reads
as "overfit" when it actually means **"these two are statistically indistinguishable."** PBO
was designed to guard against selecting the best of MANY genuinely different strategies; using
it to A/B test near-twins is outside its design.

**This concern was written into the Book K and Book L reports before the runner was run**, so
it is not a rationalisation invented to rescue a liked result. It is nonetheless exactly the
moment where "the test must be broken" becomes an excuse — so **the gate is not overridden and
the runner is not adopted.**

## What the runner has earned
A correctly specified test, not an exemption. "Is B better than A" on two highly correlated
return series is a **paired** question: a block-bootstrap or Diebold–Mariano-style test on the
return *difference*, which has power where PBO has none. That must be pre-registered, charged,
and run like anything else. Until then the certified capped book stands.

## Mechanics
Prereg written 2026-07-22 before the run; mechanism default-OFF and byte-identical to the
certified book when disabled (445 tests green at the time it shipped). 1 trial charged
(ledger **212 → 213**); the challenger's key carries `exit_variant: runner_uncapped_trail`
so it cannot dedup against the baseline. Determinism: two runs identical modulo
`generated_at` and the expected pre/post-charge `n_trials_before`. Iteration window strictly
< 2025-01-01; 2025+ holdout untouched.
Results: `validation/runner_gate_2026-07-22.json`.
