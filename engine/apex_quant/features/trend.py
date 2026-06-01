"""Trend / structure features."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.features.base import Feature


class TrendSlope(Feature):
    rationale = (
        "Normalised slope of a long moving average - captures the persistent "
        "directional component of price. Expressed as per-bar drift relative to "
        "price so it is dimensionless and comparable across instruments. Used by "
        "the rule-based regime classifier to label trending vs ranging states."
    )

    def __init__(self, ma_window: int, slope_window: int):
        if ma_window < 2 or slope_window < 1:
            raise ValueError("ma_window>=2 and slope_window>=1 required")
        self.ma_window = ma_window
        self.slope_window = slope_window

    @property
    def name(self) -> str:
        return f"trend_slope_{self.ma_window}"

    @property
    def min_obs(self) -> int:
        return self.ma_window + self.slope_window

    def _compute(self, window: pd.DataFrame) -> float:
        close = window["close"]
        ma = close.rolling(self.ma_window).mean().to_numpy()
        ma_now, ma_prev = ma[-1], ma[-1 - self.slope_window]
        price = close.to_numpy()[-1]
        if not (np.isfinite(ma_now) and np.isfinite(ma_prev)) or price <= 0:
            return np.nan
        return (ma_now - ma_prev) / (self.slope_window * price)


class DistanceFromMA(Feature):
    rationale = (
        "Price distance from a long moving average, scaled by realised volatility "
        "(a z-score-like 'stretch'). Large positive/negative stretch flags "
        "overextension; near zero flags consolidation. Complements slope: slope is "
        "direction, distance is extension."
    )

    def __init__(self, ma_window: int, vol_window: int = 21):
        if ma_window < 2 or vol_window < 2:
            raise ValueError("ma_window>=2 and vol_window>=2 required")
        self.ma_window = ma_window
        self.vol_window = vol_window

    @property
    def name(self) -> str:
        return f"dist_ma_{self.ma_window}"

    @property
    def min_obs(self) -> int:
        return self.ma_window + 1

    def _compute(self, window: pd.DataFrame) -> float:
        close = window["close"].to_numpy()
        ma = np.mean(close[-self.ma_window:])
        logret = np.diff(np.log(close))[-self.vol_window:]
        sigma = np.std(logret, ddof=1)
        if ma <= 0 or sigma <= 0:
            return np.nan
        # distance in % terms, normalised by per-bar vol
        return (close[-1] / ma - 1.0) / sigma
