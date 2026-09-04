from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_book_g_snapshot.py"
SPEC = importlib.util.spec_from_file_location("fetch_book_g_snapshot", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _sessions() -> pd.DatetimeIndex:
    return pd.DatetimeIndex(["2014-01-02", "2014-01-03"])


def _frame(
    *,
    sessions: pd.DatetimeIndex | None = None,
    multi_index: bool = False,
) -> pd.DataFrame:
    index = _sessions() if sessions is None else sessions
    data = {
        "Open": [100.0 + offset for offset in range(len(index))],
        "High": [102.0 + offset for offset in range(len(index))],
        "Low": [99.0 + offset for offset in range(len(index))],
        "Close": [101.0 + offset for offset in range(len(index))],
        "Volume": [1_000.0] * len(index),
    }
    frame = pd.DataFrame(data, index=index)
    frame.index.name = "Date"
    if multi_index:
        frame.columns = pd.MultiIndex.from_tuples(
            [(column, "SPY") for column in frame.columns],
            names=["Price", "Ticker"],
        )
    return frame


class FakeYFinance:
    __version__ = "9.9.9-test"

    def __init__(self, frame: pd.DataFrame | None = None):
        self.frame = _frame() if frame is None else frame
        self.calls: list[tuple[str, dict[str, object]]] = []

    def download(self, symbol: str, **kwargs):
        self.calls.append((symbol, kwargs))
        result = self.frame.copy(deep=True)
        if isinstance(result.columns, pd.MultiIndex):
            result.columns = pd.MultiIndex.from_tuples(
                [(price, symbol) for price, _ticker in result.columns],
                names=result.columns.names,
            )
        return result


def test_frozen_request_and_universe_are_exact():
    assert MODULE.DOWNLOAD_START == "2014-01-01"
    assert MODULE.DOWNLOAD_END_EXCLUSIVE == "2026-09-04"
    assert MODULE.INTERVAL == "1d"
    assert len(MODULE.BOOK_G_SYMBOLS) == 13
    assert MODULE.BOOK_G_SYMBOLS == (
        "SPY",
        "XLK",
        "XLE",
        "XLV",
        "XLI",
        "XLF",
        "XLP",
        "XLU",
        "GLD",
        "TLT",
        "IEF",
        "SHY",
        "UUP",
    )


def test_download_symbol_uses_every_frozen_yfinance_argument():
    fake = FakeYFinance()
    MODULE._download_symbol(fake, "SPY")
    assert fake.calls == [
        (
            "SPY",
            {
                "start": "2014-01-01",
                "end": "2026-09-04",
                "interval": "1d",
                "auto_adjust": True,
                "actions": False,
                "repair": False,
                "threads": False,
                "progress": False,
            },
        )
    ]


def test_normalize_accepts_yfinance_single_symbol_multi_index():
    normalized = MODULE.normalize_symbol_frame(_frame(multi_index=True), "SPY")
    assert list(normalized.columns) == list(MODULE.SNAPSHOT_COLUMNS)
    assert normalized["symbol"].tolist() == ["SPY", "SPY"]
    assert normalized["open"].tolist() == [100.0, 101.0]
    assert normalized["close"].tolist() == [101.0, 102.0]
    assert normalized["date"].dt.tz is None


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("Open", np.nan, "non-finite"),
        ("Close", 0.0, "non-positive"),
        ("High", 100.5, "high is below"),
        ("Low", 101.5, "low is above"),
    ],
)
def test_normalize_rejects_invalid_ohlc(column, value, message):
    frame = _frame()
    frame.loc[frame.index[0], column] = value
    with pytest.raises(ValueError, match=message):
        MODULE.normalize_symbol_frame(frame, "SPY")


def test_normalize_rejects_duplicate_session_dates():
    duplicate_sessions = pd.DatetimeIndex(["2014-01-02", "2014-01-02"])
    with pytest.raises(ValueError, match="duplicate session date"):
        MODULE.normalize_symbol_frame(
            _frame(sessions=duplicate_sessions),
            "SPY",
        )


def test_validate_panel_fails_closed_on_missing_symbol():
    frames = {symbol: _frame() for symbol in MODULE.BOOK_G_SYMBOLS if symbol != "UUP"}
    with pytest.raises(ValueError, match="missing symbols: UUP"):
        MODULE.validate_panel(frames, expected_sessions=_sessions())


def test_validate_panel_fails_closed_on_missing_expected_session():
    frames = {symbol: _frame() for symbol in MODULE.BOOK_G_SYMBOLS}
    frames["TLT"] = _frame(sessions=pd.DatetimeIndex(["2014-01-02"]))
    with pytest.raises(ValueError, match="TLT: missing expected XNYS sessions"):
        MODULE.validate_panel(frames, expected_sessions=_sessions())


def test_validate_panel_rejects_non_xnys_rows():
    frames = {symbol: _frame() for symbol in MODULE.BOOK_G_SYMBOLS}
    frames["GLD"] = _frame(
        sessions=pd.DatetimeIndex(["2014-01-02", "2014-01-03", "2014-01-04"])
    )
    with pytest.raises(ValueError, match="GLD: non-XNYS sessions"):
        MODULE.validate_panel(frames, expected_sessions=_sessions())


def test_fetch_snapshot_writes_exact_long_schema_and_auditable_manifest(tmp_path):
    snapshot = tmp_path / "book_g.parquet"
    manifest_path = tmp_path / "book_g.manifest.json"
    fake = FakeYFinance(_frame(multi_index=True))
    retrieved = datetime(2026, 9, 4, 8, 30, tzinfo=timezone.utc)

    manifest = MODULE.fetch_snapshot(
        snapshot,
        manifest_path,
        yfinance_module=fake,
        expected_sessions=_sessions(),
        retrieved_at_utc=retrieved,
    )

    assert [symbol for symbol, _kwargs in fake.calls] == list(MODULE.BOOK_G_SYMBOLS)
    assert len(fake.calls) == 13
    stored = pd.read_parquet(snapshot)
    assert list(stored.columns) == list(MODULE.SNAPSHOT_COLUMNS)
    assert len(stored) == 26
    assert not stored.duplicated(["date", "symbol"]).any()
    assert stored.equals(stored.sort_values(["date", "symbol"]).reset_index(drop=True))

    parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert parsed == manifest
    assert parsed["retrieved_at_utc"] == "2026-09-04T08:30:00+00:00"
    assert parsed["request"] == {
        "library": "yfinance",
        "library_version": "9.9.9-test",
        "download_mode": "one_symbol_per_call_in_declared_order",
        "symbols": list(MODULE.BOOK_G_SYMBOLS),
        "start": "2014-01-01",
        "end_exclusive": "2026-09-04",
        "interval": "1d",
        "auto_adjust": True,
        "actions": False,
        "repair": False,
        "threads": False,
        "progress": False,
    }
    assert parsed["calendar"]["expected_sessions"] == 2
    assert parsed["calendar"]["missing_rows_allowed"] == 0
    assert parsed["calendar"]["forward_fill"] is False
    assert set(parsed["row_counts"]) == set(MODULE.BOOK_G_SYMBOLS)
    assert set(parsed["row_counts"].values()) == {2}
    assert parsed["snapshot"]["rows"] == 26
    assert parsed["snapshot"]["sha256"] == hashlib.sha256(
        snapshot.read_bytes()
    ).hexdigest()


def test_fetch_snapshot_refuses_to_overwrite_without_force(tmp_path):
    snapshot = tmp_path / "book_g.parquet"
    snapshot.write_bytes(b"existing")
    with pytest.raises(FileExistsError, match="--force"):
        MODULE.fetch_snapshot(
            snapshot,
            tmp_path / "book_g.manifest.json",
            yfinance_module=FakeYFinance(),
            expected_sessions=_sessions(),
        )
