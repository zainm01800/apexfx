"""Canonical OHLCV data contract.

Every DataFrame that flows through the engine obeys this contract:

  * index:   tz-aware (UTC) ``DatetimeIndex`` named ``timestamp``, sorted ascending,
             unique. Each bar's timestamp is its **close / information time** - i.e.
             the bar is considered *known* only at or after this timestamp. This is
             the single convention the point-in-time accessor relies on.
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
