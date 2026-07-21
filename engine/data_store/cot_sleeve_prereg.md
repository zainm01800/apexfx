# PRE-REGISTRATION (DRAFT — no ledger charges yet) — COT positioning filter on Book H FX+gold legs

**Status: data layer built and cached (2026-07-20); the GATE has NOT run and NO trials are
charged.** Charges happen at gate-run time, after the Book I gate completes. This draft pins the
hypothesis and config set BEFORE any signal code exists, so the experiment cannot drift toward
whatever the data happens to reward.

## Hypothesis (falsifiable)
Extreme speculative crowding degrades trend-entry quality. When net non-commercial positioning
(share of open interest, engine-signed, 156-week z-score, **release-shifted** — obs Tuesday,
usable Friday) exceeds +2σ in the direction of a new Book H FX/gold entry, vetoing that entry
improves the book's gated metrics. Symmetric variant: also veto shorts at −2σ.

## Exactly 2 configs (the full selection set when the gate runs → 2 ledger charges)
1. `book_h_gold_cotveto_252` — Book H+gold, new FX+SGLD entries vetoed when |z| ≥ 2 in the
   entry direction (both sides).
2. `book_h_gold_cotveto1_252` — same with |z| ≥ 1 (sensitivity leg, pre-registered — NOT a
   post-hoc sweep).

Gates: identical to Books H/I — DSR > 0.95 at the FULL ledger count at run time, PBO < 0.5
across {baseline, veto2, veto1}, CPCV 15 paths. Decision rule: adopt the highest-DSR passing
config; none passes → adopt nothing, report honestly.

## Data honesty notes (fixed before the run)
- Point-in-time: only the release-shifted series may join the daily panel
  (`apex_quant.data.cot.as_of_release`, +3 business days). Joining on observation date is
  lookahead and voids the run.
- GBP series is short (370 weeks vs 602): the CFTC renamed the sterling contract; the loader
  keeps the deepest single market name. Fix (union of names) is allowed BEFORE the gate run,
  never after.
- COT covers FX futures + gold only — the veto never touches equity/crypto legs.
- Dead-end guard: this is a FILTER on a passing book, not a standalone signal. A standalone
  COT signal is NOT pre-registered here and must not be run against this document.
