"""Quota circuit-breaker for Supabase.

When Supabase restricts a project it answers **402 exceed_egress_quota** to every request.
The engine's sync daemon previously retried on a 5-second loop regardless, so a quota block
turned into a permanent request storm: hundreds of rejected calls per hour, each one still
billed as egress, keeping the project pinned in the restricted state it was trying to escape.

This trips a breaker on the first 402/429 and stops issuing requests for a cooldown, so a
quota block decays instead of self-reinforcing. It is deliberately module-level state: every
call site in the process shares one breaker, because the quota is per-project, not per-caller.
"""

from __future__ import annotations

import threading
import time

#: HTTP codes that mean "the project is rate/quota limited", not "this request was wrong".
QUOTA_CODES = frozenset({402, 429})

_lock = threading.Lock()
_blocked_until: float = 0.0
_trip_count: int = 0
_suppressed: int = 0


def note_response(status_code: int, cooldown_s: float = 900.0) -> bool:
    """Record a response. Returns True if it tripped the breaker.

    Cooldown doubles per consecutive trip (capped at 4h) so a persistent block backs off
    instead of hammering every cooldown expiry.
    """
    global _blocked_until, _trip_count
    if status_code not in QUOTA_CODES:
        if status_code < 400:
            with _lock:
                _trip_count = 0          # healthy response clears the escalation
        return False
    with _lock:
        _trip_count += 1
        backoff = min(cooldown_s * (2 ** (_trip_count - 1)), 4 * 3600)
        _blocked_until = max(_blocked_until, time.time() + backoff)
    return True


def is_blocked() -> bool:
    """True while the breaker is open — callers must skip the request entirely."""
    global _suppressed
    with _lock:
        if time.time() < _blocked_until:
            _suppressed += 1
            return True
    return False


def status() -> dict:
    with _lock:
        remaining = max(0.0, _blocked_until - time.time())
    return {
        "blocked": remaining > 0,
        "seconds_remaining": round(remaining),
        "trips": _trip_count,
        "requests_suppressed": _suppressed,
    }


def describe() -> str:
    s = status()
    if not s["blocked"]:
        return "supabase: ok"
    return (f"supabase: QUOTA-BLOCKED for another {s['seconds_remaining']}s "
            f"(trip {s['trips']}, {s['requests_suppressed']} requests suppressed)")


def reset() -> None:
    """Clear the breaker — for tests, or after the quota is known to be restored."""
    global _blocked_until, _trip_count, _suppressed
    with _lock:
        _blocked_until = 0.0
        _trip_count = 0
        _suppressed = 0
