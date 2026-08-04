"""Regression test: the paper stepper's cache top-up must not persist a
still-forming terminal bar (D-H2 extended to the manual ``store.save`` path).

Observed 2026-07-30: a mid-day local run of run_paper_portfolio.py cached
PLTR's *forming* 1d bar (close 121.20, volume 9.06M vs settled 122.26 /
27.8M) because ``_top_up`` saved via ``store.save`` — which normalises and
rejects off-calendar rows but deliberately does NOT trim forming tails (only
``get_or_fetch`` does). Any reader of the parquet between runs then consumed a
partial bar as if settled. The fix trims the tail in ``_top_up`` before save.
"""

import sys
from pathlib import Path

import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

from apex_quant.data import ParquetStore  # noqa: E402
from run_paper_portfolio import _top_up  # noqa: E402


class _FakeAdapter:
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def get_history(self, instrument, start, end, timeframe="1d"):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        return self._frame.loc[(self._frame.index >= start) & (self._frame.index <= end)]


def _frame(idx) -> pd.DataFrame:
    n = len(idx)
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1_000_000.0] * n,
        },
        index=pd.DatetimeIndex(idx, tz="UTC", name="timestamp"),
    )


def test_top_up_does_not_persist_forming_terminal_bar(tmp_path):
    """Mid-day run: today's bar is still forming — it must be neither
    persisted nor returned; yesterday's settled bar is the new tail."""
    # Wednesday 2026-07-15 12:00 UTC: the bar labelled 2026-07-15 is forming.
    now = pd.Timestamp("2026-07-15 12:00", tz="UTC")
    cutoff = pd.Timestamp("2026-07-15", tz="UTC")
    idx = pd.date_range("2026-07-13", "2026-07-15", freq="D", tz="UTC")
    store = ParquetStore(root=tmp_path)
    adapter = _FakeAdapter(_frame(idx))

    out = _top_up(store, adapter, "PLTR", cutoff, now)

    assert out.index[-1] == pd.Timestamp("2026-07-14", tz="UTC")
    persisted = store.load("PLTR", "1d")
    assert persisted.index[-1] == pd.Timestamp("2026-07-14", tz="UTC")
    assert len(persisted) == 2


def test_top_up_keeps_settled_terminal_bar(tmp_path):
    """After the close the same bar is complete and must be kept."""
    now = pd.Timestamp("2026-07-16 01:00", tz="UTC")  # next day: 07-15 settled
    cutoff = pd.Timestamp("2026-07-16", tz="UTC")
    idx = pd.date_range("2026-07-13", "2026-07-15", freq="D", tz="UTC")
    store = ParquetStore(root=tmp_path)
    adapter = _FakeAdapter(_frame(idx))

    out = _top_up(store, adapter, "PLTR", cutoff, now)

    assert out.index[-1] == pd.Timestamp("2026-07-15", tz="UTC")
    assert len(store.load("PLTR", "1d")) == 3
