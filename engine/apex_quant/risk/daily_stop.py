"""Daily-loss stop: session anchoring and enforcement.

Prop firms measure their daily-loss rule from the session's OPENING equity. The drawdown
breaker measures from PEAK, so a losing day that begins at a fresh high shows ~0 drawdown
while blowing the daily rule — the account is gone and the breaker never fired.

This lives in the risk package rather than in the live script because it is safety-critical
and must be importable without side effects. The live script cannot be imported in a test:
it builds an executor and mutates global config at import time.

The load-bearing design decision is **persistence**. The live loop runs every ~900s and the
process can restart mid-session. An in-memory anchor would re-anchor at the already-down
equity after a restart, measure zero daily loss, and resume trading on exactly the day the
stop exists for. That is the failure mode this module exists to prevent — not "the stop
didn't fire".
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def session_key(now: datetime | None = None) -> str:
    """UTC date string identifying the trading session."""
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def read_anchor(path: Path, now: datetime | None = None) -> float | None:
    """Stored opening equity for the CURRENT session, or None.

    Returns None for a missing file, a stale (previous-day) anchor, corrupt JSON, or a
    non-positive value — every one of which means "no valid anchor for today".
    """
    try:
        if not path.exists():
            return None
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored.get("date") != session_key(now):
            return None                      # stale: yesterday's anchor must never be reused
        eq = float(stored.get("equity", 0.0))
        return eq if eq > 0 else None
    except Exception:                        # noqa: BLE001 - corrupt file == no anchor
        return None


def write_anchor(path: Path, equity: float, now: datetime | None = None) -> bool:
    """Persist ``equity`` as this session's opening equity. False on any I/O failure."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": session_key(now), "equity": float(equity)}),
                        encoding="utf-8")
        return True
    except Exception:                        # noqa: BLE001
        return False


def resolve_anchor(path: Path, live_equity: float, now: datetime | None = None) -> float:
    """This session's opening equity: the stored one, or ``live_equity`` (anchoring it).

    Never raises. On I/O failure it returns ``live_equity``, which yields a measured loss of
    zero — i.e. the check is DISABLED rather than blocking all trading on a disk error.
    """
    existing = read_anchor(path, now)
    if existing is not None:
        return existing
    write_anchor(path, live_equity, now)
    return live_equity


def daily_loss(anchor_equity: float, live_equity: float) -> float:
    """Fractional loss since the session open, in [0, 1). Zero when flat or up."""
    if anchor_equity <= 0:
        return 0.0
    return max(0.0, 1.0 - live_equity / anchor_equity)


def breached(anchor_equity: float, live_equity: float, limit: float) -> bool:
    """True when the session's loss has reached ``limit`` (0.0 disables the check)."""
    if limit <= 0.0:
        return False
    return daily_loss(anchor_equity, live_equity) >= limit
