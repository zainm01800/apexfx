"""Feature base class.

Every feature is a **pure function of point-in-time data** with a **documented
economic rationale**. The rationale is structurally enforced - a concrete
feature without one fails at import. ``compute`` only ever reads the accessor's
``window(t, min_obs)``, so a feature physically cannot see the future.

Features return ``NaN`` (never a fabricated number) when history is too short.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor


class Feature(ABC):
    #: Economic rationale - REQUIRED on every concrete feature.
    rationale: str = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Skip still-abstract intermediates; enforce on concrete features only.
        if getattr(cls, "__abstractmethods__", None):
            return
        if not (isinstance(cls.rationale, str) and cls.rationale.strip()):
            raise TypeError(
                f"{cls.__name__} must define a non-empty `rationale` "
                "(every feature needs a documented economic rationale)."
            )

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable column name in the feature matrix."""

    @property
    @abstractmethod
    def min_obs(self) -> int:
        """Bars required to compute honestly."""

    @abstractmethod
    def _compute(self, window: pd.DataFrame) -> float:
        """Compute from exactly the last ``min_obs`` bars (most recent last)."""

    def compute(self, pit: PointInTimeAccessor, t: pd.Timestamp | str) -> float:
        """Leakage-safe evaluation at ``t``. Returns NaN if history is short."""
        w = pit.window(t, self.min_obs)
        if len(w) < self.min_obs:
            return float("nan")
        val = self._compute(w)
        return float(val) if val is not None and np.isfinite(val) else float("nan")

    def describe(self) -> dict:
        return {"name": self.name, "min_obs": self.min_obs, "rationale": self.rationale}
