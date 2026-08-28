# Book R-252 — exact 10-year causal retrospective audit

**Status:** research-only, causal retrospective replay; **not a true blind backtest**.

**Window:** 2016-08-19 to 2026-08-19 (2513 common daily sessions).  All values are USD.

## Locked method audited

R-252 is not retuned here: monthly decision at the final common-session close, next common-session open fill, positive 252-session momentum divided by 63-session log-return volatility, maximum three equal-weight long ETFs, 95% gross, one ETF per predeclared economic cluster, 5 bps/side (plus 10 bps/side stress), and a paid final liquidation. Cached bars are price-return OHLCV; dividends and cash interest are not reconstructed.

## Full-window results

| Cost assumption | Total return | Annualized return | Sharpe | Max drawdown | Final NAV | Transaction cost |
|---|---:|---:|---:|---:|---:|---:|
| 5 bps/side | +405.22% | +17.64% | 0.974 | 23.63% | $505,217.46 | $6,196.25 |
| 10 bps/side (2x stress) | +390.37% | +17.29% | 0.958 | 23.65% | $490,374.65 | $12,171.49 |

## Activity: fills are not independent trades

The base run produced **406 order fills** (403 at monthly rebalances plus 3 final liquidation fills) across **120 scheduled monthly selections**. These correspond to **86 continuous per-ETF holding episodes**, not 406 independent trade ideas. Median completed episode length was 62 calendar days.

At 2x costs, fill count and episodes are unchanged by construction: **406 fills**, **86 holding episodes**. Only the cost assumption changes.

## Flat-start calendar folds (not additive)

Each fold starts at $100,000 flat. It uses prior closes only to calculate the already-frozen 252-session lookback; no position is carried between folds. Returns below therefore should not be compounded into the full-window result.

| Fold | Base return | 2x-cost return | Base max DD | Scheduled selections | Fills | Holding episodes | Note |
|---|---:|---:|---:|---:|---:|---:|---|
| 2016 (2016-08-19–2016-12-30) | +0.00% | +0.00% | 0.00% | 4 | 0 | 0 | pre-warmup / no R-252 signal |
| 2017 (2017-01-03–2017-12-29) | +20.08% | +19.59% | 4.79% | 11 | 46 | 13 | flat start |
| 2018 (2018-01-02–2018-12-31) | -11.84% | -12.21% | 23.63% | 11 | 43 | 12 | flat start |
| 2019 (2019-01-02–2019-12-31) | +18.26% | +17.96% | 4.60% | 11 | 38 | 8 | flat start |
| 2020 (2020-01-02–2020-12-31) | +33.91% | +33.43% | 17.08% | 11 | 44 | 11 | flat start |
| 2021 (2021-01-04–2021-12-31) | +10.29% | +9.83% | 13.96% | 11 | 46 | 13 | flat start |
| 2022 (2022-01-03–2022-12-30) | +3.88% | +3.58% | 22.30% | 11 | 27 | 6 | flat start |
| 2023 (2023-01-03–2023-12-29) | +7.06% | +6.50% | 13.21% | 11 | 44 | 14 | flat start |
| 2024 (2024-01-02–2024-12-31) | +16.55% | +16.18% | 11.07% | 11 | 43 | 10 | flat start |
| 2025 (2025-01-02–2025-12-31) | +23.27% | +22.95% | 16.49% | 11 | 41 | 8 | flat start |
| 2026 (2026-01-02–2026-08-19) | +27.69% | +27.40% | 12.44% | 7 | 28 | 7 | flat start |

## Causality and reproducibility checks

- Every monthly-rebalance fill is recorded strictly after its decision date; none is a same-bar close fill.
- Every requested input parquet is SHA-256 hashed in the JSON artifact, alongside this runner, the frozen R source, and the preregistration document.
- The test uses a strict common-session ETF panel. It does not substitute stale prices for missing sessions.
- The annual folds are independent flat starts, so they make calendar variation visible without hiding a prior-year open position.

## Important limitation

This is **not a blind 10-year backtest**: this repository's historical cache was already accessible before this audit. Causal timing avoids look-ahead inside the simulation, but it cannot undo prior human exposure to the data. Do not fund or deploy Book R from this result. The next valid evidence is an externally held vendor lockbox or forward-paper period after the 2026-08-28 specification freeze.

## Artifacts

- Compressed JSON audit parts: `engine/data_store/validation/book_r_10y_causal_audit_2026-08-28.parts/`
- Frozen specification: `engine/data_store/book_r_usd_etf_prereg_2026-08-28.md`
- Frozen source audited: `engine/apex_quant/research/book_r_usd_etf.py`

Reconstruct the full JSON with:

```bash
cat engine/data_store/validation/book_r_10y_causal_audit_2026-08-28.parts/part-* | gzip -dc > book_r_10y_causal_audit_2026-08-28.json
```
