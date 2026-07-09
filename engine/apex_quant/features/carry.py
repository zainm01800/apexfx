"""Carry (interest-rate differential) - pluggable forex feature, off by default.

Carry is a core FX factor: higher-yielding currencies tend to outperform on
average, compensated by occasional sharp drawdowns (Lustig, Roussanov &
Verdelhan 2011). It needs a rate-differential data source, which is NOT wired in
Phase 1 - so this feature is disabled unless a ``rate_provider`` is supplied.

A ``rate_provider`` is any callable ``(instrument, t) -> (base_rate, quote_rate)``
in annualised decimal terms (e.g. 0.045 for 4.5%). It MUST be point-in-time: it
may only return rates known at ``t``. When absent, the feature returns NaN - it
never fabricates a value.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.features.base import Feature

RateProvider = Callable[[str, pd.Timestamp], "tuple[float, float] | None"]


class Carry(Feature):
    rationale = (
        "Interest-rate differential (carry): long the higher-yielding leg of a "
        "pair earns the rate spread. A persistent FX risk premium, but crash-prone "
        "in risk-off regimes - hence carry is gated by the regime layer and never "
        "sized on its own."
    )

    def __init__(self, instrument: str, rate_provider: RateProvider | None = None):
        self.instrument = instrument
        self.rate_provider = rate_provider

    @property
    def name(self) -> str:
        return "carry"

    @property
    def min_obs(self) -> int:
        return 1

    @property
    def available(self) -> bool:
        return self.rate_provider is not None

    def _compute(self, window: pd.DataFrame) -> float:
        # Not used directly - carry needs the timestamp, so we override compute().
        return np.nan

    def compute(self, pit: PointInTimeAccessor, t: pd.Timestamp | str) -> float:
        if self.rate_provider is None:
            return float("nan")
        rates = self.rate_provider(self.instrument, pd.Timestamp(t))
        if not rates:
            return float("nan")
        base_rate, quote_rate = rates
        return float(base_rate - quote_rate)  # carry of being long base/quote
