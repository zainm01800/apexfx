"""Session-calendar convention, atomic/locked persistence, forming-bar guard.

Covers the 2026-07-17 audit fixes:
  * D-C1: forex day bars land Mon-Fri only (Sunday 17:00-NY session -> Monday);
    off-calendar rows are rejected on write and flagged as surplus by quality.
  * D-H2: still-forming bars never persist (OANDA complete=false dropped;
    get_or_fetch trims the forming terminal bar).
  * D-H3: parquet writes are atomic (tmp + os.replace); load() treats a
    corrupt file as missing.
  * D-H4: TrialLedger writes are atomic and the load->record->save cycle can
    run under a file lock; corrupt JSON loads as empty.
"""

from __future__ import annotations

import json
import logging
import os

import pandas as pd
import pytest

from apex_quant.data._filelock import file_lock
from apex_quant.data.adapter import DataAdapter
from apex_quant.data.calendar import (
    asset_class_for,
    bar_close_utc,
    off_calendar_mask,
    session_dates,
    trim_forming_tail,
)
from apex_quant.data.quality import check_quality
from apex_quant.data.store import ParquetStore
from apex_quant.validation.trials import TrialLedger


def _frame(idx, close=1.10):
    n = len(idx)
    return pd.DataFrame(
        {
            "open": [close] * n,
            "high": [close + 0.01] * n,
            "low": [close - 0.01] * n,
            "close": [close] * n,
            "volume": [100.0] * n,
        },
        index=pd.DatetimeIndex(idx, name="timestamp"),
    )


class _FakeAdapter(DataAdapter):
    def __init__(self, df):
        self._df = df

    def get_history(self, instrument, start, end, timeframe="1d"):
        s = pd.Timestamp(start, tz="UTC") if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start)
        e = pd.Timestamp(end, tz="UTC") if pd.Timestamp(end).tzinfo is None else pd.Timestamp(end)
        return self._df.loc[(self._df.index >= s) & (self._df.index <= e)]

    def get_latest(self, instrument, timeframe="1d"):
        return None


# ── asset class + session-date mapping (D-C1) --------------------------------
def test_asset_class_for():
    assert asset_class_for("EUR/USD") == "forex"
    assert asset_class_for("GBP_JPY") == "forex"       # store-slug form
    assert asset_class_for("XAU/USD") == "forex"       # metals trade FX sessions
    assert asset_class_for("BTC/USD") == "crypto"
    assert asset_class_for("SOL_USD") == "crypto"
    assert asset_class_for("AAPL") == "equity"


def test_oanda_labels_map_to_session_dates():
    """OANDA D candles (17:00-NY open labels, Sun-Thu) become Mon-Fri dates."""
    labels = pd.to_datetime(
        ["2024-07-14 21:00", "2024-07-15 21:00", "2024-07-16 21:00",
         "2024-07-17 21:00", "2024-07-18 21:00"]
    ).tz_localize("UTC")
    mapped = session_dates(labels, "EUR/USD", "1d")
    assert list(mapped.strftime("%Y-%m-%d")) == [
        "2024-07-15", "2024-07-16", "2024-07-17", "2024-07-18", "2024-07-19",
    ]
    assert (mapped.dayofweek < 5).all()
    assert (mapped == mapped.normalize()).all()


def test_midnight_labels_are_identity_on_weekdays():
    """Yahoo-style 00:00 UTC Mon-Fri labels keep their date (idempotent)."""
    labels = pd.bdate_range("2024-07-15", periods=5, tz="UTC")
    mapped = session_dates(labels, "EUR/USD", "1d")
    assert (mapped == labels).all()


def test_session_mapping_is_idempotent():
    labels = pd.to_datetime(["2024-07-14 21:00"]).tz_localize("UTC")
    once = session_dates(labels, "EUR/USD", "1d")
    assert (session_dates(once, "EUR/USD", "1d") == once).all()


def test_bar_close_utc_forex_daily():
    # Friday session closes Friday 17:00 NY = 21:00 UTC under DST.
    close = bar_close_utc(pd.Timestamp("2026-07-17", tz="UTC"), "EUR/USD", "1d")
    assert close == pd.Timestamp("2026-07-17 21:00", tz="UTC")
    # Winter: 17:00 EST = 22:00 UTC.
    close_w = bar_close_utc(pd.Timestamp("2026-01-16", tz="UTC"), "EUR/USD", "1d")
    assert close_w == pd.Timestamp("2026-01-16 22:00", tz="UTC")


# ── weekend rejection on write (D-C1) -----------------------------------------
def test_save_rejects_forex_weekend_rows(tmp_path):
    store = ParquetStore(root=tmp_path)
    idx = pd.DatetimeIndex(
        ["2024-07-13", "2024-07-14", "2024-07-15", "2024-07-16"], tz="UTC"
    )  # Sat, Sun, Mon, Tue (midnight-labelled vendor junk + good rows)
    store.save("EUR/USD", _frame(idx), "1d")
    loaded = store.load("EUR/USD", "1d")
    assert list(loaded.index.strftime("%Y-%m-%d")) == ["2024-07-15", "2024-07-16"]
    assert (loaded.index.dayofweek < 5).all()


def test_save_keeps_crypto_weekends(tmp_path):
    store = ParquetStore(root=tmp_path)
    idx = pd.date_range("2024-07-13", "2024-07-16", tz="UTC")  # Sat-Tue
    store.save("BTC/USD", _frame(idx), "1d")
    loaded = store.load("BTC/USD", "1d")
    assert len(loaded) == 4  # crypto: 7-day calendar


def test_save_rejects_non_monday_forex_weekly(tmp_path):
    store = ParquetStore(root=tmp_path)
    idx = pd.DatetimeIndex(["2024-07-08", "2024-07-12"], tz="UTC")  # Mon, Fri
    store.save("EUR/USD", _frame(idx), "1w")
    loaded = store.load("EUR/USD", "1w")
    assert list(loaded.index.strftime("%Y-%m-%d")) == ["2024-07-08"]


def test_save_rejects_forex_intraday_weekend_junk(tmp_path):
    store = ParquetStore(root=tmp_path)
    idx = pd.DatetimeIndex(
        ["2024-07-13 10:00",  # Saturday      -> junk
         "2024-07-14 10:00",  # Sunday < 21h  -> junk
         "2024-07-14 21:00",  # Sunday open   -> legit
         "2024-07-19 21:00",  # Friday close  -> legit (winter close bar)
         "2024-07-19 22:00",  # Friday >= 22h -> junk
         ],
        tz="UTC",
    )
    store.save("EUR/USD", _frame(idx), "1h")
    loaded = store.load("EUR/USD", "1h")
    assert list(loaded.index.strftime("%Y-%m-%d %H:%M")) == [
        "2024-07-14 21:00", "2024-07-19 21:00",
    ]


def test_off_calendar_mask_matches_save_rules():
    idx = pd.DatetimeIndex(["2024-07-13", "2024-07-15"], tz="UTC")  # Sat, Mon
    mask = off_calendar_mask(idx, "EUR/USD", "1d")
    assert list(mask) == [True, False]
    assert not off_calendar_mask(idx, "BTC/USD", "1d").any()


# ── surplus bars in quality (D-C1) --------------------------------------------
def test_quality_flags_surplus_weekend_bars(clean_daily):
    dirty = clean_daily.copy()
    weekend = _frame(pd.DatetimeIndex(["2022-01-15", "2022-01-16"], tz="UTC"))  # Sat, Sun
    rep = check_quality(
        pd.concat([dirty, weekend]), instrument="EUR/USD", timeframe="1d"
    )
    assert rep.n_surplus == 2
    assert not rep.is_clean
    assert "Saturday" in rep.surplus_detail and "Sunday" in rep.surplus_detail


def test_quality_crypto_weekend_hole_is_a_hole():
    idx = pd.DatetimeIndex(["2024-07-12", "2024-07-13", "2024-07-16"], tz="UTC")
    # Fri, Sat, Tue present; Sun + Mon missing (2 calendar days).
    rep = check_quality(_frame(idx), instrument="BTC/USD", timeframe="1d")
    assert rep.n_holes >= 1  # crypto: calendar-day expectation
    rep_fx = check_quality(_frame(idx), instrument="EUR/USD", timeframe="1d")
    assert rep_fx.n_holes == 0  # forex: weekend gap is expected


# ── atomic save + corruption-tolerant load (D-H3) ------------------------------
def test_save_is_atomic_when_write_fails(tmp_path, clean_daily, monkeypatch):
    store = ParquetStore(root=tmp_path)
    store.save("EUR/USD", clean_daily)
    p = store.path_for("EUR/USD", "1d")
    before = p.read_bytes()

    def _boom(*a, **k):
        raise IOError("simulated crash mid-write")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _boom)
    with pytest.raises(IOError):
        store.save("EUR/USD", clean_daily)
    assert p.read_bytes() == before  # original untouched, no tmp litter
    assert not list(tmp_path.glob("*.tmp*"))
    monkeypatch.undo()
    assert store.load("EUR/USD").equals(clean_daily)


def test_load_treats_corrupt_file_as_missing(tmp_path, clean_daily, caplog):
    store = ParquetStore(root=tmp_path)
    store.save("EUR/USD", clean_daily)
    p = store.path_for("EUR/USD", "1d")
    p.write_bytes(b"not a parquet file")
    with caplog.at_level(logging.WARNING):
        out = store.load("EUR/USD", "1d")
    assert out.empty
    assert "treating as missing" in caplog.text


def test_get_or_fetch_recovers_from_corrupt_cache(tmp_path, clean_daily):
    store = ParquetStore(root=tmp_path)
    p = store.path_for("EUR/USD", "1d")
    p.write_bytes(b"garbage")
    adapter = _FakeAdapter(clean_daily)
    out = store.get_or_fetch(
        "EUR/USD", adapter, clean_daily.index[0], clean_daily.index[-1]
    )
    assert len(out) == len(clean_daily)  # refetched + persisted
    assert store.load("EUR/USD").equals(clean_daily)


def test_file_lock_creates_sidecar_and_roundtrips(tmp_path):
    target = tmp_path / "x.parquet"
    with file_lock(target) as lock_path:
        assert str(lock_path).endswith(".lock")
        assert os.path.exists(lock_path)


# ── forming-bar guard (D-H2) ---------------------------------------------------
def test_get_or_fetch_trims_forming_terminal_bar(tmp_path, monkeypatch):
    """A 1h bar still forming at fetch time is neither persisted nor returned."""
    import apex_quant.data.store as store_mod

    # Pin "now" so the test is deterministic any day of the week: Wednesday
    # 10:30 UTC — the 10:00 bar is forming, the 09:00 bar is complete.
    fixed_now = pd.Timestamp("2026-07-15 10:30", tz="UTC")
    real_trim = store_mod.trim_forming_tail
    monkeypatch.setattr(
        store_mod,
        "trim_forming_tail",
        lambda df, instrument, timeframe, now=None: real_trim(
            df, instrument, timeframe, now=fixed_now
        ),
    )
    idx = pd.date_range("2026-07-15 06:00", "2026-07-15 10:00", freq="h", tz="UTC")
    store = ParquetStore(root=tmp_path)
    adapter = _FakeAdapter(_frame(idx))
    out = store.get_or_fetch(
        "EUR/USD", adapter, idx[0], fixed_now + pd.Timedelta(hours=1), "1h"
    )
    assert out.index[-1] == idx[-2]  # forming 10:00 bar trimmed
    persisted = store.load("EUR/USD", "1h")
    assert persisted.index[-1] == idx[-2]
    assert len(persisted) == len(idx) - 1


def test_trim_forming_tail_daily_forex():
    friday = pd.Timestamp("2026-07-17", tz="UTC")  # Friday session
    idx = pd.DatetimeIndex(["2026-07-15", "2026-07-16", "2026-07-17"], tz="UTC")
    df = _frame(idx)
    # During Friday's session (before 21:00 UTC close): Friday bar is forming.
    during = pd.Timestamp("2026-07-17 13:00", tz="UTC")
    assert trim_forming_tail(df, "EUR/USD", "1d", now=during).index[-1] == idx[-2]
    # After the close: the Friday bar is complete.
    after = pd.Timestamp("2026-07-17 21:30", tz="UTC")
    assert len(trim_forming_tail(df, "EUR/USD", "1d", now=after)) == 3


def test_oanda_adapter_drops_incomplete_candles(monkeypatch):
    from apex_quant.data.oanda_adapter import OandaAdapter

    monkeypatch.setenv("APEX_OANDA_API_KEY", "test-key")
    monkeypatch.setattr(
        OandaAdapter, "_probe_endpoint", lambda self: "https://api-fxpractice.oanda.com"
    )
    monkeypatch.setattr("time.sleep", lambda *_: None)
    adapter = OandaAdapter()

    candles = [
        {"time": "2024-07-15T21:00:00.000000000Z",
         "mid": {"o": "1.10", "h": "1.20", "l": "1.00", "c": "1.15"},
         "volume": 100, "complete": True},
        {"time": "2024-07-16T21:00:00.000000000Z",
         "mid": {"o": "1.11", "h": "1.21", "l": "1.01", "c": "1.16"},
         "volume": 90, "complete": False},  # still forming -> dropped
    ]

    def _fetch(ticker, start_iso, end_iso, granularity):
        s, e = pd.Timestamp(start_iso), pd.Timestamp(end_iso)
        return {
            "candles": [c for c in candles if s <= pd.Timestamp(c["time"]) <= e]
        }

    monkeypatch.setattr(adapter, "_fetch_chunk", _fetch)
    df = adapter.get_history("EUR/USD", "2024-07-14", "2024-07-18", "1d")
    assert len(df) == 1
    assert df.index[0] == pd.Timestamp("2024-07-15 21:00", tz="UTC")
    assert df.iloc[0]["close"] == 1.15


# ── merge policy consistency (D-H3) --------------------------------------------
def test_merge_uses_config_duplicate_policy(tmp_path, clean_daily):
    """Freshly fetched rows win a same-session collision under keep_last."""
    store = ParquetStore(root=tmp_path)
    store.save("EUR/USD", clean_daily)
    shifted = clean_daily.copy()
    shifted["close"] = clean_daily["close"] + 0.01
    adapter = _FakeAdapter(shifted)
    # end one hour past the cache edge so a top-up fetch (and merge) triggers
    out = store.get_or_fetch(
        "EUR/USD", adapter, clean_daily.index[0],
        clean_daily.index[-1] + pd.Timedelta(hours=1),
    )
    assert (out["close"].to_numpy() == (clean_daily["close"] + 0.01).to_numpy()).all()


# ── trial ledger (D-H4) ---------------------------------------------------------
def test_ledger_locked_roundtrip_accumulates(tmp_path):
    p = tmp_path / "ledger.json"
    with TrialLedger.locked(p) as led:
        led.record({"a": 1}, 0.5)
    with TrialLedger.locked(p) as led:
        led.record({"b": 2}, 0.7)
    reloaded = TrialLedger.load(p)
    assert reloaded.n_trials == 2
    assert sorted(reloaded.sharpes) == [0.5, 0.7]
    assert (tmp_path / "ledger.json.lock").exists()


def test_ledger_load_treats_corrupt_json_as_empty(tmp_path, caplog):
    p = tmp_path / "ledger.json"
    p.write_text('{"a": 1.0, broken', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        led = TrialLedger.load(p)
    assert led.n_trials == 0
    assert "starting empty" in caplog.text


def test_ledger_save_is_atomic_when_write_fails(tmp_path, monkeypatch):
    led = TrialLedger()
    led.record({"a": 1}, 0.5)
    p = led.save(tmp_path / "ledger.json")
    before = p.read_bytes()

    def _boom(*a, **k):
        raise IOError("simulated crash")

    monkeypatch.setattr(json, "dump", _boom)
    with pytest.raises(IOError):
        led.save(p)
    assert p.read_bytes() == before
    monkeypatch.undo()
    assert TrialLedger.load(p).n_trials == 1


# ── rates staleness (D-M2) -------------------------------------------------------
def test_rates_staleness_warns_once(tmp_path, caplog):
    import apex_quant.data.rates as rates_mod
    from apex_quant.data.rates import CSVRateProvider

    csv = tmp_path / "rates.csv"
    csv.write_text("effective_date,USD,EUR\n2025-01-01,0.04,0.02\n", encoding="utf-8")
    provider = CSVRateProvider(csv)
    rates_mod._staleness_warned = False  # reset the once-per-process latch
    stale_query = pd.Timestamp("2025-06-01", tz="UTC")  # >45d after 2025-01-01
    with caplog.at_level(logging.WARNING, logger="apex_quant.data.rates"):
        r1 = provider("EUR/USD", stale_query)
        r2 = provider("EUR/USD", stale_query)
    assert r1 == r2 == (0.02, 0.04)  # values still returned, no fabrication
    warnings = [rec for rec in caplog.records if "stale" in rec.getMessage()]
    assert len(warnings) == 1  # logged once per process
    fresh_query = pd.Timestamp("2025-01-10", tz="UTC")  # within 45d -> no warning
    caplog.clear()
    rates_mod._staleness_warned = False
    with caplog.at_level(logging.WARNING, logger="apex_quant.data.rates"):
        provider("EUR/USD", fresh_query)
    assert not [rec for rec in caplog.records if "stale" in rec.getMessage()]
    rates_mod._staleness_warned = False
