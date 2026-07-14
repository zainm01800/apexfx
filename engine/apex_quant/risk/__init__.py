"""Risk management - the supreme layer with veto authority over every signal."""

from apex_quant.risk.bayesian_sizer import BayesianRiskSizer, BetaBinomialWinRate
from apex_quant.risk.circuit_breaker import breaker_tripped, drawdown
from apex_quant.risk.limits import correlation_cap, gross_exposure_cap
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.sizing import (
    fractional_kelly,
    full_kelly,
    round_lot_size,
    units_from_risk,
    vol_target_notional,
)
from apex_quant.risk.stops import atr, atr_stop
from apex_quant.risk.types import (
    AccountState,
    Direction,
    MarketState,
    OpenPosition,
    Position,
    Signal,
)

__all__ = [
    "RiskManager",
    "BayesianRiskSizer",
    "BetaBinomialWinRate",
    "Signal",
    "Position",
    "Direction",
    "AccountState",
    "MarketState",
    "OpenPosition",
    "fractional_kelly",
    "full_kelly",
    "round_lot_size",
    "vol_target_notional",
    "units_from_risk",
    "atr",
    "atr_stop",
    "gross_exposure_cap",
    "correlation_cap",
    "drawdown",
    "breaker_tripped",
]
