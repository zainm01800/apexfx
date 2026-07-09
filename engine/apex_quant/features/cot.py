"""CFTC Commitments of Traders positioning - pluggable feature, off by default.

Speculative positioning extremes tend to mean-revert: when large speculators are
crowded long (high percentile of net positioning), forward returns are on average
weaker, and vice versa. The weekly COT report needs a data source not wired in
Phase 1, so this is disabled unless a ``cot_provider`` is supplied.

A ``cot_provider`` is a callable ``(instrument, t) -> net_position_percentile`` in
[0, 1] (fraction of the trailing lookback the current net spec position sits at).
It MUST be point-in-time and respect the report's publication lag (COT is released
with a multi-day delay; the provider must only surface data actually public at t).
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.features.base import Feature

CotProvider = Callable[[str, pd.Timestamp], "float | None"]


class CotPositioning(Feature):
    rationale = (
        "COT speculative positioning percentile: crowded one-sided positioning by "
        "large speculators tends to precede mean reversion. A contrarian filter on "
        "stretched sentiment, used to temper (not trigger) directional signals."
    )

    def __init__(self, instrument: str, cot_provider: CotProvider | None = None):
        self.instrument = instrument
        self.cot_provider = cot_provider

    @property
    def name(self) -> str:
        return "cot_pctile"

    @property
    def min_obs(self) -> int:
        return 1

    @property
    def available(self) -> bool:
        return self.cot_provider is not None

    def _compute(self, window: pd.DataFrame) -> float:
        return float("nan")  # COT needs the timestamp; see compute()

    def compute(self, pit: PointInTimeAccessor, t: pd.Timestamp | str) -> float:
        if self.cot_provider is None:
            return float("nan")
        pct = self.cot_provider(self.instrument, pd.Timestamp(t))
        return float("nan") if pct is None else float(pct)
