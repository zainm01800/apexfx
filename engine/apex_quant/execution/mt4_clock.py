"""Broker-clock normalisation for MT4 timestamps.

MT4's ``OrderOpenTime()`` / ``OrderCloseTime()`` return the BROKER's server clock —
typically UTC+2 in winter and UTC+3 under DST — but store it as though it were a unix
epoch. Anything comparing those numbers to a real UTC timestamp is therefore skewed by
hours. Measured on the live account: ``open_time`` runs ~+3h ahead of real UTC, so a
freshly opened trade appears to have opened three hours in the future.

That skew leaked into ``outcome_date``, which the post-exit hindsight rescan uses to
decide where to start scanning price — so the hindsight verdicts were computed over a
shifted window, and those verdicts feed the Bayesian sizer's learning.

Why the offset is configured rather than detected
-------------------------------------------------
The only thing observable from trade data is ``newest_open_time - utc_now``, which
equals ``offset - age_of_newest_trade``. It can only ever UNDER-state the offset: on
live data, a 30-minute-old trade on a UTC+3 broker reported just 2.5h. Snapping that
to the nearest half hour produces a confidently wrong answer, and a wrong offset
silently corrupts what the sizer learns — so a declared constant beats a guess.

That same quantity is, however, a sound **one-sided alarm**: because it can only
under-state, anything above the configured value proves the configuration wrong.

The real fix is for the EA to report ``TimeCurrent()`` so the offset can be computed
exactly. Until then this is a declared constant that must be updated at DST.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from apex_quant.config import get_config

logger = logging.getLogger("apex_quant.mt4_clock")


def observed_min_broker_offset(mt4_history: list | None) -> float:
    """A LOWER BOUND on the broker clock's offset from UTC, in seconds.

    A trade cannot open in the future, so ``newest_open_time - utc_now`` under-states
    the true offset by the age of the newest trade. Useless as an estimator; ideal as
    a one-sided check. Returns 0.0 when there is no evidence either way.
    """
    if not mt4_history:
        return 0.0
    try:
        newest = max(float(h.get("open_time") or 0.0) for h in mt4_history)
    except (ValueError, TypeError, AttributeError):
        return 0.0
    if newest <= 0:
        return 0.0
    return max(0.0, newest - datetime.now(timezone.utc).timestamp())


_LIVE_OFFSET_STATE: tuple[float, float] | None = None
_EXPIRY_LOGGED = False


def set_live_broker_offset(offset_seconds: float | None) -> None:
    """Set the live-reported broker clock offset dynamically from heartbeat data."""
    global _LIVE_OFFSET_STATE, _EXPIRY_LOGGED
    if offset_seconds is None:
        _LIVE_OFFSET_STATE = None
    else:
        _LIVE_OFFSET_STATE = (float(offset_seconds), time.monotonic())
        _EXPIRY_LOGGED = False


def mt4_utc_offset_seconds(mt4_history: list | None = None, max_age_s: float = 300.0) -> float:
    """The broker server-clock offset vs real UTC, in seconds, preferring live-reported.
    
    Falls back to config if the live offset is missing or older than max_age_s.
    Keeps the one-sided under-read alarm.
    """
    global _EXPIRY_LOGGED
    cfg_offset = float(
        getattr(get_config().execution.mt4, "server_utc_offset_hours", 0.0)
    ) * 3600.0
    
    offset = cfg_offset
    if _LIVE_OFFSET_STATE is not None:
        live_offset, received_at = _LIVE_OFFSET_STATE
        if time.monotonic() - received_at <= max_age_s:
            offset = live_offset
        else:
            if not _EXPIRY_LOGGED:
                logger.warning("live broker offset stale, falling back to config")
                _EXPIRY_LOGGED = True
    
    if mt4_history:
        floor = observed_min_broker_offset(mt4_history)
        if floor > offset + 900:  # 15-min grace; the bound can only under-state
            logger.warning(
                "MT4 CLOCK: broker timestamps are at least %.2fh ahead of UTC but "
                "offset in use is %.2f. Hindsight scan windows "
                "will be wrong — update config or verify EA connection.",
                floor / 3600.0, offset / 3600.0,
            )
    return offset


def broker_epoch_to_utc(broker_epoch: float, mt4_history: list | None = None) -> datetime:
    """Convert a raw MT4 timestamp into a genuinely tz-aware UTC instant.

    Replaces the original ``datetime.fromtimestamp(x).isoformat() + "Z"``, which had
    two stacked faults: it treated a broker epoch as UTC, and ``fromtimestamp`` without
    ``tz`` renders in the LOCAL machine timezone — which the appended "Z" then falsely
    declared to be UTC.
    """
    return datetime.fromtimestamp(
        float(broker_epoch) - mt4_utc_offset_seconds(mt4_history), tz=timezone.utc
    )
