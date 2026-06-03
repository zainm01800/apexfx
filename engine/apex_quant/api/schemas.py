"""Typed API response models. ``extra='allow'`` keeps the contract explicit for
the core fields while permitting the richer detail the service attaches."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class HealthResponse(BaseModel):
    status: str
    service: str
    version: int
    instruments: list[str]                       # full multi-asset universe
    by_class: dict[str, list[str]] = {}          # {"forex": [...], "equity": [...], "crypto": [...]}


class RegimeResponse(_Loose):
    instrument: str
    as_of: str
    trend: str
    vol: str
    confidence: float
    name: str
    method: str


class UncertaintyBand(BaseModel):
    lower: float
    upper: float


class SignalResponse(_Loose):
    instrument: str
    as_of: str
    direction: str
    probability: float
    confidence: float
    reward_risk: float
    uncertainty: UncertaintyBand | None = None
    contributing_features: dict
    reason: str = ""
    fitted: bool = True


class RiskResponse(_Loose):
    instrument: str
    as_of: str
    permitted: bool
    direction: str
    rationale: str
    assumed_equity: float


class ValidationResponse(_Loose):
    strategy: str
    instrument: str
    verdict: dict
