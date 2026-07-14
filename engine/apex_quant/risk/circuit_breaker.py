"""Three-state drawdown circuit-breaker: ACTIVE / REDUCING / HALTED.

Inspired by NautilusTrader's RiskEngine which separates a "warning zone"
(reduce exposure, no new positions) from a full halt. This gives the engine a
graceful degradation path instead of an abrupt binary on/off switch.

States
------
ACTIVE   — drawdown < reducing_threshold: normal operation, full sizing.
REDUCING — reducing_threshold <= drawdown < halted_threshold:
             new positions are BLOCKED (same as halted for entries) but the
             engine flags this state so dashboards and logs can show an amber
             warning. Existing positions remain managed by their stops.
HALTED   — drawdown >= halted_threshold: hard block, same as current behaviour.

Why three states?
-----------------
With only two states (active/halted) a strategy either runs at 100 % or stops
completely. Real prop firms often operate a "soft limit" zone where a trader
can close losers but not add new risk. This mirrors that model.

The reducing_threshold should be set at ~50-60 % of the halted threshold so
the REDUCING window is meaningful. Defaults:
  reducing_threshold: 0.10  (10 % drawdown → start warning)
  halted_threshold:   0.20  (20 % drawdown → hard halt)
"""

from __future__ import annotations

from enum import Enum

from apex_quant.risk.types import AccountState


class BreakerState(str, Enum):
    """Three-state circuit-breaker status."""
    ACTIVE   = "ACTIVE"    # Normal operation — full sizing allowed.
    REDUCING = "REDUCING"  # Amber alert — no NEW entries, close losers only.
    HALTED   = "HALTED"    # Hard halt — no new positions under any circumstance.


def drawdown(account: AccountState) -> float:
    """Return the current drawdown fraction (0.0 → 1.0)."""
    return account.drawdown


def breaker_state(
    account: AccountState,
    halted_threshold: float,
    reducing_threshold: float | None = None,
    *,
    eps: float = 1e-9,
) -> BreakerState:
    """Return the current BreakerState given drawdown thresholds.

    Args:
        account:             Current account state (carries .drawdown).
        halted_threshold:    Drawdown at which ALL new positions are blocked
                             (hard limit — maps to ``drawdown_breaker`` in config).
        reducing_threshold:  Drawdown at which REDUCING mode is activated. If
                             None, defaults to 50 % of ``halted_threshold``,
                             giving a sensible amber zone out-of-the-box.
        eps:                 Float tolerance so boundary values trip correctly.

    Returns:
        BreakerState enum value.
    """
    if reducing_threshold is None:
        reducing_threshold = halted_threshold * 0.5

    dd = account.drawdown

    if dd >= halted_threshold - eps:
        return BreakerState.HALTED
    if dd >= reducing_threshold - eps:
        return BreakerState.REDUCING
    return BreakerState.ACTIVE


def breaker_tripped(account: AccountState, threshold: float, *, eps: float = 1e-9) -> bool:
    """Backward-compatible helper — True when drawdown >= threshold.

    Used by existing code that only cares about the binary HALTED condition.
    All existing callers continue to work without modification.
    """
    return account.drawdown >= threshold - eps
