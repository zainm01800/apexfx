"""Multi-horizon trend ensemble (2026-07-27; prereg
engine/data_store/trend_ensemble_prereg.md).

The ``momentum_lookbacks`` flag defaults to certified behaviour (``None`` ->
``[momentum_lookback]`` -> the single-252 score, byte-identical). When several
horizons are given, the vol-scaled momentum score is their equal-weight mean,
NaN unless EVERY leg is finite — everything downstream (regime agreement gate,
probability map, sizing, exits) is the unchanged certified machinery.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.features.momentum import VolScaledMomentum
from apex_quant.strategies.baseline import RegimeGatedMomentum


def _panel(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    close = 100 * np.exp(np.cumsum(0.0004 + 0.01 * rng.randn(n)))
    return pd.DataFrame(
        {"open": close, "high": close * 1.005, "low": close * 0.995,
         "close": close, "volume": 1e6},
        index=idx,
    )


# -- the flag defaults to certified behaviour ------------------------------------
def test_default_is_certified_single_lookback():
    s = RegimeGatedMomentum(momentum_lookback=252, vol_window=63, instrument="AAPL")
    assert s.momentum_lookbacks == [252]
    assert len(s._mom_legs) == 1
    assert s._mom.lookback == 252


def test_explicit_single_lookback_matches_certified_score_exactly():
    pit = PointInTimeAccessor(_panel())
    t = pit.as_of(pit.end).index[-1]
    certified = RegimeGatedMomentum(momentum_lookback=252, vol_window=63, instrument="AAPL")
    flagged = RegimeGatedMomentum(momentum_lookback=252, vol_window=63,
                                  instrument="AAPL", momentum_lookbacks=[252])
    ev_c, ev_f = certified._evaluate(pit, t), flagged._evaluate(pit, t)
    assert ev_c["score"] == ev_f["score"]          # identical, not approximate
    assert ev_c["direction"] == ev_f["direction"]


# -- the blend ------------------------------------------------------------------
def test_blend_is_equal_weight_mean_of_vol_scaled_legs():
    pit = PointInTimeAccessor(_panel())
    t = pit.as_of(pit.end).index[-1]
    s = RegimeGatedMomentum(momentum_lookback=252, vol_window=63, instrument="AAPL",
                            momentum_lookbacks=[63, 126, 252])
    legs = [VolScaledMomentum(lb, 63).compute(pit, t) for lb in (63, 126, 252)]
    assert s._evaluate(pit, t)["score"] == pytest.approx(float(np.mean(legs)), rel=1e-12)


def test_barbell_differs_from_full_blend_and_from_control():
    pit = PointInTimeAccessor(_panel())
    t = pit.as_of(pit.end).index[-1]
    mk = lambda lbs: RegimeGatedMomentum(momentum_lookback=252, vol_window=63,
                                         instrument="AAPL",
                                         momentum_lookbacks=lbs)._evaluate(pit, t)["score"]
    ctrl, blend, barbell = mk([252]), mk([63, 126, 252]), mk([63, 252])
    assert barbell != ctrl and blend != ctrl and barbell != blend


def test_blend_is_nan_unless_every_leg_is_finite():
    pit = PointInTimeAccessor(_panel())
    idx = pit.as_of(pit.end).index
    t_early = idx[100]                       # 63-leg finite, 126/252 legs not
    blend = RegimeGatedMomentum(momentum_lookback=252, vol_window=63, instrument="AAPL",
                                momentum_lookbacks=[63, 126, 252])
    fast = RegimeGatedMomentum(momentum_lookback=63, vol_window=63, instrument="AAPL",
                               momentum_lookbacks=[63])
    assert not np.isfinite(blend._evaluate(pit, t_early)["score"])
    assert np.isfinite(fast._evaluate(pit, t_early)["score"])


def test_blend_min_obs_is_the_slowest_leg():
    s = RegimeGatedMomentum(momentum_lookback=252, vol_window=63, instrument="AAPL",
                            momentum_lookbacks=[63, 126, 252])
    assert s._mom_min_obs == max(252, 63) + 1
    fast = RegimeGatedMomentum(momentum_lookback=63, vol_window=63, instrument="AAPL")
    assert fast._mom_min_obs == fast._mom.min_obs


def test_invalid_lookbacks_rejected():
    with pytest.raises(ValueError):
        RegimeGatedMomentum(momentum_lookback=252, instrument="AAPL",
                            momentum_lookbacks=[63, 0])


# -- the fit() cache path agrees with the compute path ---------------------------
def test_fit_cache_matches_point_in_time_compute():
    df = _panel()
    pit = PointInTimeAccessor(df)
    s = RegimeGatedMomentum(momentum_lookback=252, vol_window=63, instrument="AAPL",
                            momentum_lookbacks=[63, 126, 252])
    s.fit(pit, list(df.index[300:]))
    t = df.index[-1]
    assert hasattr(s, "_score_cache") and t in s._score_cache
    cached = s._score_cache[t]
    legs = [VolScaledMomentum(lb, 63).compute(pit, t) for lb in (63, 126, 252)]
    assert cached == pytest.approx(float(np.mean(legs)), rel=1e-12)
    # _evaluate must prefer the cache and return the same value
    assert s._evaluate(pit, t)["score"] == cached


def test_fit_cache_single_leg_matches_certified_series():
    df = _panel()
    pit = PointInTimeAccessor(df)
    s = RegimeGatedMomentum(momentum_lookback=252, vol_window=63, instrument="AAPL",
                            momentum_lookbacks=[252])
    s.fit(pit, list(df.index[300:]))
    t = df.index[-1]
    close = df["close"]
    vol = np.log(close).diff().rolling(63).std(ddof=1)
    expected = float(((close / close.shift(252) - 1.0) / vol).iloc[-1])
    assert s._score_cache[t] == pytest.approx(expected, rel=1e-12)
