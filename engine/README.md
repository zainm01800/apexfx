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

## Running a backtest / reading the validation report

> Built after the foundation checkpoint (backtest → validation → API → frontend).
> This section will document how to run an end-to-end backtest and how to
> interpret the CPCV paths, Deflated Sharpe Ratio, and Probability of Backtest
> Overfitting. **A strategy only "passes" with positive DSR and low PBO across
> CPCV paths — most candidate strategies should fail here. That is the system
> working.**

## Status

Phase 1 — foundation (validation + risk + regime). See `config.yaml` `version`.
