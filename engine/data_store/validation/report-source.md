# Apex Quant Funded-100K Qualification Audit

**Decision date:** 3 September 2026
**Candidate:** Book C / `C_FUNDED_V2`
**Decision:** **NO_FUNDED_STRATEGY — DO NOT DEPLOY TO A PAID OR FUNDED-STAGE ACCOUNT**
**Evidence ceiling:** retrospective, daily-OHLC, validation-only research; not a true blind or executable venue replay

## Executive decision

Book C at 0.85% remains the incumbent and best-documented return/drawdown compromise among the Apex configurations evaluated in this audit; it is not the leader on every individual metric, and it is not a funded-account strategy. Its historical 14.01% maximum drawdown and −3.68% worst close-to-close day are outside a defensible safety envelope for a hard-loss account, before intraday excursions, gaps, spread, swaps, rejected orders, and currency conversion are added.

The new `C_FUNDED_V2` research design materially reduces nominal exposure and adds stop-aware backtest accounting. A separate fail-closed guard component has been unit-tested, but it is not integrated with the replay, paper engine, or venue state. The candidate has two decisive observed screening failures in the synthetic replay, plus independent data and implementation blockers:

1. **The provisional return diagnostics are too weak.** On the frozen 2023–2024 pseudo-out-of-sample window, the mixed-quote evaluation model produced 1.10% annualized with Sharpe 0.485 and profit factor 1.121. The mixed-quote payout model produced 0.50% annualized with Sharpe 0.342 and profit factor 1.097. Under doubled modeled costs, payout annualized return fell to 0.076% and Sharpe to 0.058.
2. **The designed loss cap is not an invariant.** Within the same unconverted model, carried positions allowed current mark-to-stop loss to rise to 1.310% of its synthetic capital base against a 0.90% evaluation design cap, and to 0.766% against a 0.60% payout design cap. New entries are blocked when the cap is full, but existing positions are not continuously de-risked. A stop being present is therefore not the same as the account being funded-safe.

Exact account-currency conversion could change those percentages, so they are not broker-account statistics. It cannot remove the independent blockers: exact product/contract inputs are unfrozen, ordinary costs are absent from planned-loss reservation, next-open orders are not authoritatively revalidated, total open-plus-pending risk is not atomically reserved, the persistent guard is not replay- or venue-integrated, concentration is unreconciled, and no blind forward evidence exists. No candidate evaluated in this report passes the combined speed, drawdown, execution, and evidence requirements. The honest result is no promotion, no website funded label, and no paid challenge.

## What was tested

Two no-retuning protocols were frozen before their respective experiments:

- `engine/data_store/funded_100k_prereg_2026-09-03.md`
- `engine/data_store/funded_100k_v2_prereg_2026-09-03.md`

V1 exposed a units error in the original funded-sizing idea: at a fresh 100,000 account, 0.85% of a 3,000 daily buffer is only 25.50 units of planned risk per trade. A smoke result was intentionally not retained as qualification evidence; the frozen formula itself was enough to reject V1 as economically mis-scaled.

V2 corrected the cash-risk formula. It keeps Book C's frozen `[63, 126, 252]` signal ensemble and managed exits while separating two irreversible modes:

| Mode | Designed base risk per instrument | Designed aggregate stop-risk cap | Designed gross cap | Max positions | Intended day block / flatten | Intended cycle halt |
|---|---:|---:|---:|---:|---:|---:|
| Evaluation | 0.35% of `min(equity, initial)` | 0.90% | 0.60× | 5 | 0.90% / 1.50% | 5.00% |
| Payout | 0.25% of `min(equity, initial)` | 0.60% | 0.45× | 4 | 0.60% / 1.20% | 4.00% |

These are frozen research design limits, not controls proven active on a venue. Every synthetic entry carries a protective stop in the backtester. The replay gives stops priority on an ambiguous daily bar, applies a worse opening fill when price gaps through the stop, charges commissions per fill, and checks entry-bar stops. These are conservative corrections, not evidence that an actual venue accepted or executed protection at the requested price.

## Provisional 2023–2024 mixed-quote diagnostic

The result below is the hardened validation-only replay in `engine/data_store/validation/funded_100k_v2_gate_2026-09-03.json`. Static and completed-EOD-trailing variants were identical in this window. The artifact explicitly labels its currency basis `UNCONVERTED_RAW_QUOTE_CURRENCY`: balances, P&L, exposure, and stop risk combine different quote currencies without executable account-FX conversion. The percentages below are therefore **synthetic within-model diagnostics**, not account-currency returns, drawdowns, or cap measurements.

| Synthetic cell | Total return | Annualized | Sharpe | Profit factor | Max DD | Worst daily OHLC bound | Mean month | 95% lower mean-month bound |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Evaluation, base costs | +2.217% | 1.101% | 0.485 | 1.121 | 3.176% | −0.803% | +0.096% | −0.295% |
| Evaluation, doubled costs | +2.225% | 1.105% | 0.490 | 1.125 | 3.216% | — | — | — |
| Payout, base costs | +0.997% | 0.497% | 0.342 | 1.097 | 2.424% | −0.546% | +0.043% | −0.217% |
| Payout, doubled costs | +0.152% | 0.076% | 0.058 | 1.020 | 3.699% | — | — | — |

The small improvement in the evaluation doubled-cost cell is a path/selection effect: costs alter which constrained candidates fit, so it must not be interpreted as evidence that higher costs improve the strategy. The payout cell shows fragility inside this model. None of the figures is an accurate expected funded-account return until contract values and causal FX conversion are applied.

The corrected run is explicitly non-binding because deep diagnostics were stopped once deterministic screening gates had failed. Completing hundreds of order permutations or 100,000 whole-day bootstrap paths cannot repair those observed values inside the same synthetic model. Exact currency/contract replay may change the values, but it is itself a mandatory data gate rather than permission to promote the strategy. The runner prevents reduced diagnostic arguments from crossing the qualification boundary.

## Gate decision

| Required property | Threshold | Observed | Decision |
|---|---:|---:|---|
| Evaluation Sharpe | ≥0.75 | 0.485, mixed-quote | Provisional screen fail |
| Evaluation profit factor | ≥1.15 | 1.121, mixed-quote | Provisional screen fail |
| Payout annualized return | ≥4.00% | 0.497%, mixed-quote | Provisional screen fail |
| Payout doubled-cost annualized return | ≥2.00% | 0.076%, mixed-quote | Provisional screen fail |
| Payout monthly mean, lower 95% bound | >0 | −0.217%, mixed-quote | Provisional screen fail |
| Evaluation aggregate stop risk | ≤0.90% of capital base | 1.310%, raw-quote sum | Provisional screen fail |
| Payout aggregate stop risk | ≤0.60% of capital base | 0.766%, raw-quote sum | Provisional screen fail |
| Exact contract, account-FX, bid-ask, and stress replay | Required | Missing / not integrated | Data-blocked |
| Planned loss includes entry/exit costs | Required | Stop distance only | Data-blocked |
| Atomic open-plus-pending reservation and next-open revalidation | Required | Not integrated | Data-blocked |
| Venue stop acknowledgement and persistent guard transitions | Required | No event stream / no integration | Data-blocked |
| Official opened-position trading-day ledger | Required | Backtester opening count only | Data-blocked |
| Reconciled terminal positions and concentration | Required | Incomplete | Data-blocked |
| True blind forward evidence | ≥6 months and ≥100 completed trades | Not available | Fail |

Whole-day bootstrap output is now marked diagnostic-only. It cannot be a binding survival probability because it does not rebuild positions and stops at every sampled event, enforce block/flatten/cycle-halt transitions, or reproduce pending-order reservations. DSR is also data-blocked: the historical trial ledger does not record the annualization convention for every prior Sharpe observation. Concentration is data-blocked until terminal open positions and partial realizations reconcile exactly to final equity.

## Why a stop loss does not by itself protect a funded account

A protective stop bounds loss only under its execution assumptions. It does not guarantee the requested fill through an overnight gap, a disconnected terminal, rejected/cancelled protection, stale FX, partial liquidation, or insufficient liquidity. It also does not automatically cap the **sum** of all open and pending risks.

The corrected backtest/risk boundary now rejects missing, non-finite, zero, or wrong-side stops and targets. It also rejects non-finite price, volatility, ATR, FX, sizing, cap, unit, notional, and risk values. This closes a real fail-open defect where a `NaN` stop could previously create a permitted position whose exit comparison never fired.

The isolated guard component correctly fails closed in unit tests on stale, corrupt, or out-of-order state and persists daily/cycle latches. It is not yet called by the V2 replay, website paper engine, or a venue event loop. Two production properties also remain unimplemented:

- carried positions are not deterministically trimmed or re-stopped whenever total mark-to-stop risk rises above the cap; and
- approvals do not create one atomic broker-backed reservation for the complete open-plus-pending book, so independent approvals cannot be treated as separately spendable budgets.

Until those are solved with authoritative venue state, a trade cannot honestly be described as impossible to lose 10,000. The proposed internal flatten thresholds create distance from the firm's hard floor, but a sufficiently large gap or execution failure can jump both a stop and a software threshold.

## Existing-engine comparison

These figures come from overlapping studies with different windows, universes, and evidence quality. They compare the evaluated candidates; they do not prove an exhaustive or universally best ranking.

| Candidate | Return evidence | Sharpe | Drawdown/tail evidence | Funded assessment |
|---|---:|---:|---:|---|
| Book C 0.85% | 12.87% CAGR; 1,767 account units/month | 1.144 | 14.01% DD; −3.68% worst close day | Incumbent documented compromise; not funded-safe |
| Book C 0.75% | 11.56% CAGR; 1,503 units/month | 1.149 | 12.80% DD | Safer, still outside hard-loss envelope |
| Book H 0.50% | 4.95% annualized; 413 units/month | 0.922 | 8.2% forward p95 DD | Better tail, too slow and not execution-certified |
| Book R-252 | 17.63% CAGR | 0.972 | 23.63% DD; −6.54% day | Reject |
| Book R 3ATR sensitivity | 6.84% CAGR | 0.987 | 9.01% DD; −2.98% day | Post-result sensitivity; not promotable |
| Book N base | 9.37% CAGR | 0.936 | 12.13% DD | Current-universe selection bias; reject |
| `C_FUNDED_V2` payout | 0.50% synthetic annualized | 0.342 | 2.42% synthetic DD; raw-quote cap overrun | Provisional screen fail and operationally blocked |

Book C 0.75% previously achieved only an optimistic close-only 54.8% all-start 2-Step pass rate, with a 264-day median among passers. A different 0.75%/2.5%-cap frontier cell had 4.6% CAGR and only 13.1% of resampled paths reached +10% inside 252 sessions. These are overlapping retrospective proxies, not independent pass probabilities. They demonstrate the speed/safety conflict rather than solve it.

## Best product shape, if the engineering and evidence later pass

For Book C's roughly 21-session holding logic, the closest current FTMO product fit is **2-Step Swing**, not 1-Step Standard. FTMO currently states that 2-Step uses +10% Challenge and +5% Verification targets, a 5% maximum daily loss, a static 10% maximum loss, and at least four trading days on each of which a position is opened. The profit target is met only with all positions closed. FTMO also states that Swing is exclusive to 2-Step and permits overnight, weekend, and news holding. Standard funded-stage restrictions would force closures and therefore change Book C's tested strategy. See [FTMO Trading Objectives](https://ftmo.com/en/trading-objectives/), [FTMO Swing account type](https://ftmo.com/faq/ftmo-swing-account-type/), and [overnight/weekend rules](https://ftmo.com/faq/do-i-have-to-close-my-positions-overnight-or-before-the-weekend/).

This is a product-fit conclusion, not a recommendation to buy a challenge. FTMO requires continuous compliance using live equity including floating P/L, swaps, and commissions, and its exact symbols, spreads, specifications, and trading hours are platform-specific. Algorithmic trading is allowed only when legitimate, properly risk-managed, and replicable under real-market conditions. See [FTMO strategy rules](https://ftmo.com/faq/which-instruments-can-i-trade-and-what-strategies-am-i-allowed-to-use/) and [FTMO symbols/specifications](https://ftmo.com/en/symbols/).

`C_FUNDED_V2` was **not** replayed as an exact FTMO 2-Step Swing account. It used a synthetic 3% internal daily envelope and evaluated both static and completed-EOD-trailing maximum-loss variants. The product section identifies a plausible future container only; exact FTMO qualification requires the selected platform's rules, opened-position trading days, contract values, account currency, and event-level fills.

## Better-strategy research direction

No Apex candidate evaluated in this audit qualifies. The next defensible family is a new, platform-native diversified time-series trend strategy across the exact available equity indices, major FX, metals/commodities, and—where the selected platform actually offers them—rates/bond markets. It should not reuse today's selected-stock-heavy universe or substitute convenient ETFs for unavailable contracts.

The hypothesis has a sound research basis: Moskowitz, Ooi, and Pedersen document time-series momentum across liquid equity-index, currency, commodity, and bond futures ([paper](https://fairmodel.econ.yale.edu/ec439/mosk.pdf)); Moreira and Muir report that reducing exposure when volatility is high improved factor Sharpe ratios in their historical samples ([NBER](https://www.nber.org/papers/w22208)). Neither result guarantees a profitable implementation. Daniel and Moskowitz show that momentum can suffer persistent crashes, especially after market declines, during high volatility, and into rebounds ([NBER paper](https://www.nber.org/papers/w20439.pdf)). Those crash states must be designed into the stress protocol, not cited away.

## Implementation plan from here

1. **Freeze the actual product.** Record firm, product, account currency, platform, timezone/reset, automation permission, news/weekend rules, maximum allocation, and exact symbol list. No generic “100k funded” profile is sufficient.
2. **Acquire executable data.** Export contract multipliers, tick/lot sizes, leverage/margin, commissions, swaps, and at least one-minute bid/ask plus order/fill/stop-acknowledgement history. Add timestamped account-currency FX conversion.
3. **Build the execution safety invariant.** Revalidate every pending order from authoritative opening equity; atomically reserve all open-plus-pending planned loss; include ordinary costs; and trim/cancel/flatten whenever total stressed risk exceeds its cap. Confirm fills and persist latches across restarts.
4. **Pre-register a new candidate.** Either fix Book C as `C_FUNDED_V3` without using V2 outcomes to optimize it, or freeze the platform-native diversified trend family above. Define the universe, signals, stops, costs, phase rules, and every pass gate before viewing results.
5. **Run event-level qualification.** Fresh accounts for build/interim/validation; static and any applicable trailing floors; Challenge, Verification, and payout modes; exact four-day accounting; gap, spread, swap, outage, missed-stop, and partial-fill stresses; fixed-model combinatorial OOS; concentration reconciliation; and a unit-complete DSR reference.
6. **Shadow only after a full pass.** Run a separate 100,000-unit paper account unchanged for at least six months and 100 completed trades. Require zero official breaches, zero unexplained backtest/paper parity differences, and venue-stop acknowledgement on every entry.
7. **Paid use requires a second decision.** A retrospective or shadow pass is not automatic authority to buy a challenge or route orders. Recheck the then-current rulebook and run a final go/no-go audit.

The qualitative/LLM validation node should remain shadow-only as well. Point-in-time fundamentals, filings, insider data, and short-interest history are not available across the full historical universe, so it cannot yet be backtested causally as a hard override. It may log an independent score, but it must not rescue or veto `C_FUNDED_V2` until it passes a separately frozen walk-forward gate.

## Engineering changes and verification

The isolated implementation adds:

- stop-aware funded equity traces with conservative gap-through-stop fills;
- entry-bar stop enforcement and per-fill commissions;
- correct cash-risk bases and separate evaluation/payout limits;
- an isolated, account-scoped, DST-aware, fail-closed persistent guard component, not yet execution-integrated;
- scalar/vector rule replay requiring balance, equity, and verified-flat target state, with explicit opened-position counts instead of a closed-P&L trading-day proxy;
- hard rejection of non-finite or wrong-side risk inputs;
- immutable source/config/data/prior-trial manifests, no-retry ledgers, distinct output paths, and a qualification boundary that reduced runs cannot cross; and
- explicit machine-readable blockers for every missing funded-critical property.

All targeted funded/risk tests and the complete repository test suite passed locally after the corrections. Python compilation and `git diff --check` also passed. This is local verification, not an external CI or venue certification. No Book A/B/C/R paper state, website strategy, broker position, paid challenge, or external account was changed by the qualification run.

## Limitations

- The 2023–2024 window is pseudo-OOS relative to the latest design, not genuinely unseen history; earlier Book C research has already inspected it.
- Daily OHLC supplies a conservative set of co-extreme bounds, not the true event order inside a bar.
- Historical P&L and exposure mix quote currencies without executable account-currency conversion.
- The data use today's selected instruments historically; delisted names and point-in-time membership are incomplete.
- Stops reduce ordinary losses but cannot guarantee fills through gaps or venue failures.
- Backtests and resampling do not guarantee future returns, passing a challenge, retaining an account, or receiving payouts.

## Claim-to-source ledger

| Claim supported | Source | Use and boundary |
|---|---|---|
| Book C 0.85% statistics, concentration, funded proxy, no true blind interval | `engine/data_store/BOOK_C_DEEP_AUDIT_2026-08-19.md`; `engine/data_store/validation/book_c_risk_frontier_2026-08-20.json` | Local reproducible research; daily data and current-universe caveats apply |
| V1 sizing-units rejection | `engine/data_store/funded_100k_prereg_2026-09-03.md`; `engine/scripts/run_funded_100k_gate.py` | Formula-level rejection; no V1 smoke result is presented as evidence |
| V2 synthetic metrics, raw-quote cap overrun, blockers, source/data hashes | `engine/data_store/validation/funded_100k_v2_gate_2026-09-03.json`; dedicated V2 ledger | Non-binding, unconverted 2023–2024 diagnostic; not account-currency or venue-certified |
| Earlier funded frontier speed/safety trade-off | `engine/data_store/prop_condition_frontier.md` | Retrospective EOD Monte Carlo; not independent pass odds |
| Book H 0.50% comparison statistics | `engine/data_store/profit_frontier_2026-07-23.md` | Earlier local frontier study; different scope from V2 |
| Book R stop-overlay rejection | `engine/data_store/validation/book_r_stop_overlay_audit_2026-09-03.md` | Retrospective causal test; sensitivity not promotable |
| Book N selection-bias warning | `engine/data_store/validation/book_n_qe_10y_causal_audit_2026-08-28.md` | Current static universe; no delisted-member reconstruction |
| Risk boundary, opened-position day accounting, and local regression verification | `engine/tests/test_risk.py`; `engine/tests/test_funded_guard.py`; `engine/tests/test_funded_simulator.py`; `engine/tests/test_portfolio_funded_trace.py` | Executable local tests; not broker integration or external CI evidence |
| Current FTMO targets, daily/max-loss mechanics, four-day minimum | [FTMO Trading Objectives](https://ftmo.com/en/trading-objectives/) | Official current rule summary; must be rechecked before any purchase |
| Swing availability and holding permissions | [FTMO Swing FAQ](https://ftmo.com/faq/ftmo-swing-account-type/); [holding FAQ](https://ftmo.com/faq/do-i-have-to-close-my-positions-overnight-or-before-the-weekend/) | Official current product-fit evidence |
| Algorithmic strategy permissibility and replicability requirement | [FTMO strategy FAQ](https://ftmo.com/faq/which-instruments-can-i-trade-and-what-strategies-am-i-allowed-to-use/) | Official policy; does not certify this engine |
| Platform-specific symbols/specifications | [FTMO symbols](https://ftmo.com/en/symbols/) | Exact values must come from the selected platform/export |
| Diversified time-series momentum hypothesis | [Moskowitz, Ooi & Pedersen](https://fairmodel.econ.yale.edu/ec439/mosk.pdf) | Academic historical evidence; hypothesis, not forecast |
| Volatility-management hypothesis | [Moreira & Muir](https://www.nber.org/papers/w22208) | Academic factor evidence; implementation-specific testing required |
| Momentum crash risk | [Daniel & Moskowitz](https://www.nber.org/papers/w20439.pdf) | Academic tail-risk evidence motivating stress tests |

## Final verdict

**Book C remains the incumbent, best-documented compromise among the Apex configurations evaluated here for unconstrained paper research. It is not a funded-account strategy, and no evaluated candidate has passed the funded qualification gate.** `C_FUNDED_V2` appears lower-risk inside a provisional mixed-quote model, but that model screens poorly and is operationally/data-blocked. Keep it research-only, do not show it as funded-ready, and build the next candidate only after the exact funded product and executable data are frozen.
