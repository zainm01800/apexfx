# AUDIT — the certified Sharpe is largely an artifact of instrument ORDER (2026-07-22)

**Finding: running the identical book, identical data and identical seed, changing ONLY the
order instruments are iterated, moves Sharpe from 0.217 to 0.863 and total return from 19% to
193%. The certified ordering produced the BEST of seven tested orderings.**

| Ordering | Sharpe | Return | Trades | maxDD |
|---|---|---|---|---|
| **gate order (certified)** | **0.863** | **192.6%** | 1,637 | 16.3% |
| shuffle 1 | 0.504 | 64.0% | 1,672 | 18.2% |
| shuffle 2 | **0.217** | **19.2%** | 1,717 | 18.2% |
| shuffle 3 | 0.502 | 60.8% | 1,666 | 17.7% |
| shuffle 4 | 0.823 | 130.7% | 1,688 | 16.6% |
| shuffle 5 | 0.525 | 65.7% | 1,688 | 18.6% |
| shuffle 6 | 0.663 | 94.6% | 1,647 | 19.0% |

Sharpe: min 0.217, max 0.863, **spread 0.645, sd 0.204**. Median shuffle ≈ 0.52.
Measurement only — no strategy change, no ledger charge. Script:
`scratch/audit_ordering_sensitivity.py`.

## Mechanism

`PortfolioBacktester.run()` evaluates same-bar candidates in **dict insertion order** and
provisionally books each permitted one so later candidates see the caps:

```python
for inst in instruments:            # arbitrary order
    pos = self.risk.permit(...)
    if pos.permitted:
        book = book + [OpenPosition(...)]   # occupies a slot immediately
```

`RiskManager` caps the swing bucket at **10 concurrent positions**
(`_BUCKET_LIMITS["swing"]`). Once full, every later candidate that bar is vetoed —
`timeframe_bucket_full` fires **18,147 times** in the certified book, versus 1,840 for the next
most-binding constraint. So on any crowded day the book takes **the first ten instruments it
happens to iterate over**, not the ten best.

`EQUITY_CORE` is hardcoded as `AAPL, MSFT, NVDA, META, AMZN, GOOGL, TSLA, AMD, PLTR, TSM,
NFLX, UBER` — the mega-cap winners of the backtest decade, listed first. The book therefore
hands scarce slots to the best-performing names *because of the order they were typed into a
list*.

## Consequences

1. **The honest expectation for this book is nearer Sharpe ~0.5 than 0.863.** Live, there is no
   reason to believe the arbitrary ordering in use is the lucky one. The certified figure
   should be treated as the top of a distribution, not the centre of it.
2. **Every gate comparison run to date shares this fragility.** All books were measured on the
   same ordering, so *relative* verdicts (breadth dilutes, runner edges ahead) are unaffected —
   but absolute numbers are not robust.
3. This explains the 2026-07-22 discrepancy where an ad-hoc harness produced 1,718 trades and
   the gate produced 1,639 from identical inputs: the harness sorted the panel alphabetically.

## The fix, and why it is not curve-fitting

`Signal.probability` — *"calibrated P(trade is profitable)"* — is already computed for every
candidate and then **discarded at exactly the moment capital is scarce**. Allocating slots by
expected value (`p·b − (1−p)`) instead of iteration order:

* uses only point-in-time data already in hand (no lookahead),
* replaces an arbitrary tie-break with the definition of allocating scarce capital,
* **removes the ordering dependency entirely**, so the result stops being luck.

This must still be pre-registered and gated — and critically, **gated against a
shuffled-order baseline, not the lucky certified ordering**, or the comparison inherits the
same artifact it is meant to remove.

## Required change to gate methodology

Every future gate should report Sharpe across **N shuffled orderings** (median and spread), not
a single pass. A book whose edge survives only one ordering has not been demonstrated to have
an edge.
