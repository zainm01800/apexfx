"""Crypto cross-sectional momentum: weekly-rebalance gating, top-3 long-only
selection, min-history / min-universe gating, the BTC regime filter, leakage
safety, and PortfolioBacktester integration."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.backtest import PortfolioBacktester
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction
from apex_quant.strategies import CryptoXsMomentum


def _panel(drifts, names=None, n=400, noise=0.003, seed=1):
    """A panel of coins with prescribed drifts (index 0 strongest up). Calendar-day
    index — crypto trades 7 days a week. First name defaults to BTC/USD (the
    regime proxy)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, tz="UTC", name="timestamp")
    names = names or ["BTC/USD"] + [f"P{i}/USD" for i in range(1, len(drifts))]
    panel = {}
    for name, dr in zip(names, drifts):
        close = 100.0 * np.exp(np.cumsum(rng.normal(dr, noise, n)))
        op = np.concatenate([[100.0], close[:-1]])
        hi = np.maximum(op, close) * 1.002
        lo = np.minimum(op, close) * 0.998
        panel[name] = pd.DataFrame(
            {"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)
    return panel


def _last_rebalance(model):
    return sorted(model._rebalance)[-1]


def test_top_n_long_only_on_rebalance_bar():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = CryptoXsMomentum(panel, top_n=2, min_universe=4)
    ranks = m.ranks_at(_last_rebalance(m))
    assert ranks and all(d == 1 for d, _z in ranks.values())   # long-only
    assert set(ranks) == {"BTC/USD", "P1/USD"}                 # the two strongest
    assert m.signal_for("P4/USD", _last_rebalance(m)).direction == Direction.FLAT


def test_no_signal_mid_week():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = CryptoXsMomentum(panel, top_n=2, min_universe=4)
    idx = next(iter(panel.values())).index
    mid_week = [t for t in idx[300:] if t not in m._rebalance]
    assert mid_week, "expected non-rebalance bars in the eligible region"
    for t in mid_week:
        assert m.ranks_at(t) == {}                             # weekly rebalance only


def test_min_universe_gating():
    panel = _panel([0.002, 0.0, -0.002])                       # only 3 instruments
    m = CryptoXsMomentum(panel, top_n=2, min_universe=4)
    assert m.ranks_at(_last_rebalance(m)) == {}


def test_min_history_excludes_late_listing():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012], n=400)
    late = _panel([0.010], names=["LATE/USD"], n=100, seed=9)  # strongest, but only 100 bars
    late["LATE/USD"] = late["LATE/USD"].set_index(
        next(iter(panel.values())).index[-100:])               # listed 100 bars ago
    panel.update(late)
    m = CryptoXsMomentum(panel, top_n=1, min_universe=4, min_history=300)
    ranks = m.ranks_at(_last_rebalance(m))
    assert "LATE/USD" not in ranks                             # too young to rank
    assert "BTC/USD" in ranks                                  # the strongest eligible name


def test_regime_filter_blocks_when_btc_trend_down():
    panel = _panel([-0.0025, 0.0025, 0.0012, 0.0, -0.0012])    # BTC down, alts up
    t = None
    m_on = CryptoXsMomentum(panel, top_n=2, min_universe=4, regime_filter=True)
    t = _last_rebalance(m_on)
    assert m_on.ranks_at(t) == {}                              # class trend down -> flat
    m_off = CryptoXsMomentum(panel, top_n=2, min_universe=4, regime_filter=False)
    assert m_off.ranks_at(t) != {}                             # unfiltered sleeve trades


def test_leakage_safe():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = CryptoXsMomentum(panel, top_n=2, min_universe=4)
    cutoff = sorted(m._rebalance)[-3]                          # a real weekly boundary
    r_clean = m.ranks_at(cutoff)

    poisoned = {k: v.copy() for k, v in panel.items()}
    for v in poisoned.values():
        v.loc[v.index > cutoff, ["open", "high", "low", "close"]] *= 1000.0
    r_poison = CryptoXsMomentum(poisoned, top_n=2, min_universe=4).ranks_at(cutoff)

    assert r_clean == r_poison          # future poison cannot change the rank at t


def test_signal_direction_and_bounded_probability():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = CryptoXsMomentum(panel, top_n=2, min_universe=4)
    t = _last_rebalance(m)
    s_long, s_flat = m.signal_for("BTC/USD", t), m.signal_for("P2/USD", t)
    assert s_long.direction == Direction.LONG
    assert s_flat.direction == Direction.FLAT
    assert 0.52 <= s_long.probability <= 0.70


def test_integration_with_portfolio_backtester():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025], n=500, noise=0.004)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    m = CryptoXsMomentum(panel, lookback=21, vol_window=63, top_n=2, min_universe=4,
                         min_history=300)
    res = PortfolioBacktester().run(pits, m.strategies(),
                                    timeframes={k: "1d" for k in panel}, warmup=250)
    assert len(res.equity) > 0
    assert res.metrics["n_trades"] >= 1
    directions = {t.direction for t in res.trades}
    assert directions == {"long"}       # long-only sleeve: no short ever flows through
