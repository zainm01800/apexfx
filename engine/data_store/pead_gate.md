# Sleeve Gate — PEAD: post-earnings-announcement drift, halal-screened large caps — 2026-07-19

**Pre-registration:** `engine/data_store/pead_prereg.md` (written BEFORE any run; universe,
6-config grid, gates, no-more-sweeps clause — this report changes nothing that was
pre-registered).
**Window:** ITERATION only, strictly < 2025-01-01 (daily bars). **The 2025+ holdout was not
touched in any way.**
**Script:** `engine/scripts/run_pead_gate.py`. Machine-readable output:
`engine/data_store/validation/pead_gate_2026-07-19.json`.
**Ledger:** 6 trials recorded at pre-registration, **n_trials 199 → 205**; every DSR below is
deflated by **205**.

## What the sleeve is

Event-driven PEAD on 33 halal-screened US large caps (no banks): enter LONG one bar after an
earnings-gap of ≥ +2%, hold the pre-registered horizon (5/10/20 bars), exit on time. Barrier
exit mode; warmup 70 (documented deviation from the 250-bar trend-gate warmup — event-driven
signals don't need a trend lookback). Ablations: `vol` (volume filter), `madj`
(market-adjusted gap). The full managed risk stack (max_portfolio_risk 6.5%, gross 3×,
cluster caps, regime scale) was binding throughout, per the constraint log.

## Verdicts (DSR deflated by 205; PBO across the full 6-config selection set)

**PBO across 6 configs = 0.829 > 0.5 → every config fails the set-level overfit gate.**

| Config | Sharpe | PF | Win | MaxDD | Trades | DSR (>0.95) | CPCV med / frac+ | Verdict |
|---|---|---|---|---|---|---|---|---|
| `pead_h10` (headline) | 0.86 | 1.82 | 61% | 2.7% | 389 | **0.961 ✓** | +0.063 / 93% ✓ | REJECT (PBO) |
| `pead_h05` | 0.62 | 1.46 | 57% | 2.5% | 397 | 0.853 ✗ | +0.036 / 87% ✓ | REJECT |
| `pead_h20` | 0.86 | 1.93 | 59% | 5.0% | 363 | **0.960 ✓** | +0.060 / 93% ✓ | REJECT (PBO) |
| `pead_h10_vol` | 0.90 | 1.86 | 62% | 2.7% | 386 | **0.970 ✓** | +0.066 / 93% ✓ | REJECT (PBO) |
| `pead_h20_vol` | 0.87 | 1.95 | 59% | 5.0% | 362 | **0.962 ✓** | +0.063 / 93% ✓ | REJECT (PBO) |
| `pead_h10_madj` | 0.87 | 1.81 | 60% | 3.1% | 387 | **0.961 ✓** | +0.058 / 93% ✓ | REJECT (PBO) |

PASS required ALL of DSR > 0.95, PBO < 0.5, CPCV median > 0 with > 50% positive paths.
Five of six configs clear DSR at the hardest ledger count this project has ever used, and
every config's CPCV is 13–14 of 15 paths positive — **and none of it matters, because PBO
0.83 fails the entire selection set. Per the prereg: REJECT — not another sweep.**

## The honest read — the strongest rejection this project has issued

- **Why PBO kills a family where everything looks positive:** the six configs are near-clones
  (hold 5/10/20 × minor filters; Sharpes 0.62–0.90). When the selection set is that tight,
  the in-sample *ranking* among configs is mostly luck, and PBO measures exactly that: an
  ~83% chance the config you'd pick in-sample underperforms out-of-sample. The gate is
  behaving as designed — it certifies *selections*, not vibes.
- **What the texture says (recorded, not claimed):** the mechanism check passed — exit
  reasons are ~99–100% `time` (389/389, 386/386, 360/363…), i.e. the P&L is earned by
  *drift over the holding window*, not by stop/target artifacts. Winners are the names PEAD
  literature would predict (high-uncertainty growth: PLTR, TSLA top every config). The
  signal character is consistent with one of the most replicated anomalies in the
  literature. That is why this is the strongest rejection issued to date: the family is
  plausibly real AND uncertifiable under selection pressure at the same time.
- **Capacity honesty:** even taking the headline at face value, the sleeve returns ~1.6%/yr
  on 1.8% vol — about £1.6k/yr standalone on a £100k book at the tested sizing. Its only
  portfolio value would be as a diversifier, and a rejected sleeve cannot claim that slot
  under the discipline. Half-risk-budget deployment (the audit's original suggestion) is
  therefore off the table.
- **Consequence per prereg:** PEAD is closed as a certified sleeve. A future
  re-registration is permitted only as a single fixed config (no grid, headline designated
  in advance), ideally on fresh earnings-date data with point-in-time correctness re-audited
  — and it would still face the full ledger deflation, now at 205.

## Determinism & data

- Headline executed twice; identical output (`determinism_check: true`).
- Universe: AAPL ABBV AMAT AMD AMZN AVGO BA CAT COST GOOGL HD JNJ KO MA MCD META MRK MSFT MU
  NFLX NKE NVDA PEP PFE PG PLTR QCOM TSLA TXN UBER V WMT XOM (33 halal-screened names).
- Earnings events from the FMP historical-earnings feed (key kept in env, never printed);
  event dates used as published — point-in-time correctness limitation documented in the
  prereg (restatement/look-ahead risk on historical earnings calendars is a known caveat and
  one more reason the verdict is stated without deployment consequence).

## Ledger

- n_trials before pre-registration: 199 → after: **205** (+6 pead_book configs).
- Concurrent campaigns at this count (book_h × 3 recorded earlier at 190→193, st_reversal
  × 6 at 193→199) are all deflated at their own final counts in their own reports; every
  report notes the sensitivity where relevant.

## Files

- `engine/data_store/pead_prereg.md` — the pre-registration. (Existing.)
- `engine/scripts/run_pead_gate.py` — the gate runner. (Existing from the campaign.)
- `engine/apex_quant/strategies/pead.py` — the sleeve. (Existing.)
- `engine/data_store/validation/pead_gate_2026-07-19.json` — machine-readable output.
- `engine/data_store/pead_gate.md` — this report. **New.**
- No engine source, configs, live scripts, or data were modified. The live daemon and the
  Book D paper test were not disturbed.
