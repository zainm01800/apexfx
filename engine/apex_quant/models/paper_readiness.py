"""Fail-closed data/state checks shared by legacy paper runners."""
from __future__ import annotations

import pandas as pd
import exchange_calendars as xcals
from apex_quant.config import CRYPTO_BASES


def utc(t):
    stamp = pd.Timestamp(t)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


class PaperInputError(ValueError):
    """A blocked input with public, credential-free remediation details."""

    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def daily_sessions(instrument, start, end):
    """Expected labels, without imputing a price on an exchange holiday."""
    start, end = utc(start).normalize(), utc(end).normalize()
    if start > end:
        return pd.DatetimeIndex([], tz="UTC")
    if "/" in instrument and instrument.split("/")[0] in CRYPTO_BASES:
        return pd.date_range(start, end, freq="D")
    if "/" in instrument:
        return pd.date_range(start, end, freq="B")
    cal = xcals.get_calendar("XLON" if instrument.endswith(".L") else "XNYS")
    labels = cal.sessions_in_range(start.tz_localize(None), end.tz_localize(None))
    return pd.DatetimeIndex(pd.to_datetime(labels, utc=True))


def require_daily_panel(panel, instruments, cutoff, *, after=None):
    """Every pinned symbol needs its latest expected day strictly before cutoff.

    US/UK exchange holidays are respected; crypto is 7-day and FX is weekday.
    With ``after``, every unprocessed expected session is required as well as
    the latest one. A fresh terminal bar cannot hide an earlier provider hole.
    This validates freshness, not the underlying provider's execution quality.
    """
    end = utc(cutoff).normalize() - pd.Timedelta(days=1)
    for sym in instruments:
        frame = panel.get(sym)
        if frame is None or frame.empty:
            raise PaperInputError(f"{sym}: missing required paper input",
                                  instrument=sym, timeframe="1d", kind="missing_input")
        expected = daily_sessions(sym, end-pd.Timedelta(days=14), end)[-1]
        dates = pd.to_datetime(frame.index, utc=True).normalize()
        required = pd.DatetimeIndex([expected])
        if after is not None:
            required = required.union(daily_sessions(sym, utc(after)+pd.Timedelta(days=1), end))
        missing = required.difference(dates)
        if len(missing):
            labels = [str(day.date()) for day in missing]
            raise PaperInputError(
                f"{sym}: stale/missing settled session(s) {', '.join(labels)}; latest {dates.max().date()}",
                instrument=sym, timeframe="1d", kind="missing_settled_sessions",
                missing_sessions=labels, latest_available_session=str(dates.max().date()),
                action="Retrieve the missing original daily bars; do not impute, skip, or reseed.",
            )


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
