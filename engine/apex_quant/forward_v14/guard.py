"""Static GBP cash-floor controls copied from the sealed V14 definitions."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .spec import BookSpec


@dataclass(frozen=True, slots=True)
class Floors:
    external_daily: float
    external_maximum: float
    internal_daily: float
    internal_maximum: float

    @property
    def internal(self) -> float:
        return max(self.internal_daily, self.internal_maximum)


def floors(spec: BookSpec, day_balance: float, day_equity: float) -> Floors:
    if not isfinite(day_balance) or not isfinite(day_equity):
        raise ValueError("day anchors must be finite")
    initial = spec.initial_equity_gbp
    external_daily = max(day_balance, day_equity) - initial * spec.daily_loss_fraction
    external_maximum = initial * (1.0 - spec.maximum_loss_fraction)
    return Floors(
        external_daily=external_daily,
        external_maximum=external_maximum,
        internal_daily=(
            external_daily
            + initial * spec.daily_loss_fraction * spec.internal_buffer_fraction
        ),
        internal_maximum=(
            external_maximum
            + initial * spec.maximum_loss_fraction * spec.internal_buffer_fraction
        ),
    )


def cash_budget(equity: float, limits: Floors, *, halted: bool = False) -> float:
    if not isfinite(equity):
        raise ValueError("equity must be finite")
    return 0.0 if halted or equity <= 0 else max(0.0, equity - limits.internal)


def latch(previous: bool, observed_equity: float, limits: Floors) -> bool:
    if not isfinite(observed_equity):
        raise ValueError("observed equity must be finite")
    return bool(previous or observed_equity <= limits.internal_maximum)
