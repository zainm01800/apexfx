# Paper deployment verification — 8 September 2026

Implementation commit: `cc09df6bb181ce69572c7bc365986d11cb6698c6`.
Vercel reported successful production deployment. The production dashboard,
V33 rules and Book S position card were inspected in the browser. All twelve
paper-state API requests returned HTTP 200 after the runs below. This proves
publication and readable ledgers, **not** that every strategy can currently trade.

## Authoritative cloud runs

| Books | GitHub Actions run | Result |
| --- | --- | --- |
| V33 | [34247385017](https://github.com/zainm01800/apexfx/actions/runs/34247385017) | Tests passed; separate GBP100,000 seed persisted and read back. Entry processing blocked by AAPL SEC submissions HTTP 403. |
| V27B | [34247387058](https://github.com/zainm01800/apexfx/actions/runs/34247387058) | Tests passed; separate GBP100,000 seed persisted and read back. Same SEC 403 blocker. |
| V6 / V10 | [34247385039](https://github.com/zainm01800/apexfx/actions/runs/34247385039) | Success. Fresh settled XNYS inputs through 4 September; safe unexposed-seed input revision recorded. Both GBP100,000, no positions or pending instructions. |
| V24 / V30 | [34247385840](https://github.com/zainm01800/apexfx/actions/runs/34247385840) | Success, but waiting for frozen warm-up. Three complete cash sessions archived; exact prior fifteen required. Both GBP100,000, entries disabled. |
| A / B / C / R / S / F | [34247385045](https://github.com/zainm01800/apexfx/actions/runs/34247385045) | Every account attempted; combined job failed because A/B/C/F inputs remain incomplete. R and S passed fresh-input checks with no new eligible bar. |

Observed at approximately 15:53 UTC:

- A: SUI/USD missing 6–7 September, latest source bar 4 June 2024.
- B/C: ISWD.L missing 7 September, latest 4 September.
- F: SGLD.L missing 7 September, latest 4 September; one existing pending proposal preserved.
- R: USD100,000, no position; fresh inputs verified.
- S: USD100,057.23 equity, one open position and four closed trades. Existing
  incomplete historical drawdown warning retained; these figures are not a
  profitability audit or proof of contemporaneous pre-open submission.
- V27B/V33: zero imported profits, zero entries, explicit blocked health state.

All workflows remain enabled. No original account or archive was reset. No
broker calls or orders were enabled. The new accounts' explicit activation
cannot overwrite an existing account on retry.

## Outstanding requirements

V27B/V33 require working SEC access for the complete stock universe. The
repository has no SEC_USER_AGENT contact configured; a monitored contact email
was requested from the user. Configuring identification is a first step, not a
guarantee that an HTTP 403 will disappear. Do not bypass the filing exclusions
or substitute today's filings into historical decisions.

A/B/C/F require verified missing bars from their configured feeds or a separately
validated data-source repair. Do not fill gaps with synthetic bars, remove
instruments, or relax completeness checks to make the workflows green.

V24/V30 must finish collecting immutable minute-session warm-up. A successful
collector run is not permission to simulate entries before sufficient history.

## Tests and final display correction

The production V33 kernel exactly matched all 28 audited higher-profile research
cases (maximum net-GBP difference zero). The broader local Python regression
suite passed 143 tests. The JavaScript suite passed 65 tests after adding a
regression for blocked activation: an account seed date must not be shown as
verified market-data freshness. The UI also suppresses stale initial-assessment
dates and assessment promises while blocked or warming up.

Deployment is complete; operational readiness is mixed as explicitly listed
above. No funded-account certification or monthly-income guarantee is made.
