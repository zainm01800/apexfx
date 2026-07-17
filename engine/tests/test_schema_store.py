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


# -- day-bar convention (2026-07-17 session-date migration, D-C1) ---------------
def test_store_normalizes_day_bars_to_midnight(tmp_path, clean_daily):
    """Mixed 00:00 / 21:00 UTC labels are remapped to SESSION dates (forex:
    NY open date + 1 day), then collapsed one bar per session.

    The synthetic "+21h" frame mimics OANDA 17:00-NY open labels: Mon-Thu
    labels land on Tue-Fri sessions (colliding with the midnight rows, where
    the row later in the written frame wins under keep_last); the Friday
    21:00 label maps to Saturday — off-calendar for forex — and is rejected.
    """
    oanda = clean_daily.copy()
    oanda.index = oanda.index + pd.Timedelta(hours=21)  # OANDA-style labels
    oanda["close"] = clean_daily["close"].to_numpy() + 0.01  # distinguishable
    store = ParquetStore(root=tmp_path)
    store.save("EUR/USD", pd.concat([clean_daily, oanda]))
    loaded = store.load("EUR/USD")
    assert len(loaded) == len(clean_daily)  # one bar per Mon-Fri session date
    assert (loaded.index == loaded.index.normalize()).all()
    assert (loaded.index.dayofweek < 5).all()
    # Mondays (no OANDA collision) keep the midnight bar; each Tue-Fri session
    # was won by the OANDA bar opened the PRIOR bday at 21:00 (its session
    # date is one bday later than its label), i.e. shifted by one day + 0.01.
    mon = loaded.index.dayofweek == 0
    assert (loaded.loc[mon, "close"].to_numpy() == clean_daily.loc[mon, "close"].to_numpy()).all()
    prev_bday_close = clean_daily["close"].reindex(loaded.index[~mon] - pd.Timedelta(days=1))
    assert (loaded.loc[~mon, "close"].to_numpy() == (prev_bday_close + 0.01).to_numpy()).all()


def test_get_or_fetch_collapses_mixed_day_conventions(tmp_path):
    """A midnight-convention cache topped up with 17:00-NY-labelled bars keeps
    one bar per session date, and the freshly fetched row wins the collision.

    The fetched frame uses realistic OANDA daily labels (Sun-Thu 21:00 UTC
    opens), which map onto Mon-Fri session dates — the Sunday 21:00 candle is
    the Monday session."""
    week = pd.bdate_range("2024-07-15", periods=5, tz="UTC", name="timestamp")  # Mon-Fri

    def _mk(idx, close):
        n = len(idx)
        return pd.DataFrame(
            {"open": [close] * n, "high": [close + 0.01] * n,
             "low": [close - 0.01] * n, "close": [close] * n,
             "volume": [100.0] * n},
            index=pd.DatetimeIndex(idx, name="timestamp"),
        )

    cached = _mk(week, 1.10)
    store = ParquetStore(root=tmp_path)
    store.save("EUR/USD", cached)  # midnight-convention cache

    oanda_labels = pd.DatetimeIndex(
        ["2024-07-14 21:00", "2024-07-15 21:00", "2024-07-16 21:00",
         "2024-07-17 21:00", "2024-07-18 21:00"], tz="UTC", name="timestamp")
    fetched = _mk(oanda_labels, 1.20)
    adapter = _FakeAdapter(fetched)
    # start early enough that the Sunday-labelled (Monday-session) candle is in range
    out = store.get_or_fetch(
        "EUR/USD", adapter, week[0] - pd.Timedelta(days=1), week[-1] + pd.Timedelta(hours=1)
    )
    assert list(out.index) == list(week)  # Mon-Fri session dates, one bar each
    assert (out["close"] == 1.20).all()   # fresh fetch won every session
    persisted = store.load("EUR/USD")
    assert list(persisted.index) == list(week)

    # intraday frames are NOT session-normalized
    intra = cached.copy()
    intra.index = week + pd.Timedelta(hours=21)  # Mon-Fri 21:00 UTC, all in-session
    store.save("EUR/USD", intra, "1h")
    assert (store.load("EUR/USD", "1h").index == intra.index).all()
