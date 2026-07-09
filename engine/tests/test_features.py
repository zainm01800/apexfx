"""Feature layer: economic-direction sanity, NaN discipline, and leakage."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.features import (
    DistanceFromMA,
    Momentum,
    ParkinsonVol,
    RealizedVol,
    TrendSlope,
    VolScaledMomentum,
    compute_feature_matrix,
    default_features,
    feature_catalog,
)
from apex_quant.features.carry import Carry
from apex_quant.features.cot import CotPositioning


def _trending(n=300, drift=0.001, start="2022-01-03", base=1.10):
    idx = pd.bdate_range(start=start, periods=n, tz="UTC", name="timestamp")
    close = base * np.exp(np.cumsum(np.full(n, drift)))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.001
    lo = np.minimum(op, close) * 0.999
    return pd.DataFrame(
        {"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx
    )


# -- economic direction ---------------------------------------------------------
def test_momentum_positive_on_uptrend():
    df = _trending(drift=0.001)
    pit = PointInTimeAccessor(df)
    assert Momentum(63).compute(pit, df.index[-1]) > 0


def test_momentum_negative_on_downtrend():
    df = _trending(drift=-0.001)
    pit = PointInTimeAccessor(df)
    assert Momentum(63).compute(pit, df.index[-1]) < 0


def test_trend_slope_sign_tracks_drift():
    pit_up = PointInTimeAccessor(_trending(drift=0.001))
    pit_dn = PointInTimeAccessor(_trending(drift=-0.001))
    f = TrendSlope(100, 21)
    assert f.compute(pit_up, pit_up.end) > 0
    assert f.compute(pit_dn, pit_dn.end) < 0


def test_realized_vol_higher_for_noisier_series(make_ohlcv):
    calm = PointInTimeAccessor(make_ohlcv(vol=0.002, seed=1))
    wild = PointInTimeAccessor(make_ohlcv(vol=0.02, seed=1))
    f = RealizedVol(21)
    assert f.compute(wild, wild.end) > f.compute(calm, calm.end)


def test_parkinson_vol_positive(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    assert ParkinsonVol(21).compute(pit, pit.end) > 0


def test_vol_scaled_momentum_finite(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    assert np.isfinite(VolScaledMomentum(63).compute(pit, pit.end))


# -- NaN discipline -------------------------------------------------------------
def test_feature_returns_nan_when_history_short(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    early = clean_daily.index[5]
    assert np.isnan(Momentum(63).compute(pit, early))
    assert np.isnan(DistanceFromMA(200).compute(pit, early))


def test_rationale_is_enforced():
    """Every concrete feature must carry a non-empty economic rationale."""
    for f in default_features():
        assert isinstance(f.rationale, str) and f.rationale.strip()


def test_defining_a_feature_without_rationale_fails():
    from apex_quant.features.base import Feature

    with pytest.raises(TypeError):

        class Bad(Feature):  # noqa: D401 - intentionally missing rationale
            @property
            def name(self):
                return "bad"

            @property
            def min_obs(self):
                return 1

            def _compute(self, window):
                return 0.0


# -- leakage: the whole feature matrix is point-in-time --------------------------
def test_feature_matrix_has_no_lookahead(clean_daily):
    feats = default_features()
    stamps = clean_daily.index[210:260]

    clean_mat = compute_feature_matrix(PointInTimeAccessor(clean_daily), stamps, feats)

    poisoned = clean_daily.copy()
    cut = stamps[-1]
    poisoned.loc[poisoned.index > cut, ["open", "high", "low", "close"]] *= 1000.0
    poison_mat = compute_feature_matrix(PointInTimeAccessor(poisoned), stamps, feats)

    pd.testing.assert_frame_equal(clean_mat, poison_mat)


def test_feature_matrix_shape_and_names(clean_daily):
    feats = default_features()
    stamps = clean_daily.index[-30:]
    mat = compute_feature_matrix(PointInTimeAccessor(clean_daily), stamps, feats)
    assert list(mat.columns) == [f.name for f in feats]
    assert len(mat) == 30
    assert mat.iloc[-1].notna().all()  # full history at the end -> all computable


# -- pluggable carry / COT -------------------------------------------------------
def test_carry_disabled_returns_nan(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    assert not Carry("EUR/USD").available
    assert np.isnan(Carry("EUR/USD").compute(pit, pit.end))


def test_carry_with_provider(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    f = Carry("EUR/USD", rate_provider=lambda inst, t: (0.02, 0.05))  # EUR 2%, USD 5%
    assert f.available
    assert f.compute(pit, pit.end) == pytest.approx(-0.03)


def test_cot_disabled_returns_nan(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    assert np.isnan(CotPositioning("EUR/USD").compute(pit, pit.end))


def test_catalog_lists_rationales():
    cat = feature_catalog()
    assert len(cat) >= 5
    assert all(c["rationale"].strip() for c in cat)
