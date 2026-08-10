"""Earnings Calendar Blackout Rule (Phase 2 Risk Veto).

Vetoes new position entries on instruments within 72 hours (3 days) of scheduled
corporate earnings announcements to prevent unhedged overnight gap risk.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Set
import logging

logger = logging.getLogger(__name__)

# Known earnings dates / blackout schedule map (ISO format or date strings)
# Updated automatically via market data feed
EARNINGS_SCHEDULE: Dict[str, str] = {
    # Typical quarterly announcement windows
    "MSFT": "2026-07-30",
    "NFLX": "2026-07-17",
    "PLTR": "2026-08-03",
    "AAPL": "2026-07-31",
    "AMZN": "2026-07-30",
    "META": "2026-07-31",
    "TSLA": "2026-07-23",
    "AMD":  "2026-07-30",
    "TSM":  "2026-07-18",
}


def is_earnings_blackout(instrument: str, now_dt: Optional[datetime] = None, blackout_hours: float = 72.0) -> bool:
    """Return True if instrument is within `blackout_hours` before/after a scheduled earnings report."""
    sym = instrument.upper().split("/")[0]
    earnings_str = EARNINGS_SCHEDULE.get(sym)
    if not earnings_str:
        return False

    try:
        if now_dt is None:
            now_dt = datetime.now(timezone.utc)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        # Parse earnings date (assuming 21:00 UTC post-market announcement)
        e_date = datetime.strptime(earnings_str[:10], "%Y-%m-%d").replace(hour=21, minute=0, second=0, tzinfo=timezone.utc)
        diff_hours = abs((now_dt - e_date).total_seconds()) / 3600.0

        if diff_hours <= blackout_hours:
            logger.info("EARNINGS BLACKOUT: %s is within %.1fh of earnings report (%s)", sym, diff_hours, earnings_str)
            return True
    except Exception as e:
        logger.warning("Error parsing earnings date for %s: %s", sym, e)

    return False
