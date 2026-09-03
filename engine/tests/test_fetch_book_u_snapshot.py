from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_book_u_snapshot.py"
SPEC = importlib.util.spec_from_file_location("fetch_book_u_snapshot", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_snapshot_excludes_the_in_progress_freeze_day():
    assert MODULE.DOWNLOAD_END_EXCLUSIVE == "2026-09-03"


def _payload(*, scale: float = 1.0):
    return {
        "chart": {
            "result": [{
                "timestamp": [1_600_000_000, 1_600_086_400],
                "indicators": {
                    "quote": [{
                        "open": [100.0, 51.0],
                        "high": [110.0, 55.0],
                        "low": [90.0, 45.0],
                        "close": [100.0, 50.0],
                        "volume": [10.0, None],
                    }],
                    "adjclose": [{"adjclose": [100.0 * scale, 100.0 * scale]}],
                },
            }],
            "error": None,
        }
    }


def test_adjusted_ohlcv_scales_every_price_field_consistently():
    frame = MODULE.adjusted_ohlcv_from_yahoo(_payload())
    assert frame.iloc[0]["close"] == pytest.approx(100.0)
    assert frame.iloc[1]["adjustment_factor"] == pytest.approx(2.0)
    assert frame.iloc[1]["open"] == pytest.approx(102.0)
    assert frame.iloc[1]["high"] == pytest.approx(110.0)
    assert frame.iloc[1]["low"] == pytest.approx(90.0)
    assert frame.iloc[1]["close"] == pytest.approx(100.0)
    assert frame.iloc[1]["volume"] == 0.0


def test_adjusted_ohlcv_rejects_missing_adjusted_close():
    payload = _payload()
    payload["chart"]["result"][0]["indicators"].pop("adjclose")
    with pytest.raises(ValueError, match="adjusted-close"):
        MODULE.adjusted_ohlcv_from_yahoo(payload)


def test_adjusted_ohlcv_rejects_nonpositive_factor():
    payload = _payload()
    payload["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"][0] = 0.0
    with pytest.raises(ValueError, match="factor"):
        MODULE.adjusted_ohlcv_from_yahoo(payload)
