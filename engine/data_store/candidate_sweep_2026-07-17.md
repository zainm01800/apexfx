# Phase 3C Candidate Sweep — 2026-07-17

**Window:** ITERATION only, 2014-01-01 → strictly < 2025-01-01 (daily bars, ~3,120–3,150/pair). No `--final` run; the 2025+ holdout was not touched.
**Costs:** per-pair realized costs, config v5 (majors ~1 pip RT, crosses up to ~10 pips RT).
**Gate:** DSR > 0.95 **and** PBO < 0.5 **and** CPCV median OOS Sharpe > 0 with > 50% of 15 paths positive. DSR deflated by the shared TrialLedger's full count: **n_trials = 104** for every row below.
**Evidence base:** `docs/research/2026-07-17_fx_edges_evidence.md`.

---

## Candidate 1 — Long-horizon daily trend (RegimeGatedMomentum, rule_based, vol_window 63)

12-config grid per pair (lookback {63,126,252} × hold {10,21} × rr {1.5,2.0}); each row gates the headline config. PBO is computed on the pair's full 12-config matrix, so it is identical across headlines of the same pair — it is a property of the grid, not the headline.

### Headline: lookback 252, hold 21, rr 2.0 (12-month formation)
| Pair | DSR | PBO | CPCV med OOS | frac +ve | Verdict |
|---|---|---|---|---|---|
| EUR/USD | 0.100 | 0.233 | +0.002 | 53% | REJECT |
| GBP/USD | 0.000 | 0.794 | −0.049 | 0% | REJECT |
| USD/JPY | 0.017 | 0.490 | −0.031 | 0% | REJECT |
| AUD/USD | 0.017 | 0.600 | −0.031 | 0% | REJECT |
| EUR/JPY | 0.000 | 0.463 | −0.031 | 0% | REJECT |
| GBP/JPY | 0.016 | 0.490 | −0.031 | 0% | REJECT |

### Headline: lookback 126, hold 21, rr 1.5 (3-month formation)
| Pair | DSR | PBO | CPCV med OOS | frac +ve | Verdict |
|---|---|---|---|---|---|
| EUR/USD | 0.166 | 0.233 | +0.011 | 73% | REJECT |
| GBP/USD | 0.000 | 0.794 | −0.050 | 0% | REJECT |
| USD/JPY | 0.016 | 0.490 | −0.031 | 0% | REJECT |
| AUD/USD | 0.017 | 0.090 | −0.034 | 0% | REJECT |
| EUR/JPY | 0.000 | 0.463 | −0.030 | 0% | REJECT |
| GBP/JPY | 0.002 | 0.463 | −0.031 | 0% | REJECT |

### Headline: lookback 63, hold 21, rr 1.5
| Pair | DSR | PBO | CPCV med OOS | frac +ve | Verdict |
|---|---|---|---|---|---|
| EUR/USD | 0.180 | 0.233 | +0.013 | 73% | REJECT |
| GBP/USD | 0.000 | 0.794 | −0.044 | 0% | REJECT |
| USD/JPY | 0.016 | 0.490 | −0.031 | 0% | REJECT |
| AUD/USD | 0.014 | 0.196 | −0.034 | 0% | REJECT |
| EUR/JPY | 0.000 | 0.225 | −0.030 | 0% | REJECT |
| GBP/JPY | 0.003 | 0.735 | −0.031 | 0% | REJECT |

## Candidate 2 — Monthly currency-leg cross-sectional momentum (22-pair book)

`CurrencyCrossSectionalMomentum`, monthly rotation (holding 21), grid: lookback {21,63,126} × k {1,2,3}; headline 63/k=2. Portfolio-level gate (`run_portfolio_validation`), same ledger.

| Candidate | DSR | PBO | CPCV med OOS | frac +ve | Verdict |
|---|---|---|---|---|---|
| XS currency momentum (22 pairs) | 0.002 | 0.318 | −0.044 | 7% | REJECT |

## Candidate 3 — Carry-as-filter trend (lookback 126, vol 63)

`CarryTrendFilter` (new thin wrapper: baseline signal, vetoes trades whose direction earns negative carry; point-in-time policy rates from `data_store/central_bank_rates.csv`). Grid: hold {10,21} × rr {1.5,2.0}; headline hold 21, rr 1.5. Veto verified firing (sampled: USD/JPY 14/34 signals vetoed — all shorts; EUR/USD 15/25).

| Pair | DSR | PBO | CPCV med OOS | frac +ve | Verdict |
|---|---|---|---|---|---|
| EUR/USD | 0.416 | 0.987 | +0.024 | 87% | REJECT |
| GBP/USD | 0.000 | 0.964 | −0.042 | 20% | REJECT |
| USD/JPY | 0.020 | 0.490 | −0.031 | 0% | REJECT |
| AUD/USD | 0.027 | 0.834 | −0.034 | 0% | REJECT |
| EUR/JPY | 0.019 | 0.473 | −0.031 | 0% | REJECT |
| GBP/JPY | 0.018 | 0.490 | −0.031 | 0% | REJECT |

## Candidate 4 — Meta-labeling probation (lookback 63, hold 10, daily)

MetaLabeledStrategy (gbm, threshold 0.5 headline; grid + gbm@0.55, linear@0.5) vs primary-only RegimeGatedMomentum 63/10/1.5.

| Pair | Variant | DSR | PBO | CPCV med OOS | frac +ve | Verdict |
|---|---|---|---|---|---|---|
| EUR/USD | primary-only | 0.138 | 0.857 | +0.001 | 60% | REJECT |
| EUR/USD | meta-labeled | 0.000 | 1.000 | +0.004 | 60% | REJECT |
| GBP/USD | primary-only | 0.000 | 0.536 | −0.040 | 0% | REJECT |
| GBP/USD | meta-labeled | 0.144 | 0.716 | +0.000 | 13% | REJECT |

**Probation answer:** the meta gate nudges CPCV median OOS Sharpe up on both pairs (EUR/USD +0.001 → +0.004; GBP/USD −0.040 → +0.000) but both stay ≈ 0 and both variants fail the gate. Meta-labeling is not a rescue for this primary.

---

## Shortlist

**Nothing passed the full gate.** Closest candidates, honestly stated:

1. **EUR/USD carry-filtered trend (126)** — best single row of the day: DSR 0.416, CPCV +0.024 with 87% positive paths (vs +0.011/73% unfiltered). The carry filter visibly helps on EUR/USD. But PBO 0.987 within its own 3-config grid says the headline is the lucky pick of the selection; gate fails closed. REJECT stands.
2. **EUR/USD plain trend (63/126, hold 21)** — PBO 0.233 pass, CPCV pass, but DSR 0.17–0.18 ≪ 0.95 once deflated by 104 trials. The raw OOS edge is real-but-tiny (~zero after costs); it cannot carry the multiple-testing weight of this campaign.

## Commentary

- **The deflation denominator is doing its job.** Several EUR/USD rows pass PBO and CPCV; what rejects them is DSR at n_trials=104. A 0.15–0.4 DSR means "plausibly a small positive edge, nowhere near proven under this much searching."
- **Costs and the JPY complex:** USD/JPY, EUR/JPY, GBP/JPY show a near-uniform −0.031 median with 0% positive paths across *every* candidate including the carry filter — a slow bleed, not variance. Whatever the signal does there, per-pair costs on the JPY legs plus regime-gating leave nothing.
- **GBP/USD is the worst pair in the basket** (PBO 0.79 on the 12-config trend grid, 0% positive paths everywhere): in-sample selection on GBP/USD is systematically overfit. AUD/USD not far behind.
- **XS currency momentum:** PBO now fine (0.318, vs 0.868 in the prior run) but the book's OOS Sharpe is −0.044 with 7% positive paths — net of v5 per-pair costs the monthly rotation loses on most paths. Consistent with the literature's "partially cost-explained" caveat, at retail costs.
- **Carry filter mechanics check out** (point-in-time monthly policy rates; vetoes fire in the right directions) — it improves EUR/USD and does nothing harmful elsewhere; it just doesn't create edge where the underlying trend signal has none.

## Ledger

- **n_trials before this sweep: 6** (plus 2 benchmark configs recorded at sweep start = 8)
- **n_trials after: 104** (+96 distinct: 72 trend grid × 6 pairs net of dups, 18 carry, 6 meta, 5 portfolio)

## Compute notes

- Full 6-pair basket and full grids ran for every candidate; nothing was cut for compute (each single-pair gate run ≈ 4–8 s; the 22-pair portfolio gate ≈ 2 min).
- Candidate 2 used managed exits, warmup 250, 22/22 pairs loaded from the parquet store (all ≥ 300 bars in window).
- Rates CSV covers the whole iteration window monthly (2013-01 → 2024-12); the 2025-01-01 row never enters a strict < 2025-01-01 run.
- Logs: `engine/scratch/p3c_logs/*.log`; validation JSONs: `engine/data_store/validation/*.json`.
