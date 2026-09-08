# V33 experimental forward paper

The user explicitly authorized deployment after the completed 8 September 2026
research audit. Deploy only the higher `cost_aware_half` candidate as **Book V33**.
The unsuccessful lower variant is not substituted for any existing account.

V33 is a separate GBP100,000 namespace:
`__apex_book_v33_forward_paper_runtime__`. It uses generic daily5%/static10%
loss rules, a GBP92,500 internal static floor, half stock-request risk/gross/name
budgets, and the frozen prior-close stressed-cost screen. Stops and actual
partial quantity reductions retain the audited V27B semantics. Broker execution
is disabled; this is an observation account, not funded certification.

Local parity checked the deployable kernel against **all 28 audited higher
V33 base/stress period cases**. Every daily and trade ledger field matches;
maximum net profit difference GBP0. Forward execution deliberately differs
from research terminal liquidation: it preserves open lots, replays from the
original seed, and executes only an instruction durably saved before its next
eligible opening session. After-close daily bars settle simulated fills.

The historical higher-profile evidence is a trade-off: GBP699.64/month full
2017–July2026 average, 6.35% drawdown estimate, Sharpe1.161; stressed GBP353.52/month
and 9.08% drawdown. Fresh2017–2025 annual starts average GBP555.72/month equivalent.
The overall improvement and GBP1,000/month target screens failed. These profits
are never imported into the forward account. Website labels retain that warning.

## Operation

`engine/scripts/run_v33_forward.py` reads only its authoritative namespace.
Normal invocations cannot seed a missing account. Explicit `--activate` may
insert one zero-profit account only when absent. `--dry-run` never persists.
Writes use optimistic revisions and exact authoritative readback. Retrying
activation cannot reset an existing account. Failure preserves positions and
cash and publishes a blocked health state.

`.github/workflows/forward-v33.yml` uses a separate single-writer concurrency
group, daily23:45UTC assessment and10:15UTC retry on weekdays. The XNYS calendar
and pre-open deadlines are checked by the engine; a schedule is not a promise
of an exactly timed GitHub start. No retrospective catch-up entry is allowed.

Required repository secret: `SUPABASE_SERVICE_KEY`. `SEC_USER_AGENT` should
identify the application and a monitored contact email. Complete current SEC
submissions are required for all31stocks; HTTP failures cannot be replaced with
unfiltered signals. Fresh Yahoo prices and publication-qualified BoE FX are
also required. A deployed/seeded account may legitimately remain visibly blocked
until these inputs pass validation. Never describe a successful page/API response
as proof that an engine is currently able to trade.

## Verification

Lifecycle/regression tests cover account identity, independent namespace, no
imported profit, weekends/holidays, pre-open durability, late activation, true
stops, idempotent cash/partial handling, immutable bars, revisions, permanent
halts, strict floors and cost-filter evidence. Frontend tests cover V33 API
isolation, all-book switching, exact profile checks, partial-lot win rates,
research disclosure and detailed trade cards.

All older accounts and their archives are preserved. Deployment-time workflow
and authoritative API outcomes must be recorded separately after publication;
external missing-price, SEC or intraday-warmup blockers must remain visible.
