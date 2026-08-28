# A/B/C Audit and Book R Research Result

**Audit date:** 2026-08-28
**Scope:** public paper-book records through 2026-08-27, repository code and
cached daily data.  No broker orders, paper-state writes, or deployment were
performed.

## Executive conclusion

There is **no defensible overall A/B/C winner yet**.

- Book A has a materially poor forward sample, but a short sample is not proof
  that every signal should be inverted.
- Book B has a smaller negative sample and is not an isolated A/B test.
- Book C has no closed trades, so its small positive mark and annualised ratios
  are not evidence of an edge.
- The current multi-asset figures are labelled GBP even though the accounting
  layer does not apply historical quote-to-GBP conversion consistently.  Their
  cash values therefore cannot be used to rank books or size a live account.

The best **new research candidate**, using the frozen Book R rule below, is
**R-252**.  It passed its pre-registered in-cache selection rule and produced a
positive 2023–2024 retrospective-validation result after 2x costs.  It is still
**research-only, not promoted, not funded, and not a true blind result**.

## What the live website records actually show

Source: the public `/api/paper` endpoint, queried 2026-08-28.  The table uses
the stored daily equity series; it does not repeat the site's client-side live
mark overlay.

| Website book | Stored paper window | Stored return | Stored max DD | Closed-trade ledger |
|---|---|---:|---:|---|
| A — internally `book_d_multiasset_252` | 16 Jul–27 Aug (43 snapshots) | -4.0451% | 6.4824% | 17 trades; 3 wins / 14 losses; -4,936.59 |
| B — internally `book_h_gold_252_spill50` | 10–27 Aug (18 snapshots) | -0.5485% | 1.6663% | 5 trades; 1 win / 4 losses; -1,098.13 |
| C — internally `book_c_champion_ensemble_63_126_252` | 19–27 Aug (9 snapshots) | +0.2107% | 0.6401% | 0 closed trades |

These values are useful for **direction, trade counts, and diagnosis**.  They
must not be read as reconciled GBP profit because of the accounting issue below.

### Book A

The live failure is concentrated rather than diffuse: all 11 closed shorts
lost, totalling -5,093.67, while the six closed longs totalled +157.08.  Fifteen
of its 17 exits were stops.  This is a legitimate warning that the short sleeve
and its exposure control were badly matched to this short forward regime.

It is not enough evidence to flip the strategy.  Existing historical research
also finds longs stronger than shorts, but the previously tried short-veto did
not pass its overfitting gate (`PBO = 0.89475`).  A post-loss reversal of every
short rule would be another data-mined variant, not a validated repair.

The historical source for the website's “Certified” A label is
`portfolio_gate_multiasset_2026-07-17.json`.  Its own gate says
`passed: false` because DSR was 0.9335, below the 0.95 requirement.  The
dashboard label should therefore be softened or accompanied by the exact
protocol/version.

### Book B

B is neither sufficient evidence of a fix nor a clean test of the spillover
gate.  It changes the universe as well as the gate; and the spillover gate only
applies to crypto/FX entries, not the equity shorts that dominate its current
losses.  Across its 17-return overlap with A it led by only 0.1202 percentage
points—far too little to declare a winner.

Its historical `PBO = 0.48525` is only narrowly inside the stated 0.50 gate,
and the spill50 version's historical maximum drawdown was worse than its
baseline.  That is not a robust enough foundation to call it a production
replacement.

### Book C

C's +0.2107% is entirely an open-position mark.  Its reported Sharpe 1.76 and
Sortino 6.75 come from eight return intervals and zero closed trades; these
ratios are not interpretable.  Its existing deep audit already labels its
post-2025 section `iteration_plus_nonblind_verification`, not a fresh blind
test.  It also loses its historical edge when the selected 12 US names are
removed, which exposes concentration/survivorship sensitivity.

## Website and engine faults found

1. **Race-page max drawdown.** `public/ab-race.js` used `Math.min` even though
   stored drawdown is a positive loss fraction.  It could display 0.00% max DD
   whenever an at-peak row existed.  The local source is corrected to
   `Math.max`; this change has **not been deployed**.
2. **Currency accounting.** The engine labels the account GBP but calculates
   raw `price difference × units` without an as-of quote-to-account rate for
   USD equities/crypto or non-USD FX quotes.  That affects cash, NAV, gross
   exposure, risk, costs, and the historical comparison—not just formatting.
3. **Live-overlay double count.** Stored daily equity already includes the
   engine's open mark.  The race page adds a separately reconstructed live
   open P&L on top, producing a non-reconciling display.  The Book page also
   mixes raw closed P&L and client-side converted open P&L.
4. **Entry-bar execution.** The core portfolio/paper loop fills a next-open
   order after managing the bar's existing positions, so a new entry cannot
   stop or target on its entry bar.  A future engine fix needs an explicit,
   conservative stop-first intrabar convention and tests.
5. **Exit-policy mismatch.** A full 1.5R target is checked before the claimed
   1.5R partial target, so the full target closes first.  A book must declare
   a coherent fixed target or a genuine partial/runner ladder before another
   engine-wide result is promoted.

Until 2–3 are repaired and historical NAV is recomputed, do not compare the
books by their displayed GBP amounts or use them for broker sizing.

## Book R — the replacement research control

Book R deliberately does **not** bolt subjective SMC annotations onto a daily
multi-asset backtest.  The repository's EUR/USD M1/M5 coverage is only days or
weeks long, midpoint rather than bid/ask, and cannot support a credible
multi-year 4h → 1h → 15m → 5m/1m SMC promotion test.

Instead, R is a small, controlled USD benchmark that avoids the present
currency and intrabar bugs:

- USD account, $100,000 starting NAV;
- ten explicitly whitelisted US-listed USD ETFs only;
- long-only positive cross-sectional momentum;
- at most three equal-weight positions and at most one per predeclared
  economic cluster;
- month-end-close signal, next-common-session-open fill;
- 95% maximum gross, no leverage or shorting;
- 5 bps per side base cost plus a 10 bps per-side stress run; and
- final close liquidation cost, so the last open position is not a free exit.

It uses daily **price** returns.  Dividends are not reconstructed, so this is
not a total-return ETF benchmark.  The full frozen specification and input
hashes are in `book_r_usd_etf_prereg_2026-08-28.md` and the accompanying JSON.

## Pre-registered selection result

The only candidates were 63-, 126-, and 252-session lookbacks.  They were
selected using only 2016-01-04–2022-12-30.  Eligibility required positive base
and 2x-cost return, at least four positive years, base max DD <= 25%, and at
least 48 scheduled selections.  The rank was highest 2x-cost Calmar, then
lower base DD, then longer lookback.

| Candidate | Research return | Research max DD | 2x-cost return | 2x-cost Calmar | Outcome |
|---|---:|---:|---:|---:|---|
| R-63 | +73.10% | 25.76% | +67.43% | 0.296 | Ineligible: DD > 25% |
| R-126 | +40.76% | 35.42% | +36.72% | 0.127 | Ineligible: DD > 25% |
| **R-252** | **+126.65%** | **23.63%** | **+122.39%** | **0.512** | **Selected** |

### R-252 retrospective validation (2023-01-03–2024-12-31)

| Cost assumption | Total return | Annualised return | Sharpe | Max DD | Calmar | Cost paid |
|---|---:|---:|---:|---:|---:|---:|
| 5 bps per side | +26.73% | 12.65% | 0.86 | 13.21% | 0.96 | $812.83 |
| **10 bps per side (2x)** | **+25.78%** | **12.23%** | **0.83** | **13.27%** | **0.92** | **$1,620.02** |

The selected R-252 candidate therefore passes its internal validation return
and drawdown gates.  Its 2025-01-02–2026-08-19 **known-data replication** was
also positive (+68.79% base, +68.08% at 2x costs; 16.49%/16.55% max DD), but it
is expressly not called OOS or blind because those data were already present
in the repository.

## What “blind” means here

The test is reproducible, causal, frozen, and contains a retrospective holdout.
It is **not a true blind historical test** because the data cache had already
been available to prior repository research.  No code can honestly turn an
already-viewed cache into an unseen lockbox.

The true next test is to freeze this exact R-252 code/hash/data manifest, then
either have an independent party release an unseen data interval once, or run
forward paper from the freeze date without tuning.  Only after that—and after
the account-currency/NAV fixes—should it be compared to A/B/C as a replacement.

## Reproducibility

```bash
cd /path/to/apexfx
engine/.venv-mac/bin/python -m pytest -q engine/tests/test_book_r_usd_etf.py
engine/.venv-mac/bin/python engine/scripts/run_book_r_preregistered.py
```

The generated JSON includes source hashes, each parquet SHA-256, every
selection/fill, an equity curve, all three research candidates, and the frozen
promotion status.  It never writes a trial ledger or paper state.

The published generated ledgers are losslessly split into numbered gzip parts
under `engine/data_store/validation/book_r_*2026-08-28.parts/`. Reconstruct one
with `cat <parts-directory>/part-* | gzip -dc > audit.json`; verify individual
parts against the adjacent `SHA256SUMS` first.

Verification completed for this audit: all engine tests passed, the dedicated
Book R regression tests passed, JavaScript syntax checks passed for the changed
race page, and an actual-data future-poison check confirmed that changing every
ETF bar after 2022-12-30 cannot alter the frozen R-252 research-segment result.
