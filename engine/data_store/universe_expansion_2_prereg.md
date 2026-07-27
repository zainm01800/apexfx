# PRE-REGISTRATION — Universe expansion gate (2026-07-27, owner-commissioned)

**Status: pre-registered BEFORE any gate run.** 2 NEW ledger trials will be recorded by the
gate script BEFORE execution (expected ledger 294 → 296 — the ledger stood at 282 when this
task began; a separate pre-registered 12-config prop-condition frontier study (commit
`5ea48b9`, 2026-07-27) moved it to 294 before this gate ran; DSR deflates by the FULL count
at run time, whatever it is; the control re-records the certified `book_h_gold_252`
canonical key and dedups). No gate number below comes from a run that has
already happened — the only completed computations are (i) the certified-anchor hard-check
(no ledger, no CPCV) and (ii) data probes + a correlation screen, both structural (price
data only, no strategy, no performance figures).

**Filename note.** `universe_expansion_prereg.md` is Book J's document (2026-07-22) and is
left untouched; this is the second universe-expansion pre-registration, hence `_2_`.
Gate report will land in `universe_expansion_gate.md` (free name), per the commissioning task.

**Base book:** `book_h_gold_252` (certified 2026-07-22, gap-aware engine, max_risk_per_trade
0.01). Universe-only change; signal, sizing, exits, regime, HTF gate, caps, costs, window
(strictly < 2025-01-01) and seed 42 are byte-identical. **Anchor hard-check before this
prereg: EXACT** — Sharpe 0.86284, PF 1.32452, win 55.77%, maxDD 16.32%, 1637 trades,
final equity 292,551.34 (`scratch/anchor_check_univexp.py`, 2026-07-27).

**Context — the trend ensemble.** The 2026-07-27 ensemble gate ADOPTED the
`[63,126,252]` blend as a validated candidate (Sharpe 0.92377; certified default remains
`[252]`). This gate therefore measures the expansion on BOTH configs; §5 states which is
the comparison of record.

---

## 1. Relationship to the Book K closure — stated before anything else

Book J (+24 screened large caps) and Book K (+12 independence-selected large caps) both
tested **same-class US large-cap breadth** on the 2026-07-22 snapshot and both REJECTED;
Book K's prereg §1 fixed the boundary in advance: *"breadth is closed permanently for this
book: no further selection rules, no further config counts."*

This gate is an explicit, owner-commissioned re-opening — **not** a quiet re-roll. The
commission (2026-07-27) changes the question in three ways, and the prereg narrows it in a
fourth:

1. **Composition:** the commissioned pool is UCITS sector/thematic ETFs + *untested*
   healthcare/industrials/semis-equipment large caps + other asset classes (sukuk,
   metals) — not the Book J/K staples-and-pharma pool. **The Book J/K names are excluded
   from the candidate pool by design** (that line stays closed).
2. **Anchor:** the gate runs on the current certified gap-aware anchor (0.86284), which
   Book J/K predate (their baseline printed 1.032 on the older snapshot/engine).
3. **A new second axis:** the expansion is also measured on top of the validated
   `[63,126,252]` ensemble config, which did not exist when Book K ran.
4. **Single shot, binding:** this is the one commissioned test. Whatever the verdict, no
   further universe-expansion variant of this book may be proposed without a new owner
   commission and a new pre-registration. Written here so the boundary is fixed in
   advance, exactly as Book K fixed its own.

## 2. Candidate pool (fixed before screening) and data verification

25 candidates, aligned with the commission's three buckets. **Book J/K names deliberately
absent.** Data verified via the engine's own YahooAdapter/store path (the Book H probe
path): `scratch/probe_univexp_candidates.py`, raw output
`scratch/probe_univexp_candidates.json`. "In-window" = daily bars 2016-01-01 → 2024-12-31
(iteration window only; the 2025+ holdout was not read).

| Bucket | Candidates | Data result |
|---|---|---|
| (a) UCITS sector ETFs (USD lines) | SXLV.L (health care), SXLI.L (industrials), SXLB.L (materials), SXLP.L (staples), SXLY.L (discretionary), IUHC.L (health care, iShares alt) | **all clean: USD, LSE, 2273 in-window bars each, max gap ≤ 5d, zero weekend bars** |
| (a) UCITS thematic ETFs (USD lines) | RBOT.L (automation & robotics), DGTL.L (digitalisation) | **clean: USD, 2099 bars from 2016-09-08** |
| (a) excluded at the data step | SSLN.L (silver ETC), SPGP.L (gold miners), INRG.L (clean energy) | **GBp pence lines, no USD LSE line** — excluded under the standing mapping-doc preference rule (2026-07-24 doc, rule 2: the ISWD.L pence artifact embeds GBP/USD in daily returns; the book already tolerates exactly one such line for a must-have exposure). INRG would additionally face the R2 screen below; SSLN/SPGP would face it against SGLD.L. |
| (a) documentation rows | IUIT.L, SXLE.L, BTEC.L, CNDX.L | data fine (2273 / 2273 / 1818 / 2273 bars) — expected near-duplicates or halal-fail; screened so the exclusion is on numbers, not assumption |
| (b) large caps (halal_screen_2026-07-22 pool, **untested in Book J/K**) | AMGN, VRTX, DHR, SYK (healthcare); EMR, ETN, PH, SHW, ECL (industrials/materials); AMAT, LRCX, KLAC (semis-equipment) | **all cached, 2264 in-window bars each** |
| (c) other asset class | SPSK (SP Funds DJ Global Sukuk, halal-certified) | cached, **1259 in-window bars from 2019-12-31** — the only sukuk fund with usable history (SKUK 2024-01 ≈170 bars, HBKS.L 2023-09 ≈330, HSKD.L 2025-01 zero — Book H prereg row 5 documented the same finding) |

Futures-based commodity ETCs (copper, broad commodity, agriculture) are excluded on the
halal bar by **structure** — interest-bearing collateral and no Sharia certification —
not screened. No additional Islamic UCITS sector lines exist beyond ISWD/ISDU/ISDE
(established by the 2026-07-24 mapping research).

## 3. The selection rule (mechanical, ex-ante, no performance data) — Book K's rule verbatim

`scratch/screen_univexp_candidates.py`, output `scratch/screen_univexp_candidates.json`.
Daily returns, iteration window only:

- **R0 data:** ≥ 300 in-window daily bars.
- **R1 halal bar:** AAOIFI-style activity screen (per-instrument documentation in §4).
- **R2 independence:** reject any candidate with max |corr| ≥ **0.50** vs **any** of the
  39 certified-book instruments (Book K's threshold, unchanged).
- **R3 internal dedup:** among survivors, in listing order, drop |corr| ≥ 0.90 vs an
  already-kept candidate.

**No threshold was tuned.** The rule was copied from Book K before any output was seen.

## 4. Screen result — the honest centre of this prereg

**2 survivors of 25.** The full table (this is the candidate list with screens the
commission asks for — every exclusion has its number):

| Candidate | max \|corr\| | against | mean \|corr\| | Verdict |
|---|---|---|---|---|
| SXLV.L health care UCITS | 0.776 | ISDU.L | 0.200 | EXCLUDE R2 |
| SXLI.L industrials UCITS | 0.851 | ISDU.L | 0.262 | EXCLUDE R2 |
| SXLB.L materials UCITS | 0.820 | ISDU.L | 0.262 | EXCLUDE R2 |
| SXLP.L staples UCITS | 0.634 | ISDU.L | 0.152 | EXCLUDE R2 |
| SXLY.L discretionary UCITS | 0.829 | ISDU.L | 0.298 | EXCLUDE R2 |
| IUHC.L health care UCITS | 0.772 | ISDU.L | 0.205 | EXCLUDE R2 |
| RBOT.L robotics UCITS | 0.839 | ISDU.L | 0.347 | EXCLUDE R2 |
| DGTL.L digitalisation UCITS | 0.826 | ISDU.L | 0.338 | EXCLUDE R2 |
| **SPSK sukuk** | **0.283** | NZD/USD | **0.135** | **KEEP** |
| **AMGN** | **0.476** | XBI | **0.191** | **KEEP** |
| VRTX | 0.515 | XBI | 0.184 | EXCLUDE R2 (borderline) |
| DHR | 0.585 | XLK | 0.275 | EXCLUDE R2 |
| SYK | 0.583 | XLK | 0.284 | EXCLUDE R2 |
| EMR | 0.645 | XLE | 0.301 | EXCLUDE R2 |
| ETN | 0.613 | XLK | 0.304 | EXCLUDE R2 |
| PH | 0.619 | XLE | 0.317 | EXCLUDE R2 |
| SHW | 0.518 | XLK | 0.248 | EXCLUDE R2 (borderline) |
| ECL | 0.613 | XLK | 0.298 | EXCLUDE R2 |
| AMAT | 0.882 | SOXX | 0.363 | EXCLUDE R2 (as Book K found) |
| LRCX | 0.869 | SOXX | 0.359 | EXCLUDE R2 |
| KLAC | 0.861 | SOXX | 0.353 | EXCLUDE R2 |
| IUIT.L | 0.828 | ISDU.L | 0.320 | EXCLUDE R2 |
| SXLE.L | 0.730 | XLE | 0.178 | EXCLUDE R2 |
| BTEC.L | 0.661 | ISDU.L | 0.233 | EXCLUDE R2 |
| CNDX.L | 0.837 | ISDU.L | 0.328 | EXCLUDE R1 halal bar (Nasdaq-100 applies no activity/debt screens — mapping doc; same ruling as Book H dropping QQQ) |

**Why the list is 2, not 20 — the structural finding.** The certified book holds ISDU.L
(the whole US market, USD line), XLK/SMH/SOXX (tech/semis), XLE (energy) and XBI
(biotech). Every US equity sector correlates 0.63–0.88 with that block on daily returns;
every sector UCITS is a *slice of an already-held whole*. Semis-equipment names are
near-duplicates of SOXX (0.86–0.88), exactly as Book K's audit found. **Under any honest
independence rule, "uncorrelated US equity breadth" does not exist for this book.** The
commission's expectation of ~15–25 additions is not achievable without abandoning the
independence requirement — which is precisely the dilution Book J measured. The two
survivors are the only genuinely different exposures in the pool: a different asset
class (sukuk) and one borderline-independent pharma name.

**The 2 survivors, with per-instrument rationale and screens:**

1. **SPSK** — SP Funds Dow Jones Global Sukuk ETF. Halal-**certified** (sukuk fund;
   Book H prereg row 5). Asset class absent from the certified gold book (its only
   defensive leg is SGLD.L). Most independent candidate screened: max |corr| 0.283.
   Caveats carried: late start (2019-12-31, 1259 bars — late-listing path, same as
   PLTR/SOL); the 2026-07-27 defensive-sleeve gate REJECTED a gold/sukuk *idle-cash
   sleeve* — a different question from SPSK as a full trend instrument (its kill leg
   was the sleeve's standalone maxDD as a cash substitute; SPSK here trades the same
   252/63/21/1.5R trend signal as every other leg). Book H's `book_h_sukuk_252` config
   (core+sukuk, no gold) passed all three gates on 2026-07-19; **gold+sukuk together
   has never been tested**.
2. **AMGN** — Amgen. In the 59 screened 2026-07-22 (healthcare; AAOIFI activity pass,
   constituency proxy per `halal_screen_2026-07-22.md` method §2 — the engine has no
   point-in-time fundamentals feed). Never tested in Book J/K. Passes R2 borderline:
   max |corr| 0.476 vs XBI (threshold 0.50). Documented as the weakest independence
   claim in the expansion; if the whole expansion is adopted, AMGN's marginal status
   is part of what is being adopted.

## 5. Configs — exactly 3 (the full selection set), and the comparison of record

| Config | Universe | Score | Ledger |
|---|---|---|---|
| control | certified 39 (`book_h_gold_252`) | `[252]` certified | **dedup** — re-records the certified canonical key |
| `univexp_expanded_252` | certified 39 + SPSK + AMGN (41) | `[252]` | **1 NEW charge** |
| `univexp_expanded_ens` | certified 39 + SPSK + AMGN (41) | `[63,126,252]` adopted blend | **1 NEW charge** |

**Comparison of record (stated as commissioned): the certified 252-only config** —
control vs `univexp_expanded_252`. The ensemble-config read is secondary/informational:
`univexp_expanded_ens` is compared against the already-recorded
`trend_ens_blend_63_126_252` run (`validation/trend_ensemble_gate_2026-07-27.json` —
same universe, machinery, seed; deterministic, so a valid control; its DSR was computed
at n=279, this gate's at the current full count — the comparison notes the difference).

**Expanded-panel insertion order (ordering-sensitive book, pre-registered):** the
certified 39 in certified order (EQUITY_CORE, SGLD.L, crypto, FX majors), then the new
names appended **last**, in survivor-rank order: SPSK, then AMGN. Certified allocations
keep first claim on slots/risk caps; new names take residual capacity.

Expected ledger: **294 → 296** (DSR deflates by the full count at run time, whatever it
is — see the header for the 282 → 294 prop-frontier study that landed first). Trials
recorded by the script BEFORE any run, dedup-safe.

## 6. Gates + binding adoption rule (verbatim from the commission)

For the comparison of record, ADOPT the expansion ONLY if ALL of:

1. **Sharpe leg:** expanded full-window Sharpe − control full-window Sharpe ≥ **+0.05**;
2. **CPCV leg:** expanded CPCV median OOS Sharpe ≥ control's (15 paths, purge 21);
3. **DSR leg:** expanded DSR > **0.95** at the full updated ledger count;
4. **PBO leg:** PBO < **0.5** across the 3-config selection set.

**Kill: any leg fails.** Lower-Sharpe/lower-drawdown is not a pass (Book J ruling,
restated). The secondary ensemble read reports the same four legs against the recorded
ensemble control but does not bind.

**PBO caveat, pre-registered (same form as the ensemble gate §4):** the two expanded
configs share 39 of 41 instruments with the control, so their daily returns are highly
collinear; PBO's discriminative power on this selection set is limited by construction.
The leg is computed and applied as stated in the rule — but if it is the ONLY failing
leg, the gate report will say plainly what that does and does not show, rather than
treating the number as informative.

## 7. Hypothesis and the honest counter

**H-expansion:** one genuinely independent asset-class leg (sukuk) plus one independent
pharma name raise risk-adjusted quality and the monthly rate at unchanged per-trade
risk (Grinold: IR ≈ IC·√breadth — with *independent* bets).

**Pre-registered counter-hypothesis (at least as likely, given Book J/K):** +2 names on
39 moves the book by a fraction of the +0.05 bar; SPSK's low-vol carry-like profile may
never clear the 1.5R barrier after costs, and AMGN's 0.476 residual correlation to XBI
may make it a de-facto duplicate in the trade set. Expected value of the Sharpe leg is
~0.00–0.02 — **a REJECT is the prior**. The gate is run to measure, not to confirm:
the deliverable includes the per-instrument P&L attribution of both names either way.

## 8. Caveats (carried, not fixed)

1. **Present-day screening = survivorship/lookahead** in universe construction
   (`halal_screen_2026-07-22.md` §3). Carried since Book D.
2. **Correlations are computed on the iteration window** — the same data the book was
   certified on (Book K caveat §6.1, restated). Selection using evaluation data; the
   trial charges + DSR/PBO/CPCV stack are the defence.
3. SPSK late start (2019-12-31): CPCV folds entirely before 2020 trade 40 instruments;
   the engine's late-listing path handles it (as PLTR pre-2020).
4. New UCITS parquets were fetched 2026-07-27 via the standard adapter path and are
   unaudited beyond `clean()` (Book J caveat §5.5); none of them entered the book, so
   this affects only the documentation rows.
5. The engine does not model TER (per-class spread/slippage cost model only); UCITS TERs
   documented in the probe notes are compliance records, not costs.
6. Determinism: seed 42; the gate is run twice; results JSONs must be byte-identical
   modulo `generated_at` and the ledger bookkeeping line.
7. 2025+ holdout untouched; iteration window ends 2024-12-31. Frozen paper test and
   `config.yaml` live sections untouched (no config change of any kind — the gate pins
   its own universe in the script, as every post-Book-H gate has).

## 9. Deliverables

`scripts/run_portfolio_gate_univexp.py`, `validation/univexp_gate_2026-07-27.json`
(+ determinism twin), `data_store/universe_expansion_gate.md` (verdict in the first
sentence), this prereg, `scratch/{probe,screen,fetch}_univexp_*` (+ JSON outputs).
Exit code 0 only if the expansion is ADOPTED under §6.
