"""Volatility features - realised vol over multiple windows.

Volatility clusters and is far more forecastable than returns (Engle 1982), so
it is a high-value input for both regime detection and position sizing. These are
*feature* estimators; the GARCH forward forecast lives in ``volatility/``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.features.base import Feature


class RealizedVol(Feature):
    rationale = (
        "Realised volatility (annualised std of log returns). Volatility is "
        "persistent and mean-reverting, making it predictable where returns are "
        "not. Drives regime classification and inverse-vol position sizing."
    )

    def __init__(self, window: int, annualization: int = 252):
        if window < 2:
            raise ValueError("window must be >= 2")
        self.window = window
        self.annualization = annualization

    @property
    def name(self) -> str:
        return f"rvol_{self.window}"

    @property
    def min_obs(self) -> int:
        return self.window + 1  # need window+1 closes for window returns

    def _compute(self, window: pd.DataFrame) -> float:
        logret = np.diff(np.log(window["close"].to_numpy()))[-self.window:]
        return float(np.std(logret, ddof=1) * np.sqrt(self.annualization))


class ParkinsonVol(Feature):
    rationale = (
        "Parkinson high-low range volatility - uses intrabar extremes, so it is "
        "~5x more efficient than close-to-close for a given window. Cross-checks "
        "the realised-vol estimate and is robust to bars with little net change."
    )

    def __init__(self, window: int, annualization: int = 252):
        if window < 1:
            raise ValueError("window must be >= 1")
        self.window = window
        self.annualization = annualization
        self._k = 1.0 / (4.0 * np.log(2.0))

    @property
    def name(self) -> str:
        return f"pvol_{self.window}"

    @property
    def min_obs(self) -> int:
        return self.window

    def _compute(self, window: pd.DataFrame) -> float:
        hi = window["high"].to_numpy()[-self.window:]
        lo = window["low"].to_numpy()[-self.window:]
        log_hl = np.log(hi / lo)
        daily_var = self._k * np.mean(log_hl**2)
        return float(np.sqrt(daily_var * self.annualization))
