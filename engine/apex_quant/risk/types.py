"""Typed inputs/outputs for the risk layer.

A ``Signal`` is the ONLY thing a model/strategy may emit - a direction and a
calibrated probability with an optional edge. It explicitly carries NO size. The
risk layer consumes it plus account/market state and returns a ``Position``,
which may be flat. This boundary is the heart of "the risk layer is supreme".
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Signal(BaseModel):
    """A probabilistic suggestion. Never sets size; never an order."""

    model_config = ConfigDict(allow_inf_nan=False)

    instrument: str
    direction: Direction
    probability: float = Field(ge=0.0, le=1.0, description="calibrated P(trade is profitable)")
    reward_risk: float = Field(default=1.0, gt=0.0, description="target:stop payoff ratio b")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="model self-confidence")
    rationale: str = ""
    timeframe: str = "1h"  # e.g. '15m', '1h', '1d', '1w' — used for per-bucket slot counting
    sleeve: str = "default"  # e.g. 'trend', 'tom', 'crypto_xs' — used for per-sleeve slot capacity allocation
    stop_price: float | None = Field(default=None, description="Optional custom stop loss price")
    target_price: float | None = Field(default=None, description="Optional custom take profit price")


class OpenPosition(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    instrument: str
    direction: Direction
    notional: float = Field(ge=0.0)
    risk: float = Field(default=0.0, description="Absolute risk of position in account currency (GBP)")
    timeframe: str = "1h"  # trading style bucket: '15m', '1h', '1d', '1w'
    sleeve: str = "default"  # strategy sleeve identifier



class AccountState(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    equity: float = Field(gt=0.0)
    peak_equity: float = Field(gt=0.0)
    open_positions: list[OpenPosition] = []
    #: Equity at the START of the current trading day. Prop firms measure their daily-loss
    #: rule against this, NOT against peak equity — a from-peak breaker cannot see a bad day
    #: that begins from a fresh high. None means "unknown", and the daily check is skipped.
    day_start_equity: float | None = Field(default=None, gt=0.0)
    #: Optional capital base for NEW risk/vol sizing.  Funded accounts may require
    #: a base smaller than marked equity (for example the remaining official loss
    #: buffer).  None preserves the certified behaviour exactly.  Book-level gross,
    #: correlation, notional and portfolio caps continue to use actual ``equity``.
    risk_sizing_base: float | None = Field(default=None, ge=0.0)
    #: Optional absolute cash ceiling for the candidate's NEW entry-to-stop risk.
    #: This is deliberately distinct from ``risk_sizing_base``: funded-account
    #: policies size from ordinary account capital, then constrain the resulting
    #: cash loss by the currently available rule cushions.  None preserves the
    #: historical percentage-only sizing path exactly.
    candidate_stop_risk_cap_dollars: float | None = Field(default=None, ge=0.0)
    #: Optional absolute ceiling for aggregate post-order entry-to-stop risk,
    #: including every open position plus the candidate.  RiskManager enforces it
    #: in sequential mode; simultaneous portfolio allocation consumes the same
    #: value after collecting that bar's candidate set.  None is a strict no-op.
    aggregate_stop_risk_cap_dollars: float | None = Field(default=None, ge=0.0)

    @property
    def drawdown(self) -> float:
        """Fractional drawdown from peak equity, in [0, 1)."""
        return max(0.0, 1.0 - self.equity / self.peak_equity)

    @property
    def daily_loss(self) -> float:
        """Fractional loss since the day's opening equity, in [0, 1). Zero when up on
        the day, or when ``day_start_equity`` was not supplied."""
        if not self.day_start_equity:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.day_start_equity)

    @property
    def gross_notional(self) -> float:
        return sum(abs(p.notional) for p in self.open_positions)


class MarketState(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    instrument: str
    price: float = Field(gt=0.0)
    ann_vol: float = Field(gt=0.0, description="annualised forward vol (from volatility model)")
    atr: float = Field(gt=0.0, description="ATR in price terms, for stop distance")
    quote_to_account_rate: float = Field(
        default=1.0,
        gt=0.0,
        description="Exchange rate to convert quote currency to account currency (GBP)",
    )
    # |correlation| of this instrument to each currently-open instrument
    correlations: dict[str, float] = {}
    #: Optional Cornish-Fisher tail multipliers (W2, 2026-07-25), precomputed by the
    #: backtester from rolling skew/excess-kurtosis and clipped to [tau_min, tau_max].
    #: None = "not computed" -> tau 1.0, certified sizing. Only consumed when
    #: risk.cf_cvar_enabled is true; every other MarketState producer (live loop,
    #: single-instrument engine) leaves them None, which is a strict no-op.
    cf_tail_long: float | None = None
    cf_tail_short: float | None = None


class Position(BaseModel):
    """The risk layer's authoritative output. ``permitted=False`` / zero size is
    a valid, common result. ``constraints_applied`` is the transparency log -
    every binding rule that shaped this decision."""

    model_config = ConfigDict(allow_inf_nan=False)

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
