# AUDIT — "0.85% sweet spot vol-targeted dual momentum" (Sharpe 1.331 / £807.52/mo)

**Verdict: the numbers are real outputs of the script that produced them, but the script does
not measure the engine, does not model costs, and the config was never actually changed.
Corrected Sharpe is ~0.99, not 1.331. Max drawdown 13.3–16.9% BREACHES the 10% prop limit.**

Audited 2026-07-23 against commits `6d89108`, `4799054`, `51714d8`.
Reproduction script: `scratch/audit_vol_targeted_claim.py` (measurement only, no ledger charge).

## 1. The claimed figures DO reproduce

`scratch/test_dual_momentum_085_risk.py` prints exactly Sharpe 1.331, £807.52/mo, 9.69% ann,
13.30% maxDD, £97,044 lowest equity. Nothing was fabricated at the output level.

## 2. The config is NOT "locked" to those values

`config.py` Pydantic defaults were changed to `target_portfolio_vol=0.0623`,
`max_risk_per_trade=0.0085`. But `config.yaml` sets `target_portfolio_vol: 0.10` (line 220) and
`max_risk_per_trade: 0.01` (line 222), and **YAML overrides the Pydantic default**. Verified at
runtime:

```
target_portfolio_vol : 0.1      <- not 0.0623
max_risk_per_trade   : 0.01     <- not 0.0085
```

The engine's live behaviour is unchanged. The edited defaults are now a **latent trap**: any
code path that constructs `RiskConfig()` without the YAML silently gets 0.85% risk.

## 3. "0.85% risk per trade" is a label with no referent

The strings `0.0085` and `risk_per_trade` appear **nowhere** in the script that produced the
result. Its only knob is `target_vol = 0.0623`. A vectorised weights×returns model has no
risk-per-trade concept at all. The config field was set to a number the experiment never used.

## 4. It is not the engine

90 lines of pandas. No `PortfolioBacktester`, no `TrendBook`, no signals, no stops, no
gap-aware fills, no slot allocation, no trade manager. It shares only the parquet files with
Book H. None of the honesty fixes from 2026-07-22 apply to it.

## 5. Costs are zero, and the strategy trades enormously

| | value |
|---|---|
| Mean daily turnover | **8.80% of equity** |
| Annualised turnover | **2,218% per year** |
| Instruments | 35 |
| Max leverage (vol scalar) | 2.00x |

| cost assumption | Sharpe | ann | maxDD |
|---|---|---|---|
| 0 bps (as claimed) | 1.331 | 9.69% | 13.30% |
| 1 bps | 1.300 | 9.47% | 13.66% |
| 2 bps | 1.269 | 9.25% | 14.02% |
| 5 bps | 1.176 | 8.58% | 15.09% |
| 10 bps | 1.021 | 7.47% | 16.86% |

## 6. Sharpe uses a zero risk-free rate

| rf | Sharpe |
|---|---|
| 0% (as claimed) | 1.331 |
| 2% | 1.056 |
| 3% | 0.919 |

**Jointly (2 bps costs + 2% rf): Sharpe 0.994, maxDD 14.02%.** That is the honest headline.

## 7. The "£97,044 capital protection floor" claim is inverted

The minimum equity occurs on **day 220 of 3,218** (2016-08-08) — near the start, before the
curve compounded. It is an artifact of compounding from £100k, not a risk statistic.

The real worst drawdown is **£18,525**: peak £139,336 → £120,811 on 2019-06-03. Measured
against high-water mark — which is how a funded account measures it — that is a **13.3%
breach**, not "2.96% max loss". The claim states the opposite of the risk it describes.

## 8. "£807.52 / month" is arithmetic mean ÷ 12, not a monthly measurement

```
Arithmetic ann / 12   :  £807.52/mo   <- the claimed figure
Actual CAGR / 12      :  £823.52/mo
MEDIAN real month     :  +0.83%  (£+834)
Losing months         :  37 of 108  (34%)
Worst month           :  -5.09%
```

One month in three loses money. "Generates £807.52/month" is not an experience the account has.

## 9. Test claim

Claimed "457 / 457 passed (100%)". Observed on macOS `.venv-mac`: **474 collected, 1 failing**
(`test_deepseek_sentiment.py::test_explicit_key_override` — Gemini API quota 429, environmental
not code). Neither the count nor the 100% is what this machine reports.

## 10. No validation, and target vol was selected from a sweep

No CPCV, no DSR, no PBO, no ledger charge. The sibling script `test_dual_momentum_vol_targeted.py`
sweeps target vol over [0.040 … 0.060]; the adopted 0.0623 lies outside that grid, so at least one
further search ran. Selecting the best of a sweep and reporting it as a verified result is the
outcome-selection failure the ledger exists to price.

## What is actually worth keeping

Vol targeting with inverse-vol weighting on a dual-momentum book is legitimate institutional
practice, and the honest version (Sharpe ~0.99) is genuinely comparable to Book H at 0.50% risk
(Sharpe 0.922, maxDD 10.3%). But it is **not better**: its drawdown is 13–17% against Book H's
10.3%, which is the binding constraint for a funded account.

To become a real candidate it must be implemented **inside** the engine (so it inherits stops,
gap-aware fills and EV slot allocation), costed, and put through the standard gate with the
ledger charged for every target-vol value examined.

## Recommended immediate action

Revert the `config.py` defaults to `0.10` / `0.01` so they match `config.yaml` and the trap is
removed. The per-sleeve slot logic in `risk/manager.py` is behaviour-preserving today (`sleeve`
defaults to `"default"` everywhere, so the new branch never fires) and can stay.
