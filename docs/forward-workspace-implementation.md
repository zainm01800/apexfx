# Compact forward-book workspace

## Scope

- Existing Vercel website retained. IBKR navigation and home redirects removed; broker APIs and execution backend are not changed.
- Books is the home page. V6 and V10 are independent experimental GBP100,000 paper accounts using the five-day V14 regime-switch method.
- Equity, forward observations and loss headroom share one overview. Open, pending, closed and rules are separate tabs. Legacy books preserve their stored ledgers and detailed cards.
- Compare reads official equity, without adding floating P&L twice. Research holds retrospective evidence and the existing experiment feed; it does not duplicate account-progress panels or claim automatic graduation.

## Truthfulness and boundaries

- No retrospective profit is imported into the new books. A missing state produces an unavailable/not-activated message, not a fabricated £100,000 balance.
- GBP values come from the saved ledger. No browser quote or guessed exchange rate changes official new-book metrics.
- New cards show actual protective stops, five-session time exits and account guards. No invented take-profit, partial exit or breakeven protection is displayed.
- The historical validation failed. Forward observation is not funded approval and cannot guarantee £1,000 monthly profit or compliance with a firm's tick-level rules.
- Paper fills are evaluated from completed daily bars after the close. No broker order is submitted.

## Local checks

```sh
node --test tests/paper-api.test.js tests/forward-model.test.js
node scripts/preview-forward.mjs
# Separate, visibly labelled synthetic UI fixtures only:
node scripts/preview-forward.mjs --fixtures
```

The normal preview serves only public files and read-only paper/research endpoints. Fixture mode listens separately on port 3002 and does not contact or modify a portfolio. Test fixtures and preview scripts are excluded from Vercel deployment.

## UI verification

- Desktop and 390px mobile layouts inspected in the browser; no horizontal page overflow on the mobile book view.
- New profile selection, detail tabs, expanded stop/rationale cards and missing-ledger state checked.
- Legacy Book C fetched its actual stored positions and summary through the corrected API. Legacy comparisons returned all six existing books without altering their data.
- Settings and primary navigation are shared across existing pages; the broker terminal remains available only by its unlisted direct URL.

## Rollout

Publish only these scoped implementation changes, keeping earlier research-only local commits separate from the production branch. Verify production HTML/API, run the isolated V14 workflow, then read back both namespaced states. Do not report forward readiness until the new workflow and persisted payloads have been checked.
