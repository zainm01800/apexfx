"""Position sizing primitives: volatility targeting + fractional Kelly.

Two complementary ideas:
  * Volatility targeting sets the base notional so the position contributes a
    target amount of portfolio volatility - normalises risk across calm/turbulent
    assets and time.
  * Fractional Kelly scales that base by the *edge*. Full Kelly is ruinous under
    parameter uncertainty (our edge estimates are noisy), so we deploy only a
    fraction (default 0.25). Negative edge -> zero. This is itself a veto.
"""

from __future__ import annotations


def full_kelly(p: float, b: float) -> float:
    """Full-Kelly fraction for a bet that wins ``b:1`` with probability ``p``:
    f* = p - (1-p)/b. Can be negative (no edge) or >1 (huge edge)."""
    if b <= 0:
        return 0.0
    return p - (1.0 - p) / b


def fractional_kelly(p: float, b: float, fraction: float) -> float:
    """Fractional Kelly, clamped to [0, 1]. ``fraction`` in (0, 1] (e.g. 0.25).

    Returns the fraction of equity to put *at risk* on this trade. Zero when the
    edge is non-positive - the sizing layer refuses a no-edge trade outright."""
    f = fraction * full_kelly(p, b)
    return float(min(1.0, max(0.0, f)))


def vol_target_notional(equity: float, target_vol: float, ann_vol: float) -> float:
    """Notional whose annualised volatility equals ``target_vol`` of equity.

    notional * ann_vol = target_vol * equity  =>  notional = target_vol*equity/ann_vol
    """
    if ann_vol <= 0:
        return 0.0
    return target_vol * equity / ann_vol


def units_from_risk(equity: float, risk_fraction: float, stop_distance: float) -> float:
    """Units such that being stopped out loses exactly ``risk_fraction`` of equity.

    loss_if_stopped = units * stop_distance = risk_fraction * equity
    """
    if stop_distance <= 0:
        return 0.0
    return risk_fraction * equity / stop_distance
