"""ML dataset (meta-labelling, leakage) + models (learn signal, refuse noise)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.ml import (
    CalibratedModel,
    GBMModel,
    LinearModel,
    build_dataset,
    compute_feature_frame,
)


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


# -- dataset --------------------------------------------------------------------
def test_build_dataset_shapes_and_validity():
    pit = PointInTimeAccessor(_trend())
    ds = build_dataset(pit)
    assert len(ds) > 20
    assert ds.X.shape[0] == len(ds.y)
    assert set(np.unique(ds.y)).issubset({0, 1})
    assert np.isfinite(ds.X.to_numpy()).all()          # no NaN features
    assert set(np.unique(ds.directions)).issubset({-1, 1})


def test_feature_frame_is_point_in_time():
    df = _trend()
    cutoff = df.index[500]
    base = compute_feature_frame(df).loc[:cutoff]

    poisoned = df.copy()
    poisoned.loc[poisoned.index > cutoff, ["open", "high", "low", "close"]] *= 1000.0
    after = compute_feature_frame(poisoned).loc[:cutoff]
    pd.testing.assert_frame_equal(base, after)


def test_dataset_only_includes_trades_with_labels():
    pit = PointInTimeAccessor(_trend())
    ds = build_dataset(pit, holding_horizon=10)
    # every row had a primary trade (direction != 0) and a resolved barrier label
    assert (ds.directions != 0).all()
    assert len(ds.y) == len(ds.directions) == ds.X.shape[0]


# -- models ---------------------------------------------------------------------
def _signal_data(n=1500, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, (n, 3))
    p = 1 / (1 + np.exp(-(0.9 * x[:, 0] - 0.2)))   # y depends on feature 0
    y = (rng.uniform(0, 1, n) < p).astype(int)
    return x, y


def _noise_data(n=1500, seed=1):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 1, (n, 3)), (rng.uniform(0, 1, n) < 0.5).astype(int)


def test_linear_learns_signal():
    x, y = _signal_data()
    m = LinearModel().fit(x, y)
    hi = m.raw_proba([[2.0, 0, 0]])[0]
    lo = m.raw_proba([[-2.0, 0, 0]])[0]
    assert hi > lo


def test_gbm_learns_signal():
    x, y = _signal_data()
    m = GBMModel().fit(x, y)
    hi = m.raw_proba([[2.0, 0, 0]])[0]
    lo = m.raw_proba([[-2.0, 0, 0]])[0]
    assert hi > lo


def test_calibrated_model_refuses_noise():
    x, y = _noise_data()
    cm = CalibratedModel(LinearModel()).fit(x, y)
    p_hi = cm.predict_one([3, 0, 0]).probability
    p_lo = cm.predict_one([-3, 0, 0]).probability
    assert abs(p_hi - p_lo) < 0.15            # no fabricated edge from noise
    assert 0.3 < cm.predict_one([0, 0, 0]).probability < 0.7


def test_calibrated_model_band_and_bounds():
    x, y = _signal_data()
    cm = CalibratedModel(GBMModel()).fit(x, y)
    cp = cm.predict_one([1.0, 0.0, 0.0])
    assert 0.02 <= cp.probability <= 0.98
    assert cp.lower <= cp.probability <= cp.upper


def test_models_handle_single_class():
    x = np.random.default_rng(2).normal(0, 1, (50, 3))
    y = np.ones(50, dtype=int)
    for M in (LinearModel(), GBMModel()):
        M.fit(x, y)
        p = M.raw_proba([[0, 0, 0]])[0]
        assert 0.0 <= p <= 1.0
