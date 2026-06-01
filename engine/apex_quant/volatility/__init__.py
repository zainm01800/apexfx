"""Volatility model: realised/EWMA estimators + GARCH forward forecast.

``forecast_volatility`` is the single entry point used by regime detection,
position sizing and the API. It is point-in-time: it reads only ``as_of(t)``.
"""

from __future__ import annotations

import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.volatility.garch import GarchEstimator
from apex_quant.volatility.realized import (
    VolForecast,
    ewma_vol,
    log_returns,
    realized_vol,
)

__all__ = [
    "VolForecast",
    "realized_vol",
    "ewma_vol",
    "log_returns",
    "GarchEstimator",
    "forecast_volatility",
]


def forecast_volatility(
    pit: PointInTimeAccessor,
    t: pd.Timestamp | str,
    *,
    method: str = "ewma",
    lookback: int = 504,
) -> VolForecast:
    """Forward volatility known at ``t``.

    method: ``"ewma"`` (default, fast, responsive), ``"realized"`` (equal-weight),
    or ``"garch"`` (GARCH(1,1) with EWMA fallback). ``lookback`` caps how much
    history feeds the model (2y of daily bars by default)."""
    window = pit.as_of(t).iloc[-lookback:]
    if method == "garch":
        return GarchEstimator().forecast(window)
    if method == "realized":
        return realized_vol(window)
    if method == "ewma":
        return ewma_vol(window)
    raise ValueError(f"unknown volatility method '{method}'")
