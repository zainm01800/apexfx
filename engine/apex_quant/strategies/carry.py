"""Cross-sectional interest-rate carry strategy.

Ranks currency pairs by their point-in-time interest rate differential,
longing the highest-yielding pairs and shorting the lowest-yielding pairs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy


class CrossSectionalCarry:
    """Shared cross-sectional carry model over a panel of instruments.

    Parameters
    ----------
    panel :
        ``{instrument: OHLCV DataFrame}`` — the full history for each instrument.
    rate_provider :
        Callable ``(instrument, t) -> (base_rate, quote_rate) | None``
    long_frac / short_frac :
        Fraction of the ranked universe to go long / short each bar.
    min_universe :
        Minimum instruments with a valid score before any signal is emitted.
    allow_short :
        If False, only long the top fraction.
    reward_risk, holding_horizon, timeframe :
        Passed through onto the emitted signals.
    """

    def __init__(
        self,
        panel: dict[str, pd.DataFrame],
        rate_provider,
        *,
        long_frac: float = 0.30,
        short_frac: float = 0.30,
        min_universe: int = 4,
        allow_short: bool = True,
        reward_risk: float = 1.5,
        holding_horizon: int = 21,
        timeframe: str = "1d",
    ) -> None:
        if not (0.0 < long_frac <= 1.0):
            raise ValueError("long_frac must be in (0, 1]")
        if not (0.0 <= short_frac <= 1.0):
            raise ValueError("short_frac must be in [0, 1]")
        self.rate_provider = rate_provider
        self.long_frac = long_frac
        self.short_frac = short_frac if allow_short else 0.0
        self.min_universe = max(2, min_universe)
        self.allow_short = allow_short
        self.reward_risk = reward_risk
        self.holding_horizon = holding_horizon
        self.timeframe = timeframe
        self.instruments = list(panel.keys())
        
        # Pre-align index
        self._timeline = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in panel.values()])))
        self._cache: dict[pd.Timestamp, dict] = {}

    @staticmethod
    def _norm_t(t) -> pd.Timestamp:
        ts = pd.Timestamp(t)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    def ranks_at(self, t) -> dict[str, tuple[int, float]]:
        """Return ``{instrument: (direction, z_score)}`` for the carry buckets at t."""
        t = self._norm_t(t)
        cached = self._cache.get(t)
        if cached is not None:
            return cached

        result: dict[str, tuple[int, float]] = {}
        
        # Calculate rate differentials for all available instruments at t
        differentials = {}
        for inst in self.instruments:
            # We must verify if this instrument is actually active at time t
            # (i.e. has a valid bar at t)
            rates = self.rate_provider(inst, t)
            if rates is not None:
                base_rate, quote_rate = rates
                differentials[inst] = base_rate - quote_rate

        n = len(differentials)
        if n >= self.min_universe:
            row = pd.Series(differentials)
            ordered = row.sort_values(ascending=False)
            
            n_long = max(1, int(round(n * self.long_frac)))
            n_short = max(1, int(round(n * self.short_frac))) if self.allow_short else 0
            n_short = min(n_short, n - n_long)

            mu = float(row.mean())
            sd = float(row.std(ddof=1)) or 1.0
            
            for inst in ordered.index[:n_long]:
                result[inst] = (1, (float(row[inst]) - mu) / sd)
            for inst in (ordered.index[n - n_short:] if n_short > 0 else []):
                result[inst] = (-1, (float(row[inst]) - mu) / sd)

        self._cache[t] = result
        return result

    def signal_for(self, instrument: str, t) -> Signal:
        entry = self.ranks_at(t).get(instrument)
        if entry is None:
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=self.reward_risk, timeframe=self.timeframe,
                rationale="cross-sectional carry: not in long/short bucket",
            )
        d, z = entry
        direction = Direction.LONG if d > 0 else Direction.SHORT
        p = float(np.clip(0.52 + 0.05 * abs(z), 0.52, 0.70))
        return Signal(
            instrument=instrument, direction=direction, probability=p,
            reward_risk=self.reward_risk, confidence=float(min(1.0, abs(z) / 2.0)),
            timeframe=self.timeframe,
            rationale=f"cross-sectional carry {direction.value.upper()} | z={z:+.2f} | p={p:.2f}",
        )

    def strategies(self) -> dict[str, CrossSectionalCarryStrategy]:
        """One per-instrument adapter for every instrument."""
        return {inst: CrossSectionalCarryStrategy(self, inst) for inst in self.instruments}


class CrossSectionalCarryStrategy(Strategy):
    """Adapter for CrossSectionalCarry strategy."""
    name = "cross_sectional_carry"

    def __init__(self, model: CrossSectionalCarry, instrument: str) -> None:
        self.model = model
        self.instrument = instrument
        self.holding_horizon = model.holding_horizon
        self.reward_risk = model.reward_risk
        self.timeframe = model.timeframe

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        return self.model.signal_for(instrument or self.instrument, t)
