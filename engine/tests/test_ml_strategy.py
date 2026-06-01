"""ML meta-strategy: Strategy-interface compliance, gating, leakage, backtest."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.backtest import Backtester
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies import MLStrategy


def _series(rets, start="2016-01-01", base=1.10):
    n = len(rets)
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range(start, periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


def _trend(n=900, drift=0.0012, noise=0.004, seed=3):
    rng = np.random.default_rng(seed)
    return _series(rng.normal(drift, noise, n))


def test_ml_strategy_fits_and_generates_signal():
    df = _trend()
    pit = PointInTimeAccessor(df)
    strat = MLStrategy(model="gbm")
    strat.fit(pit, df.index[:600])
    assert strat.is_fitted()
    sig = strat.generate(pit, pit.end, "EUR/USD")
    assert isinstance(sig, Signal)
    assert sig.direction in (Direction.LONG, Direction.SHORT, Direction.FLAT)
    if sig.direction != Direction.FLAT:
        assert 0.02 <= sig.probability <= 0.98


def test_ml_linear_variant_also_works():
    df = _trend()
    pit = PointInTimeAccessor(df)
    strat = MLStrategy(model="linear")
    strat.fit(pit, df.index[:600])
    sig = strat.generate(pit, pit.end, "EUR/USD")
    assert sig.reward_risk == strat.reward_risk
    assert strat.name == "ml_linear"


def test_unfitted_is_flat():
    df = _trend()
    pit = PointInTimeAccessor(df)
    sig = MLStrategy().generate(pit, pit.end, "EUR/USD")
    assert sig.direction == Direction.FLAT


def test_ml_signal_is_point_in_time():
    df = _trend()
    pit = PointInTimeAccessor(df)
    strat = MLStrategy(model="gbm")
    strat.fit(pit, df.index[:600])

    t0 = df.index[700]
    base = strat.generate(pit, t0, "EUR/USD")

    poisoned = df.copy()
    poisoned.loc[poisoned.index > t0, ["open", "high", "low", "close"]] *= 1000.0
    after = strat.generate(PointInTimeAccessor(poisoned), t0, "EUR/USD")
    assert base.direction == after.direction
    assert base.probability == after.probability   # cache keyed by data identity


def test_ml_strategy_runs_in_backtester():
    df = _trend()
    pit = PointInTimeAccessor(df)
    strat = MLStrategy(model="gbm")
    strat.fit(pit, df.index[:500])
    res = Backtester().run(pit, strat, "EUR/USD", start=df.index[500], warmup=0)
    assert "sharpe" in res.metrics
    assert res.metrics["final_equity"] > 0


def test_explain_keys():
    df = _trend()
    pit = PointInTimeAccessor(df)
    strat = MLStrategy(model="gbm")
    strat.fit(pit, df.index[:600])
    info = strat.explain(pit, pit.end, "EUR/USD")
    assert info["model"] == "gbm"
    assert "contributing_features" in info and "fitted" in info
