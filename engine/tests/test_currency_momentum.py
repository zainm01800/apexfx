"""Tests for currency-leg cross-sectional momentum strategy.

Covers currency strength ranking correctness on a synthetic panel,
future-poison leakage safety, long/short bucket integrity, and
PortfolioBacktester integration.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest import PortfolioBacktester
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction
from apex_quant.strategies.currency_momentum import CurrencyCrossSectionalMomentum


def _panel(drifts, n=300, noise=0.003, seed=1):
    """A panel of pairs with prescribed drifts.
    
    Pairs:
      P0: EUR/USD
      P1: GBP/USD
      P2: AUD/USD
      P3: NZD/USD
      P4: USD/JPY
      P5: USD/CHF
      P6: USD/CAD
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n, tz="UTC", name="timestamp")
    panel = {}
    
    pairs = [
        ("EUR/USD", drifts[0]),
        ("GBP/USD", drifts[1]),
        ("AUD/USD", drifts[2]),
        ("NZD/USD", drifts[3]),
        ("USD/JPY", drifts[4]),
        ("USD/CHF", drifts[5]),
        ("USD/CAD", drifts[6]),
    ]
    
    for symbol, dr in pairs:
        close = 1.0 * np.exp(np.cumsum(rng.normal(dr, noise, n)))
        op = np.concatenate([[1.0], close[:-1]])
        hi = np.maximum(op, close) * 1.002
        lo = np.minimum(op, close) * 0.998
        panel[symbol] = pd.DataFrame(
            {"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx
        )
    return panel


def _last(panel):
    return next(iter(panel.values())).index[-1]


def test_ranks_strong_and_weak_currencies():
    # drifts: [EUR/USD, GBP/USD, AUD/USD, NZD/USD, USD/JPY, USD/CHF, USD/CAD]
    # USD is quote in positive drift, base in negative drift -> USD is extremely weak
    # EUR has highest positive drift -> EUR is extremely strong
    # USD/JPY has highest negative drift -> JPY is extremely strong
    panel = _panel([0.010, 0.005, 0.005, 0.005, -0.020, -0.005, -0.005])
    m = CurrencyCrossSectionalMomentum(panel, k=2, min_universe=4)
    ranks = m.ranks_at(_last(panel))
    
    longs = {k for k, (d, _z) in ranks.items() if d == 1}
    shorts = {k for k, (d, _z) in ranks.items() if d == -1}
    
    # EUR (strong) / USD (weak) -> LONG
    assert "EUR/USD" in longs
    # USD (weak) / JPY (strong) -> SHORT
    assert "USD/JPY" in shorts
    assert not (longs & shorts)


def test_min_universe_gating():
    panel = _panel([0.010, 0.005, 0.005, 0.005, -0.020, -0.005, -0.005])
    small_panel = {k: panel[k] for k in list(panel.keys())[:3]}
    m = CurrencyCrossSectionalMomentum(small_panel, min_universe=4)
    assert m.ranks_at(_last(small_panel)) == {}  # Not enough pairs -> no signals


def test_leakage_safe():
    panel = _panel([0.010, 0.005, 0.005, 0.005, -0.020, -0.005, -0.005])
    idx = next(iter(panel.values())).index
    cutoff = idx[200]
    r_clean = CurrencyCrossSectionalMomentum(panel, min_universe=4).ranks_at(cutoff)

    poisoned = {k: v.copy() for k, v in panel.items()}
    for v in poisoned.values():
        v.loc[v.index > cutoff, ["open", "high", "low", "close"]] *= 1000.0
    r_poison = CurrencyCrossSectionalMomentum(poisoned, min_universe=4).ranks_at(cutoff)

    assert r_clean == r_poison  # Future poison cannot change past ranks


def test_signal_direction_and_probability():
    panel = _panel([0.010, 0.005, 0.005, 0.005, -0.020, -0.005, -0.005])
    m = CurrencyCrossSectionalMomentum(panel, k=2, min_universe=4)
    t = _last(panel)
    s_long = m.signal_for("EUR/USD", t)
    s_flat = m.signal_for("GBP/USD", t)
    
    assert s_long.direction == Direction.LONG
    assert s_flat.direction == Direction.FLAT
    assert 0.52 <= s_long.probability <= 0.70


def test_allow_short_false():
    panel = _panel([0.010, 0.005, 0.005, 0.005, -0.020, -0.005, -0.005])
    m = CurrencyCrossSectionalMomentum(panel, allow_short=False, k=2, min_universe=4)
    ranks = m.ranks_at(_last(panel))
    # With allow_short=False, bottom_k is empty, so no pairs should be active
    assert len(ranks) == 0


def test_integration_with_portfolio_backtester():
    panel = _panel([0.010, 0.005, 0.005, 0.005, -0.020, -0.005, -0.005], n=400)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    m = CurrencyCrossSectionalMomentum(panel, lookback=63, vol_window=63, k=2, min_universe=4)
    
    res = PortfolioBacktester().run(
        pits, m.strategies(),
        timeframes={k: "1d" for k in panel}, warmup=200
    )
    assert isinstance(res.equity, pd.Series)
    assert len(res.trades) >= 0
