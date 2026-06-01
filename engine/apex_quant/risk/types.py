"""Typed inputs/outputs for the risk layer.

A ``Signal`` is the ONLY thing a model/strategy may emit - a direction and a
calibrated probability with an optional edge. It explicitly carries NO size. The
risk layer consumes it plus account/market state and returns a ``Position``,
which may be flat. This boundary is the heart of "the risk layer is supreme".
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Signal(BaseModel):
    """A probabilistic suggestion. Never sets size; never an order."""

    instrument: str
    direction: Direction
    probability: float = Field(ge=0.0, le=1.0, description="calibrated P(trade is profitable)")
    reward_risk: float = Field(default=1.0, gt=0.0, description="target:stop payoff ratio b")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="model self-confidence")
    rationale: str = ""


class OpenPosition(BaseModel):
    instrument: str
    direction: Direction
    notional: float = Field(ge=0.0)


class AccountState(BaseModel):
    equity: float = Field(gt=0.0)
    peak_equity: float = Field(gt=0.0)
    open_positions: list[OpenPosition] = []

    @property
    def drawdown(self) -> float:
        """Fractional drawdown from peak equity, in [0, 1)."""
        return max(0.0, 1.0 - self.equity / self.peak_equity)

    @property
    def gross_notional(self) -> float:
        return sum(abs(p.notional) for p in self.open_positions)


class MarketState(BaseModel):
    instrument: str
    price: float = Field(gt=0.0)
    ann_vol: float = Field(gt=0.0, description="annualised forward vol (from volatility model)")
    atr: float = Field(gt=0.0, description="ATR in price terms, for stop distance")
    # |correlation| of this instrument to each currently-open instrument
    correlations: dict[str, float] = {}


class Position(BaseModel):
    """The risk layer's authoritative output. ``permitted=False`` / zero size is
    a valid, common result. ``constraints_applied`` is the transparency log -
    every binding rule that shaped this decision."""

    instrument: str
    direction: Direction
    units: float = 0.0
    notional: float = 0.0
    risk_fraction: float = 0.0
    stop_price: float | None = None
    stop_distance: float | None = None
    target_price: float | None = None
    permitted: bool = False
    constraints_applied: list[str] = []
    rationale: str = ""
    sizing_detail: dict = {}

    @property
    def signed_notional(self) -> float:
        return -self.notional if self.direction == Direction.SHORT else self.notional
