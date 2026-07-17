"""Canonical OHLCV data contract.

Every DataFrame that flows through the engine obeys this contract:

  * index:   tz-aware (UTC) ``DatetimeIndex`` named ``timestamp``, sorted ascending,
             unique. Each bar's timestamp is its **open time**: the bar labelled
             ``t`` covers ``[t, t + bar_duration)`` and is fully knowable only at
             ``t + bar_duration`` (its close). Bars are labelled by open time
             because that is what every vendor feed (OANDA, Yahoo, TwelveData)
             actually delivers; an earlier revision of this contract claimed
             close-time labels, which was aspirational and false (audit D-H1).
             Day-based bars are pinned to 00:00 UTC of their *session date*
             (forex: the 17:00-NY session, Sunday-open session labelled Monday —
             see :mod:`apex_quant.data.calendar`). Consumers must not treat a
             bar as known before its close; the store trims still-forming
             terminal bars so caches only ever hold completed bars.
  * columns: exactly ``open, high, low, close, volume`` (float64).

``validate_ohlcv`` enforces the contract loudly so leakage / corruption surfaces
at the boundary rather than deep inside a feature.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, field_validator

OHLCV_COLUMNS: list[str] = ["open", "high", "low", "close", "volume"]
INDEX_NAME = "timestamp"


class Bar(BaseModel):
    """A single OHLCV bar - used in API responses and adapter ``get_latest``."""

    timestamp: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("timestamp")
    @classmethod
    def _tz_aware_utc(cls, v: pd.Timestamp) -> pd.Timestamp:
        ts = pd.Timestamp(v)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


class SchemaError(ValueError):
    """Raised when a frame violates the OHLCV contract."""


def validate_ohlcv(df: pd.DataFrame, *, name: str = "frame") -> pd.DataFrame:
    """Validate (and lightly normalise) a frame against the OHLCV contract.

    Returns the same frame (with a UTC-normalised, sorted index) or raises
    ``SchemaError``. Does NOT silently drop bad rows - corruption should be
    explicit. Use :func:`apex_quant.data.quality.clean` to repair first.
    """
    if not isinstance(df, pd.DataFrame):
        raise SchemaError(f"{name}: expected DataFrame, got {type(df).__name__}")

    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise SchemaError(f"{name}: missing columns {missing}")

    if not isinstance(df.index, pd.DatetimeIndex):
        raise SchemaError(f"{name}: index must be a DatetimeIndex, got {type(df.index).__name__}")

    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")

    out = df.copy()
    out.index = idx
    out.index.name = INDEX_NAME

    if not out.index.is_monotonic_increasing:
        out = out.sort_index()

    if out.index.has_duplicates:
        dupes = int(out.index.duplicated().sum())
        raise SchemaError(f"{name}: index has {dupes} duplicate timestamp(s) - clean() first")

    # OHLC integrity (NaN-tolerant comparisons handled by quality.check_quality)
    out[OHLCV_COLUMNS] = out[OHLCV_COLUMNS].astype("float64")
    return out[OHLCV_COLUMNS]


def empty_ohlcv() -> pd.DataFrame:
    """An empty, contract-valid OHLCV frame."""
    idx = pd.DatetimeIndex([], tz="UTC", name=INDEX_NAME)
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in OHLCV_COLUMNS}, index=idx)
