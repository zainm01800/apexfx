"""Drawdown circuit-breaker.

Once equity falls a configured fraction below its peak, the breaker halts ALL
new positions (existing positions are managed by their stops). This caps the
tail of a losing streak and buys time to diagnose whether the edge has decayed -
a hard, non-overridable rule, not a suggestion.
"""

from __future__ import annotations

from apex_quant.risk.types import AccountState


def drawdown(account: AccountState) -> float:
    return account.drawdown


def breaker_tripped(account: AccountState, threshold: float, *, eps: float = 1e-9) -> bool:
    """True when current drawdown >= threshold => block new positions.

    The ``eps`` tolerance ensures an account computed to be *at* the nominal
    threshold trips reliably despite float error (e.g. 1 - 80000/100000 evaluates
    to 0.19999999999999996, which must still trip a 20% breaker). For a safety
    limit, firing at-or-past the threshold is the conservative, correct choice."""
    return account.drawdown >= threshold - eps
