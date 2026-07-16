"""Tests for point-in-time rate provider and cross-sectional carry strategy.

Covers point-in-time rate lookup, future-poison leakage safety, ranking
correctness, and PortfolioBacktester integration.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest import PortfolioBacktester
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction
from apex_quant.data.rates import CSVRateProvider
from apex_quant.strategies.carry import CrossSectionalCarry


def test_rate_provider_point_in_time_discipline():
    # Create a temporary CSV file with policy rates
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "rates.csv"
        df = pd.DataFrame({
            "effective_date": ["2020-01-01", "2020-06-01", "2020-12-01"],
            "USD": [0.0150, 0.0025, 0.0025],
            "EUR": [0.0000, 0.0000, 0.0000],
            "JPY": [-0.0010, -0.0010, -0.0010]
        })
        df.to_csv(csv_path, index=False)
        
        provider = CSVRateProvider(csv_path)
        
        # 1. Ask for a rate before any effective date -> should return None
        assert provider("EUR/USD", pd.Timestamp("2019-12-31")) is None
        
        # 2. Ask for a rate on the exact effective date
        res = provider("EUR/USD", pd.Timestamp("2020-01-01"))
        assert res == (0.0000, 0.0150)
        
        # 3. Ask for a rate in between effective dates -> should return the prior one
        res = provider("EUR/USD", pd.Timestamp("2020-03-15"))
        assert res == (0.0000, 0.0150)
        
        # 4. Ask for a rate after the next effective date
        res = provider("EUR/USD", pd.Timestamp("2020-06-01"))
        assert res == (0.0000, 0.0025)
        
        # 5. Invalid symbol -> should return None
        assert provider("INVALID", pd.Timestamp("2020-06-01")) is None


def _panel(n=300, noise=0.003, seed=1):
    """A synthetic panel of pairs."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n, tz="UTC", name="timestamp")
    panel = {}
    
    pairs = ["EUR/USD", "GBP/USD", "AUD/USD", "NZD/USD", "USD/JPY", "USD/CHF", "USD/CAD"]
    
    for symbol in pairs:
        close = 1.0 * np.exp(np.cumsum(rng.normal(0.0, noise, n)))
        op = np.concatenate([[1.0], close[:-1]])
        hi = np.maximum(op, close) * 1.002
        lo = np.minimum(op, close) * 0.998
        panel[symbol] = pd.DataFrame(
            {"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx
        )
    return panel


def _last(panel):
    return next(iter(panel.values())).index[-1]


def test_carry_ranks_correctness():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "rates.csv"
        # Engineer rates:
        # USD: 5.0%, EUR: 0.0%, JPY: -1.0%
        # differentials:
        # EUR/USD: base EUR(0%) - quote USD(5%) = -5% (highly negative)
        # USD/JPY: base USD(5%) - quote JPY(-1%) = +6% (highly positive)
        df = pd.DataFrame({
            "effective_date": ["2018-01-01"],
            "USD": [0.0500],
            "EUR": [0.0000],
            "GBP": [0.0100],
            "JPY": [-0.0100],
            "CHF": [0.0000],
            "AUD": [0.0150],
            "NZD": [0.0175],
            "CAD": [0.0125]
        })
        df.to_csv(csv_path, index=False)
        provider = CSVRateProvider(csv_path)
        
        panel = _panel()
        m = CrossSectionalCarry(panel, provider, long_frac=0.2, short_frac=0.2, min_universe=4)
        ranks = m.ranks_at(_last(panel))
        
        longs = {k for k, (d, _z) in ranks.items() if d == 1}
        shorts = {k for k, (d, _z) in ranks.items() if d == -1}
        
        # USD/JPY is highest yield diff (+6%) -> LONG
        assert "USD/JPY" in longs
        # EUR/USD is lowest yield diff (-5%) -> SHORT
        assert "EUR/USD" in shorts
        assert not (longs & shorts)


def test_carry_min_universe_gating():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "rates.csv"
        df = pd.DataFrame({
            "effective_date": ["2018-01-01"],
            "USD": [0.0500],
            "EUR": [0.0000],
            "GBP": [0.0100],
            "JPY": [-0.0100]
        })
        df.to_csv(csv_path, index=False)
        provider = CSVRateProvider(csv_path)
        
        panel = _panel()
        small_panel = {k: panel[k] for k in list(panel.keys())[:3]}
        m = CrossSectionalCarry(small_panel, provider, min_universe=4)
        assert m.ranks_at(_last(small_panel)) == {}  # Not enough pairs -> no signals


def test_carry_leakage_safe():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "rates.csv"
        df = pd.DataFrame({
            "effective_date": ["2018-01-01", "2020-01-01"],
            "USD": [0.0500, 0.0500],
            "EUR": [0.0000, 0.0000],
            "GBP": [0.0100, 0.0100],
            "JPY": [-0.0100, -0.0100],
            "CHF": [0.0000, 0.0000],
            "AUD": [0.0150, 0.0150],
            "NZD": [0.0175, 0.0175],
            "CAD": [0.0125, 0.0125]
        })
        df.to_csv(csv_path, index=False)
        provider = CSVRateProvider(csv_path)
        
        panel = _panel()
        idx = next(iter(panel.values())).index
        cutoff = idx[200]
        r_clean = CrossSectionalCarry(panel, provider, min_universe=4).ranks_at(cutoff)

        # Poison rates in the future (after cutoff)
        # Verify that CSVRateProvider lookup at cutoff is identical
        poisoned_df = df.copy()
        poisoned_df.loc[1, ["USD", "EUR"]] = [1.0, 1.0] # Poison 2020-01-01 row
        
        poisoned_csv_path = Path(tmpdir) / "poisoned_rates.csv"
        poisoned_df.to_csv(poisoned_csv_path, index=False)
        poisoned_provider = CSVRateProvider(poisoned_csv_path)
        
        r_poison = CrossSectionalCarry(panel, poisoned_provider, min_universe=4).ranks_at(cutoff)
        
        assert r_clean == r_poison  # Future poison cannot leak back


def test_carry_integration_with_portfolio_backtester():
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "rates.csv"
        df = pd.DataFrame({
            "effective_date": ["2018-01-01"],
            "USD": [0.0500],
            "EUR": [0.0000],
            "GBP": [0.0100],
            "JPY": [-0.0100],
            "CHF": [0.0000],
            "AUD": [0.0150],
            "NZD": [0.0175],
            "CAD": [0.0125]
        })
        df.to_csv(csv_path, index=False)
        provider = CSVRateProvider(csv_path)
        
        panel = _panel(n=400)
        pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
        m = CrossSectionalCarry(panel, provider, long_frac=0.4, short_frac=0.4, min_universe=4)
        
        res = PortfolioBacktester().run(
            pits, m.strategies(),
            timeframes={k: "1d" for k in panel}, warmup=200
        )
        assert isinstance(res.equity, pd.Series)
        assert len(res.trades) >= 0
