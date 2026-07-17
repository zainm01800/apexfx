"""Ensemble vote across the engine's independent signal sleeves.

Every individual sleeve tested so far has FAILED the validation gate on this
data: time-series momentum, meta-labeled momentum, pair-ranked cross-sectional,
currency-ranked cross-sectional, and carry. This module tests the one honest
hypothesis that remains inside the current dataset: that several individually
weak, imperfectly correlated signals AGREEING is a stronger condition than any
one of them alone (the classic diversification argument — an ensemble's noise
partially cancels while any shared signal adds).

It is a hypothesis, not a promise: the ensemble goes through the same
CPCV/DSR/PBO gate as everything else, with an honest trial count that charges it
for the sweeps already spent on its component sleeves.

Mechanics
---------
Four voters per (instrument, t), each ∈ {+1, 0, -1}:

  * ts     — sign of the pair's own vol-scaled momentum (time-series vote)
  * cs     — pair-ranked cross-sectional bucket (long/short/none)
  * ccy    — currency-ranked cross-sectional bucket
  * carry  — rate-differential bucket

A trade requires the net vote |Σ| >= ``min_votes`` (default 2). Component
models are built with their canonical (already-validated-and-rejected) default
configs — no fresh per-sleeve tuning is allowed here, precisely so the ensemble
cannot become a new overfitting surface. Leakage safety is inherited: every
component reads only point-in-time windows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy
from apex_quant.strategies.carry import CrossSectionalCarry
from apex_quant.strategies.cross_sectional import CrossSectionalMomentum
from apex_quant.strategies.currency_momentum import CurrencyCrossSectionalMomentum


def _vote(sig: Signal) -> int:
    if sig.direction == Direction.LONG:
        return 1
    if sig.direction == Direction.SHORT:
        return -1
    return 0


class EnsembleVote:
    """Shared ensemble model over a panel. Mirrors the sleeve interface
    (``signal_for`` + ``.strategies()``) so it plugs straight into
    ``PortfolioBacktester`` and ``run_portfolio_validation``."""

    def __init__(
        self,
        panel: dict[str, pd.DataFrame],
        *,
        rate_provider=None,
        min_votes: int = 2,
        reward_risk: float = 1.5,
        holding_horizon: int = 21,
        timeframe: str = "1d",
    ) -> None:
        if min_votes < 1 or min_votes > 4:
            raise ValueError("min_votes must be in 1..4")
        self.min_votes = min_votes
        self.reward_risk = reward_risk
        self.holding_horizon = holding_horizon
        self.timeframe = timeframe
        self.instruments = list(panel.keys())

        # Component sleeves at their canonical defaults — deliberately NOT tunable
        # from here (see module docstring).
        self._cs = CrossSectionalMomentum(panel)
        self._ccy = CurrencyCrossSectionalMomentum(panel)
        if rate_provider is None:
            from apex_quant.data.rates import CSVRateProvider
            rate_provider = CSVRateProvider()
        self._carry = CrossSectionalCarry(panel, rate_provider)

    # -- votes ------------------------------------------------------------------
    def _ts_vote(self, instrument: str, t) -> int:
        """Time-series vote: the sign of the pair's own vol-scaled momentum,
        read from the cross-sectional model's precomputed score matrix."""
        ts = pd.Timestamp(t)
        scores = self._cs._scores
        if ts not in scores.index or instrument not in scores.columns:
            return 0
        s = scores.at[ts, instrument]
        if not np.isfinite(s):
            return 0
        return 1 if s > 0 else (-1 if s < 0 else 0)

    def votes_for(self, instrument: str, t) -> dict[str, int]:
        return {
            "ts": self._ts_vote(instrument, t),
            "cs": _vote(self._cs.signal_for(instrument, t)),
            "ccy": _vote(self._ccy.signal_for(instrument, t)),
            "carry": _vote(self._carry.signal_for(instrument, t)),
        }

    # -- sleeve interface ---------------------------------------------------------
    def signal_for(self, instrument: str, t) -> Signal:
        v = self.votes_for(instrument, t)
        total = sum(v.values())
        if abs(total) < self.min_votes:
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=self.reward_risk, timeframe=self.timeframe,
                rationale=f"ensemble: net vote {total:+d} < {self.min_votes} required ({v})",
            )
        direction = Direction.LONG if total > 0 else Direction.SHORT
        # Bounded, deliberately modest probability: more agreement -> mildly higher.
        p = float(np.clip(0.52 + 0.04 * (abs(total) - self.min_votes + 1), 0.52, 0.68))
        return Signal(
            instrument=instrument, direction=direction, probability=p,
            reward_risk=self.reward_risk, confidence=abs(total) / 4.0,
            timeframe=self.timeframe,
            rationale=f"ensemble {direction.value.upper()} | net {total:+d} of 4 votes {v} | p={p:.2f}",
        )

    def strategies(self) -> dict[str, "EnsembleVoteStrategy"]:
        return {inst: EnsembleVoteStrategy(self, inst) for inst in self.instruments}


class EnsembleVoteStrategy(Strategy):
    """Per-instrument view of a shared :class:`EnsembleVote` model."""

    name = "ensemble_vote"

    def __init__(self, model: EnsembleVote, instrument: str) -> None:
        self.model = model
        self.instrument = instrument
        self.holding_horizon = model.holding_horizon
        self.reward_risk = model.reward_risk
        self.timeframe = model.timeframe

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        return self.model.signal_for(instrument or self.instrument, t)
