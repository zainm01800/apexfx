# Owner Decision — Promote Trend Ensemble to Funded Runner Config

**Date:** 2026-07-28 · **Decided by:** owner · **Status:** ADOPTED for the funded runner (future deployment)

## The decision

The multi-horizon trend ensemble — momentum lookbacks **[63, 126, 252]** blended — is promoted from validated candidate to the **designated configuration for the funded runner** (the book that will trade the prop-firm challenge and beyond).

## The evidence (from `engine/data_store/trend_ensemble_gate.md`, gate of 2026-07-27)

| | Certified 252-only | Ensemble [63,126,252] |
|---|---|---|
| Sharpe | 0.86284 | **0.92377 (+0.061)** |
| £/month on £100k | +1,783 | **+2,002 (+£219)** |
| Max drawdown | 16.32% | **15.92%** |
| Worst day | −5.09% | **−3.45%** |
| DSR (deflated by 279 trials) | 0.9966 | **0.9984 ✓** |
| CPCV paths won vs control | — | 9/15 |

Pre-registered adoption criterion (>7/15 CPCV paths AND DSR>0.95 AND cost drag <1%/yr): **met in full.** This is the first challenger in project history to beat the certified book at the gates.

## Scope — what this does and does NOT touch

- **APPLIES TO:** the funded runner configuration (the future live/prop deployment) and any new book revision from this date forward.
- **DOES NOT APPLY TO:** the frozen forward paper test (Book D, seeded 2026-07-16, pre-registered contract `pre_registration_paper_trend_2026-07-17.md`). The proof continues on the original 252-only book untouched, and its IBKR mirror likewise. Changing a running pre-registered experiment would invalidate the 60-day proof.
- The running daemon / `engine/config.yaml` live sections are unchanged.

## Implementation note

The ensemble is a constructor kwarg on `RegimeGatedMomentum` (`momentum_lookbacks=[63,126,252]`, default `[252]` = certified). At the funded-runner build (graduation, ~mid-Sep 2026), the runner passes the ensemble lookbacks through `TrendBook` / the live loop. Barbell variant [63,252] remains the documented fallback (Sharpe 0.90937, maxDD 14.67%) should the blend underperform in forward paper.

## Caveats carried from the gate report

- Worst single *month* is slightly deeper for the blend (−£20.6k vs −£19.7k) though overall drawdown is better.
- PBO across the 3-config set read 0.96 — pre-flagged as near-degenerate for near-collinear configs (the identical caveat attaches to the certified control).
