"""Phase 3 P3-4: end-to-end research pipeline (propose -> debate -> validate -> rank)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.ai import FakeLLM, run_research
from apex_quant.ai.pipeline import DISCLAIMER
from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor


def _trend(n=620, drift=0.0012, noise=0.004, seed=3):
    rng = np.random.default_rng(seed)
    close = 1.10 * np.exp(np.cumsum(rng.normal(drift, noise, n)))
    op = np.concatenate([[1.10], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range("2018-01-01", periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


def _router(prompt, system):
    if "Output ONLY a JSON array" in prompt:
        return ('[{"thesis":"slow momentum rides the trend","config":'
                '{"strategy":"baseline","momentum_lookback":126,"holding_horizon":12},"rationale":"trend intact"},'
                '{"thesis":"gbm finds interactions","config":{"strategy":"ml_gbm","holding_horizon":10},'
                '"rationale":"nonlinearities"}]')
    if "[ROLE: BULL]" in prompt:
        return "Momentum is positive and the regime supports it."
    if "[ROLE: BEAR]" in prompt:
        return "Short sample; edge likely overfit."
    if "RISK SUPERVISOR" in prompt:
        # discard the ml idea, test the baseline idea
        return '{"verdict":"discard","reason":"compute not worth it"}' if "ml_gbm" in prompt \
            else '{"verdict":"test","reason":"cheap to validate"}'
    return "?"


def _small_cfg():
    cfg = get_config().model_copy(deep=True)
    cfg.validation.cpcv.n_groups = 4
    cfg.validation.cpcv.n_test_groups = 2
    cfg.ai.n_hypotheses = 2
    return cfg


def test_research_pipeline_end_to_end():
    pit = PointInTimeAccessor(_trend())
    report = run_research(pit, "EUR/USD", llm=FakeLLM(_router), cfg=_small_cfg(), n=2,
                          validate=True, generated_for="2024-12-31")

    assert report.llm_used is True
    assert report.n_hypotheses == 2
    assert "NOT" in report.disclaimer and report.disclaimer == DISCLAIMER

    by_label = {r.label.split()[0]: r for r in report.results}   # 'baseline' / 'ml_gbm'
    assert "baseline" in by_label and "ml_gbm" in by_label

    # baseline was 'test' -> validated; ml was 'discard' -> skipped validation
    assert by_label["baseline"].validation is not None
    assert "dsr" in by_label["baseline"].validation
    assert by_label["ml_gbm"].debate["verdict"] == "discard"
    assert by_label["ml_gbm"].validation is None

    # ranking: the validated hypothesis outranks the discarded one
    assert report.results[0].validation is not None


def test_research_pipeline_without_llm_uses_programmatic():
    pit = PointInTimeAccessor(_trend())
    report = run_research(pit, "EUR/USD", llm=None, cfg=_small_cfg(), n=2, validate=True)
    assert report.llm_used is False
    assert report.n_hypotheses == 2
    assert all(r.proposed_by == "programmatic" for r in report.results)
    # no LLM -> debate defers to validation (verdict 'test') -> all validated
    assert all(r.validation is not None for r in report.results)
