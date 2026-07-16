"""Adaptive-LLM layer quarantine.

The AdaptiveWrapperStrategy's LLM veto is non-deterministic and unvalidated, so it
must be OFF by default (deterministic pass-through, no LLM call) and only run when
a research caller explicitly opts in.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest.adaptive import AdaptiveWrapperStrategy
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy


class _StubBase(Strategy):
    name = "stub"

    def __init__(self, direction=Direction.LONG):
        self.direction = direction

    def generate(self, pit, t, instrument=""):
        return Signal(instrument=instrument, direction=self.direction, probability=0.6, reward_risk=1.5)


class _RaisingLLM:
    def complete(self, *a, **k):
        raise AssertionError("LLM must not be called when the veto is disabled")


class _FakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete(self, *a, **k):
        self.calls += 1
        return self.response


def _trend(n=400, drift=0.0009, noise=0.006, seed=3):
    rng = np.random.default_rng(seed)
    close = 1.10 * np.exp(np.cumsum(rng.normal(drift, noise, n)))
    op = np.concatenate([[1.10], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range("2016-01-01", periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


def test_experimental_marker():
    assert AdaptiveWrapperStrategy.experimental is True


def test_passthrough_by_default_never_calls_llm():
    w = AdaptiveWrapperStrategy(_StubBase(), rules=["Avoid LONG when x"], app_url="", llm=_RaisingLLM())
    assert w.enable_llm_veto is False
    sig = w.generate(pit=None, t="2020-01-01", instrument="EUR/USD")
    assert sig.direction == Direction.LONG  # base signal, untouched, no LLM invoked


def test_enabling_veto_warns():
    with pytest.warns(UserWarning, match="non-deterministic"):
        AdaptiveWrapperStrategy(_StubBase(), ["rule"], "", enable_llm_veto=True, llm=_FakeLLM("{}"))


def test_optin_veto_flattens_on_violation():
    df = _trend()
    pit = PointInTimeAccessor(df)
    fake = _FakeLLM('{"violates": true, "rule_violated": "no longs in downtrend"}')
    with pytest.warns(UserWarning):
        w = AdaptiveWrapperStrategy(_StubBase(), ["rule"], "", enable_llm_veto=True, llm=fake)
    sig = w.generate(pit, df.index[350], "EUR/USD")
    assert sig.direction == Direction.FLAT
    assert fake.calls == 1
    assert "Vetoed by adaptive rule" in sig.rationale


def test_optin_allows_when_no_violation():
    df = _trend()
    pit = PointInTimeAccessor(df)
    fake = _FakeLLM('{"violates": false, "rule_violated": ""}')
    with pytest.warns(UserWarning):
        w = AdaptiveWrapperStrategy(_StubBase(), ["rule"], "", enable_llm_veto=True, llm=fake)
    sig = w.generate(pit, df.index[350], "EUR/USD")
    assert sig.direction == Direction.LONG
    assert fake.calls == 1
