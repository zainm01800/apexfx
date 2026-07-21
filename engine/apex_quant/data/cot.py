"""CFTC Commitments of Traders (legacy futures-only) — positioning data layer.

Free weekly data: net non-commercial (speculative) positioning per futures
market, the classic crowding gauge for FX majors and gold. This module is the
DATA layer only — no signal, no sizing. Any sleeve built on it must go through
its own pre-registered gate (see data_store/cot_sleeve_prereg.md).

Point-in-time discipline (the part everyone gets wrong): COT observations are
as of TUESDAY but released FRIDAY ~15:30 ET. ``as_of_release`` therefore shifts
each observation's index to its release date (+3 business days); backtests must
join on that shifted index or they look 3 days into the future.

Source files: https://www.cftc.gov/files/dea/history/deacot{year}.zip
(one zip per year, "annual.txt" CSV inside; column layout stable since 1986).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pandas as pd

COT_URL = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data_store" / "cot"

# Engine instrument -> legacy-report market name prefix (matched case-insensitively
# against "Market and Exchange Names"). FX futures are quoted vs USD, so the
# engine's USD/JPY and USD/CHF map to the YEN/FRANC contracts with the SIGN
# FLIPPED by the caller (long yen futures = short USD/JPY).
MARKET_PREFIX = {
    "EUR/USD": "EURO FX",
    "GBP/USD": "BRITISH POUND",
    "AUD/USD": "AUSTRALIAN DOLLAR",
    "NZD/USD": "NZ DOLLAR",
    "USD/JPY": "JAPANESE YEN",     # inverted vs engine instrument
    "USD/CHF": "SWISS FRANC",      # inverted vs engine instrument
    "USD/CAD": "CANADIAN DOLLAR",  # inverted vs engine instrument
    "GOLD": "GOLD",
}
INVERTED = {"USD/JPY", "USD/CHF", "USD/CAD"}

_COLS = {
    "Market and Exchange Names": "market",
    "As of Date in Form YYYY-MM-DD": "date",
    "Noncommercial Positions-Long (All)": "noncomm_long",
    "Noncommercial Positions-Short (All)": "noncomm_short",
    "Open Interest (All)": "open_interest",
}


def parse_cot(raw_csv: bytes | str) -> pd.DataFrame:
    """Legacy annual.txt -> tidy frame [market, date, noncomm_long/short, open_interest]."""
    df = pd.read_csv(io.BytesIO(raw_csv) if isinstance(raw_csv, bytes) else io.StringIO(raw_csv),
                     low_memory=False)
    keep = {c: n for c, n in _COLS.items() if c in df.columns}
    if len(keep) < len(_COLS):
        missing = set(_COLS) - set(keep)
        raise ValueError(f"COT csv missing expected columns: {missing}")
    out = df[list(keep)].rename(columns=keep)
    out["date"] = pd.to_datetime(out["date"])
    out["market"] = out["market"].astype(str).str.strip()
    for c in ("noncomm_long", "noncomm_short", "open_interest"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.dropna(subset=["date", "noncomm_long", "noncomm_short"])


def fetch_cot_year(year: int, timeout: float = 60.0) -> pd.DataFrame:
    """Download + parse one year's legacy futures-only file."""
    r = httpx.get(COT_URL.format(year=year), timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        return parse_cot(z.read(name))


def load_cached(years: range | list[int]) -> pd.DataFrame:
    """Concatenate cached per-year parquets (fetch_and_cache fills them)."""
    frames = []
    for y in years:
        p = CACHE_DIR / f"cot_{y}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p))
    if not frames:
        return pd.DataFrame(columns=list(_COLS.values()))
    return pd.concat(frames, ignore_index=True).drop_duplicates(["market", "date"])


def fetch_and_cache(years: range | list[int]) -> dict[int, int]:
    """Fetch each year not already cached (current year always refreshed)."""
    import datetime as _dt
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    got: dict[int, int] = {}
    this_year = _dt.date.today().year
    for y in years:
        p = CACHE_DIR / f"cot_{y}.parquet"
        if p.exists() and y != this_year:
            got[y] = len(pd.read_parquet(p))
            continue
        df = fetch_cot_year(y)
        df.to_parquet(p, index=False)
        got[y] = len(df)
    return got


def net_positioning(df: pd.DataFrame, instrument: str) -> pd.Series:
    """Weekly net speculative positioning as a share of open interest, engine-signed.

    (noncomm_long - noncomm_short) / open_interest for the mapped market,
    sign-flipped for USD-inverted contracts so +ve always means specs are
    positioned LONG the engine instrument.
    """
    prefix = MARKET_PREFIX.get(instrument)
    if prefix is None:
        raise KeyError(f"no COT market mapped for {instrument}")
    sub = df[df["market"].str.upper().str.startswith(prefix)]
    if sub.empty:
        return pd.Series(dtype=float)
    # A prefix can match multiple exchanges historically; keep the deepest series.
    top_market = sub.groupby("market")["date"].count().idxmax()
    sub = sub[sub["market"] == top_market].sort_values("date").set_index("date")
    net = (sub["noncomm_long"] - sub["noncomm_short"]) / sub["open_interest"].replace(0, pd.NA)
    net = net.astype(float).dropna()
    if instrument in INVERTED:
        net = -net
    return net


def as_of_release(obs: pd.Series, lag_days: int = 3) -> pd.Series:
    """Shift Tuesday observations to their Friday release (point-in-time index)."""
    shifted = obs.copy()
    shifted.index = shifted.index + pd.tseries.offsets.BusinessDay(lag_days)
    return shifted


def positioning_zscore(net: pd.Series, window: int = 156, min_periods: int = 52) -> pd.Series:
    """Rolling z-score of net positioning (3y weekly window, 1y minimum)."""
    mu = net.rolling(window, min_periods=min_periods).mean()
    sd = net.rolling(window, min_periods=min_periods).std(ddof=0)
    return ((net - mu) / sd.replace(0.0, float("nan"))).astype(float)
