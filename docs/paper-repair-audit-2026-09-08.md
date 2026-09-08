# Paper-engine repair audit — 8 September 2026

## Publication follow-up

The user subsequently authorized publishing the repairs. The unsupported Book S monthly-profit/Sharpe promotion and mixed-currency winner badges have been removed while retaining the card layout and entry/exit details. V27B is included on the book, comparison and research pages. New regression tests cover those claims and the isolated V27B API namespace. This publication does not itself activate V27B or resolve SEC access, missing market data, or intraday warmup requirements. The section below records the audit-time state before this authorization.

## Publication state

These are local implementation and verification findings, not confirmation of deployment or activation. This repair turn has not submitted broker orders, written remote paper accounts, reset existing histories, dispatched workflows, committed, or pushed.

A concurrent editor committed `c39d295` at 14:33 BST while the audit was running. That commit combines some unfinished audit UI changes with other edits to `public/` (profit ranking, Book S promotion and research claims). Root did not make that commit. Publication is paused for coordination; do not blindly stage all files or publish those claims. Existing unrelated research files also remain untracked.

## Book S: what is and is not verified

The current repaired runtime is `__apex_book_s_repaired_v2__`, a separate **USD 100,000** account activated on 5 September 2026. It is not the archived Book S history or a GBP account.

Read-only inspection at approximately 13:01–13:06 UTC found four closed trades and one open trade. All five entry directions, entry thresholds, prior-day EMA trend conditions, hourly ATR stop distances, and simulated prices matched an independent reconstruction from Yahoo hourly bars. The coded strategy is an Asian-range breakout with a prior-day EMA50 trend filter; it does not implement fair-value-gap, order-block, liquidity-sweep, BOS/CHOCH or order-flow confirmation.

| Trade | Simulated entry | Closed net USD |
|---|---|---:|
| GBP/USD long | 7 Sep 08:00 UTC | +242.8688 |
| EUR/USD long | 7 Sep 08:00 UTC | +62.4890 |
| AUD/USD long | 7 Sep 08:00 UTC | −42.7477 |
| USD/JPY short | 7 Sep 09:00 UTC | +187.0449 |
| USD/CHF long | 8 Sep 08:00 UTC | Still open at the inspected snapshot |

The four closed trades total **USD 449.6550**, with three winners. Each closed via the 16-hour time rule, not an invented take-profit event. Fixed-unit quote-to-USD P&L reconciled to floating-point tolerance. Stops were not crossed before those exits on the examined hourly bars.

The inspected saved account equity was USD 100,607.2991, including USD 157.6441 open profit. It used only the 08:00–09:00 UTC completed hour and was already stale at inspection. A later 12:00–13:00 price applied to the same existing CHF lot would give approximately **USD −133.30** open P&L, not +157.64. That was an indicative mark check, not a full subsequent signal replay or a persisted account update.

Independent hourly-close reconstruction found approximately **0.468653% maximum drawdown (USD 470.54)**. The old 0% display was false: daily compression discarded intra-day observations. Hourly-close drawdown is itself not tick-level maximum risk. The repair adds a durable hourly running tracker, but honestly labels earlier coverage incomplete instead of claiming the historical gap has been fixed.

These are rule-consistent bar-based simulations, not certified real-time forward fills. The runner may reconstruct earlier eligible signal/open pairs during catch-up; the ledger lacks immutable proof that every instruction was durably recorded before its simulated fill. Four closed trades cannot establish a profitable edge. Fixed spreads omit variable execution costs, and funded-account suitability has not been proven.

The original archived Book S ledger remains untrusted and unchanged. An independently checked archived JPY trade exhibited a quote-currency conversion error; the old reported profits must not be combined with the repaired account.

## V27B implementation

Added a dedicated `forward_v27b` package, runner, API namespace, paper workflow and website profile. This is the selected higher joint-portfolio variant: monthly top-three ETF trend allocation plus up to four five-session stock-reversal positions sharing one fresh GBP 100,000 account. It uses a 1% instrument risk ceiling, 3.375% aggregate stop risk, 2× gross exposure, 5% daily / 12% research static loss model, and GBP 91,000 internal halt. This is not a claim that it satisfies a particular funded firm's rules.

Forward state uses first-seen immutable execution observations, exact state readback, optimistic revisions, replay from the original seed, and permanent halts. It does not terminal-flatten open holdings or add replay profits to already-mutated cash. New decisions require complete input evidence plus a hash of the entire executable instruction. The eligible session must be the next exchange session. A two-phase durable-readback confirmation before the eligible open is required for execution; unconfirmed or late plans cannot produce retrospective fills. The website must describe unconfirmed plans as non-executable.

Independent review reproduced and fixed two startup/recovery retroactive-fill cases. A seven-session gap scenario kept prior history unchanged and a large-loss halt permanent. There was no capital resurrection or duplicate P&L.

**External blocker:** SEC filing-data requests returned HTTP 403. The strategy must not bypass its filing exclusion or substitute unfiltered entries. A monitored contact email has been requested for `SEC_USER_AGENT`; configuring it still requires a successful access test. No new V27B account has been activated by this repair turn.

## Other paper books

- **A/B/C/F:** daily completeness now checks every required unprocessed session, using the appropriate asset calendar. The earlier A BTC observation became available later, but ISWD.L and SGLD.L were still genuinely missing the 7 September daily bar. No synthetic daily reconstruction or incomplete-bar substitution was introduced. Existing books and pending orders are preserved.
- **R:** existing state was fresh but had no new eligible session at inspection; absence of a trade is not evidence of a fault or profitability.
- **S:** added honest hourly observation, incomplete-risk-history and stale-market-mark display behavior. Existing trades and P&L are not rewritten.
- **V6/V10:** narrowly allow audited high/low-only provider corrections to completely unexposed seeds with saved NO_SIGNAL decisions, identical opens/closes and no positions, fills, fees or pending orders. Other nonuniform changes remain blocked. An in-memory rehearsal against both actual saved seeds passed with unchanged GBP 100,000 balances and no invented orders. Error health reporting preserves trading state.
- **V24/V30:** replaced partial-session replay with complete settled-session processing, an immutable raw-minute archive, exact prior-session features, publication-qualified BoE conversion and revision-checked existing-account persistence. No activation/reset or unavailable-remote-to-local fallback remains. Minute-level conservative drawdown is retained separately from close drawdown. These are explicitly after-close paper reconstructions, not contemporaneously submitted intraday orders.

The V24/V30 read-only live-data dry run passed. Both remained flat at GBP 100,000 with `waiting_for_frozen_warmup`. Yahoo's short 1-minute retention cannot bootstrap the exact prior 15 cash sessions; the runner must accumulate those complete sessions before entry eligibility. Missing active-session data still blocks advancement. The BoE request needed an identifying User-Agent: default httpx returned 403; the declared ApexFX client returned valid XML.

## Verification completed locally

All **114 Python tests** passed across:

- V27B lifecycle and integrity: 25
- Intraday settlement, accounting and persistence: 18
- Book S observation tracker: 12
- Paper-input and seed-correction repairs: 14
- Existing V14 forward: 17
- Existing repaired accounting: 17
- Existing repaired runtime: 11

All **58 JavaScript tests** passed across API contracts, all-book switching, trade cards, P&L, incomplete drawdown, freshness and non-executable pending-plan labels. `git diff --check` passed. These are correctness tests, not new profitability or funded-compliance backtests.

## Remaining before declaring anything live-ready

1. Coordinate with the concurrent website editor. Preserve the requested layout, remove or substantiate unsupported Book S historical figures and mixed-currency '#1 profit' rankings, integrate V27B into comparison/research pages, and rerun focused UI/API tests. The concurrent commit still contains unsupported promotional claims.
2. Resolve SEC identification/access for V27B, or expose it honestly as blocked without trading.
3. Review and commit only the intended source, tests, workflow and documentation files; do not scoop up unrelated research assets or account-state mirrors. Verify the GitHub/Vercel deployment.
4. Use explicit paper-only V27B activation and existing-account repair steps. Verify authoritative database writes and exact readback, then check actual workflow outcomes. Never reset existing books or import historical profits.
5. Confirm provider-blocked daily books remain visibly blocked and intraday books remain visibly warming up. Do not label all engines ready merely because an API returns 200 or a workflow is green.
