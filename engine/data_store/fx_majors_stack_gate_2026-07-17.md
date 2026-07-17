# FX Majors Stack — Gate Verdicts (2026-07-17)

Pre-registration: `data_store/fx_majors_stack_prereg_2026-07-17.md` (written before
any run). Universe: 7 majors only (EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD,
USD/CAD, NZD/USD). Iteration window strictly < 2025-01-01; no holdout data touched;
no `--final`. Seed 42. Costs: config v5 per-pair (majors ~1 pip RT). Gates:
DSR > 0.95 (deflated by the shared ledger's full count), PBO < 0.5, CPCV median
OOS Sharpe > 0 with > 50% of 15 paths positive.

**Ledger: n=136 before → n=145 after (+9 new trials, exactly the pre-registered
set; budget ≤ 10 respected, 1 spare unused).**

## Headline verdict: NOTHING PASSED.

| Sleeve | Gate | DSR (n) | PBO | CPCV med OOS | paths +ve | Verdict |
|---|---|---|---|---|---|---|
| A carry-filtered trend, EUR/USD | single-instrument | 0.869 (136) | n/a* | +0.024 | 87% | REJECT |
| A carry-filtered trend, GBP/USD | single-instrument | 0.000 (136) | n/a* | −0.042 | 20% | REJECT |
| A carry-filtered trend, USD/JPY | single-instrument | 0.023 (136) | n/a* | −0.031 | 0% | REJECT |
| A carry-filtered trend, USD/CHF | single-instrument | 0.639 (137) | n/a* | +0.016 | 73% | REJECT |
| A carry-filtered trend, AUD/USD | single-instrument | 0.043 (137) | n/a* | −0.032 | 0% | REJECT |
| A carry-filtered trend, USD/CAD | single-instrument | 0.020 (138) | n/a* | −0.031 | 0% | REJECT |
| A carry-filtered trend, NZD/USD | single-instrument | 0.024 (139) | n/a* | −0.031 | 20% | REJECT |
| B carry tilt book | portfolio | 0.118 (143) | 1.000 | −0.033 | 33% | REJECT |
| C XS momentum majors book | portfolio | 0.017 (141) | 0.549 | −0.018 | 13% | REJECT |
| Combined A+B+C stack (2-of-3) | portfolio | 0.001 (145) | **0.315 ✓** | −0.023 | 13% | REJECT |

\* Sleeve A was run headline-only (budget: the 3-config PBO grids for the 4 majors
gated this morning are already ledgered; replicating them on the 3 new majors would
have cost 9 trials = the whole batch budget). Single-config ⇒ per-instrument PBO
is n/a and that leg fails closed by construction; the morning's 3-config grids
(PBO 0.834–0.987 on the majors) show the PBO leg fails with configs too.

## Sleeve detail

### A — carry-filtered slow trend (lookback 126, vol 63, hold 21, rr 1.5, rule_based)
Window 2014-01-01 → 2024-12-31 (adapter-filled, as `run_candidate_check.py`),
single-instrument `Backtester` managed exits, warmup 250. Carry vetoes were heavy
(329–500 of ~430–600 signals per pair, i.e. ~76–87% of trend signals fight the
rate differential and are refused).

| Pair | trades | Sharpe | expectancy | PF | maxDD | win | net P&L | vetoes |
|---|---|---|---|---|---|---|---|---|
| EUR/USD | 92 | −0.07 | −23.7 | 0.92 | 10.9% | 58% | −2,183 | 463/556 |
| GBP/USD | 100 | +0.13 | +35.3 | 1.12 | 4.6% | 48% | +3,533 | 500/601 |
| USD/JPY | 87 | −0.01 | −6.7 | 0.98 | 7.8% | 54% | −581 | 443/531 |
| USD/CHF | 85 | −0.33 | −99.3 | 0.67 | 11.5% | 48% | −8,442 | 431/517 |
| AUD/USD | 92 | −0.19 | −61.0 | 0.83 | 10.0% | 50% | −5,611 | 404/497 |
| USD/CAD | 102 | −0.22 | −57.0 | 0.82 | 9.5% | 52% | −5,818 | 329/432 |
| NZD/USD | 103 | −0.05 | −18.4 | 0.95 | 8.0% | 49% | −1,895 | 457/560 |

EUR/USD remains the best single result (DSR 0.869, 87% positive CPCV paths) but
does not clear DSR > 0.95 under honest ledger deflation. GBP/USD's positive
full-window P&L is not robust out-of-sample (CPCV median −0.042, 20% positive).

### B — carry tilt book (CrossSectionalCarry 30/30, FX7)
Window 2016-01-03 → 2024-12-31 (store cache), `PortfolioBacktester` managed exits,
config risk caps binding. Headline: 969 trades, **−17.1%** total, Sharpe −0.36,
expectancy −17.7 pnl/trade (−0.059%/trade), PF 0.816, maxDD 19.1%, win 47%,
leverage ~2.7x, realized turnover 107.7 RT/yr (~15 RT/yr/pair ≈ 2.4-week average
holding — monthly-ish per pair, NOT daily churn; entries only fire when flat and
TradeManager drives exits). Per-pair net: GBP/USD +2,295, NZD/USD +516,
USD/CAD −157, AUD/USD −2,690, USD/JPY −5,087, USD/CHF −5,767, EUR/USD −6,290.

Two pre-registered caveats confirmed:
- The quarterly-rotation variant (hold 63) is a **no-op under managed exits** —
  identical equity (969 trades both); `holding_horizon` only binds in barrier
  mode. Turnover here is exit-driven, so the cost-sensitivity question answers
  itself: at ~15 RT/yr/pair and ~1 pip RT, costs ≈ 0.06%/trade of the −0.059%
  expectancy — the sleeve loses before costs matter much, and loses more with them.
- The backtester credits **no swap income**; at retail swap markup (research doc
  sec.1) net carry ≈ 0 or worse, so this is the honest retail view of the book.

### C — XS momentum, majors-only (CurrencyCrossSectionalMomentum, 63/k2/hold21)
Same window/engine as B. Gate: DSR 0.017 (n=141), PBO 0.549, CPCV median −0.018,
13% of 15 paths positive — REJECT on all three legs. Headline book: 609 trades,
**−16.9%** total, Sharpe −0.31, expectancy −28.4 pnl/trade (−0.070%/trade),
PF 0.828, maxDD 19.4%, win 45%. Per-pair net: GBP/USD +5,291, EUR/USD +3,788,
NZD/USD +557, USD/CAD −3,169, USD/CHF −5,382, AUD/USD −8,783, USD/JPY −9,283.
Majors-only at ~1 pip does not rescue the 22-pair failure: the effect is simply
not there at monthly rotation on 7 pairs, before or after costs.

### Combined stack — A+B+C, per-instrument majority vote (min_votes 2)
Gate: DSR 0.001 (n=145), **PBO 0.315 (the only gate leg passed anywhere in this
batch)**, CPCV median −0.023, 13% of 15 paths positive — REJECT (DSR and CPCV fail
decisively). Headline book: 547 trades, **−18.1%** total, Sharpe −0.49,
expectancy −33.9 pnl/trade (−0.114%/trade), PF 0.717, maxDD 19.3%, turnover 60.8
RT/yr. Per-pair net: GBP/USD +879, USD/CAD −953, NZD/USD −1,703, USD/CHF −2,492,
EUR/USD −3,360, USD/JPY −4,235, AUD/USD −6,462.
Unanimity variant (min_votes 3): 126 trades, −7.2%, Sharpe −0.22, PF 0.810,
maxDD 14.5% — fewer, still losing trades. Stacking three weak sleeves produced a
weak stack, not diversification: the sleeves overlap (all three were long/short the
same rate/price differentials) and each component's expectancy is ≈ 0 or negative.

## Determinism

Seed 42 (config.yaml). The Sleeve B headline backtest was run twice in-process:
identical equity series and identical trade count (969 = 969). OK.

## Honest read

**FX at retail costs has no certifiable stack from the evidence-backed sleeves.**
The complete pre-registered selection set — carry-filtered trend per-instrument on
7 majors, carry tilt book, majors-only XS momentum book, and the combined
majority-vote stack — fails the gate at honest costs with honest multiple-testing
deflation. The single most promising configuration in the entire FX research
program remains EUR/USD carry-filtered slow trend (DSR 0.869, 87% positive CPCV
paths), and it still falls short of DSR > 0.95; nothing here earns a `--final`
holdout look. Recommend: stop iterating on FX daily-bar directional sleeves at
retail costs (the ledger, n=145, makes the treadmill visible); the vol-management
layer and non-FX sleeves are where the remaining evidence points.

## Artifacts

- Pre-reg: `data_store/fx_majors_stack_prereg_2026-07-17.md`
- This file: `data_store/fx_majors_stack_gate_2026-07-17.md`
- Gate script (B + stack): `scripts/run_fx_majors_stack_gate.py`
- B/stack raw results: `data_store/validation/fx_majors_stack_gate_2026-07-17.json`
- Sleeve A/C trade stats: `data_store/validation/fx_majors_stack_metrics_harvest.json`
  (harvester: `scratch/harvest_fx_majors_stack_metrics.py`; identical pre-registered
  configs, no new trials)
- Sleeve A per-instrument reports: `data_store/validation/carry_trend_filter__*.json`
- Extended for universe/grid CLI: `scripts/validate_currency_momentum.py`
  (defaults unchanged — the 22-pair default run behaves exactly as before)
