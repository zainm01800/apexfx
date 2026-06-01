"""Strategy interface.

A strategy turns point-in-time data into a probabilistic ``Signal`` - direction
plus a calibrated probability and an uncertainty band. It NEVER sizes a position
(that's the risk layer). Strategies separate ``fit`` (calibrate on training data
only - called once per CPCV fold) from ``generate`` (per-bar, point-in-time), so
calibration can be honest and leakage-free.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Signal


class Strategy(ABC):
    name: str = "strategy"

    def fit(self, pit: PointInTimeAccessor, train_timestamps: Iterable[pd.Timestamp]) -> None:
        """Calibrate on training data only. Default: no-op (stateless strategy)."""
        return None

    @abstractmethod
    def generate(
        self, pit: PointInTimeAccessor, t: pd.Timestamp | str, instrument: str = ""
    ) -> Signal:
        """Point-in-time signal at ``t`` (reads only ``as_of(t)``)."""

    def is_fitted(self) -> bool:
        return True
