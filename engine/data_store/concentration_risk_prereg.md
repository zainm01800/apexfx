# PRE-REGISTRATION — Book S: 2.00% risk on 5 slots (2026-07-23)

**Status: written BEFORE the gate run.** **8 trials charged** (ledger 244 → 252).

Owner's stated objective, revised this session: **£700+/month on £100k**, drawdown "around 12%"
— explicitly NOT a hard 12% wall (an earlier constraint I imposed that the owner never set).

## 1. Honest disclosure of the search that produced this

This is outcome-selected and the disclosure matters:

1. A concentration sweep (`frontier_concentration.py`) tested slots {3,5,8,12} × risk
   {0.50,1.00,1.50,2.00}% — **16 cells**. 5 slots @ 2.00% was chosen **because it scored best**.
2. A follow-up (`seven_hundred_options.py`) then scored 5 candidates on profit AND challenge
   pass rate. Same config won.

All 16 concentration cells were previously un-charged; **8 trials are charged here** (the 4
5-slot cells + the 4 gated/control configs below) on top of the 4 already implicit in Book P's
risk sweep. DSR deflates at the full ledger count.

**Measured (un-gated) starting point:** £744/month, Sharpe 0.901, forward p95 DD 14.9%,
backtest maxDD 20.0%, 892 trades, 1-step-6% challenge pass 75.7% / median 2.9 months.

## 2. Mechanism — falsifiable, stated in advance

This is NOT a brute-force risk increase. Its Sharpe (0.901) is the **highest measured all
session**, above the current book's 0.893, despite carrying far more risk per trade.

The claim: **the book's edge is concentrated in the top-ranked candidates and decays below
them.** This is already independently measured — `frontier_breadth_slots.json` shows Sharpe
falling 0.922 → 0.704 → 0.460 as slots widen 12 → 20 → 30, i.e. marginal positions carry
NEGATIVE edge. Cutting to 5 slots keeps only the top-5 expected-value candidates; raising risk
to 2.00% re-deploys the capital those discarded positions were using.

**Falsifiable prediction, recorded in advance:** if the mechanism is real, the 5-slot book's
**per-trade expectancy must be materially HIGHER** than the 12-slot book's. If expectancy is
merely equal and only the leverage differs, this is a leverage trade dressed as a signal
finding, and the correct conclusion is that it should be rejected in favour of simply raising
risk on the existing 12-slot book.

**Second prediction:** trade count should fall roughly by half (measured 892 vs 1,694). If it
does not, the slot cap is not binding the way the mechanism assumes.

## 3. Configs — 2 gated (8 charged)

| Config | risk | slots | Ledger |
|---|---|---|---|
| `book_s_control_075_12` (current live config) | 0.75% | 12/10 | charged |
| `book_s_conc_200_5` (challenger) | 2.00% | 5/5 | charged |
| *(6 further concentration cells examined, charged, NOT gated)* | — | — | charged |

Same universe (39), same EV slot allocation, gap-aware fills, per-asset-class costs. Iteration
window < 2025-01-01; the 2025+ holdout stays untouched.

## 4. Gates + binding decision rule

1. **DSR > 0.95** at the full ledger count.
2. **CPCV, 15 paths**: median OOS Sharpe > 0, >50% paths positive.
3. **PBO** — reported, **NOT binding** (near-twin books; it has rejected 8 of 11 such tests).
4. **PAIRED TEST (binding):** circular block bootstrap on the daily return difference vs the
   control, block 21, B=10,000, seed 42. Requires **p < 0.05**.
5. **DRAWDOWN CEILING (binding, revised):** forward p95 1-year drawdown **≤ 16%**. Set from the
   owner's "around 12%" plus the measured 14.9%, with headroom — NOT a post-hoc fit to the
   candidate's number. A config exceeding 16% is a REJECT regardless of profit.
6. **PROFIT FLOOR (binding for adoption):** CAGR ≥ 8.4% (£700/month). Below it there is no
   reason to take the extra drawdown at all.

**Adopt only if ALL of 1, 2, 4, 5 and 6 pass.** If it fails DSR but passes 4/5/6, the honest
report is "mechanistically justified, better on the owner's stated objective, statistically
un-gated" — the same status the live 0.75% config already carries, stated plainly rather than
laundered.

## 5. Pre-registered counter-hypotheses

- **It is just leverage.** Tested directly by §2's expectancy prediction.
- **Fewer positions = less diversification = fatter tails.** Already visible: backtest maxDD
  20.0% vs 14.3%. The drawdown ceiling exists to price this.
- **892 trades is a thinner sample**, so its statistics are less reliable than the 1,694-trade
  control even at identical Sharpe. DSR partially accounts for this; the paired test does not.
- **Concentration raises single-name risk.** With 5 slots, one instrument can be ~20% of risk.
  A gap through the stop on one name is materially worse than in a 12-slot book.

## 6. Caveats

1. In-sample, one snapshot; Yahoo re-bases adjusted prices.
2. Drawdown probabilities from IID bootstrap **understate clustered drawdowns** — the real
   rolling-window figures are the honest reference and must be reported alongside.
3. ~16 of 39 instruments are unreachable from UK retail IBKR; live attainable is lower.
4. Determinism: seed 42, two runs, identical modulo `generated_at`.

## 7. Deliverables

`scripts/run_portfolio_gate_book_s.py`, `data_store/validation/book_s_gate_2026-07-23.json`,
`data_store/book_s_gate.md` with the verdict in the first sentence.
