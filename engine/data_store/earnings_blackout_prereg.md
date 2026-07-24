# PRE-REGISTRATION — Earnings-blackout gate: ±1 trading day entry block (2026-07-24)

**Status: pre-registered BEFORE any blackout run.** This document fixes the hypothesis, the
configuration set, the gates, the falsification rule, and the ledger plan before execution.
Changing anything after the run requires a new pre-registration and new ledger charges.

**Base book:** the **equity sleeve of Book H gold** (21 instruments: 12 screened stocks —
AAPL MSFT NVDA META AMZN GOOGL TSLA AMD PLTR TSM NFLX UBER — plus ISWD.L/ISDU.L/ISDE.L,
XLK/XLE/XBI/SMH/SOXX, SGLD.L), certified params verbatim: lookback 252, vol 63, hold 21,
rr 1.5, `rule_based` regime, HTF 1w×50 gate, managed exits, vol-scaled sizing, certified risk
anchor `max_risk_per_trade = 0.01`, remaining config caps, per-asset-class v5 costs (borrow-fee
machinery at default OFF), daily bars, iteration window strictly < 2025-01-01, seed 42,
warmup 250, CPCV purge 21, 15 paths, certified panel insertion order. Equity universe only, per
the work order: earnings are an equity-idiosyncratic event; crypto/FX legs are excluded from
THIS gate (their inclusion would only add noise to a per-stock question). The 2025+ holdout is
not touched in any way.

---

## 1. Hypothesis

Earnings releases are the dominant single-name gap risk in the book, and the 2026-07-22
gap-aware TradeManager fills gap-through-stops at the open — the loss is in the certified
numbers. Blocking **NEW entries within ±1 trading day of a stock's earnings date** should
reduce idiosyncratic gap-through-stop losses. The cost is lost trend entries: earnings drift
(PEAD) is a real phenomenon and some of the book's best entries may cluster near releases.

**H:** the ±1-day earnings blackout reduces gap-tail losses **without losing trend entries**
(book performance not degraded beyond noise).

## 2. Data

`engine/data_store/earnings_calendar/*.json` — SEC EDGAR 8-K Item 2.02 filing dates (actual
earnings-release dates, FMP/EDGAR-sourced, builder `scripts/build_earnings_calendar.py`).
Coverage of the Book H equity sleeve: **11 of 12 stocks** (TSM, an ADR, is absent — it trades
unblocked in both configs; documented, not silently ignored). ETFs (XLK XLE XBI SMH SOXX
ISWD ISDU ISDE SGLD) have no earnings dates and are unaffected. Using realized release dates is
point-in-time valid for an entry block: the ±1-day window around date `d` is knowable on the
day (companies pre-announce; the block does not require knowing the announcement *time* —
BMO/AMC ambiguity is exactly why the window is ±1 full trading day).

## 3. Configurations (the full selection set: exactly 2)

| Config | Change | Question |
|---|---|---|
| `book_h_gold_equity` (control) | none — certified params, equity sleeve only | anchor |
| `book_h_gold_equity_blackout1d` (challenger) | new entries suppressed on the trading day before, of, and after each covered stock's earnings date | does the block buy tail protection for free? |

Implementation: a strategy wrapper (`EarningsBlackout`) around each per-instrument
MultiTimeframeMomentum in the gate script — `generate()` returns FLAT when the decision bar is
inside that instrument's blocked set (bar positions `[loc-1, loc, loc+1]` around each event
date on the instrument's own bar calendar). **Open positions are never touched** — the block
suppresses NEW entries only. No engine change; the wrapper flows through CPCV's model factory
identically to the full-window run.

## 4. Gates (identical machinery and thresholds to every prior gate)

For EACH config: DSR > 0.95 (deflated by the **full updated TrialLedger count**, §6) **and**
CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive (purge 21) **and** PBO < 0.5 across
the 2-config selection set (16 splits, 4000 combos, seed 42; standing caveat: the 2 configs
share ~100% of their universe — reported as computed).

## 5. Pre-committed falsification / decision rule

- **H1 (tail improvement):** the challenger's **worst daily loss** is at least 10% smaller in
  magnitude than the control's, **or** its max drawdown is at least 1.0 percentage point smaller.
- **H2 (no lost trend entries):** challenger Sharpe ≥ control Sharpe − 0.10 **and** challenger
  profit factor ≥ control PF − 0.10.
- **Verdict CONFIRMED** = H1 and H2 both hold **and** the challenger passes all three gates.
  **REJECTED** otherwise (including: the block almost never fires — reported, not silently
  accepted).

Measured but not verdict-binding: trade count delta, blocked-entry count (estimated as
control-minus-challenger trades plus slot displacement), expectancy, win rate, per-instrument
P&L for the 11 covered stocks.

## 6. Ledger plan

`TrialLedger` loaded fresh at **n = 264** (258 at the start of the 2026-07-24 work order + 4
for the W2 borrow-fee measurements + 2 for the W3 notional-cap gate, all recorded before their
runs). Exactly **2 new trials** (`book_h_gold_equity`, `book_h_gold_equity_blackout1d`, kind
`earnings_blackout_gate`) recorded BEFORE the first run → **n = 266** deflates every DSR in
this gate. No other configs will be evaluated; any follow-up (±2-day window, PEAD-long overlay,
TSM data) is a new pre-registration.

## 7. Known limitations

- **Coverage gap: TSM** (ADR) has no EDGAR 8-K cache entry and trades unblocked — stated
  plainly; the ADR is one of 12 stocks.
- **Announcement-time ambiguity** (BMO vs AMC) is handled by the full ±1-day window; a tighter
  window would need announcement timestamps the cache does not carry.
- **In-window earnings are point-in-time valid for entry blocking** (§2) but the cache was
  pulled in 2026 — survivorship of the FILING RECORD is not an issue for these 12 survivors;
  stated for completeness.
- **Equity-sleeve-only book ≠ certified Book H gold** (no crypto/FX legs): the anchor numbers
  here are not directly comparable to the certified 0.863 — the comparison that matters is
  control-vs-challenger inside this gate, both on the same sleeve.
- The block can interact with the swing-slot bucket: a suppressed entry frees a slot for a
  later candidate that bar (sequential allocation) — second-order, measured only through the
  book-level numbers.
