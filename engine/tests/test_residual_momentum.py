"""Residual (idiosyncratic) momentum strategy.

The two properties that make this signal what it is, and must not silently regress:
  1. **Point-in-time safety** — the score at t must not move when future bars are appended.
  2. **The residualisation actually happens** — an instrument that only moves WITH the market
     must not out-rank one with genuine idiosyncratic strength, even if its raw return is
     higher. That is the entire thesis; without it this is total-return momentum with extra
     arithmetic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.risk.types import Direction
from apex_quant.strategies.residual_momentum import (
    ResidualMomentum, ResidualMomentumStrategy,
)

N = 700
IDX = pd.date_range("2018-01-01", periods=N, freq="B", tz="UTC")


def _frame(rets: np.ndarray) -> pd.DataFrame:
    close = pd.Series(100.0 * np.cumprod(1.0 + rets), index=IDX)
    return pd.DataFrame(
        {"open": close, "high": close * 1.002, "low": close * 0.998,
         "close": close, "volume": 1_000.0},
        index=IDX,
    )


def _panel(n_names: int = 50, seed: int = 7) -> dict[str, pd.DataFrame]:
    """A market factor plus idiosyncratic noise — the setting residualisation assumes."""
    rng = np.random.default_rng(seed)
    mkt = rng.normal(0.0004, 0.010, N)
    panel = {}
    for i in range(n_names):
        beta = 0.5 + (i % 5) * 0.25
        panel[f"S{i:02d}"] = _frame(beta * mkt + rng.normal(0.0, 0.008, N))
    return panel


def test_scores_are_point_in_time():
    """Appending future bars must not change the score at an earlier timestamp."""
    panel = _panel()
    t = IDX[600]

    full = ResidualMomentum(panel, min_universe=10)
    truncated = ResidualMomentum(
        {k: v.loc[:t] for k, v in panel.items()}, min_universe=10
    )
    a, b = full.ranks_at(t), truncated.ranks_at(t)
    assert set(a) == set(b), "top-N membership changed when future bars were removed"
    for k in a:
        assert a[k] == pytest.approx(b[k], rel=1e-9), f"{k} score used future data"


def test_residualisation_demotes_a_pure_beta_rider():
    """A high-beta name that ONLY tracks the market must lose to a lower-raw-return name
    with real idiosyncratic strength. Total-return momentum would rank them the other way."""
    rng = np.random.default_rng(11)
    mkt = rng.normal(0.0008, 0.010, N)          # persistently rising market

    panel = {f"N{i:02d}": _frame(1.0 * mkt + rng.normal(0.0, 0.006, N)) for i in range(45)}
    # Pure beta rider: big raw return, but ALL of it is market.
    panel["RIDER"] = _frame(2.0 * mkt)
    # Genuine idiosyncratic winner: lower raw return, but its own drift.
    panel["ALPHA"] = _frame(0.3 * mkt + 0.0009 + rng.normal(0.0, 0.004, N))

    t = IDX[650]
    model = ResidualMomentum(panel, top_n=10, min_universe=10)
    scores = model._scores.loc[t]

    raw = {k: float(v["close"].loc[t] / v["close"].iloc[0]) for k, v in panel.items()}
    assert raw["RIDER"] > raw["ALPHA"], "fixture invalid: rider should have higher raw return"
    assert scores["ALPHA"] > scores["RIDER"], (
        "residualisation failed — the pure beta rider still out-ranks genuine alpha"
    )


def test_no_signal_until_the_cross_section_is_wide_enough():
    """Residualising against a handful of names is noise, not a factor."""
    panel = _panel(n_names=8)
    model = ResidualMomentum(panel, min_universe=40, top_n=3)
    assert model.ranks_at(IDX[600]) == {}

    wide = ResidualMomentum(_panel(n_names=50), min_universe=40, top_n=3)
    assert len(wide.ranks_at(IDX[600])) == 3


def test_only_top_n_get_a_long_signal_and_the_rest_are_flat():
    panel = _panel(n_names=50)
    model = ResidualMomentum(panel, top_n=5, min_universe=10)
    t = IDX[600]
    chosen = set(model.ranks_at(t))
    assert len(chosen) == 5

    longs = [i for i in panel if model.signal_for(i, t).direction == Direction.LONG]
    flats = [i for i in panel if model.signal_for(i, t).direction == Direction.FLAT]
    assert set(longs) == chosen
    assert len(flats) == len(panel) - 5
    # long-only by design: shorting residual losers is a different strategy
    assert not any(model.signal_for(i, t).direction == Direction.SHORT for i in panel)


def test_probability_stays_in_the_declared_band():
    panel = _panel(n_names=50)
    model = ResidualMomentum(panel, top_n=10, min_universe=10)
    for t in (IDX[400], IDX[600], IDX[-1]):
        for inst in panel:
            p = model.signal_for(inst, t).probability
            assert 0.5 <= p <= 0.70, f"probability {p} escaped the band"


def _score_after_spike(panel, tgt, spike_at, t):
    spiked = {k: v.copy() for k, v in panel.items()}
    df = spiked[tgt].copy()
    df.loc[spike_at:, ["open", "high", "low", "close"]] *= 1.30
    spiked[tgt] = df
    model = ResidualMomentum(spiked, lookback=252, skip=21, min_universe=10)
    return float(model._scores.loc[t, tgt])


def test_skip_window_blunts_a_recent_spike_far_more_than_an_older_one():
    """12-1 momentum: a move inside the skip window must barely register, while the same
    move just OUTSIDE it moves the score hard.

    Not asserted as exactly zero: the rolling market-beta regression necessarily estimates
    on recent bars, so a spike large enough to shift the cross-sectional mean leaks in at
    second order. The differential is the real property, and it is what the skip buys.
    """
    panel = _panel(n_names=50, seed=3)
    t = IDX[650]
    base = float(
        ResidualMomentum(panel, lookback=252, skip=21, min_universe=10)._scores.loc[t, "S07"]
    )

    inside = _score_after_spike(panel, "S07", IDX[645], t)   # within the skipped month
    outside = _score_after_spike(panel, "S07", IDX[600], t)  # inside the scored window

    moved_inside = abs(inside - base)
    moved_outside = abs(outside - base)

    assert moved_outside > 0.5, "fixture invalid: the spike should move an in-window score"
    assert moved_inside < 0.2 * moved_outside, (
        f"skip window is not doing its job: a spike inside it moved the score "
        f"{moved_inside:.3f} vs {moved_outside:.3f} outside"
    )


def test_adapter_delegates_to_the_shared_model():
    panel = _panel(n_names=50)
    model = ResidualMomentum(panel, top_n=5, min_universe=10)
    adapters = model.strategies()
    assert len(adapters) == len(panel)

    t = IDX[600]
    for inst, ad in adapters.items():
        assert isinstance(ad, ResidualMomentumStrategy)
        assert ad.generate(None, t, inst).direction == model.signal_for(inst, t).direction


@pytest.mark.parametrize("kwargs", [
    {"lookback": 1}, {"skip": -1}, {"top_n": 0}, {"vol_window": 1},
])
def test_invalid_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        ResidualMomentum(_panel(n_names=5), **kwargs)
