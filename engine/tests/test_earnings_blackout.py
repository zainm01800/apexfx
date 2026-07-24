"""Earnings-blackout wrapper (W4, 2026-07-24): ±1-trading-day NEW-entry suppression.

The wrapper is gate-script machinery (scripts/run_portfolio_gate_earnings_blackout.py);
these tests pin its calendar math and its delegation behaviour.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

from apex_quant.risk.types import Direction, Signal  # noqa: E402
from run_portfolio_gate_earnings_blackout import (  # noqa: E402
    EarningsBlackout,
    _blocked_set,
    _Model,
)


def _df(n=30):
    idx = pd.bdate_range("2020-01-01", periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)


def test_blocked_set_plus_minus_one_trading_day():
    df = _df()
    # Event 2020-01-14 (Tue, bar index 9): block bars 8, 9, 10.
    blocked = _blocked_set(df, ["2020-01-14"], 1)
    assert blocked == {df.index[8], df.index[9], df.index[10]}


def test_blocked_set_weekend_event_lands_on_next_bar():
    df = _df()
    # 2020-01-11 is a Saturday -> loc = Monday 2020-01-13 (bar 8): block 7, 8, 9.
    blocked = _blocked_set(df, ["2020-01-11"], 1)
    assert blocked == {df.index[7], df.index[8], df.index[9]}


def test_blocked_set_clips_at_edges():
    df = _df()
    blocked = _blocked_set(df, ["2020-01-01"], 1)   # first bar: loc-1 clips
    assert blocked == {df.index[0], df.index[1]}


class _Stub:
    holding_horizon = 21

    def generate(self, pit, t, instrument=""):
        return Signal(instrument=instrument, direction=Direction.LONG,
                      probability=0.60, reward_risk=1.5, timeframe="1d")


def test_wrapper_flats_only_blocked_bars():
    df = _df()
    blocked = _blocked_set(df, ["2020-01-14"], 1)
    w = EarningsBlackout(_Stub(), blocked, "AAPL")
    assert w.holding_horizon == 21                      # proxies engine-read attrs
    sig = w.generate(None, df.index[10], "AAPL")        # blocked bar -> FLAT
    assert sig.direction == Direction.FLAT
    assert "blackout" in sig.rationale
    sig = w.generate(None, df.index[12], "AAPL")        # unblocked -> delegates
    assert sig.direction == Direction.LONG
    assert sig.probability == pytest.approx(0.60)


def test_model_wraps_only_covered_instruments():
    class _TB:
        def strategies(self):
            return {"AAPL": _Stub(), "MSFT": _Stub()}
    m = _Model.__new__(_Model)
    m._tb = _TB()
    m._blackout_days = 1
    m._blocked = {"AAPL": {pd.Timestamp("2020-01-14", tz="UTC")}}
    strats = m.strategies()
    assert isinstance(strats["AAPL"], EarningsBlackout)
    assert not isinstance(strats["MSFT"], EarningsBlackout)
    # Blackout off -> no wrapping at all
    m._blackout_days = 0
    strats = m.strategies()
    assert not isinstance(strats["AAPL"], EarningsBlackout)
