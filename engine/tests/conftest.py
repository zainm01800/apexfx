"""Shared fixtures: deterministic synthetic OHLCV that obeys the contract.

Synthetic data (not live Yahoo) keeps tests fast, offline, and reproducible.
The builder guarantees OHLC integrity and positivity so quality tests can inject
specific defects against a known-clean baseline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.config import set_global_seeds


def _make_ohlcv(
    n: int = 300,
    start: str = "2022-01-03",  # a Monday
    seed: int = 42,
    base: float = 1.10,
    vol: float = 0.005,
) -> pd.DataFrame:
    """Build a clean daily forex-like OHLCV frame on a business-day calendar."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=start, periods=n, tz="UTC", name="timestamp")
    rets = rng.normal(0.0, vol, n)
    close = base * np.exp(np.cumsum(rets))
    open_ = np.empty(n)
    open_[0] = base
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0.0, vol * 0.6, n)) + 0.0005
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.integers(1_000, 5_000, n).astype("float64")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


@pytest.fixture(autouse=True)
def _seed():
    set_global_seeds(42)


@pytest.fixture
def make_ohlcv():
    """Expose the builder to test modules."""
    return _make_ohlcv


@pytest.fixture
def clean_daily() -> pd.DataFrame:
    return _make_ohlcv()
