# PRE-REGISTRATION - Book C deep audit and improvement campaign (2026-08-19)

Status: written before any Book C candidate in this campaign was run. This campaign is
conditional follow-up research: the 252-only versions of several mechanisms were examined in
earlier gates, but none of their interactions with the promoted [63,126,252] Book C was known
when this document was written.

## Objective

Test whether the current Book C champion can be improved without hiding lower return behind a
better ratio, or hiding higher return behind more tail risk. The production/paper Book C state
is not modified by this study.

## Frozen control

- Universe and insertion order: Book H gold, 39 instruments after MATIC/USD drops for missing
  data (`EQUITY_CORE + SGLD.L + configured crypto + FX_MAJORS_7`).
- Signal: multi-horizon trend ensemble `[63, 126, 252]`.
- Risk: maximum risk/trade 1.00%, portfolio open-risk cap 6.50%, no per-position notional cap.
- Exit: certified capped 1.5R TradeManager.
- Decisions at close, fills at next open, gap-aware stops, configured costs.
- Iteration window: bars strictly before 2025-01-01.
- Post-2025 data is a verification sample, not a blind holdout: it has already been viewed by
  earlier repository work and must never be described as pristine OOS evidence.

The legacy 252-day reporting anchor must reproduce Sharpe 0.9237660179784476, max drawdown
0.15921488068143141, 1,654 trades and final equity 316,181.0796516242. The campaign also
reports calendar-consistent 365-day metrics because the union timeline contains crypto
weekends. This reporting correction changes no trades or equity values.

## Stage 1: isolated candidates

Exactly four single-change candidates plus the control:

1. `book_c_control`: frozen control.
2. `book_c_runner`: same book; 50% exits at +1R, the remainder rides the existing 2x ATR
   chandelier with no fixed 1.5R cap and no second partial.
3. `book_c_notional15`: same book; entry notional capped at 15% of account equity.
4. `book_c_portcap045`: same book; aggregate open-risk cap reduced from 6.50% to 4.50%.
5. `book_c_risk075`: same book; maximum per-trade risk reduced from 1.00% to 0.75%.

No parameter sweep is allowed. These values come from earlier pre-registered 252-only studies
or the current global risk policy, not from Book C candidate results.

## Stage 1 decision rule

A candidate earns combination eligibility only if all conditions hold on the pre-2025 window:

- calendar-consistent Sharpe is at least the control's;
- profit factor is at least the control's;
- maximum drawdown and worst daily return are no worse than the control's;
- average monthly P&L is at least 95% of the control's;
- paired circular 21-bar block bootstrap has one-sided p < 0.10 for Sharpe superiority;
- CPCV median OOS Sharpe is positive, at least 12/15 paths are positive, and the candidate
  beats the control on at least 8/15 paired paths;
- DSR > 0.95 using the full shared trial-ledger count.

PBO is calculated and disclosed but is not a binding A/B statistic for these near-collinear
return streams. The paired bootstrap and head-to-head CPCV tests are binding.

## Stage 2 rule

Only independently eligible Stage-1 mechanisms may be combined. If two or more qualify, the
combination consists of all eligible mechanisms; if exactly one qualifies, it is already the
challenger; if none qualify, there is no Stage 2 and the control remains champion. A combined
candidate must satisfy every Stage-1 condition again and must not underperform the control on
post-2025 Sharpe, max drawdown, or total P&L. Interaction failure rejects the combination.

## Mandatory robustness reporting

- Metrics by calendar year and for 2025 onward.
- 2x configured transaction-cost stress.
- Current-universe bias stress: remove the 12 hand-selected single stocks and report the
  ETF/gold/crypto/FX remainder. This is a validity diagnostic, not a candidate.
- Direction, asset-class and instrument P&L attribution.
- Worst day, worst month, maximum drawdown and gross leverage.
- FTMO 1-Step 3% daily-rule and 10% trailing-loss lower-bound diagnostics from daily closes,
  explicitly labelled as unable to observe intraday breaches.
- Trial ledger charged before execution; deterministic rerun required for any promoted result.

## Known limits

- The equity universe is not point-in-time and excludes delisted/failed stocks. No result from
  this cache can eliminate survivorship/selection bias; the ETF-only stress only measures
  dependence on the hand-selected stocks.
- Cached Yahoo bars do not carry an account-currency conversion series. Cash P&L is therefore
  reported as account-currency units, not pounds.
- Daily OHLC cannot prove intraday prop-rule compliance.
- The 2025+ segment has already been inspected and is verification evidence only.
