"""US large-cap short-term reversal: weekly-rebalance gating, bottom-N long-only
loser selection, min-history / min-universe gating, the de Groot cost filter
(significance + liquidity), the Nagel SPY vol-state filter, regime-instrument
exclusion, leakage safety, and PortfolioBacktester integration."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.backtest import PortfolioBacktester
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction
from apex_quant.strategies import ShortTermReversal


def _panel(drifts, names=None, n=400, noise=0.001, seed=1, spy_noise=0.003):
    """A panel of stocks with prescribed drifts (index 0 strongest up) plus a SPY
    reference. Business-day index — cash equities trade ~5 days a week."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n, tz="UTC", name="timestamp")
    names = names or [f"P{i}" for i in range(len(drifts))]
    panel = {}
    for name, dr in zip(names, drifts):
        close = 100.0 * np.exp(np.cumsum(rng.normal(dr, noise, n)))
        op = np.concatenate([[100.0], close[:-1]])
        hi = np.maximum(op, close) * 1.002
        lo = np.minimum(op, close) * 0.998
        panel[name] = pd.DataFrame(
            {"open": op, "high": hi, "low": lo, "close": close, "volume": 1e6}, index=idx)
    spy_close = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, spy_noise, n)))
    panel["SPY"] = pd.DataFrame(
        {"open": spy_close, "high": spy_close * 1.002, "low": spy_close * 0.998,
         "close": spy_close, "volume": 1e8}, index=idx)
    return panel


def _last_rebalance(model):
    return sorted(model._rebalance)[-1]


def test_bottom_n_long_only_buys_losers():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = ShortTermReversal(panel, bottom_n=2, min_universe=4)
    ranks = m.ranks_at(_last_rebalance(m))
    assert ranks and all(d == 1 for d, _z in ranks.values())   # long-only
    assert set(ranks) == {"P3", "P4"}                          # the two biggest losers
    assert m.signal_for("P0", _last_rebalance(m)).direction == Direction.FLAT


def test_spy_never_traded():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = ShortTermReversal(panel, bottom_n=5, min_universe=4)
    assert "SPY" not in m.instruments
    assert "SPY" not in m.strategies()
    assert "SPY" not in m.ranks_at(_last_rebalance(m))


def test_no_signal_mid_week():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = ShortTermReversal(panel, bottom_n=2, min_universe=4)
    idx = next(iter(panel.values())).index
    mid_week = [t for t in idx[300:] if t not in m._rebalance]
    assert mid_week, "expected non-rebalance bars in the eligible region"
    for t in mid_week:
        assert m.ranks_at(t) == {}                             # weekly rebalance only


def test_min_universe_gating():
    panel = _panel([0.002, 0.0, -0.002])                       # only 3 tradable names
    m = ShortTermReversal(panel, bottom_n=2, min_universe=4)
    assert m.ranks_at(_last_rebalance(m)) == {}


def test_min_history_excludes_late_listing():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012], n=400)
    late = _panel([-0.010], names=["LATE"], n=100, seed=9)     # biggest loser, 100 bars
    late["LATE"] = late["LATE"].set_index(
        next(iter(panel.values())).index[-100:])               # listed 100 bars ago
    panel.update({k: v for k, v in late.items() if k != "SPY"})
    m = ShortTermReversal(panel, bottom_n=1, min_universe=4, min_history=300)
    ranks = m.ranks_at(_last_rebalance(m))
    assert "LATE" not in ranks                                 # too young to rank
    assert "P3" in ranks                                       # the weakest eligible name


def test_cost_filter_requires_significant_move():
    """de Groot mode: quiet names (|5d ret| below the 1.5-sigma bar) are skipped;
    only the name with a genuine sell-off is eligible, and only while it sits in
    the liquid half of the universe."""
    panel = _panel([0.0, 0.0, 0.0, 0.0, 0.0], n=400, noise=0.0, spy_noise=0.003)
    big = panel["P0"].copy()
    c = big["close"].to_numpy().copy()
    c[-5:] = c[-5] * np.exp(np.cumsum(np.full(5, -0.022)))     # ~-10.5% over 5 bars
    big["close"] = c
    big["high"] = np.maximum(big["open"], big["close"]) * 1.001
    big["low"] = np.minimum(big["open"], big["close"]) * 0.999
    big["volume"] = 1e9                                        # keeps it in the liquid half
    panel["P0"] = big
    m = ShortTermReversal(panel, formation=5, filter_mode="cost", bottom_n=2,
                          min_universe=4, min_history=300)
    ranks = m.ranks_at(_last_rebalance(m))
    assert set(ranks) == {"P0"}                                # the only eligible name
    assert ranks["P0"][0] == 1


def test_vol_state_stands_down_in_low_vol():
    """Nagel mode: flat when SPY 21d realised vol is below its 126d median."""
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025], n=500, spy_noise=0.02)
    idx = panel["SPY"].index
    quiet = 100.0 * np.exp(np.cumsum(np.concatenate([
        np.random.default_rng(3).normal(0.0, 0.02, 440),       # loud history
        np.full(60, 0.0002),                                   # recent dead-calm stretch
    ])))
    panel["SPY"] = pd.DataFrame(
        {"open": quiet, "high": quiet * 1.001, "low": quiet * 0.999,
         "close": quiet, "volume": 1e8}, index=idx)
    m_on = ShortTermReversal(panel, filter_mode="vol_state", bottom_n=2, min_universe=4)
    t = _last_rebalance(m_on)
    assert m_on.ranks_at(t) == {}                              # vol below median -> flat
    m_off = ShortTermReversal(panel, filter_mode="plain", bottom_n=2, min_universe=4)
    assert m_off.ranks_at(t) != {}                             # unconditioned sleeve trades


def test_leakage_safe():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = ShortTermReversal(panel, filter_mode="cost", bottom_n=2, min_universe=4)
    cutoff = sorted(m._rebalance)[-3]                          # a real weekly boundary
    r_clean = m.ranks_at(cutoff)

    poisoned = {k: v.copy() for k, v in panel.items()}
    for v in poisoned.values():
        v.loc[v.index > cutoff, ["open", "high", "low", "close"]] *= 1000.0
        v.loc[v.index > cutoff, "volume"] *= 1000.0
    r_poison = ShortTermReversal(poisoned, filter_mode="cost", bottom_n=2,
                                 min_universe=4).ranks_at(cutoff)

    assert r_clean == r_poison          # future poison cannot change the rank at t


def test_signal_direction_and_bounded_probability():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025])
    m = ShortTermReversal(panel, bottom_n=2, min_universe=4)
    t = _last_rebalance(m)
    s_long, s_flat = m.signal_for("P4", t), m.signal_for("P2", t)
    assert s_long.direction == Direction.LONG
    assert s_flat.direction == Direction.FLAT
    assert 0.52 <= s_long.probability <= 0.70


def test_integration_with_portfolio_backtester():
    panel = _panel([0.0025, 0.0012, 0.0, -0.0012, -0.0025], n=500, noise=0.004)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items() if k != "SPY"}
    m = ShortTermReversal(panel, formation=5, bottom_n=2, min_universe=4,
                          min_history=300)
    res = PortfolioBacktester().run(pits, m.strategies(),
                                    timeframes={k: "1d" for k in pits}, warmup=250)
    assert len(res.equity) > 0
    assert res.metrics["n_trades"] >= 1
    directions = {t.direction for t in res.trades}
    assert directions == {"long"}       # long-only sleeve: no short ever flows through
    instruments = {t.instrument for t in res.trades}
    assert "SPY" not in instruments     # the vol-state reference is never traded
