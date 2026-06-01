"""Schema contract + parquet store (offline, via a fake adapter)."""

from __future__ import annotations

import pandas as pd
import pytest

from apex_quant.data.adapter import DataAdapter
from apex_quant.data.schema import SchemaError, validate_ohlcv
from apex_quant.data.store import ParquetStore


# -- schema ------------------------------------------------------------------
def test_validate_ohlcv_accepts_clean(clean_daily):
    out = validate_ohlcv(clean_daily)
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index.tz is not None


def test_validate_localises_naive_index(clean_daily):
    naive = clean_daily.copy()
    naive.index = naive.index.tz_localize(None)
    out = validate_ohlcv(naive)
    assert str(out.index.tz) == "UTC"


def test_validate_rejects_missing_column(clean_daily):
    with pytest.raises(SchemaError):
        validate_ohlcv(clean_daily.drop(columns=["volume"]))


def test_validate_rejects_duplicates(clean_daily):
    dup = pd.concat([clean_daily, clean_daily.iloc[[0]]])
    with pytest.raises(SchemaError):
        validate_ohlcv(dup)


# -- store (no network) --------------------------------------------------------
class _FakeAdapter(DataAdapter):
    def __init__(self, df):
        self._df = df

    def get_history(self, instrument, start, end, timeframe="1d"):
        s = pd.Timestamp(start, tz="UTC") if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start)
        e = pd.Timestamp(end, tz="UTC") if pd.Timestamp(end).tzinfo is None else pd.Timestamp(end)
        return self._df.loc[(self._df.index >= s) & (self._df.index <= e)]

    def get_latest(self, instrument, timeframe="1d"):
        return None


def test_store_roundtrip(tmp_path, clean_daily):
    store = ParquetStore(root=tmp_path)
    store.save("EUR/USD", clean_daily)
    loaded = store.load("EUR/USD")
    assert len(loaded) == len(clean_daily)
    assert loaded.index.equals(clean_daily.index)


def test_store_get_or_fetch_persists(tmp_path, clean_daily):
    store = ParquetStore(root=tmp_path)
    adapter = _FakeAdapter(clean_daily)
    start, end = clean_daily.index[0], clean_daily.index[-1]

    # cold: must fetch + persist
    out = store.get_or_fetch("EUR/USD", adapter, start, end)
    assert len(out) == len(clean_daily)
    assert store.path_for("EUR/USD", "1d").exists()

    # warm: served from cache for an inner range
    inner = store.get_or_fetch("EUR/USD", adapter, clean_daily.index[10], clean_daily.index[20])
    assert len(inner) == 11
