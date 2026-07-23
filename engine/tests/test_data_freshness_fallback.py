"""SmartDataProvider must fall back on STALE primary data, not just on empty responses.

Measured cause of a dead scan: OANDA publishes daily FX bars about a day late. At 2026-07-23
20:07 UTC its newest 1d bar was 2026-07-21 21:00 — exactly the 47.1h the engine reported. The
provider accepted it because the only test was `len(df) >= 10`, so every FX pair and several
crypto names were then rejected by the scan's own 36h staleness limit and the Yahoo fallback
(which had fresh data) never ran. Twenty-four instruments scanned, zero tradeable.

The comment above that code already claimed "fall back to Yahoo if OANDA returns no/stale
data". These tests make the claim true.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
LIVE = ENGINE_DIR / "scripts" / "run_live_paper_trading.py"


def _src() -> str:
    return LIVE.read_text(encoding="utf-8")


def _age_seconds(df) -> float:
    """Mirror of SmartDataProvider._age_seconds (the script cannot be imported: it builds an
    executor and mutates global config at import time)."""
    try:
        last = df.index[-1]
        last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
        return (pd.Timestamp.now(tz="UTC") - last).total_seconds()
    except Exception:
        return float("inf")


def _frame(hours_old: float, rows: int = 300) -> pd.DataFrame:
    end = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours_old)
    idx = pd.date_range(end=end, periods=rows, freq="D", tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0},
                        index=idx)


def test_provider_checks_freshness_not_only_row_count():
    src = _src()
    block = src[src.index("class SmartDataProvider"):]
    block = block[: block.index("\ndata_provider =")]
    assert "STALE_AFTER_S" in block
    assert "_age_seconds" in block
    assert "age <= self.STALE_AFTER_S" in block, (
        "a populated but stale primary response must NOT be returned"
    )


def test_stale_threshold_sits_below_the_scans_own_36h_limit():
    """Otherwise the scan rejects the instrument before the fallback can help."""
    src = _src()
    block = src[src.index("class SmartDataProvider"):]
    block = block[: block.index("\ndata_provider =")]
    line = next(l for l in block.splitlines() if "STALE_AFTER_S =" in l)
    hours = eval(line.split("=", 1)[1].strip()) / 3600  # noqa: S307 - our own literal
    assert hours < 36, f"must trigger before the scan's 36h 1d limit, got {hours}h"


@pytest.mark.parametrize("hours,expect_stale", [
    (0.0, False),      # live
    (23.0, False),     # yesterday's close — normal for a daily book
    (25.4, False),     # OANDA's observed lag when healthy
    (47.1, True),      # the exact failure observed on 2026-07-23
    (72.0, True),
])
def test_age_calculation_matches_the_observed_failure(hours, expect_stale):
    age = _age_seconds(_frame(hours))
    # abs= as well as rel=, since the 0h case has no relative tolerance to work with
    assert age == pytest.approx(hours * 3600, rel=0.01, abs=1.0)
    assert (age > 30 * 3600) is expect_stale


def test_unparseable_index_is_treated_as_infinitely_stale():
    """Never prefer a frame whose age cannot be established."""
    assert _age_seconds(pd.DataFrame()) == float("inf")


def test_naive_timestamps_are_handled_without_raising():
    idx = pd.date_range(end=pd.Timestamp.utcnow().tz_localize(None), periods=5, freq="D")
    df = pd.DataFrame({"close": 1.0}, index=idx)
    assert _age_seconds(df) < 24 * 3600
