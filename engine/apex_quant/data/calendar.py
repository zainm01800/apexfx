"""Session calendar: ONE session->date convention per asset class (D-C1 root fix).

THE CONVENTION
--------------
All bars in the engine are **open-time labelled** (see schema.py). Day-based
bars are then pinned to 00:00 UTC of their *session date*:

* **Forex daily** -- one bar per 17:00-New-York -> 17:00-New-York session,
  labelled 00:00 UTC of the session date, where the session date is the New
  York calendar date on which the session *closes* (equivalently: the UTC
  date containing the bulk of the session). The session that opens Sunday
  17:00 NY -- the first of the trading week -- is labelled **Monday**. Forex
  daily bars therefore land Mon-Fri only. There is **no Saturday or Sunday
  bar, ever**: a feed row dated Sat/Sun is either the Sunday-open session
  (remapped to Monday) or vendor junk (rejected on write).
* **Forex weekly** -- the Mon-Fri trading week, labelled 00:00 UTC of its
  Monday session date. The same NY-date+1 mapping turns OANDA's Sunday-
  labelled W candles into Monday labels.
* **Forex intraday** -- the trading week runs Sun 21:00 UTC -> Fri 22:00 UTC
  (17:00 NY, DST shifts the UTC hour). Rows outside that window (all of
  Saturday, Sunday before 21:00 UTC, Friday at/after 22:00 UTC) are vendor
  junk and rejected on write.
* **Crypto** -- trades 24/7: daily bars keep a Mon-Sun calendar (Yahoo
  crypto daily has known holes, e.g. BTC_USD ~149 missing days over
  2016-2026; documented, never fabricated).
* **Equity/ETF** -- exchange days, Mon-Fri.

Feeds disagree on labelling (OANDA pins D/W candles to the 17:00 NY open,
Yahoo to 00:00 UTC), so the mapping below normalises every label through the
New York calendar date of the bar's open plus one day. That rule is the
identity for Mon-Fri midnight-UTC labels, maps the Sunday-open session to
Monday, and pushes vendor weekend junk onto Sat/Sun dates where the calendar
validation rejects it.
"""

from __future__ import annotations

import re

import pandas as pd

_NY = "America/New_York"

# Fiat (and metals) codes: a "XXX/YYY" pair of these trades forex sessions.
_FIAT = {"EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "XAU", "XAG"}

_DAY_TIMEFRAMES = {"1d", "1w"}
_TF_RE = re.compile(r"^(\d+)([mMhHdDwW])$")
_TF_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def asset_class_for(instrument: str) -> str:
    """Classify an instrument id (slash or store-slug form) as
    'forex' | 'crypto' | 'equity'. Pure symbol-shape heuristic: both legs
    fiat -> forex; a known crypto base -> crypto; otherwise equity."""
    from apex_quant.config import CRYPTO_BASES  # local import: config -> data cycle

    sym = instrument.upper().replace("-", "/")
    if "/" not in sym and "_" in sym:
        parts = sym.split("_")
        # store slugs join BASE_QUOTE with "_"; provider-prefixed slugs
        # (BINANCE_BTC_USD) classify on the base token.
        if len(parts) == 2:
            sym = f"{parts[0]}/{parts[1]}"
        elif len(parts) > 2 and parts[1] in CRYPTO_BASES:
            return "crypto"
    if "/" in sym:
        base, _, quote = sym.partition("/")
        if base in _FIAT and quote in _FIAT:
            return "forex"
        if base in CRYPTO_BASES:
            return "crypto"
        return "forex" if quote in _FIAT else "equity"
    return "equity"


def _utc_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def session_dates(idx: pd.DatetimeIndex, instrument: str, timeframe: str) -> pd.DatetimeIndex:
    """Map bar-open timestamps to 00:00 UTC session-date labels (day TFs only).

    forex: NY calendar date of the open, + 1 day (see module docstring);
    other classes: the UTC calendar date. Intraday frames pass through.
    Idempotent: a session-dated bar (00:00 UTC Mon-Fri) maps to itself.
    """
    idx = _utc_index(idx)
    if timeframe not in _DAY_TIMEFRAMES or len(idx) == 0:
        return idx
    if asset_class_for(instrument) == "forex":
        ny = idx.tz_convert(_NY)
        mapped = pd.to_datetime((ny + pd.Timedelta(days=1)).date)
        return pd.DatetimeIndex(mapped, tz="UTC")
    return idx.normalize()


def session_normalize(df: pd.DataFrame, instrument: str, timeframe: str) -> pd.DataFrame:
    """Return a copy of ``df`` with its index replaced by session-date labels."""
    if timeframe not in _DAY_TIMEFRAMES or df.empty:
        return df
    out = df.copy()
    out.index = pd.DatetimeIndex(
        session_dates(out.index, instrument, timeframe), name=out.index.name
    )
    return out


def off_calendar_mask(
    idx: pd.DatetimeIndex,
    instrument: str,
    timeframe: str,
    asset_class: str | None = None,
) -> "pd.Series":
    """Boolean Series (indexed like ``idx``) marking rows that violate the
    asset-class session calendar. The store rejects such rows on write;
    the quality checker counts them as *surplus* bars."""
    idx = _utc_index(idx)
    ac = asset_class or asset_class_for(instrument)
    dow = pd.Series(idx.dayofweek, index=idx)
    if ac == "crypto":
        return pd.Series(False, index=idx)  # 24/7 calendar
    if timeframe == "1w":
        if ac == "forex":
            return dow != 0  # Monday-labelled weeks only
        return dow > 4
    if timeframe == "1d":
        return dow > 4  # Mon-Fri for forex and equity
    # intraday
    if ac == "forex":
        hour = pd.Series(idx.hour, index=idx)
        # week runs Sun 21:00 UTC -> Fri 22:00 UTC (17:00 NY, DST-shifted)
        return (dow == 5) | ((dow == 6) & (hour < 21)) | ((dow == 4) & (hour >= 22))
    return dow > 4  # equity intraday: exchange weekdays only


def timeframe_seconds(timeframe: str) -> int | None:
    """Bar length in seconds for m/h/d/w timeframes (None if unparseable)."""
    m = _TF_RE.match(timeframe)
    if not m:
        return None
    return int(m.group(1)) * _TF_SECONDS[m.group(2).lower()]


def bar_close_utc(ts: pd.Timestamp, instrument: str, timeframe: str) -> pd.Timestamp | None:
    """When the bar labelled ``ts`` (open time) is complete, in UTC.

    forex 1d: the session date's 17:00 NY close; forex 1w: Friday 17:00 NY of
    the labelled week; other day bars: label + 24h / 7d; intraday: +bar length.
    """
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    ac = asset_class_for(instrument)
    if timeframe == "1d" and ac == "forex":
        ny_midnight = ts.tz_localize(None).tz_localize(_NY)  # 00:00 NY of session date
        return (ny_midnight + pd.Timedelta(hours=17)).tz_convert("UTC")
    if timeframe == "1w" and ac == "forex":
        friday_ny = ts.tz_localize(None).tz_localize(_NY) + pd.Timedelta(days=4)
        return (friday_ny + pd.Timedelta(hours=17)).tz_convert("UTC")
    secs = timeframe_seconds(timeframe)
    if secs is None:
        return None
    return ts + pd.Timedelta(seconds=secs)


def trim_forming_tail(
    df: pd.DataFrame,
    instrument: str,
    timeframe: str,
    now: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Drop still-forming bars off the END of a frame (D-H2).

    A bar is forming while ``now < bar_close_utc``. Only the tail is ever
    affected -- historical bars are complete by definition -- so this loops
    from the end and stops at the first complete bar. Used by
    ``ParquetStore.get_or_fetch`` so a still-forming terminal bar is never
    persisted (nor handed back to callers).
    """
    if df.empty:
        return df
    now = pd.Timestamp.utcnow() if now is None else pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    out = df
    while len(out):
        close = bar_close_utc(out.index[-1], instrument, timeframe)
        if close is None or now >= close:
            break
        out = out.iloc[:-1]
    return out
