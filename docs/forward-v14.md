# Books V6 and V10: experimental forward-paper operation

Books V6 and V10 are independent virtual GBP100,000 accounts. They run the
sealed V14 `regime_switch_5day` hypothesis under the static 3%/6% and 5%/10%
loss profiles respectively. They are **experimental paper evidence**, not live
broker accounts and not funded-qualified strategies. The sealed V14 study did
not pass its funded-qualification gates.

The workflow runs after the US close on weekdays. A signal uses a settled ETF
close and a strictly earlier Cboe VIX observation. It can fill only at the
immediate next official XNYS open and only when the pending instruction was
persisted before that open. Missed historical decisions are logged as evidence
gaps and are never backfilled. Existing positions continue through deterministic
stops and time exits if a workflow run catches up after an outage.

Fresh ETFs use the same `yfinance` adjusted-OHLC definition as V14. Each pending
decision freezes its input hash. Previously observed anchor bars are never
silently rewritten: a uniform corporate-action adjustment rebases synthetic
units and price levels without changing accrued P&L, while a non-uniform vendor
revision fails closed. GBP conversion uses publication-aware Bank of England
XUDLUSS references available by the XNYS open and at most six calendar days old.
This is a reference conversion, not a provider CFD execution quote.

Runtime state is one atomic namespaced `apex_analyses.feature_vector` document
per book. The service-role write is read back and hash-verified before the local
mirror advances. `--dry-run` writes neither store. No module in this package
imports or calls IBKR, MT4, or any broker adapter.

The GitHub workflow is the single authorized writer for these two namespace
IDs and serializes every run globally. The stored parent hash, revision check
and read-back verification detect stale or conflicting writes, but the generic
Supabase upsert is not a database compare-and-swap transaction. Do not run a
second external/manual writer against either ID; any future multi-writer design
must first add a transactional database lock or conditional-write function.
