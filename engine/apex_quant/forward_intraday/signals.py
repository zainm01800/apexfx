"""Deterministic signal calculations and position sizing for SPY intraday strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import numpy as np

from .spec import BookSpec, Profile


@dataclass(frozen=True, slots=True)
class SignalDecision:
    direction: int            # +1 for Long, -1 for Short, 0 for Neutral
    barrier: float | None     # Stop loss price barrier
    upper: float              # Upper breakout band
    lower: float              # Lower breakout band
    decision_bar_offset: int  # Minute index of evaluation bar (e.g. 29, 59, ...)
    decision_bar_time: str    # ISO timestamp of the decision bar
    session_open: float       # Session open price
    prior_close: float        # Prior close
    volatility: float         # 14-session daily volatility
    vwap: float | None = None
    sigma: float | None = None
    atr_14: float | None = None


def compute_v24_signal(
    offset_minute: int,
    bar_close: float,
    session_open: float,
    prior_close: float,
    sigma: float,
    vwap: float,
    volatility: float,
    bar_time_str: str,
) -> SignalDecision | None:
    """Evaluate SPY Noise-Band Momentum at 30-minute intervals (10:00 to 15:30 NY).

    Offsets: 30, 60, 90, ..., 360 (evaluating bar minute 29, 59, ..., 359).
    """
    if offset_minute not in range(30, 361, 30):
        return None
    if not (np.isfinite([bar_close, session_open, prior_close, sigma, vwap, volatility]).all() and volatility > 0):
        return None

    upper = max(session_open, prior_close) * (1.0 + sigma)
    lower = min(session_open, prior_close) * (1.0 - sigma)
    if lower <= 0:
        return None

    side = 1 if (bar_close > upper and bar_close > vwap) else (-1 if (bar_close < lower and bar_close < vwap) else 0)
    barrier = max(upper, vwap) if side == 1 else (min(lower, vwap) if side == -1 else None)

    return SignalDecision(
        direction=side,
        barrier=barrier,
        upper=upper,
        lower=lower,
        decision_bar_offset=offset_minute - 1,
        decision_bar_time=bar_time_str,
        session_open=session_open,
        prior_close=prior_close,
        volatility=volatility,
        vwap=vwap,
        sigma=sigma,
    )


def compute_v30_signal(
    offset_minute: int,
    bar_close: float,
    session_open: float,
    prior_close: float,
    atr_14: float,
    volatility: float,
    bar_time_str: str,
) -> SignalDecision | None:
    """Evaluate SPY ATR Breakout at 15-minute intervals (10:00 to 15:45 NY).

    Offsets: 30, 45, 60, ..., 375 (evaluating bar minute 29, 44, ..., 374).
    Protective stop is strictly locked at session_open.
    """
    if offset_minute not in range(30, 376, 15):
        return None
    if not (np.isfinite([bar_close, session_open, atr_14, volatility]).all() and volatility > 0 and atr_14 > 0):
        return None

    upper = session_open + 0.5 * atr_14
    lower = session_open - 0.5 * atr_14

    side = 1 if bar_close > upper else (-1 if bar_close < lower else 0)
    # The protective stop is strictly today's session open
    barrier = session_open if side != 0 else None

    return SignalDecision(
        direction=side,
        barrier=barrier,
        upper=upper,
        lower=lower,
        decision_bar_offset=offset_minute - 1,
        decision_bar_time=bar_time_str,
        session_open=session_open,
        prior_close=prior_close,
        volatility=volatility,
        atr_14=atr_14,
    )


def entry_units(
    equity: float,
    price: float,
    barrier: float,
    direction: int,
    fx: float,
    volatility: float,
    floor: float,
    profile: Profile,
    fee_bps: float = 1.0,
    slip_bps: float = 1.0,
) -> tuple[float, float]:
    """Inclusive risk is measured before fees; its ceiling uses after-fee equity.

    Caps:
    1. Volatility target gross limit: min(gross, vol_target / daily_vol)
    2. Absolute gross exposure limit: gross * equity
    3. Per-trade risk ceiling: risk * equity
    4. Internal account floor limit: max(0, equity - floor) / risk_unit

    Sizing utilization: 90% of the minimum cap.
    Minimum notional: £1,000 GBP.
    """
    fee = fee_bps / 10_000.0
    slip = slip_bps / 10_000.0

    if equity <= 0 or volatility <= 0 or direction not in (-1, 1) or direction * (price - barrier) <= 0:
        return 0.0, 0.0

    stopped = barrier * (1.0 - direction * slip)
    if stopped <= 0:
        return 0.0, 0.0

    price_gbp = price / fx
    entry_fee = price * fee / fx
    risk_unit = (direction * (price - stopped) + price * fee + stopped * fee) / fx
    if risk_unit <= 0:
        return 0.0, 0.0

    desired_gross = min(profile.gross, profile.vol_target / volatility)
    caps = (
        desired_gross * equity / (price_gbp + desired_gross * entry_fee),
        profile.gross * equity / (price_gbp + profile.gross * entry_fee),
        profile.risk * equity / (risk_unit + profile.risk * entry_fee),
        max(0.0, equity - floor) / risk_unit,
    )
    units = profile.utilization * max(0.0, min(caps))
    if units * price_gbp < profile.min_notional_gbp:
        return 0.0, risk_unit
    return float(units), float(risk_unit)
