"""Turning closed trades into honest evidence for the Bayesian sizer.

The sizer models P(target hit before stop) with payoff b = reward_risk, so a "win"
must mean **the setup was right** — not that we happened to book a few pounds by
bailing out early, and not merely that we held on long enough for a barrier to be
touched.

Why this module exists
----------------------
The live loop used to learn from ``outcome=in.(tp_hit,sl_hit)`` only — the ~25% of
trades that ran all the way to a barrier. Every managed or expired exit (about 75%
of the book) was invisible to it. Learning from just the quarter that resolved
cleanly is textbook **survivorship bias**, and it cost real money: the sizer learned
a 49.4% win rate against a realised 36.8%, which pushed fractional Kelly positive,
clamped every trade to the 2% ``max_risk``, and turned a tight ATR stop into 10-18
lot positions on a 100k account.

The repair is not to guess at those trades but to **ask the market**. The post-exit
hindsight rescan (``scripts/update_lessons.check_hindsight_trajectory``) waits a
timeframe-appropriate number of bars after an exit and reports whether the setup
would have reached its target or its stop had it been left alone. That converts a
bailed-out trade from a blind spot back into a data point.

Trades the market has not answered yet resolve to ``None`` — deliberately. Recording
a guess is how you poison a posterior; the old rule booked every one of them as a
loss.
"""

from __future__ import annotations


def _hindsight(trade: dict) -> str:
    feats = trade.get("setup_features") or {}
    if not isinstance(feats, dict):
        return ""
    return str(feats.get("hindsight_outcome") or "").lower().strip()


def resolve_learning_outcome(trade: dict) -> bool | None:
    """Resolve a closed setup to a clean win/loss for the sizer's posterior.

    Returns
    -------
    True  — the setup reached its target (directly, or would have per hindsight).
    False — the setup reached its stop (directly, or would have per hindsight).
    None  — the market has not answered yet. NOT a loss; simply no information.
    """
    outcome = str(trade.get("outcome") or "").lower().strip()
    # The market answered directly — a later scan cannot override it.
    if outcome == "tp_hit":
        return True
    if outcome == "sl_hit":
        return False

    # We exited before the market answered; let the rescan answer it.
    h = _hindsight(trade)
    if h == "tp_hit":
        return True
    if h == "sl_hit":
        return False
    return None


def exit_decision_quality(trade: dict) -> str | None:
    """Was bailing out early the right call? Managed/expired exits only.

    Returns
    -------
    "good"      — hindsight says it would have hit the STOP: the exit saved money.
    "premature" — hindsight says it would have hit the TARGET: the exit cost a win.
    None        — it resolved on its own (there was no decision to judge), or the
                  market has not answered yet.
    """
    outcome = str(trade.get("outcome") or "").lower().strip()
    if outcome in ("tp_hit", "sl_hit"):
        return None  # the barrier decided, not us — nothing to grade

    h = _hindsight(trade)
    if h == "sl_hit":
        return "good"
    if h == "tp_hit":
        return "premature"
    return None
