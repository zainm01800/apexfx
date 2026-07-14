"""Microstructure features: NOFI, Yang-Zhang volatility, and GARCH(1,1) forecast
(including the refit-throttling optimisation). These are opt-in features, not part
of the default matrix, so they are exercised directly here."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.features.microstructure import GARCHForecast, NormalizedOFI, YangZhangVol


def _idx(n, start="2019-01-01"):
    return pd.bdate_range(start, periods=n, tz="UTC", name="timestamp")


def _ohlc_from_returns(rets, base=1.10):
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.002
    lo = np.minimum(op, close) * 0.998
    return pd.DataFrame(
        {"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0},
        index=_idx(len(rets)),
    )


# -- NOFI ----------------------------------------------------------------------
def test_nofi_name_and_min_obs():
    f = NormalizedOFI(20)
    assert f.name == "nofi_20"
    assert f.min_obs == 20


def test_nofi_sign_and_range():
    n = 25
    base = np.linspace(1.00, 1.05, n)
    up = pd.DataFrame({"open": base, "high": base * 1.01, "low": base,
                       "close": base * 1.01, "volume": 1.0}, index=_idx(n))
    down = pd.DataFrame({"open": base * 1.01, "high": base * 1.01, "low": base,
                         "close": base, "volume": 1.0}, index=_idx(n))
    doji = pd.DataFrame({"open": base, "high": base * 1.005, "low": base * 0.995,
                         "close": base, "volume": 1.0}, index=_idx(n))
    f = NormalizedOFI(20)
    assert f.compute(PointInTimeAccessor(up), up.index[-1]) == pytest.approx(1.0, abs=1e-6)
    assert f.compute(PointInTimeAccessor(down), down.index[-1]) == pytest.approx(-1.0, abs=1e-6)
    assert f.compute(PointInTimeAccessor(doji), doji.index[-1]) == pytest.approx(0.0, abs=1e-6)


def test_nofi_rejects_bad_window():
    with pytest.raises(ValueError):
        NormalizedOFI(0)


def test_nofi_nan_on_short_history():
    df = _ohlc_from_returns(np.random.default_rng(0).normal(0, 0.005, 10))
    f = NormalizedOFI(20)  # needs 20 bars, only 10 available
    assert np.isnan(f.compute(PointInTimeAccessor(df), df.index[-1]))


# -- Yang-Zhang volatility -----------------------------------------------------
def test_yz_name_min_obs_and_validation():
    f = YangZhangVol(21, 252)
    assert f.name == "yzvol_21"
    assert f.min_obs == 22
    with pytest.raises(ValueError):
        YangZhangVol(1)


def test_yz_positive_and_orders_by_volatility():
    calm = _ohlc_from_returns(np.random.default_rng(1).normal(0.0, 0.002, 120))
    wild = _ohlc_from_returns(np.random.default_rng(1).normal(0.0, 0.02, 120))
    f = YangZhangVol(21, 252)
    vc = f.compute(PointInTimeAccessor(calm), calm.index[-1])
    vw = f.compute(PointInTimeAccessor(wild), wild.index[-1])
    assert np.isfinite(vc) and np.isfinite(vw)
    assert vw > vc > 0  # a more volatile tape reads as higher YZ vol


# -- GARCH forecast + refit throttling -----------------------------------------
def test_garch_rejects_bad_params():
    with pytest.raises(ValueError):
        GARCHForecast(window=40)          # < 50
    with pytest.raises(ValueError):
        GARCHForecast(window=60, refit_every=0)


def test_garch_positive_forecast():
    df = _ohlc_from_returns(np.random.default_rng(2).normal(0.0002, 0.01, 120))
    f = GARCHForecast(window=60, annualization=252)
    v = f.compute(PointInTimeAccessor(df), df.index[-1])
    assert np.isfinite(v) and v > 0
    assert f._n_fits == 1


def test_garch_refit_throttle_reduces_fits_and_tracks():
    df = _ohlc_from_returns(np.random.default_rng(3).normal(0.0003, 0.01, 140))
    pit = PointInTimeAccessor(df)
    stamps = df.index[-12:]

    fresh = GARCHForecast(window=60, annualization=252, refit_every=1)
    throttled = GARCHForecast(window=60, annualization=252, refit_every=5)
    fresh_vals = [fresh.compute(pit, t) for t in stamps]
    thr_vals = [throttled.compute(pit, t) for t in stamps]

    # refit_every=1 fits every bar; throttling fits only ~once per 5 bars.
    assert fresh._n_fits == len(stamps)
    assert throttled._n_fits < fresh._n_fits
    assert throttled._n_fits <= len(stamps) // 5 + 2

    # Between refits the analytic roll-forward closely tracks a fresh fit.
    for a, b in zip(fresh_vals, thr_vals):
        assert np.isfinite(a) and a > 0
        assert np.isfinite(b) and b > 0
        assert 0.5 < b / a < 2.0


def test_garch_constant_series_degrades_gracefully():
    # A flat tape gives near-zero returns; fitting may fail -> realised-vol fallback,
    # which must still return a finite, non-negative number (never NaN/raise).
    n = 120
    base = np.full(n, 1.10)
    df = pd.DataFrame({"open": base, "high": base * 1.0001, "low": base * 0.9999,
                       "close": base, "volume": 1.0}, index=_idx(n))
    f = GARCHForecast(window=60)
    v = f.compute(PointInTimeAccessor(df), df.index[-1])
    assert np.isfinite(v) and v >= 0
