"""Fail-closed data/state checks shared by legacy paper runners."""
from __future__ import annotations

import pandas as pd
import exchange_calendars as xcals
from apex_quant.config import CRYPTO_BASES


def utc(t):
    stamp = pd.Timestamp(t)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def require_daily_panel(panel, instruments, cutoff):
    """Every pinned symbol needs its latest expected day strictly before cutoff.

    US/UK exchange holidays are respected; crypto is 7-day and FX is weekday.
    This validates freshness, not the underlying provider's execution quality.
    """
    end = utc(cutoff).normalize() - pd.Timedelta(days=1)
    for sym in instruments:
        frame = panel.get(sym)
        if frame is None or frame.empty:
            raise ValueError(f"{sym}: missing required paper input")
        if "/" in sym and sym.split("/")[0] in CRYPTO_BASES:
            expected = end
        elif "/" in sym:
            expected = end
            while expected.weekday() >= 5:
                expected -= pd.Timedelta(days=1)
        else:
            cal = xcals.get_calendar("XLON" if sym.endswith(".L") else "XNYS")
            sessions = cal.sessions_in_range((end-pd.Timedelta(days=14)).tz_localize(None), end.tz_localize(None))
            expected = utc(sessions[-1])
        dates = pd.to_datetime(frame.index, utc=True).normalize()
        if expected not in dates:
            raise ValueError(f"{sym}: stale/missing settled session {expected.date()}; latest {dates.max().date()}")


def require_hourly_panel(panel, instruments, now):
    # Yahoo hourly timestamps are period starts. Only a completed hour is eligible.
    expected = (utc(now)-pd.Timedelta(minutes=2)).floor("h") - pd.Timedelta(hours=1)
    for _ in range(72):
        ny = expected.tz_convert("America/New_York")
        if not (ny.weekday()==5 or (ny.weekday()==4 and ny.hour>=17) or (ny.weekday()==6 and ny.hour<17)):
            break
        expected -= pd.Timedelta(hours=1)
    for sym in instruments:
        frame = panel.get(sym)
        if frame is None or frame.empty or expected not in pd.to_datetime(frame.index, utc=True):
            raise ValueError(f"{sym}: missing completed FX hour {expected.isoformat()}")
    return expected


def require_restored_state(state, *, initialize=False, no_remote=False, state_path=None, original_path=None):
    if state is not None:
        if initialize:
            raise ValueError("Initialization cannot overwrite an existing ledger")
        return
    if not initialize:
        raise ValueError("Authoritative state missing; refusing automatic historical reseed")
    if not no_remote or state_path is None or state_path.resolve() == original_path.resolve():
        raise ValueError("Repaired initialization requires --no-supabase and a new, separate --state path")
