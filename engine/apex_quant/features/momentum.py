"""Time-series momentum - the strongest evidence-based FX/futures factor."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.features.base import Feature


class Momentum(Feature):
    rationale = (
        "Time-series momentum: an asset's own past return positively predicts its "
        "near-future return across currencies, commodities and equity indices "
        "(Moskowitz, Ooi & Pedersen 2012). It is the most robust single forex edge, "
        "but still weak in isolation - used as one input to a regime-gated, "
        "risk-sized decision, never as a standalone signal."
    )

    def __init__(self, lookback: int):
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        self.lookback = lookback

    @property
    def name(self) -> str:
        return f"mom_{self.lookback}"

    @property
    def min_obs(self) -> int:
        return self.lookback + 1

    def _compute(self, window: pd.DataFrame) -> float:
        c = window["close"].to_numpy()
        past = c[-1 - self.lookback]
        return c[-1] / past - 1.0 if past > 0 else np.nan


class VolScaledMomentum(Feature):
    rationale = (
        "Momentum scaled by realised volatility - equalises signal strength across "
        "calm and turbulent periods so a fixed threshold means the same thing in "
        "every regime, and prevents high-vol pairs from dominating a multi-asset book."
    )

    def __init__(self, lookback: int, vol_window: int | None = None):
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        self.lookback = lookback
        self.vol_window = vol_window or lookback

    @property
    def name(self) -> str:
        return f"mom_vs_{self.lookback}"

    @property
    def min_obs(self) -> int:
        return max(self.lookback, self.vol_window) + 1

    def _compute(self, window: pd.DataFrame) -> float:
        c = window["close"].to_numpy()
        ret = c[-1] / c[-1 - self.lookback] - 1.0
        logret = np.diff(np.log(c))[-self.vol_window:]
        sigma = np.std(logret, ddof=1)
        return ret / sigma if sigma > 0 else np.nan
