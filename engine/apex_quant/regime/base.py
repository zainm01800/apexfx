"""Regime label schema + classifier interface.

A regime is two orthogonal axes:
  * trend: up / down / ranging
  * vol:   low / normal / high

The label carries a confidence in [0, 1]. Downstream, the strategy layer uses it
to GATE which behaviours may act (e.g. don't trend-follow in a ranging regime)
and the risk layer uses it to SCALE aggression (smaller in high-vol). The label
itself never sizes a position - it is an input, never an order.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from apex_quant.data.point_in_time import PointInTimeAccessor

Trend = Literal["up", "down", "ranging"]
Vol = Literal["low", "normal", "high"]


class RegimeLabel(BaseModel):
    trend: Trend
    vol: Vol
    confidence: float = Field(ge=0.0, le=1.0)
    method: str
    detail: str = ""

    @property
    def name(self) -> str:
        return f"{self.trend}/{self.vol}-vol"

    @property
    def is_trending(self) -> bool:
        return self.trend in ("up", "down")

    def aggression_scalar(self) -> float:
        """A regime-based suggestion in [0, 1] for how much to lean in. Advisory
        only - the risk layer makes the final call. Trending+confident in calm
        vol -> near 1; ranging or high-vol -> damped."""
        base = self.confidence
        if self.vol == "high":
            base *= 0.5
        elif self.vol == "low":
            base *= 1.0
        else:
            base *= 0.8
        if self.trend == "ranging":
            base *= 0.5
        return float(max(0.0, min(1.0, base)))


class RegimeClassifier(ABC):
    @abstractmethod
    def classify(self, pit: PointInTimeAccessor, t: pd.Timestamp | str) -> RegimeLabel:
        """Classify the regime as known at ``t`` (point-in-time)."""
