"""Cross-sectional (rank) momentum: ranking correctness, leakage safety, the
long-only variant, min-universe gating, and PortfolioBacktester integration."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.backtest import PortfolioBacktester
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction
from apex_quant.strategies import CrossSectionalMomentum


def _panel(drifts, n=300, noise=0.003, seed=1):
    """A panel of pairs P0/USD..Pk/USD with prescribed drifts (P0 strongest up)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n, tz="UTC", name="timestamp")
    panel = {}
    for i, dr in enumerate(drifts):
        close = 1.0 * np.exp(np.cumsum(rng.normal(dr, noise, n)))
        op = np.concatenate([[1.0], close[:-1]])
        hi = np.maximum(op, close) * 1.002
        lo = np.minimum(op, close) * 0.998
        panel[f"P{i}/USD"] = pd.DataFrame(
            {"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)
    return panel


def _last(panel):
    return next(iter(panel.values())).index[-1]


def test_ranks_long_strong_short_weak():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = CrossSectionalMomentum(panel, long_frac=0.2, short_frac=0.2, min_universe=4)
    ranks = m.ranks_at(_last(panel))
    longs = {k for k, (d, _z) in ranks.items() if d == 1}
    shorts = {k for k, (d, _z) in ranks.items() if d == -1}
    assert "P0/USD" in longs          # strongest uptrend -> long
    assert "P4/USD" in shorts         # strongest downtrend -> short
    assert not (longs & shorts)


def test_min_universe_gating():
    panel = _panel([0.002, 0.0, -0.002])   # only 3 instruments
    m = CrossSectionalMomentum(panel, min_universe=4)
    assert m.ranks_at(_last(panel)) == {}  # not enough cross-section -> no signals


def test_leakage_safe():
    panel = _panel([0.002, 0.001, 0.0, -0.001, -0.002])
    idx = next(iter(panel.values())).index
    cutoff = idx[200]
    r_clean = CrossSectionalMomentum(panel, min_universe=4).ranks_at(cutoff)

    poisoned = {k: v.copy() for k, v in panel.items()}
    for v in poisoned.values():
        v.loc[v.index > cutoff, ["open", "high", "low", "close"]] *= 1000.0
    r_poison = CrossSectionalMomentum(poisoned, min_universe=4).ranks_at(cutoff)

    assert r_clean == r_poison          # future poison cannot change the rank at t


def test_signal_direction_and_bounded_probability():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = CrossSectionalMomentum(panel, long_frac=0.2, short_frac=0.2, min_universe=4)
    t = _last(panel)
    s_long, s_short, s_flat = m.signal_for("P0/USD", t), m.signal_for("P4/USD", t), m.signal_for("P2/USD", t)
    assert s_long.direction == Direction.LONG
    assert s_short.direction == Direction.SHORT
    assert s_flat.direction == Direction.FLAT
    assert 0.52 <= s_long.probability <= 0.70
    assert 0.52 <= s_short.probability <= 0.70


def test_long_only_variant():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = CrossSectionalMomentum(panel, allow_short=False, long_frac=0.2, min_universe=4)
    ranks = m.ranks_at(_last(panel))
    assert ranks and all(d == 1 for d, _z in ranks.values())
    assert m.signal_for("P4/USD", _last(panel)).direction == Direction.FLAT


def test_no_long_short_overlap_extreme_fracs():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = CrossSectionalMomentum(panel, long_frac=0.8, short_frac=0.8, min_universe=4)
    ranks = m.ranks_at(_last(panel))
    longs = {k for k, (d, _z) in ranks.items() if d == 1}
    shorts = {k for k, (d, _z) in ranks.items() if d == -1}
    assert not (longs & shorts)


def test_integration_with_portfolio_backtester():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025], n=400, noise=0.004)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    m = CrossSectionalMomentum(panel, lookback=63, vol_window=63,
                               long_frac=0.4, short_frac=0.4, min_universe=4)
    res = PortfolioBacktester().run(pits, m.strategies(),
                                    timeframes={k: "1d" for k in panel}, warmup=200)
    assert len(res.equity) > 0
    assert res.metrics["n_trades"] >= 1
    directions = {t.direction for t in res.trades}
    assert "short" in directions       # short signals flow through the book
