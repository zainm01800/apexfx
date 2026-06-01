"""Regime detection: rule-based direction, HMM fit, leakage, confidence bounds."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.regime import HmmRegime, RuleBasedRegime, classify_regime
from apex_quant.regime.base import RegimeLabel


def _series(rets: np.ndarray, start="2020-01-01", base=1.10) -> pd.DataFrame:
    n = len(rets)
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.0015
    lo = np.minimum(op, close) * 0.9985
    idx = pd.bdate_range(start, periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame(
        {"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx
    )


def _trend(n=320, drift=0.0015, noise=0.001, seed=3):
    rng = np.random.default_rng(seed)
    return _series(rng.normal(drift, noise, n))


# -- rule-based trend axis ------------------------------------------------------
def test_rule_based_detects_uptrend():
    pit = PointInTimeAccessor(_trend(drift=0.0015))
    lbl = RuleBasedRegime().classify(pit, pit.end)
    assert lbl.trend == "up"


def test_rule_based_detects_downtrend():
    pit = PointInTimeAccessor(_trend(drift=-0.0015))
    lbl = RuleBasedRegime().classify(pit, pit.end)
    assert lbl.trend == "down"


def test_rule_based_detects_ranging():
    # near-zero drift, tiny noise -> flat MA slope
    pit = PointInTimeAccessor(_trend(drift=0.0, noise=0.0005, seed=9))
    lbl = RuleBasedRegime().classify(pit, pit.end)
    assert lbl.trend == "ranging"


# -- rule-based vol axis --------------------------------------------------------
def test_rule_based_detects_high_vol_at_end():
    rng = np.random.default_rng(5)
    rets = np.concatenate([rng.normal(0, 0.002, 270), rng.normal(0, 0.02, 40)])
    pit = PointInTimeAccessor(_series(rets))
    assert RuleBasedRegime().classify(pit, pit.end).vol == "high"


def test_rule_based_detects_low_vol_at_end():
    rng = np.random.default_rng(5)
    rets = np.concatenate([rng.normal(0, 0.02, 270), rng.normal(0, 0.002, 40)])
    pit = PointInTimeAccessor(_series(rets))
    assert RuleBasedRegime().classify(pit, pit.end).vol == "low"


# -- HMM ------------------------------------------------------------------------
def test_hmm_returns_valid_label():
    rng = np.random.default_rng(7)
    rets = np.concatenate(
        [rng.normal(0.001, 0.004, 200), rng.normal(-0.001, 0.02, 200)]
    )
    pit = PointInTimeAccessor(_series(rets))
    lbl = HmmRegime().classify(pit, pit.end)
    assert isinstance(lbl, RegimeLabel)
    assert lbl.method.startswith("hmm")
    assert lbl.trend in ("up", "down", "ranging")
    assert lbl.vol in ("low", "normal", "high")
    assert 0.0 <= lbl.confidence <= 1.0


def test_hmm_is_deterministic():
    pit = PointInTimeAccessor(_trend(seed=4))
    a = HmmRegime().classify(pit, pit.end)
    b = HmmRegime().classify(pit, pit.end)
    assert (a.trend, a.vol, round(a.confidence, 6)) == (b.trend, b.vol, round(b.confidence, 6))


def test_hmm_falls_back_when_too_short(make_ohlcv):
    pit = PointInTimeAccessor(make_ohlcv(n=60))
    lbl = HmmRegime().classify(pit, pit.end)
    assert lbl.method == "hmm->rule_fallback"
    assert 0.0 <= lbl.confidence <= 1.0


# -- leakage + bounds -----------------------------------------------------------
def test_regime_is_point_in_time():
    df = _trend()
    t0 = df.index[250]
    base = classify_regime(PointInTimeAccessor(df), t0, method="rule_based")

    poisoned = df.copy()
    poisoned.loc[poisoned.index > t0, ["open", "high", "low", "close"]] *= 1000.0
    after = classify_regime(PointInTimeAccessor(poisoned), t0, method="rule_based")
    assert (base.trend, base.vol) == (after.trend, after.vol)
    assert base.confidence == after.confidence


def test_hmm_is_point_in_time():
    df = _trend()
    t0 = df.index[280]
    base = HmmRegime().classify(PointInTimeAccessor(df), t0)
    poisoned = df.copy()
    poisoned.loc[poisoned.index > t0, ["open", "high", "low", "close"]] *= 1000.0
    after = HmmRegime().classify(PointInTimeAccessor(poisoned), t0)
    assert (base.trend, base.vol) == (after.trend, after.vol)
    assert base.confidence == after.confidence


def test_aggression_scalar_bounds():
    for trend in ("up", "down", "ranging"):
        for vol in ("low", "normal", "high"):
            lbl = RegimeLabel(trend=trend, vol=vol, confidence=1.0, method="x")
            assert 0.0 <= lbl.aggression_scalar() <= 1.0
    # high-vol damps below a calm equivalent
    calm = RegimeLabel(trend="up", vol="low", confidence=1.0, method="x")
    loud = RegimeLabel(trend="up", vol="high", confidence=1.0, method="x")
    assert loud.aggression_scalar() < calm.aggression_scalar()
