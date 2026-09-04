"""Causal fresh inputs for the V14 forward-paper books.

Equities deliberately use the same yfinance adjusted-OHLC request definition as
the sealed V14 research.  Cboe VIX is lagged by at least one observation date,
and the Bank of England XUDLUSS reference is eligible only after its row-level
``LAST_UPDATED`` time.  Nothing in this module writes a cache or portfolio state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Mapping
from urllib.parse import urlencode
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from .spec import BookSpec, SYMBOLS


XNYS = xcals.get_calendar("XNYS")
PRICE_COLUMNS = ("open", "high", "low", "close")
OHLC_RELATIVE_TOLERANCE = 1e-12
MINIMUM_HISTORY_SESSIONS = 260
SETTLEMENT_GRACE = pd.Timedelta(minutes=30)
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
BOE_XML_ENDPOINT = (
    "https://www.bankofengland.co.uk/boeapps/database/"
    "_iadb-fromshowcolumns.asp"
)


class DataUnavailable(RuntimeError):
    """A required settled, causal input is absent or stale."""


@dataclass(frozen=True, slots=True)
class MarketData:
    panel: dict[str, pd.DataFrame]
    vix: pd.DataFrame
    fx: pd.DataFrame
    latest_completed_session: pd.Timestamp
    retrieved_at_utc: pd.Timestamp
    provenance: dict


def utc_timestamp(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def session_label(value: Any) -> pd.Timestamp:
    """Return a timezone-naive XNYS session label."""

    return utc_timestamp(value).tz_localize(None).normalize()


def iso_date(value: Any) -> str:
    return session_label(value).strftime("%Y-%m-%d")


def session_open_utc(value: Any) -> pd.Timestamp:
    return pd.Timestamp(XNYS.session_open(session_label(value))).tz_convert("UTC")


def session_close_utc(value: Any) -> pd.Timestamp:
    return pd.Timestamp(XNYS.session_close(session_label(value))).tz_convert("UTC")


def next_session(value: Any, count: int = 1) -> pd.Timestamp:
    result = session_label(value)
    for _ in range(count):
        result = pd.Timestamp(XNYS.next_session(result)).normalize()
    return result


def latest_completed_session(now: Any) -> pd.Timestamp:
    now_utc = utc_timestamp(now)
    start = (now_utc - pd.Timedelta(days=14)).tz_localize(None).normalize()
    end = (now_utc + pd.Timedelta(days=1)).tz_localize(None).normalize()
    sessions = XNYS.sessions_in_range(start, end)
    completed = [
        pd.Timestamp(day).normalize()
        for day in sessions
        if session_close_utc(day) + SETTLEMENT_GRACE <= now_utc
    ]
    if not completed:
        raise DataUnavailable("no settled XNYS session is available")
    return completed[-1]


def _flatten_yfinance(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise DataUnavailable(f"{symbol}: adjusted daily download returned no data")
    if isinstance(frame.columns, pd.MultiIndex):
        for level in range(frame.columns.nlevels):
            labels = frame.columns.get_level_values(level).astype(str)
            if set(PRICE_COLUMNS).issubset({value.casefold() for value in labels}):
                output = frame.copy()
                output.columns = labels
                if output.columns.duplicated().any():
                    raise DataUnavailable(f"{symbol}: ambiguous adjusted OHLC columns")
                return output
        raise DataUnavailable(f"{symbol}: unsupported yfinance column layout")
    return frame.copy()


def normalize_symbol_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    raw = _flatten_yfinance(frame, symbol)
    by_name = {str(column).casefold(): column for column in raw.columns}
    missing = sorted(set(PRICE_COLUMNS) - set(by_name))
    if missing:
        raise DataUnavailable(f"{symbol}: missing OHLC columns: {', '.join(missing)}")
    dates = pd.DatetimeIndex(pd.to_datetime(raw.index, errors="coerce"))
    if dates.isna().any():
        raise DataUnavailable(f"{symbol}: invalid daily date")
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    output = pd.DataFrame(index=dates.normalize())
    for column in PRICE_COLUMNS:
        output[column] = pd.to_numeric(raw[by_name[column]].to_numpy(), errors="coerce")
    output = output.sort_index(kind="stable")
    if output.index.has_duplicates:
        raise DataUnavailable(f"{symbol}: duplicate daily session")
    values = output.loc[:, PRICE_COLUMNS].to_numpy(float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise DataUnavailable(f"{symbol}: non-finite or non-positive OHLC")
    tolerance = np.abs(values).max(axis=1) * OHLC_RELATIVE_TOLERANCE
    high_excess = values[:, [0, 2, 3]].max(axis=1) - values[:, 1]
    low_excess = values[:, 2] - values[:, [0, 1, 3]].min(axis=1)
    if (high_excess > tolerance).any() or (low_excess > tolerance).any():
        raise DataUnavailable(f"{symbol}: invalid OHLC bounds")
    return output.astype(float)


def validate_panel(
    raw: Mapping[str, pd.DataFrame], latest: Any
) -> dict[str, pd.DataFrame]:
    if set(raw) != set(SYMBOLS):
        raise DataUnavailable("adjusted panel must contain exactly the V14 eight-symbol universe")
    latest_label = session_label(latest)
    start = (latest_label - pd.Timedelta(days=500)).normalize()
    expected_all = pd.DatetimeIndex(XNYS.sessions_in_range(start, latest_label))
    expected = expected_all[-MINIMUM_HISTORY_SESSIONS:]
    if len(expected) < MINIMUM_HISTORY_SESSIONS:
        raise DataUnavailable("XNYS calendar did not provide enough indicator history")
    checked: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        frame = normalize_symbol_frame(raw[symbol], symbol)
        missing = expected.difference(frame.index)
        if len(missing):
            preview = ", ".join(iso_date(day) for day in missing[:3])
            raise DataUnavailable(f"{symbol}: missing settled XNYS session(s): {preview}")
        checked[symbol] = frame.loc[expected, list(PRICE_COLUMNS)].copy()
    return checked


def normalize_vix(frame: pd.DataFrame) -> pd.DataFrame:
    by_name = {str(column).casefold(): column for column in frame.columns}
    date_col = by_name.get("date")
    close_col = by_name.get("close")
    if date_col is None or close_col is None:
        raise DataUnavailable("Cboe VIX response lacks DATE/CLOSE")
    dates = pd.to_datetime(frame[date_col], errors="coerce", utc=True)
    close = pd.to_numeric(frame[close_col], errors="coerce")
    output = pd.DataFrame({"close": close.to_numpy()}, index=dates.dt.tz_localize(None).dt.normalize())
    output = output.loc[~output.index.isna()].sort_index(kind="stable")
    if output.empty or output.index.has_duplicates:
        raise DataUnavailable("Cboe VIX dates are empty or duplicated")
    if not np.isfinite(output["close"]).all() or (output["close"] <= 0).any():
        raise DataUnavailable("Cboe VIX contains an invalid close")
    return output.astype(float)


def normalize_boe_xml(content: bytes) -> pd.DataFrame:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise DataUnavailable("Bank of England XUDLUSS XML is not parseable") from exc
    rows = []
    london = ZoneInfo("Europe/London")
    for element in root.iter():
        attrs = element.attrib
        if not {"TIME", "OBS_VALUE", "LAST_UPDATED"}.issubset(attrs):
            continue
        try:
            observation = pd.Timestamp(attrs["TIME"]).normalize()
            rate = float(attrs["OBS_VALUE"])
            local_update = datetime.strptime(
                attrs["LAST_UPDATED"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=london)
            available = pd.Timestamp(local_update.astimezone(timezone.utc))
        except (TypeError, ValueError) as exc:
            raise DataUnavailable("invalid Bank of England XUDLUSS observation") from exc
        rows.append((observation, rate, available))
    if not rows:
        raise DataUnavailable("Bank of England XUDLUSS XML contains no observations")
    output = pd.DataFrame(rows, columns=["observation_date", "close", "available_at_utc"])
    output = output.sort_values("observation_date", kind="stable").set_index("observation_date")
    if output.index.has_duplicates or not np.isfinite(output["close"]).all() or (output["close"] <= 0).any():
        raise DataUnavailable("Bank of England XUDLUSS observations are invalid")
    available = pd.to_datetime(output["available_at_utc"], utc=True, errors="coerce")
    observed = pd.DatetimeIndex(output.index).tz_localize("UTC")
    if available.isna().any() or (available.to_numpy() < observed.to_numpy()).any():
        raise DataUnavailable("Bank of England publication timing is invalid")
    output["available_at_utc"] = available
    return output


def select_vix(vix: pd.DataFrame, decision_session: Any, spec: BookSpec) -> dict:
    session = session_label(decision_session)
    eligible = vix.loc[vix.index < session]
    if eligible.empty:
        raise DataUnavailable(f"no prior Cboe VIX close for {iso_date(session)}")
    source = pd.Timestamp(eligible.index[-1]).normalize()
    age = int((session - source).days)
    if not 1 <= age <= spec.maximum_vix_age_days:
        raise DataUnavailable(
            f"Cboe VIX for {iso_date(session)} is stale ({age} calendar days)"
        )
    return {
        "close": float(eligible.iloc[-1]["close"]),
        "source_date": iso_date(source),
        "age_days": age,
    }


def select_fx(fx: pd.DataFrame, execution_session: Any, spec: BookSpec) -> dict:
    session = session_label(execution_session)
    opening = session_open_utc(session)
    available = pd.to_datetime(fx["available_at_utc"], utc=True)
    eligible = fx.loc[(fx.index < session) & (available <= opening)]
    if eligible.empty:
        raise DataUnavailable(f"no published XUDLUSS reference for {iso_date(session)}")
    source = pd.Timestamp(eligible.index[-1]).normalize()
    age = int((session - source).days)
    if not 1 <= age <= spec.maximum_fx_age_days:
        raise DataUnavailable(
            f"XUDLUSS reference for {iso_date(session)} is stale ({age} calendar days)"
        )
    row = eligible.iloc[-1]
    return {
        "rate": float(row["close"]),
        "source_date": iso_date(source),
        "available_at_utc": pd.Timestamp(row["available_at_utc"]).tz_convert("UTC").isoformat(),
        "cutoff_at_utc": opening.isoformat(),
        "age_days": age,
    }


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return sha256(raw).hexdigest()


def panel_observation(panel: Mapping[str, pd.DataFrame], session: Any) -> dict:
    day = session_label(session)
    return {
        symbol: {column: float(panel[symbol].at[day, column]) for column in PRICE_COLUMNS}
        for symbol in SYMBOLS
    }


def decision_input_hash(
    panel: Mapping[str, pd.DataFrame], session: Any, vix_row: Mapping[str, Any]
) -> str:
    day = session_label(session)
    window = {}
    for symbol in SYMBOLS:
        rows = panel[symbol].loc[:day, PRICE_COLUMNS].tail(MINIMUM_HISTORY_SESSIONS)
        window[symbol] = [
            [iso_date(index), *(float(row[column]) for column in PRICE_COLUMNS)]
            for index, row in rows.iterrows()
        ]
    return _canonical_hash({"window": window, "vix": dict(vix_row)})


def _download_adjusted(yfinance_module: Any, symbol: str, start: str, end: str) -> pd.DataFrame:
    return yfinance_module.download(
        symbol,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        actions=False,
        repair=False,
        threads=False,
        progress=False,
    )


def fetch_market_data(now: Any | None = None) -> MarketData:
    """Download and validate the complete fresh bundle; fail closed on any gap."""

    now_utc = utc_timestamp(now or datetime.now(timezone.utc))
    latest = latest_completed_session(now_utc)
    start = (latest - pd.Timedelta(days=550)).strftime("%Y-%m-%d")
    end = (latest + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        import httpx
        import yfinance
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment dependency
        raise DataUnavailable("fresh V14 inputs require httpx and yfinance") from exc

    raw = {symbol: _download_adjusted(yfinance, symbol, start, end) for symbol in SYMBOLS}
    panel = validate_panel(raw, latest)

    try:
        # The BoE database returns HTTP 403 to the library-default User-Agent
        # even for its documented public export URL.  A stable descriptive
        # client identifier receives the same official XML used by V13/V14.
        with httpx.Client(
            timeout=45,
            follow_redirects=True,
            headers={"User-Agent": "ApexFX-ForwardPaper/1.0"},
        ) as client:
            vix_response = client.get(CBOE_VIX_URL)
            vix_response.raise_for_status()
            params = {
                "CodeVer": "new",
                "xml.x": "yes",
                "Datefrom": (latest - pd.Timedelta(days=550)).strftime("%d/%b/%Y"),
                "Dateto": now_utc.strftime("%d/%b/%Y"),
                "SeriesCodes": "XUDLUSS",
                "VPD": "Y",
                "VFD": "Y",
            }
            fx_response = client.get(BOE_XML_ENDPOINT, params=params)
            fx_response.raise_for_status()
    except Exception as exc:
        raise DataUnavailable(f"official VIX/FX retrieval failed: {exc}") from exc

    try:
        from io import BytesIO

        vix = normalize_vix(pd.read_csv(BytesIO(vix_response.content)))
    except Exception as exc:
        if isinstance(exc, DataUnavailable):
            raise
        raise DataUnavailable("Cboe VIX CSV is not parseable") from exc
    fx = normalize_boe_xml(fx_response.content)

    provenance = {
        "retrieved_at_utc": now_utc.isoformat(),
        "latest_completed_session": iso_date(latest),
        "equity_source": "Yahoo Finance adjusted daily OHLC (auto_adjust=True)",
        "equity_symbols": list(SYMBOLS),
        "vix_source_url": CBOE_VIX_URL,
        "vix_response_sha256": sha256(vix_response.content).hexdigest(),
        "fx_source_url": f"{BOE_XML_ENDPOINT}?{urlencode(params)}",
        "fx_response_sha256": sha256(fx_response.content).hexdigest(),
        "fx_series": "Bank of England XUDLUSS, USD per GBP",
    }
    return MarketData(panel, vix, fx, latest, now_utc, provenance)
