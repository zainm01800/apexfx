"""Baseline strategy: calibration honesty, barrier labels, gating, leakage."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction
from apex_quant.strategies import ConformalCalibrator, RegimeGatedMomentum, triple_barrier_label


def _series(rets, start="2019-01-01", base=1.10):
    n = len(rets)
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.002
    lo = np.minimum(op, close) * 0.998
    idx = pd.bdate_range(start, periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


def _trend(n=600, drift=0.001, noise=0.003, seed=3):
    rng = np.random.default_rng(seed)
    return _series(rng.normal(drift, noise, n))


# -- conformal calibration ------------------------------------------------------
def test_calibrator_learns_monotone_relationship():
    rng = np.random.default_rng(0)
    scores = rng.uniform(0, 3, 2000)
    p = 1 / (1 + np.exp(-(scores - 1.5)))      # win prob rises with score
    outcomes = (rng.uniform(0, 1, 2000) < p).astype(int)
    cal = ConformalCalibrator(alpha=0.1).fit(scores, outcomes)
    assert cal.predict(2.5).probability > cal.predict(0.5).probability


def test_calibrator_finds_no_edge_in_noise():
    rng = np.random.default_rng(1)
    scores = rng.uniform(0, 3, 2000)
    outcomes = (rng.uniform(0, 1, 2000) < 0.5).astype(int)   # independent of score
    cal = ConformalCalibrator(alpha=0.1).fit(scores, outcomes)
    # no fabricated edge: prob stays near base rate across the score range
    assert abs(cal.predict(3.0).probability - cal.predict(0.0).probability) < 0.15
    assert 0.4 < cal.predict(1.5).probability < 0.6


def test_calibrator_handles_single_class():
    cal = ConformalCalibrator().fit(np.linspace(0, 3, 100), np.ones(100, dtype=int))
    cp = cal.predict(1.0)
    assert 0.0 <= cp.probability <= 1.0
    assert cp.band_width > 0


# -- triple-barrier labelling ---------------------------------------------------
def test_barrier_long_target_hit():
    high = np.array([100, 104, 104, 104, 104, 104, 104.0])
    low = np.array([100, 99, 99, 99, 99, 99, 99.0])
    assert triple_barrier_label(high, low, 100.0, +1, 2.0, 3.0, 0, 5) == 1


def test_barrier_long_stop_hit():
    high = np.array([100, 101, 101, 101, 101, 101, 101.0])
    low = np.array([100, 97, 97, 97, 97, 97, 97.0])
    assert triple_barrier_label(high, low, 100.0, +1, 2.0, 3.0, 0, 5) == 0


def test_barrier_short_target_hit():
    high = np.array([100, 101, 101, 101, 101, 101, 101.0])
    low = np.array([100, 96, 96, 96, 96, 96, 96.0])
    assert triple_barrier_label(high, low, 100.0, -1, 2.0, 3.0, 0, 5) == 1


def test_barrier_none_when_insufficient_forward():
    high = np.array([100, 101, 102.0])
    low = np.array([100, 99, 98.0])
    assert triple_barrier_label(high, low, 100.0, +1, 2.0, 3.0, 0, 5) is None


# -- strategy behaviour ---------------------------------------------------------
def test_flat_in_ranging_regime():
    rng = np.random.default_rng(9)
    pit = PointInTimeAccessor(_series(rng.normal(0.0, 0.0005, 400)))  # flat
    strat = RegimeGatedMomentum()
    strat.fit(pit, pit.timestamps()[:300])
    sig = strat.generate(pit, pit.end, "EUR/USD")
    assert sig.direction == Direction.FLAT


def test_long_signal_in_uptrend_after_fit():
    df = _trend(drift=0.001)
    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum()
    strat.fit(pit, df.index[:400])
    sig = strat.generate(pit, pit.end, "EUR/USD")
    assert sig.direction == Direction.LONG
    assert 0.02 <= sig.probability <= 0.98
    assert sig.instrument == "EUR/USD"


def test_explain_has_contributing_features():
    df = _trend()
    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum()
    strat.fit(pit, df.index[:400])
    info = strat.explain(pit, pit.end, "EUR/USD")
    assert "contributing_features" in info
    assert "vol_scaled_momentum" in info["contributing_features"]
    assert info["fitted"] is True


def test_signal_generation_is_point_in_time():
    df = _trend()
    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum()
    strat.fit(pit, df.index[:400])

    t0 = df.index[450]
    base = strat.generate(pit, t0, "EUR/USD")

    poisoned = df.copy()
    poisoned.loc[poisoned.index > t0, ["open", "high", "low", "close"]] *= 1000.0
    after = strat.generate(PointInTimeAccessor(poisoned), t0, "EUR/USD")
    assert base.direction == after.direction
    assert base.probability == after.probability
