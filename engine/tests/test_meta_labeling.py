"""Meta-labelling wrapper: the secondary model gates the primary's weak trades.

Covers pass-through when unfitted, fitting the secondary on triple-barrier labels,
the veto gate (high threshold -> flat, low threshold -> keep), the calibrated
probability override, backtester integration, and validation-spec registration.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest import Backtester
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction
from apex_quant.strategies import MetaLabeledStrategy, RegimeGatedMomentum


def _series(rets, start="2016-01-01", base=1.10):
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range(start, periods=len(rets), tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


def _trend(n=750, drift=0.0006, noise=0.012, seed=3):
    # Noisy enough that momentum-longs both win AND lose -> two-class meta-labels.
    return _series(np.random.default_rng(seed).normal(drift, noise, n))


@pytest.fixture(scope="module")
def fitted():
    """A meta-labelled strategy fit once (threshold only affects generate)."""
    df = _trend()
    pit = PointInTimeAccessor(df)
    base = RegimeGatedMomentum()
    meta = MetaLabeledStrategy(base, model="linear", threshold=0.5, min_samples=8, holding_horizon=10)
    meta.fit(pit, df.index[252:660])
    return df, pit, base, meta


def _first_fired_bar(base, pit, stamps):
    for t in stamps:
        if base.generate(pit, t, "EUR/USD").direction != Direction.FLAT:
            return t
    return None


# -- structure -----------------------------------------------------------------
def test_import_and_naming():
    base = RegimeGatedMomentum()
    meta = MetaLabeledStrategy(base, model="gbm")
    assert meta.name == "meta_regime_gated_momentum_gbm"
    assert meta.is_fitted() is False


def test_passthrough_when_unfitted():
    df = _trend()
    pit = PointInTimeAccessor(df)
    base = RegimeGatedMomentum()
    base.fit(pit, df.index[:400])
    meta = MetaLabeledStrategy(base, model="linear")  # never fit -> pass-through
    t = df.index[700]
    assert meta.generate(pit, t, "EUR/USD").direction == base.generate(pit, t, "EUR/USD").direction


def test_fits_secondary_model(fitted):
    _, _, _, meta = fitted
    assert meta.is_fitted() is True


# -- the meta gate -------------------------------------------------------------
def test_high_threshold_vetoes_to_flat(fitted):
    df, pit, base, meta = fitted
    meta.threshold = 0.999  # essentially nothing clears this
    fired = _first_fired_bar(base, pit, df.index[660:745])
    assert fired is not None, "primary took no trades in the test window"
    sig = meta.generate(pit, fired, "EUR/USD")
    assert sig.direction == Direction.FLAT
    assert "meta-gate" in sig.rationale


def test_low_threshold_keeps_direction_and_overrides_prob(fitted):
    df, pit, base, meta = fitted
    meta.threshold = 0.0  # never gate
    fired = _first_fired_bar(base, pit, df.index[660:745])
    assert fired is not None
    b = base.generate(pit, fired, "EUR/USD")
    m = meta.generate(pit, fired, "EUR/USD")
    assert m.direction == b.direction               # side preserved
    assert 0.0 <= m.probability <= 1.0              # calibrated meta probability
    assert "meta" in m.rationale


def test_flat_primary_stays_flat(fitted):
    df, pit, base, meta = fitted
    meta.threshold = 0.5
    # A bar with too little history makes the primary flat; the wrapper must not
    # invent a trade.
    early = df.index[5]
    assert meta.generate(pit, early, "EUR/USD").direction == Direction.FLAT


# -- integration ---------------------------------------------------------------
def test_plugs_into_backtester(fitted):
    df, pit, base, meta = fitted
    meta.threshold = 0.4
    res = Backtester().run(pit, meta, "EUR/USD", warmup=252)
    assert len(res.equity) > 0
    assert "n_trades" in res.metrics
    assert res.metrics["final_equity"] > 0


def test_validation_spec_registered():
    from apex_quant.validation.report import STRATEGY_SPECS, meta_factory
    assert "meta_labeled" in STRATEGY_SPECS
    strat = meta_factory(model="linear", threshold=0.5, holding_horizon=10)
    assert strat.__class__.__name__ == "MetaLabeledStrategy"
    assert strat.threshold == 0.5
