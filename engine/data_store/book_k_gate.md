# Book K gate — REJECTED on PBO 0.710. Breadth is now CLOSED for this book (2026-07-22)

**Verdict: adopt nothing.** Both configs failed the PBO leg (0.710 ≥ 0.5), and the challenger
also lost the DSR comparison (0.9988 vs baseline 0.9992). Per `book_k_prereg.md` §1 — which
fixed this boundary in advance — **breadth is closed permanently for this book: no further
selection rules, no further config counts.**

| | Baseline (39 inst) | +12 independent (51 inst) |
|---|---|---|
| Sharpe | **1.032** | 0.963 |
| Total return | **266%** | 253% |
| Max drawdown | 15.8% | **14.7%** |
| Trades | 1,639 | 1,685 |
| DSR (n=210) | 0.9992 | 0.9988 |
| **PBO** | **0.710 ✗** | **0.710 ✗** |
| Verdict | REJECT | REJECT |

## What was tested

The selection rule was mechanical and ex-ante, using **no performance data**: reject any of
the 59 screened candidates correlating ≥0.50 with an existing holding (removed 22 of 59 —
every semiconductor, every energy name), then take the 12 lowest mean-correlation survivors:
`ABBV, MRK, PG, KMB, PEP, CL, MDLZ, PFE, ABT, ORLY, KO, MNST` (mean |corr| 0.048–0.084).

This was a genuinely different hypothesis from Book J, not a re-roll: Book J's sector-label
proxy had admitted measurable clones (XOM 0.885, CVX 0.859 vs XLE, already held).

## The answer, and it favours the pre-registered counter-hypothesis

PBO 0.710 means that across time splits, **the config that looked better in-sample was
below-median out-of-sample 71% of the time** — the ranking is worse than a coin flip. The two
books are statistically indistinguishable; there is no reliable basis for preferring the
expanded one.

The pre-registered counter-hypothesis (§4) is supported: these 12 are defensive staples and
large pharma, and they are uncorrelated with the book **precisely because they do not
participate in the momentum regime the signal harvests**. Low correlation is not a tradeable
trend edge. Maximising independence did not rescue breadth — it produced a book that trends
less, and the comparison collapsed into noise.

Combined with Book J (24 sector-picked names: Sharpe 1.03 → 0.90, and the added names lost
£17,932 in their own right while crowding £18,224 out of the originals), the conclusion is
consistent across two independent selection methods: **this signal's edge is concentrated in
high-momentum mega-caps, and adding instruments dilutes a fixed risk budget rather than
adding breadth.**

## Mechanics
Prereg written before the run; 1 trial charged (ledger **209 → 210**). Determinism: two full
runs identical modulo `generated_at` and the expected pre/post-charge `n_trials_before`.
Iteration window strictly < 2025-01-01; 2025+ holdout untouched.

## Methodological note recorded for future gates
Book H's own DSR and pass/fail status **move with the comparison set**: 0.99955 (Book I,
n=208), 0.99658 (Book J, n=209, PASS), 0.99917 (Book K, n=210), 0.94996 (Book M, n=212,
FAIL). DSR deflation depends on the spread of trial Sharpes in the run, and PBO is
set-relative by construction. A single gate run is therefore evidence about a *comparison*,
not an absolute certificate of a book. Quote both the value and the run it came from.
