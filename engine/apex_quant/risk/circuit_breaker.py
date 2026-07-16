"""Three-state drawdown circuit-breaker: ACTIVE / REDUCING / HALTED.

Inspired by NautilusTrader's RiskEngine which separates a "warning zone"
(reduce exposure, no new positions) from a full halt. This gives the engine a
graceful degradation path instead of an abrupt binary on/off switch.

States
------
ACTIVE   — drawdown < reducing_threshold: normal operation, full sizing.
REDUCING — reducing_threshold <= drawdown < halted_threshold: new entries are
             still allowed but PROGRESSIVELY SMALLER — size ramps linearly from
             100 % at the amber edge to 0 % at the halt (see `reducing_scale`).
HALTED   — drawdown >= halted_threshold: hard block on all new positions.

Why three states?
-----------------
With only two states (active/halted) a strategy either runs at 100 % or stops
completely. The amber zone de-risks *gradually* as the account bleeds, so the
book shrinks toward zero rather than slamming off at a threshold.

Why REDUCING scales size rather than blocking entries
-----------------------------------------------------
It originally BLOCKED every new entry, which was a bug with two faces:

  1. It made ``reducing_threshold`` a second, secret hard halt — the effective
     breaker became 10 %, not the 20 % configured — while claiming to offer
     "graceful degradation instead of an abrupt binary on/off switch". Blocking
     all entries at 10 % IS an abrupt binary switch, just at a lower number.
  2. It deadlocked. The intent was "allow trades that reduce exposure", but the
     engine only ever generates a signal for an instrument it is FLAT on, so
     there was never an existing position to reduce — every entry was vetoed.
     With no entries the book goes flat, equity stops moving, drawdown never
     recovers below the threshold, and the engine is frozen permanently. In a
     22-pair backtest this fired 44,624 times and cut trades from 578 to 79.

Closing/reducing an existing position is the exit path's job (stops, targets,
TradeManager) — not the entry risk manager's, which only ever sizes NEW
positions. So the honest semantic for this layer is "bet smaller as you bleed".

If you want trading to stop at 10 %, set ``drawdown_breaker: 0.10``. Each knob
should do what its name says.

Defaults:
  reducing_threshold: 0.10  (10 % drawdown → begin de-risking)
  halted_threshold:   0.20  (20 % drawdown → hard halt)
"""

from __future__ import annotations

from enum import Enum

from apex_quant.risk.types import AccountState


class BreakerState(str, Enum):
    """Three-state circuit-breaker status."""
    ACTIVE   = "ACTIVE"    # Normal operation — full sizing allowed.
    REDUCING = "REDUCING"  # Amber alert — entries allowed at progressively smaller size.
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


def reducing_scale(
    account: AccountState,
    halted_threshold: float,
    reducing_threshold: float | None = None,
    *,
    eps: float = 1e-9,
) -> float:
    """Position-size multiplier for the drawdown ramp, in [0, 1].

    1.0 while ACTIVE, then a linear ramp down through the amber zone — 1.0 at
    ``reducing_threshold``, 0.0 at ``halted_threshold`` — so the book de-risks
    smoothly as the account bleeds instead of trading full size right up to a
    cliff. Returns 0.0 once HALTED.

    This is what makes REDUCING a real state rather than a second hard halt: the
    engine keeps taking (ever smaller) positions, so equity can still move and the
    drawdown can recover back out of the amber zone.
    """
    if reducing_threshold is None:
        reducing_threshold = halted_threshold * 0.5
    dd = account.drawdown
    # Same eps as breaker_state() — otherwise the two disagree on the boundary
    # (1 - 80000/100000 == 0.19999999999999996, which misses a bare >= 0.20).
    if dd >= halted_threshold - eps:
        return 0.0
    if dd < reducing_threshold - eps:
        return 1.0
    span = halted_threshold - reducing_threshold
    if span <= 0:
        return 0.0
    return float(min(1.0, max(0.0, 1.0 - (dd - reducing_threshold) / span)))


def breaker_tripped(account: AccountState, threshold: float, *, eps: float = 1e-9) -> bool:
    """Backward-compatible helper — True when drawdown >= threshold.

    Used by existing code that only cares about the binary HALTED condition.
    All existing callers continue to work without modification.
    """
    return account.drawdown >= threshold - eps
