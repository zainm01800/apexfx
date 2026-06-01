"""Phase 3 P3-1: LLM client behaviour + evidence grounding."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.ai import AppAILLM, EvidencePack, FakeLLM, PriorResult, extract_json, gather_evidence
from apex_quant.config import AiConfig
from apex_quant.data.point_in_time import PointInTimeAccessor


def _trend(n=600, drift=0.001, noise=0.004, seed=3):
    rng = np.random.default_rng(seed)
    close = 1.10 * np.exp(np.cumsum(rng.normal(drift, noise, n)))
    op = np.concatenate([[1.10], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range("2018-01-01", periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


# -- extract_json ---------------------------------------------------------------
def test_extract_json_from_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_from_prose():
    assert extract_json('Sure! Here it is: [{"x":2}] hope that helps') == [{"x": 2}]


def test_extract_json_none_on_garbage():
    assert extract_json("no json here") is None
    assert extract_json(None) is None


# -- FakeLLM --------------------------------------------------------------------
def test_fake_llm_list_consumes_then_repeats():
    llm = FakeLLM(["a", "b"])
    assert llm.complete("p") == "a"
    assert llm.complete("p") == "b"
    assert llm.complete("p") == "b"      # repeats last


def test_fake_llm_callable():
    llm = FakeLLM(lambda prompt, system: f"echo:{prompt[:3]}")
    assert llm.complete("hello") == "echo:hel"


# -- AppAILLM graceful degradation ---------------------------------------------
def test_app_llm_unavailable_without_url():
    llm = AppAILLM(AiConfig(app_url=""))
    assert llm.available is False
    assert llm.complete("hi") is None


# -- evidence grounding ---------------------------------------------------------
def test_gather_evidence_fields():
    pit = PointInTimeAccessor(_trend())
    ev = gather_evidence(pit, "EUR/USD",
                         prior_results=[PriorResult(strategy="regime_gated_momentum", passed=False, dsr=0.32, pbo=0.99)],
                         headlines=["ECB holds rates", "USD softens"])
    assert isinstance(ev, EvidencePack)
    assert ev.instrument == "EUR/USD"
    assert set(ev.returns) == {"1m", "3m", "6m", "12m"}
    assert ev.rvol_ann is not None and ev.rvol_ann > 0
    assert "regime_gated_momentum" in ev.to_prompt()
    assert "ECB holds rates" in ev.to_prompt()


def test_evidence_prompt_mentions_already_tested():
    pit = PointInTimeAccessor(_trend())
    ev = gather_evidence(pit, "EUR/USD")
    p = ev.to_prompt()
    assert "ALREADY TESTED" in p
    assert "NOT instructions" in p          # news framed as data, not commands
