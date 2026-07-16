"""Tests for the MultiTimeframeMomentum strategy and on-the-fly resampler."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy
from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum, resample_ohlcv


class MockBaseStrategy(Strategy):
    """A mock strategy that always returns a fixed signal direction."""
    def __init__(self, direction: Direction, timeframe: str = "1h") -> None:
        self.direction = direction
        self.timeframe = timeframe
        self.holding_horizon = 10
        self.reward_risk = 1.5

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        return Signal(
            instrument=instrument,
            direction=self.direction,
            probability=0.6,
            reward_risk=self.reward_risk,
            confidence=0.5,
            timeframe=self.timeframe,
            rationale="mock base strategy signal"
        )


def test_resample_ohlcv():
    idx = pd.date_range("2020-01-01", periods=48, freq="h", tz="UTC")
    df = pd.DataFrame({
        "open": range(48),
        "high": [x + 0.5 for x in range(48)],
        "low": [x - 0.5 for x in range(48)],
        "close": [x + 0.1 for x in range(48)],
        "volume": [1.0] * 48
    }, index=idx)
    
    res = resample_ohlcv(df, "1d")
    
    # 48 hours = 2 days
    assert len(res) == 2
    # Day 1 open is first hour open (0)
    assert res["open"].iloc[0] == 0
    # Day 1 close is hour 23 close (23.1)
    assert abs(res["close"].iloc[0] - 23.1) < 1e-7
    # Day 1 high is max high (23.5)
    assert abs(res["high"].iloc[0] - 23.5) < 1e-7
    # Day 1 low is min low (-0.5)
    assert abs(res["low"].iloc[0] - (-0.5)) < 1e-7
    # Day 1 volume sum is 24
    assert res["volume"].iloc[0] == 24.0


def test_multi_timeframe_filtering():
    # Construct a panel where close rises steadily
    idx = pd.date_range("2020-01-01", periods=1000, freq="h", tz="UTC")
    
    # Case 1: Uptrend (Close is increasing, so it is > MA)
    close_up = np.linspace(10.0, 20.0, 1000)
    df_up = pd.DataFrame({
        "open": close_up, "high": close_up, "low": close_up, "close": close_up, "volume": 1.0
    }, index=idx)
    pit_up = PointInTimeAccessor(df_up)
    t_end = idx[-1]
    
    # Base strategy returns LONG
    m_long = MultiTimeframeMomentum(MockBaseStrategy(Direction.LONG), htf_rule="1d", htf_ma_window=10)
    sig_long_allowed = m_long.generate(pit_up, t_end, "EUR/USD")
    assert sig_long_allowed.direction == Direction.LONG
    assert "aligned" in sig_long_allowed.rationale

    # Base strategy returns SHORT
    m_short = MultiTimeframeMomentum(MockBaseStrategy(Direction.SHORT), htf_rule="1d", htf_ma_window=10)
    sig_short_blocked = m_short.generate(pit_up, t_end, "EUR/USD")
    assert sig_short_blocked.direction == Direction.FLAT
    assert "Blocked" in sig_short_blocked.rationale

    # Case 2: Downtrend (Close is decreasing, so it is < MA)
    close_down = np.linspace(20.0, 10.0, 1000)
    df_down = pd.DataFrame({
        "open": close_down, "high": close_down, "low": close_down, "close": close_down, "volume": 1.0
    }, index=idx)
    pit_down = PointInTimeAccessor(df_down)
    
    sig_long_blocked = m_long.generate(pit_down, t_end, "EUR/USD")
    assert sig_long_blocked.direction == Direction.FLAT
    assert "Blocked" in sig_long_blocked.rationale
    
    sig_short_allowed = m_short.generate(pit_down, t_end, "EUR/USD")
    assert sig_short_allowed.direction == Direction.SHORT
    assert "aligned" in sig_short_allowed.rationale


def test_leakage_safety():
    idx = pd.date_range("2020-01-01", periods=300, freq="h", tz="UTC")
    close = np.linspace(10.0, 20.0, 300)
    df = pd.DataFrame({
        "open": close, "high": close, "low": close, "close": close, "volume": 1.0
    }, index=idx)
    
    cutoff = idx[200]
    m = MultiTimeframeMomentum(MockBaseStrategy(Direction.LONG), htf_rule="1d", htf_ma_window=5)
    
    pit_clean = PointInTimeAccessor(df)
    sig_clean = m.generate(pit_clean, cutoff, "EUR/USD")
    
    # Poison future data
    poisoned_df = df.copy()
    poisoned_df.loc[poisoned_df.index > cutoff, "close"] *= 0.001
    
    pit_poison = PointInTimeAccessor(poisoned_df)
    sig_poison = m.generate(pit_poison, cutoff, "EUR/USD")
    
    assert sig_clean.direction == sig_poison.direction
    assert sig_clean.rationale == sig_poison.rationale
