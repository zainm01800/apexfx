# Sleeve Gate — ST-REVERSAL: long-only large-cap short-term reversal — 2026-07-19

**Pre-registration:** `engine/data_store/st_reversal_prereg.md` (written BEFORE any run;
universe, 6-config grid, headline designation, gates, kill criterion — this report changes
nothing that was pre-registered).
**Window:** ITERATION only, strictly < 2025-01-01 (daily bars). **The 2025+ holdout was not
touched in any way.**
**Script:** `engine/scripts/run_st_reversal_gate.py`. Machine-readable output:
`engine/data_store/validation/st_reversal_gate_2026-07-19.json`.
**Ledger:** 6 trials recorded at pre-registration, **n_trials 193 → 199**; every DSR below is
deflated by **199**. (The ledger later grew to 205 via the concurrent pead_book campaign;
recomputing the headline DSR at n=205 lowers it further — the verdict is unchanged.)

## What the sleeve is

Long-only weekly reversal on 33 halal-screened US large caps (no banks): each week buy the
bottom-N performers of the trailing formation window, hold ~1 week, recycle. The mechanism is
liquidity provision, not prediction — the prereg's claim under test was **diversification vs
the trend book**, not standalone Sharpe. Filter ablations: `plain` (bottom-3), `cost` (de
Groot turnover reduction, bottom-2), `vol_state` (trade only when SPY 21d vol ≥ its 126d
median — the state where liquidity-provision returns should be richest).

## Verdicts (DSR deflated by 199; PBO across the full 6-config selection set)

**PBO across 6 configs = 0.7735 > 0.5 → every config fails the set-level overfit gate.**

| Config | Sharpe | PF | Win | MaxDD | DSR (>0.95) | CPCV med / frac+ | ρ vs trend | Verdict |
|---|---|---|---|---|---|---|---|---|
| `rev_f5_plain` (headline) | 0.85 | 1.30 | 51% | 15.9% | 0.903 ✗ | +0.047 / 80% ✓ | 0.29 | **REJECT** |
| `rev_f5_cost` | 0.68 | 1.31 | 50% | 10.4% | 0.780 ✗ | +0.030 / 100% ✓ | 0.28 | REJECT |
| `rev_f5_volstate` | 0.80 | 1.53 | 55% | 11.3% | 0.880 ✗ | +0.036 / 87% ✓ | **0.08** | REJECT |
| `rev_f10_plain` | 0.97 | 1.38 | 52% | 10.9% | **0.953 ✓** | +0.039 / 100% ✓ | 0.22 | REJECT (PBO) |
| `rev_f10_cost` | 0.58 | 1.29 | 51% | 12.0% | 0.682 ✗ | +0.033 / 100% ✓ | 0.33 | REJECT |
| `rev_f10_volstate` | 0.92 | 1.62 | 56% | 8.4% | 0.937 ✗ | +0.048 / 87% ✓ | **0.06** | REJECT |

PASS required ALL of DSR > 0.95, PBO < 0.5, CPCV median > 0 with > 50% positive paths.
**No config achieved all three. Per the prereg: the sleeve is REJECTED at retail costs —
and per the same prereg, REJECT means reject, not another sweep.**

## The honest read — what survived and what died

- **What died:** any standalone deployment claim. The headline misses DSR (0.903 < 0.95)
  *before* the even harder n=205 recount, and the set-level PBO of 0.77 says the probability
  that the in-sample-best config is an overfit artifact is ~77%. One grid mate
  (`rev_f10_plain`) clears DSR at 0.953 — the prereg names this exact situation a
  **selection-effect candidate**, which is what PBO exists to discount. No deployment claim
  is made.
- **What survived (texture, not a verdict):** every config's CPCV is positive-median with
  ≥ 80% positive paths — the return stream is real-looking, just not certifiably so after
  multiple-testing deflation. The two mechanism checks the prereg demanded both point the
  right way: (a) the sleeve earns in BOTH vol states (headline: high-vol Sharpe 0.76 on 39%
  of P&L, low-vol 0.93 on 61%), and (b) it is genuinely defensive in crises — COVID 2020
  −2.2% vs SPY −13.7%, 2022 bear −9.3% vs −18.7%, Q4-2018 −4.3% vs −14.0%.
- **The diversification claim, measured:** headline ρ vs the Book D trend curve = **0.29**;
  the vol-state variants come in at **0.06–0.08** — essentially uncorrelated. This was the
  property the audit cared about, and it measured as hoped. But a rejected sleeve cannot
  claim a portfolio slot under this project's discipline: near-zero ρ plus uncertifiable
  standalone quality is exactly the combination that produces comforting backtests and
  disappointing live returns.
- **Cost reality:** the headline turns over ~15.5×/yr for an estimated 0.62%/yr drag —
  survivable at large-cap spreads, and the `cost` (de Groot) variant cut turnover to ~9.9×
  but also cut Sharpe to 0.68. The turnover-reduction ablation did not rescue the gate.

## What this earns (pre-registered consequence)

No PASS anywhere ⇒ the sleeve is closed as a standalone certified strategy at retail costs.
The documented texture (positive CPCV across all six, ρ ≈ 0.06–0.08 for vol-state variants,
crisis defensiveness) is recorded here so that any **future re-registration** — explicitly
framed as a low-ρ diversifier with a single fixed config, gated hardest on costs — starts
from evidence, not from narrative. Until such a re-registration passes, nothing from this
sleeve touches the live book or the paper test.

## Determinism & data

- Headline run executed twice; equity curves identical (`determinism_check: true`).
- Universe: AAPL MSFT NVDA META AMZN GOOGL TSLA AMD PLTR TSM NFLX UBER XOM JNJ WMT PG KO V MA
  HD BA CAT INTC CSCO ORCL CRM ADBE PFE ABBV NKE MCD COST (33 names; V/MA borderline-call
  documented in the prereg; no banks). Regime instrument SPY used only for the vol-state
  filter, per prereg.
- Constraint log confirms the full risk stack was binding throughout (regime scaling,
  vol-target, portfolio-risk cap, per-trade cap).

## Ledger

- n_trials before pre-registration: 193 → after: **199** (+6 st_reversal configs).
- Verdicts are not marginal to the denominator: headline DSR at n=205 (current ledger) would
  be < 0.903 — still REJECT; `rev_f10_plain` at n=205 would be ≈ 0.951 — still PBO-bound
  REJECT.

## Files

- `engine/data_store/st_reversal_prereg.md` — the pre-registration. (Existing.)
- `engine/scripts/run_st_reversal_gate.py` — the gate runner. (Existing from the campaign.)
- `engine/apex_quant/strategies/st_reversal.py` — the sleeve. (Existing.)
- `engine/data_store/validation/st_reversal_gate_2026-07-19.json` — machine-readable output.
- `engine/data_store/st_reversal_gate.md` — this report. **New.**
- No engine source, configs, live scripts, or data were modified. The live daemon and the
  Book D paper test were not disturbed.
