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


def cornish_fisher_tau(skew: float, kurt_excess: float, z: float,
                       direction_long: bool) -> float:
    """Cornish-Fisher tail multiplier on the per-unit risk measure (W2, 2026-07-25;
    prereg engine/data_store/cf_cvar_prereg.md).

    ``z_CF = z' + (S/6)(z'²-1) + (K/24)(z'³-3z') - (S²/36)(2z'³-5z')`` evaluated at the
    ADVERSE tail for the trade's direction (``z' = -z`` for longs, ``+z`` for shorts),
    returned as ``|z_CF| / z``: 1.0 for a Gaussian (S=0, K=0), > 1 when the adverse
    tail is fatter than Gaussian. Raw, unclipped — the caller clips to the
    pre-registered [tau_min, tau_max] band and treats insufficient data as 1.0.
    """
    zp = -abs(z) if direction_long else abs(z)
    z_cf = (zp + (skew / 6.0) * (zp ** 2 - 1.0)
            + (kurt_excess / 24.0) * (zp ** 3 - 3.0 * zp)
            - (skew ** 2 / 36.0) * (2.0 * zp ** 3 - 5.0 * zp))
    return abs(z_cf) / abs(z)


def round_lot_size(
    volume: float,
    min_lot: float = 0.01,
    lot_step: float = 0.01,
) -> float:
    """Round ``volume`` DOWN to the nearest valid broker lot step.

    MT4 requires order volumes in exact multiples of ``lot_step`` (0.01 for most
    forex brokers). A raw sizer output such as 0.0734 must be floored to 0.07 —
    not rounded, because rounding UP could cause oversizing. If the floored value
    is below the broker's ``min_lot`` (typically 0.01), the order cannot be placed
    and this function returns 0.0 as a sentinel for "do not trade".

    Args:
        volume:   Raw computed volume in lots (e.g. 0.0734).
        min_lot:  Minimum tradeable lot size for the broker/instrument.
        lot_step: Increment between valid lot sizes (0.01 = micro-lot steps).

    Returns:
        Rounded volume in lots, or 0.0 if below minimum (don't trade).
    """
    if volume <= 0 or lot_step <= 0:
        return 0.0
    # Floor to the nearest lot_step
    rounded = float(int(volume / lot_step)) * lot_step
    # Guard precision drift (e.g. 0.07000000000000001 → 0.07)
    rounded = round(rounded, 10)
    # Below minimum → signal caller to skip this order
    if rounded < min_lot - 1e-9:
        return 0.0
    return round(rounded, 2)

