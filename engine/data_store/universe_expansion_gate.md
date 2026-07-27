# GATE — UNIVERSE EXPANSION on Book H gold: **REJECTED — Sharpe leg −0.0863 (needed ≥ +0.05) and PBO 0.868 (≥ 0.5); kill = any leg. The certified book stands unchanged (2026-07-27)**

**Pre-registration:** `engine/data_store/universe_expansion_2_prereg.md` (written BEFORE any
gate run; 2 NEW trials recorded before execution, dedup-safe). **Results:**
`engine/data_store/validation/univexp_gate_2026-07-27.json` (+ determinism twin, byte-identical
modulo `generated_at` / ledger bookkeeping). **Script:**
`engine/scripts/run_portfolio_gate_univexp.py`. **Window:** ITERATION only, strictly <
2025-01-01; the certified anchor (gap-aware engine, mrpt 0.01, EQUITY_CORE-first panel order)
was reproduced EXACT by the control in both passes (Sharpe 0.86284, 1637 trades, equity
292,551.34 — hard-check, the run aborts otherwise). **Ledger:** 294 → **296**; every DSR
deflated by 296. `config.yaml` and the frozen paper test untouched (the gate pins its own
universe in the script).

## What was tested

Owner-commissioned re-opening of the Book K closure (the prereg §1 states the boundary:
single shot, binding). A 25-candidate pool — UCITS sector/thematic ETFs (USD lines only, per
the 2026-07-24 mapping doc), halal-screened healthcare/industrials/semis-equipment large caps
**not** tested in Book J/K, and other asset classes — was put through the Book K mechanical
rule with no performance data: R0 ≥300 in-window daily bars (2016-01-01 → 2024-12-31, verified
via the engine's own YahooAdapter/store path), R1 halal bar, R2 max |corr| < 0.50 vs **every**
one of the 39 certified holdings, R3 internal dedup.

**2 survivors of 25: SPSK (sukuk, max |corr| 0.283) and AMGN (0.476 vs XBI, borderline).**
The structural finding (prereg §4): the certified book already holds ISDU.L (the whole US
market), XLK/SMH/SOXX, XLE and XBI, so every US equity sector UCITS is a slice of an
already-held whole (0.63–0.88 vs ISDU.L) and every semis-equipment name a near-duplicate of
SOXX (0.86–0.88). Under any honest independence rule, uncorrelated US equity breadth does not
exist for this book. The expansion tested is therefore the maximal one the rule permits:
certified 39 + SPSK + AMGN (41), new names appended last in the panel (certified allocations
keep first claim on slots/risk caps).

## The pre-registered scoreboard (comparison of record: certified `[252]` config)

| | control `univexp_control_252` (39) | expanded `univexp_expanded_252` (41) | expanded `univexp_expanded_ens` (41, `[63,126,252]`) |
|---|---|---|---|
| Sharpe | **0.86284** (anchor, exact) | 0.77654 (**Δ −0.08630**) | 0.86580 (Δ +0.00296 vs control; −0.05796 vs ens control) |
| Sortino | **0.9341** | 0.8266 | 0.9752 |
| Profit factor | **1.3245** | 1.3044 | 1.3538 |
| Trades | 1637 | 1632 | 1646 |
| Win rate | **55.77%** | 55.51% | 55.41% |
| Expectancy / trade | **+120.44 (+1.022%)** | +104.28 (+0.986%) | +133.96 (+1.025%) |
| Max drawdown | **16.32%** | 18.01% (+1.69pt) | **15.18%** (−1.14pt) |
| Worst daily loss | −5.09% | −4.73% | **−3.95%** |
| Worst month | −19,673 | −20,215 | **−18,991** |
| Avg monthly P&L | **+1,783** | +1,383 (**−400/mo**) | +1,799 (+16/mo; vs +2,002 for the ens control) |
| Final equity | **292,551** | 265,946 | 315,936 |
| Max gross leverage | 2.59× | 2.53× | 2.27× |
| DSR @ n=296 | 0.9940 ✓ | 0.9893 ✓ | 0.9959 ✓ |
| CPCV median / frac positive | +0.0476 / 15-of-15 | +0.0484 / 13-of-15 | +0.0535 / 14-of-15 |
| **PBO (3-config set)** | — | **0.868 (≥ 0.5) ✗** | — |

**The four pre-registered legs (prereg §6), comparison of record:**

1. **Sharpe leg** — expanded − control = **−0.08630 < +0.05 → FAIL**
2. **CPCV leg** — expanded median 0.04841 ≥ control 0.04758 (15 paths, purge 21) → PASS
3. **DSR leg** — 0.9893 > 0.95 @ n=296 → PASS
4. **PBO leg** — 0.868 ≥ 0.5 → FAIL

**Kill rule: any leg fails → REJECTED. Two legs fail.** Adopt nothing; the certified book
(and the adopted ensemble flag, default OFF) stands unchanged.

**On the pre-registered PBO caveat (prereg §6):** the caveat said that if PBO were the ONLY
failing leg, its limited discriminative power on a 3-config near-collinear selection set would
be stated plainly rather than treated as informative. It is **not** the only failing leg — the
Sharpe leg fails by a wide margin (−0.086 vs the +0.05 bar) — so the caveat changes nothing:
the rejection stands on the Sharpe leg alone, and the PBO number is recorded as computed.

## Per-new-instrument P&L attribution (the deliverable, either way)

| Instrument | Screen / rationale | expanded_252 trades / net P&L | expanded_ens trades / net P&L |
|---|---|---|---|
| **SPSK** (sukuk ETF) | Halal-certified; new asset class; most independent candidate (max \|corr\| 0.283) | 4 / **+£312.75** | 1 / +£156.57 |
| **AMGN** (pharma) | AAOIFI activity pass (constituency proxy); borderline independent (0.476 vs XBI) | 29 / **−£6,189.84** | 21 / −£6,487.84 |

Book-level net P&L delta on the comparison of record: **−£26,979.67 with −5 trades** — the
new names' direct P&L (−£5,877 net) explains only about a fifth of it; the remaining ≈ −£21.1k
is crowding: even appended last, the two new legs claim slots and risk-cap budget that
certified names were using (the expanded book's expectancy per trade fell 1.022% → 0.986%).
SPSK, as pre-registered as a risk, barely traded and never cleared the 1.5R barrier often
enough to matter (4 trades in five years of listing); AMGN traded like the de-facto XBI
duplicate its 0.476 correlation warned of, and lost. On the ensemble config the same two
names cost −£6,331 direct and the book nets −£844 vs the recorded ensemble control
(Sharpe −0.058, −£202/mo) — the secondary read fails the same way, and does not bind.

## The honest reading

The pre-registered counter-hypothesis (a REJECT was the prior, expected Sharpe-leg value
~0.00–0.02) was still too kind: the expansion *reduced* the monthly rate by £400/mo at
unchanged per-trade risk — the exact opposite of the commissioned goal. Three independent
tests have now measured the same wall from three angles: Book J (+24 screened large caps:
dilution), Book K (+12 maximally-independent names: noise), and this gate (+1 genuinely new
asset class + 1 borderline name: harm). The Book K structural audit is confirmed on the
current anchor: **the certified 39-instrument book already spans the tradeable,
independent, halal-screened trend universe reachable with daily bars**; what remains outside
either correlates with what is held or does not trend. Per prereg §1.4, universe expansion of
this book is closed: no further variant without a new owner commission and a new
pre-registration.

## CPCV paths (15, purge 21, per-period Sharpe)

- control: [0.0437, 0.0671, 0.0811, 0.0368, 0.0751, 0.0326, 0.0476, 0.0009, 0.0398, 0.0684,
  0.0267, 0.0683, 0.0648, 0.0844, 0.0381] — median 0.0476, 15/15 positive
- expanded_252: [0.0484, 0.0133, 0.0730, −0.0197, 0.0544, 0.0340, 0.0792, 0.0155, 0.0638,
  0.0596, −0.0088, 0.0417, 0.0426, 0.0862, 0.0499] — median 0.0484, 13/15 positive
- expanded_ens: [0.0418, 0.0291, 0.0613, −0.0010, 0.0599, 0.0455, 0.0706, 0.0253, 0.0690,
  0.0666, 0.0209, 0.0648, 0.0468, 0.0934, 0.0535] — median 0.0535, 14/15 positive

## Determinism

Full gate executed twice (seed 42): results payload **byte-identical** modulo `generated_at`
and the ledger bookkeeping line (first pass 294 → 296, second pass dedups 296 → 296); the
control reproduced the certified anchor EXACT in both passes. `py_compile` clean on the gate
+ all scratch scripts; full unit suite green (656 tests).

## Ledger

- **n before this gate: 294** (the 2026-07-27 prop-condition frontier study landed first,
  282 → 294 — pre-registered there; this gate's prereg states the DSR deflates by the full
  count at run time, whatever it is)
- **+2** (`univexp_expanded_252`, `univexp_expanded_ens`, kind `universe_expansion_gate`,
  universe `book_h_gold_39_plus_spsk_amgn_41`, recorded BEFORE the first run; the control
  re-recorded the certified `book_h_gold_252` canonical key and deduped) → **296**.
  Twin run deduped (296 → 296).

## Data verification record (prereg §2, in-window daily bars 2016-01-01 → 2024-12-31)

SXLV.L / SXLI.L / SXLB.L / SXLP.L / SXLY.L / IUHC.L: 2273 bars each, USD LSE lines, max gap
≤ 5d, zero weekend bars — all excluded by R2 (0.63–0.85 vs ISDU.L). RBOT.L / DGTL.L: 2099
bars from 2016-09-08 — excluded R2. SSLN.L / SPGP.L / INRG.L: GBp pence lines only, no USD
LSE line — excluded at the data step (mapping-doc rule 2). IUIT.L 2273 / SXLE.L 2273 /
BTEC.L 1818 / CNDX.L 2273 — documentation rows, excluded on numbers (R2 / R1). AMGN, VRTX,
DHR, SYK, EMR, ETN, PH, SHW, ECL, AMAT, LRCX, KLAC: 2264 bars each — all excluded R2 except
AMGN. SPSK: 1259 bars from 2019-12-31 (only sukuk fund with usable history) — KEEP.
Raw outputs: `engine/scratch/probe_univexp_candidates.json`,
`engine/scratch/screen_univexp_candidates.json`.
