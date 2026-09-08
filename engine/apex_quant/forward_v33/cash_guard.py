"""Pure GBP cash-floor controls for research; not an execution guarantee."""

from dataclasses import dataclass
from math import isfinite


def finite(value: float, name: str, *, positive: bool = False) -> float:
    value = float(value)
    if not isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError(f"invalid {name}")
    return value


@dataclass(frozen=True)
class Rules:
    initial: float = 100_000.0
    daily: float = 0.03
    maximum: float = 0.06
    mode: str = "static"
    buffer_fraction: float = 0.25

    def __post_init__(self):
        finite(self.initial, "initial", positive=True)
        for name in ("daily", "maximum", "buffer_fraction"):
            value = finite(getattr(self, name), name, positive=True)
            if value >= 1:
                raise ValueError(name)
        if self.mode not in ("static", "trailing"):
            raise ValueError("mode must be static or trailing")


@dataclass(frozen=True)
class Floors:
    external_daily: float
    external_maximum: float
    internal_daily: float
    internal_maximum: float

    @property
    def internal(self):
        return max(self.internal_daily, self.internal_maximum)


def floors(rules: Rules, day_balance: float, day_equity: float, prior_eod_peak: float) -> Floors:
    # A catastrophic gap can make the already-failed account nonpositive. Keep
    # reporting that latched path rather than dropping its worst outcome.
    for name, value in (("day balance", day_balance), ("day equity", day_equity)):
        if not isfinite(value):
            raise ValueError(f"invalid {name}")
    finite(prior_eod_peak, "peak", positive=True)
    if prior_eod_peak < rules.initial:
        raise ValueError("peak must include initial capital")
    daily = max(day_balance, day_equity) - rules.initial * rules.daily
    anchor = rules.initial if rules.mode == "static" else prior_eod_peak
    maximum = anchor - rules.initial * rules.maximum
    return Floors(daily, maximum,
                  daily + rules.initial * rules.daily * rules.buffer_fraction,
                  maximum + rules.initial * rules.maximum * rules.buffer_fraction)


def budget(equity: float, limits: Floors, open_risk: float = 0.0,
           pending_risk: float = 0.0, fee_reserve: float = 0.0,
           halted: bool = False) -> float:
    if not isfinite(equity):
        raise ValueError("invalid equity")
    for name, value in (("open risk", open_risk), ("pending risk", pending_risk), ("fees", fee_reserve)):
        finite(value, name)
    return 0.0 if halted or equity <= 0 else max(0.0, equity - limits.internal - open_risk - pending_risk - fee_reserve)


def latch(previous: bool, observed_equity: float, limits: Floors) -> bool:
    if not isfinite(observed_equity):
        raise ValueError("invalid observed equity")
    return bool(previous or observed_equity <= limits.internal_maximum)
