"""Phase 3 P3-3: bull/bear/risk-supervisor debate."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.ai.client import AppAILLM, FakeLLM
from apex_quant.ai.debate import run_debate
from apex_quant.ai.hypothesis import Hypothesis
from apex_quant.ai.retrieval import gather_evidence
from apex_quant.config import AiConfig
from apex_quant.data.point_in_time import PointInTimeAccessor


def _ev():
    rng = np.random.default_rng(3)
    close = 1.10 * np.exp(np.cumsum(rng.normal(0.001, 0.004, 600)))
    op = np.concatenate([[1.10], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range("2018-01-01", periods=600, tz="UTC", name="timestamp")
    df = pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)
    return gather_evidence(PointInTimeAccessor(df), "EUR/USD")


def _router(verdict="test"):
    def respond(prompt, system):
        if "BULLISH CATALYST" in prompt:
            return "Momentum is positive and the trend is intact; the edge is plausible."
        if "BEARISH ARBITRAGEUR" in prompt:
            return "The sample is short and the edge likely vanishes out-of-sample."
        if "QUANTITATIVE RISK SUPERVISOR" in prompt:
            return (
                f'{{"verdict": "{verdict}", "cpcv_min_sharpe": 0.5, '
                f'"pbo_max": 0.55, "reason": "borderline; {verdict} it"}}'
            )
        return "?"
    return respond


def test_debate_with_llm_parses_verdict():
    hypo = Hypothesis.create("slow momentum", {"strategy": "baseline", "momentum_lookback": 126})
    res = run_debate(FakeLLM(_router("test")), _ev(), hypo)
    assert res.verdict == "test"
    assert res.llm_used and res.bull and res.bear
    assert "edge" in res.bull.lower()
    # New structured fields from upgraded supervisor
    assert res.cpcv_min_sharpe == 0.5
    assert res.pbo_max == 0.55


def test_debate_discard_verdict():
    hypo = Hypothesis.create("noisy idea", {"strategy": "ml_gbm"})
    res = run_debate(FakeLLM(_router("discard")), _ev(), hypo)
    assert res.verdict == "discard"


def test_debate_garbage_supervisor_defaults_to_test():
    def respond(prompt, system):
        if "QUANTITATIVE RISK SUPERVISOR" in prompt:
            return "I think maybe?"          # no JSON
        return "text"
    res = run_debate(FakeLLM(respond), _ev(), Hypothesis.create("x", {}))
    assert res.verdict == "test"             # safe default -> let validation decide


def test_debate_without_llm_defaults_to_test():
    res = run_debate(AppAILLM(AiConfig(app_url="")), _ev(), Hypothesis.create("x", {}))
    assert res.verdict == "test"
    assert res.llm_used is False
    assert "unavailable" in res.bull.lower()
