# Repaired paper accounts — 5 September 2026

The user authorized: **Archive and start repaired accounts**, retaining the original account currencies.

## Scope and evidence

- A/B/C: new GBP 100,000 accounts, explicitly converted sizing, marks, realized P&L and exposure. Repaired entries receive entry-bar stop protection; partial exits below the full target are booked before that target. Legacy backtester compatibility remains opt-in by omitting account currency, but scheduled repaired writers always enable GBP accounting.
- R/S/F: separate USD 100,000 accounts. R retains its monthly ETF strategy and no per-position stop/target. S now has fixed FX units, next-hour-open entries, adverse gap stops, exact cash restoration, persistent daily entry lock and repeat-trade memory, complete saved state, and a Friday closing exit. F uses actual added-lot prices, next-open entries, stop-before-favourable-trigger ordering, realized partials booked once to cash, and 5 bps/side cost proxies.
- Unknown listing currencies, missing conversions, missing state and stale mandatory data fail closed. This is not a guarantee against market gaps, losses or funded-rule breaches.
- Separate immutable-insert archive IDs: `__apex_book_{a,b,c,r,s,f}_archive_20260905__`. Each contains the full public legacy snapshot (up to 500 daily rows; these histories are shorter), plus the original runtime document where one existed.
- Fresh runtime IDs: `__apex_book_{a,b,c,r,s,f}_repaired_v2__`. All six activation writes were read back and hash-verified. No old positions, trades, pending orders or profits were imported. Original legacy tables/runtime documents were not overwritten.
- Future writes require the previously read state hash in a server-side conditional update. A stale concurrent writer cannot overwrite a newer trading state. Operational failure status is separate metadata; recording a blocked run does not advance trading history.
- Website defaults to repaired accounts. `edition=archive` selects the frozen archive; `edition=legacy` is an explicit compatibility view of the untouched original tables. A missing repaired document never falls back to a profitable old history.
- Daily workflow now runs all six independently at 00:35 UTC. S also has an hourly weekday workflow. Both share a single-writer concurrency group. Failures are not masked as successful runs. Old-ledger research-memory resolution is not part of the new workflow.

## Listing-unit verification

Yahoo chart metadata on 5 September reported ISWD.L as `GBp` (pence), and ISDU.L/ISDE.L/SGLD.L as USD. The adapter preserves pence and converts it by 0.01, rather than treating a penny-denominated quote as pounds. The issuer identifies the [ISWD GBP London listing](https://www.ishares.com/uk/individual/en/products/251394/ISWD) and [ISDE USD London listing](https://www.ishares.com/uk/individual/en/products/251392/). Share-class currency alone is not used to infer quote units.

## First operational run

S verified fresh hourly/daily inputs and had no post-activation bar to process. A/B/C/R/F blocked on a missing 4 September stock/ETF daily closing price. Direct checks of both Yahoo chart hosts returned a null daily close, despite a bar timestamp and other OHLC fields. No intraday quote was substituted for that missing daily close. All six accounts remained at their initial balance with zero trades.

## Validation and limits

The focused suite passed 57 Python accounting/runner/storage and existing-book regression tests, plus 53 JavaScript display/API tests. It covers restart equivalence, no duplicate P&L, actual lot bases, GBP/pence conversion, fixed FX units, next-open fills, entry-bar stops, gap loss, partial ordering, daily lock persistence, immutable archive writes, compare-and-swap failures, isolated fresh seeds, and no fallback to old results.

These are engineering tests, not a new blind backtest, profitability proof or funded-account approval. The repaired execution and cost assumptions change the experiment: old statistics do not apply. Real spreads, financing, swaps, fees, liquidity and intrabar funded-loss touches still require broker/product-specific validation. A/B/C use bar-based conversion proxies; R has no position stops. S's daily guard blocks new entries but is not a guarantee of a hard intraday equity floor. No claim of £1,000/month is made.
