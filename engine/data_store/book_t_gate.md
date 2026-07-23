# BOOK T GATE — META-LABEL: **REJECTED (the gate is anti-predictive)**

**The secondary model does not just fail to help — it removes BETTER trades than it keeps.**
Win rate falls 55.4% → 52.5-53.2% and per-trade expectancy falls +1.542% → +1.058-1.472% at
every threshold. The pre-registered precision test required both to RISE. Ledger 252 → **258**.

Prereg: `meta_label_prereg.md` (written before the run).
Results: `validation/book_t_gate_2026-07-23.json`.

## Result — test window 2019-01-01 → 2025-01-01, secondary fitted only on bars before it

| config | £/mo | Sharpe | trades | win rate | expectancy | fwd p95 |
|---|---|---|---|---|---|---|
| **baseline (no gate)** | **£579** | **0.923** | 1,096 | **55.4%** | **+1.542%** | 11.3% |
| meta @ 0.50 | £223 | 0.439 | 1,034 | 52.5% | +1.058% | 11.6% |
| meta @ 0.55 | £252 | 0.503 | 962 | 53.2% | +1.287% | 11.0% |
| meta @ 0.60 | £239 | 0.482 | 894 | 53.1% | +1.472% | 10.9% |

Paired block bootstrap vs baseline: **Δsharpe −0.484 / −0.419 / −0.441, p = 0.995 / 0.979 /
0.983.** Decisively worse, not merely unproven. DSR 0.278–0.343.

## The pre-registered checks, scored

1. **"The gate must bite" (5–60% veto):** PASSED — veto rates 5.7%, 12.2%, 18.4%. The model
   was active, not inert. This matters: the failure is not a plumbing bug.
2. **"Precision must improve":** **FAILED at every threshold.** The prereg said in advance that
   if trade count falls while expectancy stays flat, the gate is filtering at random and should
   be rejected. What actually happened is worse than random — **expectancy went DOWN**, meaning
   the trades it vetoed were on average *better* than the ones it kept. The secondary model is
   anti-predictive on this book.
3. **Drawdown neutrality:** PASSED (10.9–11.6% vs 11.3%). The mechanism's one promise held —
   it did not add risk. It just destroyed return.

## Why it failed, honestly

The counter-hypothesis recorded in the prereg looks right: **momentum P&L is right-skewed.** A
minority of large winners carries the book. A classifier trained to predict "does this hit
target before stop" optimises for *frequency* of winning, not *size* — so it preferentially
vetoes the volatile, uncertain-looking setups that produce the big winners, and keeps the
tidy-looking ones that produce small gains.

The threshold pattern supports this: as the threshold rises 0.50 → 0.60 the veto gets more
aggressive AND expectancy recovers toward baseline (+1.058% → +1.472%). The gate is least
damaging when it is most selective — i.e. its low-confidence predictions are the actively
wrong ones. A model with real signal would show the opposite.

Only 27 of 39 instruments had enough clean triple-barrier labels to fit at all, which further
thins whatever signal existed.

## What this closes

Meta-labelling was the last mechanism identified that could raise Sharpe **without** touching
the risk profile. It is now tested and rejected on this book, as it previously was on
single-pair FX.

**The sizing search (~110 configurations) and the decision-quality search are both exhausted.**
Nothing further will raise profit at constant drawdown on this signal. The remaining routes are
unchanged and both external to the engine:

1. **More capital** — £120k gives £705/month at the same 12% drawdown.
2. **A genuinely different primary signal** — not a filter on this one.

## Decision

**ADOPT NOTHING.** The live config stays at 0.75% risk / 12 slots / 39 instruments,
£587/month, Sharpe 0.893, forward p95 12.0%.

Ninth consecutive experiment to fail its own pre-registered gate today. The baseline surviving
nine honest attacks is itself the strongest evidence available that it is a real local optimum.
