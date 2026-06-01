# APEX Quant Engine

A forex-first **quantitative signal & risk engine** that runs as a separate
Python service alongside the existing APEX FX JavaScript app. It does **not**
try to predict prices like an oracle. Its job is to:

1. **Harvest modest, evidence-based edges** with discipline.
2. **Enforce rigorous risk management** — the risk layer has veto over every signal.
3. **Adapt to market regime.**
4. **Ruthlessly validate everything** so we never trade on a fake edge.

## Governing philosophy (encoded in the design, not just the comments)

- **The edge is risk management, regime awareness, and validation — not prediction.**
  The signal-to-noise ratio in returns is extremely low; every single signal is weak.
- Outputs are **probabilistic with explicit uncertainty**, never certainties.
- The **risk layer is supreme** — a model suggestion is only ever an *input* to
  sizing, never an order.
- **Overfitting is the default failure mode.** Every design choice fights it; we
  assume our own backtests are optimistic until validation proves otherwise.
- **Fractional Kelly, never full Kelly.** Default fraction 0.25.
- **No look-ahead bias.** A point-in-time accessor gates every feature; leakage
  tests fail the build if future data sneaks in.
- **Full reproducibility** — fixed seeds, versioned config, deterministic pipelines.

## Architecture

```
engine/
  config.yaml              versioned params (NO magic numbers in code)
  apex_quant/
    config.py              typed config loader + reproducibility seeds
    data/                  ① adapter · Yahoo source · parquet store
                              point-in-time accessor (leakage guard) · quality checks
    features/              ② momentum · realized-vol · trend · carry/COT (pluggable)
    volatility/            ③ realized estimators + GARCH (arch) → forward vol
    regime/                ④ HMM (hmmlearn) + transparent rule-based baseline
    risk/                  ⑤ vol-targeting · fractional-Kelly · caps · ATR stops
                              drawdown breaker · authoritative permit() — has veto
    backtest/              event-driven, realistic costs   (after checkpoint)
    validation/            CPCV · Deflated Sharpe · PBO     (after checkpoint)
    strategies/            baseline regime-gated momentum   (after checkpoint)
    api/                   FastAPI — /regime /signal /risk /validation (after checkpoint)
  tests/                   leakage suite + per-module unit tests
```

The frontend consumes the API to display current regime, the latest signal with
its probability and uncertainty band, the risk module's recommended size, and
validation-health metrics. **No quant computation lives in the browser.**

## Setup (local-first)

```bash
cd engine
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt # macOS/Linux
```

`requirements.lock.txt` pins exact installed versions for reproducibility.

## Run the tests

```bash
.venv\Scripts\python.exe -m pytest -q
```

The leakage suite (`tests/test_point_in_time.py`) deliberately injects future
data and asserts the point-in-time accessor blocks it. If those tests ever pass
with leakage present, the build is broken.

## Configuration

Every tunable lives in `config.yaml`. Override per-run with `APEX_`-prefixed env
vars (nested keys use `__`), e.g. `APEX_RISK__KELLY_FRACTION=0.1`. Bump `version`
whenever a value changes — backtests record the version they ran under.

## Running a backtest

```python
from apex_quant.data import get_adapter, clean, PointInTimeAccessor
from apex_quant.strategies import RegimeGatedMomentum
from apex_quant.backtest import Backtester

df = clean(get_adapter("yahoo").get_history("EUR/USD", "2015-01-01", "2024-12-31"))
pit = PointInTimeAccessor(df)
split = df.index[len(df)//2]
strat = RegimeGatedMomentum()
strat.fit(pit, df.index[df.index <= split])           # calibrate on the first half only
res = Backtester().run(pit, strat, "EUR/USD", start=split, warmup=0)
print(res.summary())                                   # ret / Sharpe / maxDD / trades
```

## Reading the validation report

```bash
.venv\Scripts\python.exe scripts/run_validation.py EUR/USD GBP/USD
```

The report has three gates; a strategy passes only if **all three** agree:

| Metric | Meaning | Pass |
|--------|---------|------|
| **Deflated Sharpe** | Sharpe corrected for the number of configs tried, skew & kurtosis | `> 0.95` |
| **PBO** | Probability of Backtest Overfitting (in-sample-best underperforms OOS) | `< 0.50` |
| **CPCV OOS** | Out-of-sample Sharpe distribution across combinatorial purged paths | median `> 0`, majority of paths positive |

**Most candidate strategies should FAIL here. That is the system working, not a
bug.** The bundled baseline (regime-gated momentum) is correctly *rejected* on FX
majors — a weak edge dies in validation rather than in an account.

## API + frontend panel

```bash
# 1. start the engine (local)
.venv\Scripts\python.exe -m uvicorn apex_quant.api.app:app --port 8000
# 2. serve the frontend (repo root) and open the "Quant" tab
python -m http.server 3001 --directory public      # http://localhost:3001/quant.html
```

The `Quant` panel in the APEX app reads `/regime` `/signal` `/risk` `/validation`
and shows the regime, the calibrated signal with its uncertainty band, the
risk-sized position, and the validation verdict. If the engine isn't running the
panel shows an offline notice; the rest of APEX is unaffected.

## Phase 2 — ML signal expansion

Same risk layer, same validation gauntlet — now with learned signals:

- **Meta-labelling** (`ml/`): a transparent primary rule (regime-gated momentum)
  picks direction; a model predicts **P(this trade wins)** from triple-barrier labels.
- **Models**: a regularised **LightGBM** ensemble + a **linear baseline** (the
  honesty check), both **conformal-calibrated** so P(win) is trustworthy.
- **Sentiment-as-a-filter** (`sentiment/`): wired to the existing Groq/Finnhub news
  pipeline. **Veto/damp only — never initiates or enlarges a trade.** Off by default.
- Select the strategy in the panel (Baseline / ML-GBM / ML-Linear) or via
  `?strategy=` on `/signal` and `/risk`. Each is validated independently:
  ```bash
  .venv\Scripts\python.exe scripts/run_validation.py ml_gbm EUR/USD
  ```
  On FX majors the ML strategies are **also rejected** (DSR < 0.95, high PBO) — more
  model complexity does not conjure an edge that isn't in the data. Working as intended.

## Status

Phase 1 + Phase 2 COMPLETE — data, features, volatility, regime, risk, baseline +
ML strategies (LightGBM/linear, meta-labelled, conformal-calibrated), sentiment
filter, backtest, CPCV/DSR/PBO validation, API, and frontend panel. 140 tests.
Phase 3 (retrieval-grounded LLM hypothesis generation / bull-bear-risk debate that
produces *ideas to validate*, never live orders) is gated on your go-ahead.
