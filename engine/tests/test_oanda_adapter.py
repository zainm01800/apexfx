"""OandaAdapter pagination regression tests.

The HTTP layer is mocked with OANDA v20 semantics: each call returns the
candles in the requested [from, to] window, capped at 5,000 per call, with
weekend/holiday gaps making short batches NORMAL. The 2026-07 bug: the
adapter terminated on any batch shorter than 4,800 candles, so a multi-year
intraday request stopped after the first ~200-day span.
"""

from __future__ import annotations

import pandas as pd
import pytest

from apex_quant.data.oanda_adapter import OandaAdapter


def _weekday_hours(start: str, end: str) -> pd.DatetimeIndex:
    """Synthetic forex-ish calendar: hourly bars on weekdays only, ns resolution.

    Weekend thinning is what makes every full 4,800-hour span come back as a
    short (< 4,800) batch from the real API.
    """
    idx = pd.date_range(start, end, freq="h", tz="UTC", name="timestamp")
    idx = idx[idx.weekday < 5]
    return idx.astype("datetime64[ns, UTC]")


class _FakeOandaHTTP:
    """Emulates /v3/instruments/{inst}/candles: [from, to] slice + 5,000 cap."""

    CAP = 5000

    def __init__(self, times: pd.DatetimeIndex):
        self._times = times
        self.batch_sizes: list[int] = []
        self.calls: list[tuple[str, str]] = []

    def fetch(self, ticker: str, start_iso: str, end_iso: str, granularity: str) -> dict:
        start = pd.Timestamp(start_iso)
        end = pd.Timestamp(end_iso)
        window = self._times[(self._times >= start) & (self._times <= end)][: self.CAP]
        self.batch_sizes.append(len(window))
        self.calls.append((start_iso, end_iso))
        candles = [
            {
                "time": t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                "mid": {"o": "1.10", "h": "1.20", "l": "1.00", "c": "1.15"},
                "volume": 100,
            }
            for t in window
        ]
        return {"candles": candles}


@pytest.fixture
def adapter(monkeypatch):
    """Adapter wired to the fake HTTP layer, no network, no throttle delay."""
    monkeypatch.setenv("APEX_OANDA_API_KEY", "test-key")
    monkeypatch.setattr(OandaAdapter, "_probe_endpoint", lambda self: "https://api-fxpractice.oanda.com")
    monkeypatch.setattr("time.sleep", lambda *_: None)
    return OandaAdapter()


def test_paginates_full_window_past_5000_candle_cap(adapter, monkeypatch):
    """A ~3-year 1h window (~18k bars, well over one 5,000-candle span) must
    come back complete, in multiple chunks."""
    expected = _weekday_hours("2021-01-01", "2024-01-01")
    assert len(expected) > _FakeOandaHTTP.CAP  # sanity: really multi-span

    fake = _FakeOandaHTTP(expected)
    monkeypatch.setattr(adapter, "_fetch_chunk", fake.fetch)

    df = adapter.get_history("EUR/USD", "2021-01-01", "2024-01-01", "1h")

    assert len(fake.calls) > 1
    pd.testing.assert_index_equal(df.index, expected)


def test_short_batches_do_not_terminate_pagination(adapter, monkeypatch):
    """Weekend thinning makes EVERY full span return < 4,800 candles; the old
    code broke out of the loop on exactly this condition after chunk one."""
    expected = _weekday_hours("2021-01-01", "2024-01-01")
    fake = _FakeOandaHTTP(expected)
    monkeypatch.setattr(adapter, "_fetch_chunk", fake.fetch)

    df = adapter.get_history("EUR/USD", "2021-01-01", "2024-01-01", "1h")

    # Every served batch was "short" — the condition that used to kill the loop.
    assert fake.batch_sizes and all(n < 4800 for n in fake.batch_sizes)
    assert len(fake.calls) > 1
    assert df.index[0] == expected[0]
    assert df.index[-1] == expected[-1]
    assert len(df) == len(expected)


def test_empty_response_ends_pagination(adapter, monkeypatch):
    """When the requested window extends past available data, the adapter
    returns what exists and stops on the genuinely empty response."""
    available = _weekday_hours("2022-01-03", "2022-06-30")
    fake = _FakeOandaHTTP(available)
    monkeypatch.setattr(adapter, "_fetch_chunk", fake.fetch)

    df = adapter.get_history("EUR/USD", "2022-01-03", "2023-01-01", "1h")

    pd.testing.assert_index_equal(df.index, available)
    assert fake.batch_sizes[-1] == 0  # terminated on empty, not on short
