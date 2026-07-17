"""Ensemble vote sleeve: vote arithmetic, threshold gating, and integration."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.ensemble import EnsembleVote


def _panel(drifts, n=350, noise=0.005, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n, tz="UTC", name="timestamp")
    out = {}
    for i, dr in enumerate(drifts):
        close = 1.0 * np.exp(np.cumsum(rng.normal(dr, noise, n)))
        op = np.concatenate([[1.0], close[:-1]])
        out[f"P{i}/USD"] = pd.DataFrame(
            {"open": op, "high": np.maximum(op, close) * 1.002,
             "low": np.minimum(op, close) * 0.998, "close": close, "volume": 1.0}, index=idx)
    return out


def _fake_rates(instrument, t):
    # base rate 2%, quote rate 1% for every pair — a flat, valid carry surface.
    return (0.02, 0.01)


class _Stub:
    """Stub sleeve returning a fixed direction for every instrument."""
    def __init__(self, d): self.d = d
    def signal_for(self, instrument, t):
        return Signal(instrument=instrument, direction=self.d, probability=0.6, reward_risk=1.5)


def _rig(model, ts_vote, cs, ccy, carry):
    """Force known votes onto a built model."""
    model._ts_vote = lambda inst, t: ts_vote
    model._cs.signal_for = _Stub(cs).signal_for
    model._ccy.signal_for = _Stub(ccy).signal_for
    model._carry.signal_for = _Stub(carry).signal_for


@pytest.fixture(scope="module")
def base_model():
    panel = _panel([0.002, 0.001, 0.0005, -0.0005, -0.001, -0.002])
    return panel, EnsembleVote(panel, rate_provider=_fake_rates, min_votes=2)


def test_min_votes_validation(base_model):
    panel, _ = base_model
    with pytest.raises(ValueError):
        EnsembleVote(panel, rate_provider=_fake_rates, min_votes=0)
    with pytest.raises(ValueError):
        EnsembleVote(panel, rate_provider=_fake_rates, min_votes=5)


def test_agreement_trades_disagreement_stands_aside(base_model):
    panel, model = base_model
    t = next(iter(panel.values())).index[-1]

    _rig(model, 1, Direction.LONG, Direction.LONG, Direction.FLAT)   # net +3
    s = model.signal_for("P0/USD", t)
    assert s.direction == Direction.LONG and "net +3" in s.rationale

    _rig(model, -1, Direction.SHORT, Direction.FLAT, Direction.FLAT)  # net -2
    assert model.signal_for("P0/USD", t).direction == Direction.SHORT

    _rig(model, 1, Direction.SHORT, Direction.LONG, Direction.SHORT)  # net -1 -> stand aside
    s = model.signal_for("P0/USD", t)
    assert s.direction == Direction.FLAT and "< 2 required" in s.rationale


def test_probability_grows_with_agreement_but_stays_modest(base_model):
    panel, model = base_model
    t = next(iter(panel.values())).index[-1]
    _rig(model, 1, Direction.LONG, Direction.LONG, Direction.FLAT)    # |net| = 3
    p3 = model.signal_for("P0/USD", t).probability
    _rig(model, 1, Direction.LONG, Direction.LONG, Direction.LONG)    # |net| = 4
    p4 = model.signal_for("P0/USD", t).probability
    assert 0.52 <= p3 < p4 <= 0.68


def test_real_components_integration(base_model):
    """Un-rigged: real sleeves compute, votes are well-formed, and the portfolio
    backtester accepts the ensemble's strategies dict."""
    panel, _ = base_model
    model = EnsembleVote(panel, rate_provider=_fake_rates, min_votes=2)
    t = next(iter(panel.values())).index[-1]
    v = model.votes_for("P0/USD", t)
    assert set(v) == {"ts", "cs", "ccy", "carry"}
    assert all(x in (-1, 0, 1) for x in v.values())

    from apex_quant.backtest import PortfolioBacktester
    pits = {k: PointInTimeAccessor(df) for k, df in panel.items()}
    res = PortfolioBacktester().run(pits, model.strategies(),
                                    timeframes={k: "1d" for k in panel}, warmup=150)
    assert len(res.equity) > 0                      # runs end-to-end; trading optional
