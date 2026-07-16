"""Cross-sectional (rank) momentum over a universe.

Time-series momentum asks "is THIS pair trending up?" — one noisy bet per
instrument, fully exposed to the common move of the whole asset class. Cross-
sectional momentum instead ranks the WHOLE universe each bar and goes long the
strongest, short the weakest. It is more robust because it is (roughly) market-
neutral: it harvests RELATIVE strength and cancels the common factor that
time-series momentum rides. It is the standard, better-diversified cousin of the
single-pair signal (Asness, Moskowitz & Pedersen 2013; Moskowitz, Ooi & Pedersen
2012).

Limitation (documented honestly): this ranks currency PAIRS. Textbook FX cross-
sectional momentum ranks CURRENCIES — decomposing each pair into two legs and
forming currency baskets. Ranking pairs is a reasonable first approximation that
fits the per-instrument engine and portfolio backtester; a currency-leg version is
a future refinement.

Leakage safety: momentum scores use backward-only rolling windows, so the rank at
bar ``t`` depends only on data ``<= t`` — the same point-in-time discipline as the
rest of the engine. One shared model serves the whole universe; a thin per-
instrument adapter (:class:`CrossSectionalMomentumStrategy`) plugs it into the
:class:`~apex_quant.backtest.portfolio.PortfolioBacktester`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy


class CrossSectionalMomentum:
    """Shared rank-momentum model over a panel of instruments.

    Parameters
    ----------
    panel :
        ``{instrument: OHLCV DataFrame}`` — the full history for each instrument.
    lookback :
        Bars over which the momentum return is measured.
    vol_window :
        Bars for the realised-vol scaling (equalises signal strength across pairs).
    long_frac / short_frac :
        Fraction of the ranked universe to go long / short each bar. ``0.3`` ≈
        top and bottom third.
    min_universe :
        Minimum instruments with a valid score before any signal is emitted — a
        cross-section of two is not a cross-section.
    allow_short :
        If False, only long the top fraction (long-only variant).
    reward_risk, holding_horizon, timeframe :
        Passed through onto the emitted signals / consumed by the backtester.
    """

    def __init__(
        self,
        panel: dict[str, pd.DataFrame],
        *,
        lookback: int = 63,
        vol_window: int = 63,
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
        if lookback < 1 or vol_window < 2:
            raise ValueError("need lookback >= 1 and vol_window >= 2")
        self.lookback = lookback
        self.vol_window = vol_window
        self.long_frac = long_frac
        self.short_frac = short_frac if allow_short else 0.0
        self.min_universe = max(2, min_universe)
        self.allow_short = allow_short
        self.reward_risk = reward_risk
        self.holding_horizon = holding_horizon
        self.timeframe = timeframe
        self.instruments = list(panel.keys())

        # Vol-scaled momentum score per instrument, aligned on the union index.
        # Rolling windows are backward-looking, so row t uses only bars <= t.
        scores: dict[str, pd.Series] = {}
        for inst, df in panel.items():
            c = df["close"]
            ret = c / c.shift(lookback) - 1.0
            vol = np.log(c).diff().rolling(vol_window).std(ddof=1)
            scores[inst] = ret / vol.where(vol > 0)
        self._scores = pd.DataFrame(scores).sort_index()
        self._cache: dict[pd.Timestamp, dict] = {}

    @staticmethod
    def _norm_t(t) -> pd.Timestamp:
        ts = pd.Timestamp(t)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    def ranks_at(self, t) -> dict[str, tuple[int, float]]:
        """Return ``{instrument: (direction, z_score)}`` for the long/short buckets
        at ``t`` (+1 long, -1 short). Instruments not selected are absent. Cached."""
        t = self._norm_t(t)
        cached = self._cache.get(t)
        if cached is not None:
            return cached

        result: dict[str, tuple[int, float]] = {}
        if t in self._scores.index:
            row = self._scores.loc[t].dropna()
            n = len(row)
            if n >= self.min_universe:
                ordered = row.sort_values(ascending=False)
                n_long = max(1, int(round(n * self.long_frac)))
                n_short = max(1, int(round(n * self.short_frac))) if self.allow_short else 0
                n_short = min(n_short, n - n_long)          # never overlap the long bucket
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
                rationale="cross-sectional: not in long/short bucket",
            )
        d, z = entry
        direction = Direction.LONG if d > 0 else Direction.SHORT
        # Relative-strength conviction -> a bounded, honest probability. Cross-
        # sectional momentum's real hit-rate is modest, so the band is tight.
        p = float(np.clip(0.52 + 0.05 * abs(z), 0.52, 0.70))
        return Signal(
            instrument=instrument, direction=direction, probability=p,
            reward_risk=self.reward_risk, confidence=float(min(1.0, abs(z) / 2.0)),
            timeframe=self.timeframe,
            rationale=f"cross-sectional {direction.value.upper()} | z={z:+.2f} | p={p:.2f}",
        )

    def strategies(self) -> dict[str, "CrossSectionalMomentumStrategy"]:
        """One per-instrument adapter for every instrument, all sharing this model —
        ready to hand straight to ``PortfolioBacktester.run(pits, strategies)``."""
        return {inst: CrossSectionalMomentumStrategy(self, inst) for inst in self.instruments}


class CrossSectionalMomentumStrategy(Strategy):
    """Per-instrument view of a shared :class:`CrossSectionalMomentum` model.

    Stateless and rule-based (no fit): the rank is a deterministic function of the
    point-in-time cross-section, so there are no parameters to calibrate.
    """

    name = "cross_sectional_momentum"

    def __init__(self, model: CrossSectionalMomentum, instrument: str) -> None:
        self.model = model
        self.instrument = instrument
        self.holding_horizon = model.holding_horizon
        self.reward_risk = model.reward_risk
        self.timeframe = model.timeframe

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        return self.model.signal_for(instrument or self.instrument, t)
