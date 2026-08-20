# Book C deep audit — 2026-08-19

## Executive verdict

**Book C is still the best existing Apex strategy, but it does not pass a
funded-account production-readiness audit.** No tested signal or exit modification
qualified as a strict replacement. A separately preregistered sizing frontier later
selected 0.85% risk as the paper engine's return/drawdown operating point; the 1.00%
version remains the research control.

The result is a conditional statistical pass and a production/funded-readiness fail:

| Gate | Verdict | Evidence |
|---|---:|---|
| Exact reproducibility | PASS | Legacy anchor reproduced exactly: Sharpe 0.923766, DD 15.9215%, 1,654 trades, final equity 316,181.08 |
| Calendar-correct headline | PASS | 365-day mixed-calendar Sharpe 1.1118, Sortino 1.2800, CAGR 13.95% |
| CPCV path stability | PASS | 15/15 control paths positive |
| Deflated Sharpe | PASS | DSR 0.9971 after 351 logged trials |
| Doubled modeled costs | PASS | Sharpe 1.1118 → 1.0939; average month 2,001.68 → 1,992.67 account-currency units |
| 2025+ confirmation | CONDITIONAL PASS | Sharpe 0.8781, PF 1.1935, DD 9.58%, +15.33%; this window has been inspected before and is not blind |
| Improvement gate | FAIL | 0/4 isolated candidates met every preregistered leg |
| Configuration-selection risk | FAIL/WARNING | Five-cell PBO 0.915; DSR and CPCV are favorable, but the selection process is not clean enough to call institutional |
| Universe robustness | FAIL | Removing the 12 selected stocks gives Sharpe −0.166, PF 0.941, DD 19.66%, total return −12.17% |
| Funded-account fit | FAIL | Close-only FTMO proxies are only 52.6% (1-Step) and 52.3% (2-Step) of all rolling starts; intraday monitoring can only make this worse |
| True blind evidence | FAIL | No untouched historical interval remains; the live paper state has zero completed trades |

## Correct control statistics

Pre-2025 iteration window, 39 instruments, 100,000 starting equity:

| Metric | Book C control |
|---|---:|
| Total return | +216.18% |
| CAGR | 13.95% |
| Average monthly P&L | +2,001.68 account-currency units |
| Median monthly P&L | +850.83 |
| Sharpe (calendar-correct 365) | 1.1118 |
| Sharpe (legacy 252 anchor) | 0.9238 |
| Sortino | 1.2800 |
| Profit factor | 1.3409 |
| Maximum drawdown | 15.92% |
| Worst close-to-close day | −3.45% |
| Worst month | −20,634.49 |
| Win rate | 55.20% |
| Trades | 1,654 |
| Maximum gross leverage | 2.42× |

The cash figures must not be labelled GBP. The backtester defaults
`quote_to_account_rate` to 1.0 and does not perform historical quote-to-GBP conversion.

## Isolated improvement results

| Candidate | Sharpe | PF | DD | Avg/month | CPCV wins vs control | Paired p | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| Control, 1.00% risk | 1.1118 | 1.3409 | 15.92% | 2,001.68 | — | — | Champion |
| Runner exit | 1.1266 | 1.4014 | 16.85% | 1,856.01 | 6/15 | 0.4562 | Reject |
| 15% notional cap | 1.0983 | 1.3455 | 15.65% | 1,739.93 | 8/15 | 0.5882 | Reject |
| 4.5% portfolio-risk cap | 0.9966 | 1.3061 | 17.38% | 1,345.60 | 4/15 | 0.7958 | Reject |
| 0.75% per-trade risk | 1.1489 | 1.3773 | 12.80% | 1,502.94 | 10/15 | 0.2989 | Defensive mode only |

The 0.75% variant is useful for a funded-account sleeve, but it is not a strict
upgrade: drawdown improves by 3.12 percentage points while average monthly P&L falls
24.9%. Its paired improvement is not significant.

### Follow-up risk frontier — 2026-08-20

A separately preregistered seven-point grid between 0.75% and 1.00% selected **0.85%**
as the equal-weight return/drawdown compromise:

| Metric | 0.75% defensive | **0.85% middle** | 1.00% control |
|---|---:|---:|---:|
| Total return | 162.32% | **190.86%** | 216.18% |
| CAGR | 11.56% | **12.87%** | 13.95% |
| Average month | 1,502.94 | **1,767.19** | 2,001.68 |
| Sharpe | 1.1489 | **1.1437** | 1.1118 |
| Sortino | 1.3172 | **1.3153** | 1.2800 |
| Profit factor | 1.3773 | **1.3739** | 1.3409 |
| Maximum drawdown | 12.80% | **14.01%** | 15.92% |
| Worst month | −12,627.91 | **−15,871.32** | −20,634.49 |

The 0.85% point retains 88.3% of control total return and reduces drawdown by 1.91
percentage points. All 15 CPCV paths were positive and 10/15 beat control; doubled-cost
Sharpe was 1.1294. Post-2024 non-blind confirmation was Sharpe 1.1023, return 16.81%,
and drawdown 8.18%. The paired Sharpe improvement was not significant (`p=0.3091`),
so this is a sizing compromise rather than evidence of additional alpha.

## Funded-account proxy

The rules modelled are the current FTMO 100,000 objectives: 1-Step uses a 10% target,
fixed 3,000 daily-loss amount, 10,000 end-of-day trailing loss and 50% Best Day rule;
2-Step uses 10% then 5% targets, fixed 5,000 daily loss and static 10,000 maximum loss.
Every start receives up to 365 observations per phase.

| Variant | 1-Step pass/all starts | 1-Step median pass | 2-Step pass/all starts | 2-Step resolved pass | 2-Step median pass |
|---|---:|---:|---:|---:|---:|
| Control 1.00% | 52.6% | 93 days | 52.3% | 76.1% | 187 days |
| 15% notional | 52.6% | 101 days | 53.4% | 80.5% | 210 days |
| Risk 0.75% | **62.7%** | 140 days | **54.8%** | **96.3%** | 264 days |

These are overlapping, close-only, optimistic scenarios—not independent trials and
not broker-certified pass probabilities. Intraday floating equity, CE(S)T reset
boundaries, exact trade-opening-day counts, fees and closed-P&L Best Day accounting
are unavailable in a daily backtest.

## Concentration and generalisation

- The 12 selected current stocks generated 185,847.30, or **84.0% of total net P&L**.
- Long trades generated +250,754.79; short trades lost −29,410.94.
- Forex generated −5,020.70 across only 24 trades.
- Removing the 12 stocks makes the remainder unprofitable. This is a diagnostic, not
  a fair alternative portfolio, but it demonstrates that the edge is not broad.
- Using today’s winners back to 2016 is not a point-in-time constituent universe.
  Delisted names and historical membership are missing, so survivorship/selection
  bias cannot be ruled out.

## Execution and accounting audit

Implemented corrections:

1. Paper trading now supplies the daily open to managed exits, matching the
   backtester’s gap-through-stop behavior.
2. Trade records now retain the actual filled exit price, including gap/slippage,
   rather than displaying the stop level while booking a different P&L.
3. Nonzero entry commission is deducted from cash/equity immediately as well as
   retained in the trade record.

Remaining limitations:

- Management begins on the bar after entry. A census found 5 entry-day stop touches,
  2 target touches and 7 first-partial touches among 1,664 entries. A corrected
  intraday event order cannot be inferred from daily OHLC.
- `PaperPortfolio.step()` still does not implement every portfolio overlay present in
  the batch loop (including the batch daily-loss flatten and portfolio-volatility
  scalar). The current Book C paper runner also loads the base configuration, not the
  prop overlay.
- Historical quote-currency conversion is absent.
- The paper state was cleanly reseeded on 2026-08-20 at 0.85% risk, with nine pending
  entries and zero completed trades. It remains a pipeline smoke test, not forward
  validation.

## Assessment of the attached Book C+ plan

**Overall quality: 3/10.** The ideas are research-worthy, but the document is not a
safe implementation specification.

- It describes a different engine: actual stops are ATR14 × 2.5, signal activation is
  sign/regime aligned rather than a hard ±0.75 cutoff, and the managed exit ladder is
  not the one stated.
- Its projected +8,950% return, 0.92 Sharpe and 14.8% drawdown are unsupported. The
  corrected current Book C Sharpe is already 1.112, so the claimed “optimized” 0.92
  would be worse.
- Removing the first partial was tested directly as runner mode and rejected.
- Tightening the portfolio cap was tested directly and made both return and drawdown
  worse.
- The proposed Yang–Zhang stop formula annualizes twice and the sizing equation adds
  an extra price factor, producing inconsistent units.
- The proposed `tanh(score/1.5)` mapping is unsuitable without recalibration: actual
  absolute score median is about 8.46 and the map would saturate for most signals.
- Academic support for volatility management and range-based estimators establishes
  plausible hypotheses, not the document’s numerical forecasts.

## Production decision and next plan

1. **Keep the 1.00% control as the research benchmark and deploy 0.85% as Book C's
   paper operating point.** The sizing change is a return/drawdown compromise, not new
   alpha and not a statistically significant Sharpe improvement.
2. **Reserve 0.75% for a separately labelled defensive/funded-mode paper sleeve.** Its
   purpose is higher survival probability, with the explicit profit/time trade-off above.
3. **Do not fund either mode yet.** First build historical point-in-time membership,
   delisted-symbol coverage, quote/account FX conversion, and intraday equity-limit
   replay.
4. **Broaden the economic universe** with liquid bond, rate and commodity futures or
   defensible proxies. Published TSMOM evidence is diversified across equity indexes,
   currencies, commodities and sovereign bonds; the present single-stock-heavy book
   is not comparable.
5. **Freeze a genuinely unseen forward protocol from 2026-08-20 onward.** Sixty days
   is enough for execution/parity verification but not for proving a slow trend edge.
   No parameter changes may use that window; require at least 100 completed trades and
   preferably 6–12 months before capital promotion.
6. **Promotion requirements:** zero unexplained parity differences, zero funded-rule
   breaches under intraday replay, positive expectancy after realized broker costs,
   no single selected-current-stock sleeve responsible for most profit, and the same
   preregistered CPCV/bootstrap/DSR gates used here.

## Reproducible artifacts

- Pre-registration: `data_store/book_c_deep_audit_prereg_2026-08-19.md`
- Full result: `data_store/validation/book_c_deep_audit_2026-08-19.json`
- Funded/entry result: `data_store/validation/book_c_funded_diagnostics_2026-08-19.json`
- Main runner: `scripts/run_book_c_deep_audit.py`
- Funded diagnostics: `scripts/run_book_c_funded_diagnostics.py`
- Tests: `tests/test_book_c_deep_audit.py`

Research references:

- Moskowitz, Ooi & Pedersen, *Time Series Momentum*:
  https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf
- Moreira & Muir, *Volatility Managed Portfolios*:
  https://www.nber.org/papers/w22208
- Yang & Zhang, *Drift-Independent Volatility Estimation*:
  https://www.jstor.org/stable/10.1086/209650
- Bailey et al., *Backtest Overfitting in Financial Markets*:
  https://escholarship.org/uc/item/4hn4t174
- FTMO objectives used for the funded proxy:
  https://ftmo.com/en/trading-objectives/
