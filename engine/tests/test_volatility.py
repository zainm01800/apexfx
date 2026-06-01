"""Volatility model: estimator correctness, GARCH fit + fallback, leakage."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.volatility import (
    GarchEstimator,
    ewma_vol,
    forecast_volatility,
    realized_vol,
)


def _garch_like(n=800, seed=11, omega=1e-6, alpha=0.08, beta=0.90, base=1.10):
    """A series with volatility clustering so GARCH has something to fit."""
    rng = np.random.default_rng(seed)
    eps = np.zeros(n)
    var = np.full(n, omega / max(1e-9, (1 - alpha - beta)))
    for i in range(1, n):
        var[i] = omega + alpha * eps[i - 1] ** 2 + beta * var[i - 1]
        eps[i] = rng.normal(0, np.sqrt(var[i]))
    close = base * np.exp(np.cumsum(eps))
    idx = pd.bdate_range("2019-01-01", periods=n, tz="UTC", name="timestamp")
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.001
    lo = np.minimum(op, close) * 0.999
    return pd.DataFrame(
        {"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx
    )


# -- realised / ewma correctness ----------------------------------------------
def test_realized_vol_matches_manual(clean_daily):
    vf = realized_vol(clean_daily, window=63, annualization=252)
    r = np.diff(np.log(clean_daily["close"].to_numpy()))[-63:]
    expected = np.std(r, ddof=1) * np.sqrt(252)
    assert vf.annualized == np.float64(expected) or abs(vf.annualized - expected) < 1e-9
    assert vf.per_bar > 0


def test_ewma_responds_to_recent_shock(clean_daily):
    calm = clean_daily.copy()
    shocked = clean_daily.copy()
    # inject a recent volatility burst
    shocked.iloc[-5:, shocked.columns.get_loc("close")] *= [1.05, 0.95, 1.06, 0.94, 1.05]
    assert ewma_vol(shocked).annualized > ewma_vol(calm).annualized


def test_higher_vol_series_higher_estimate(make_ohlcv):
    lo = realized_vol(make_ohlcv(vol=0.003, seed=2)).annualized
    hi = realized_vol(make_ohlcv(vol=0.03, seed=2)).annualized
    assert hi > lo


# -- GARCH --------------------------------------------------------------------
def test_garch_fits_and_forecasts():
    df = _garch_like()
    vf = GarchEstimator().forecast(df)
    assert vf.converged
    assert vf.method.startswith("garch")
    assert np.isfinite(vf.annualized) and vf.annualized > 0
    assert vf.horizon == GarchEstimator().cfg.horizon


def test_garch_falls_back_when_too_short(make_ohlcv):
    short = make_ohlcv(n=50)  # < garch min_obs
    vf = GarchEstimator().forecast(short)
    assert not vf.converged
    assert "ewma_fallback" in vf.method
    assert np.isfinite(vf.annualized)


def test_garch_in_reasonable_range():
    """GARCH annualised vol should be in the same ballpark as realised vol."""
    df = _garch_like()
    g = GarchEstimator().forecast(df).annualized
    rv = realized_vol(df, window=252).annualized
    assert 0.25 * rv < g < 4.0 * rv


# -- leakage ------------------------------------------------------------------
def test_forecast_volatility_is_point_in_time(clean_daily):
    t0 = clean_daily.index[200]
    base = forecast_volatility(PointInTimeAccessor(clean_daily), t0, method="ewma").annualized

    poisoned = clean_daily.copy()
    poisoned.loc[poisoned.index > t0, "close"] *= 1000.0
    after = forecast_volatility(PointInTimeAccessor(poisoned), t0, method="ewma").annualized
    assert after == base


def test_forecast_volatility_methods(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    t = pit.end
    for m in ("ewma", "realized", "garch"):
        vf = forecast_volatility(pit, t, method=m)
        assert np.isfinite(vf.annualized) and vf.annualized > 0
