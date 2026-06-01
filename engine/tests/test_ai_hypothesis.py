"""Phase 3 P3-2: hypothesis sanitation, safe mapping, parsing, proposer."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.ai.hypothesis import (
    Hypothesis,
    map_to_strategy,
    parse_llm_hypotheses,
    programmatic_proposer,
    sanitize_config,
)
from apex_quant.ai.retrieval import gather_evidence
from apex_quant.data.point_in_time import PointInTimeAccessor


def _trend(n=600, seed=3):
    rng = np.random.default_rng(seed)
    close = 1.10 * np.exp(np.cumsum(rng.normal(0.001, 0.004, n)))
    op = np.concatenate([[1.10], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range("2018-01-01", periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


# -- sanitation = safety boundary ----------------------------------------------
def test_sanitize_clamps_and_whitelists():
    dirty = {"strategy": "rm -rf", "momentum_lookback": 99999, "holding_horizon": -5,
             "reward_risk": 50, "regime_method": "evil", "exec": "import os", "extra": 1}
    c = sanitize_config(dirty)
    assert c["strategy"] == "baseline"               # unknown strategy -> safe default
    assert c["momentum_lookback"] == 252             # clamped to max
    assert c["holding_horizon"] == 3                 # clamped to min
    assert c["reward_risk"] == 4.0                   # clamped to max
    assert c["regime_method"] == "rule_based"
    assert "exec" not in c and "extra" not in c       # arbitrary keys dropped


def test_sanitize_defaults_on_empty():
    c = sanitize_config(None)
    assert c["strategy"] == "baseline" and c["momentum_lookback"] == 63


def test_hypothesis_create_sanitizes():
    h = Hypothesis.create("x" * 999, {"momentum_lookback": "abc", "reward_risk": "oops"}, "r" * 999)
    assert len(h.thesis) <= 300 and len(h.rationale) <= 600
    assert h.config["momentum_lookback"] == 63 and h.config["reward_risk"] == 1.5


# -- safe mapping to runnable strategies ---------------------------------------
def test_map_baseline_builds_runnable():
    h = Hypothesis.create("baseline idea", {"strategy": "baseline", "momentum_lookback": 40})
    factory, grid = map_to_strategy(h)
    strat = factory(**grid[0])
    assert strat.__class__.__name__ == "RegimeGatedMomentum"
    assert len(grid) >= 2                              # neighbourhood for PBO


def test_map_ml_builds_runnable():
    h = Hypothesis.create("ml idea", {"strategy": "ml_gbm", "holding_horizon": 8})
    factory, grid = map_to_strategy(h)
    strat = factory(**grid[0])
    assert strat.name == "ml_gbm"


# -- parsing LLM output --------------------------------------------------------
def test_parse_llm_hypotheses():
    text = '''```json
    [{"thesis":"slow momentum","config":{"strategy":"baseline","momentum_lookback":126},"rationale":"trend"},
     {"thesis":"gbm","config":{"strategy":"ml_gbm"}}]```'''
    hs = parse_llm_hypotheses(text, n=5)
    assert len(hs) == 2
    assert hs[0].config["momentum_lookback"] == 126
    assert hs[1].config["strategy"] == "ml_gbm"


def test_parse_llm_ignores_invalid():
    assert parse_llm_hypotheses("not json", 5) == []
    assert parse_llm_hypotheses('[{"no_thesis": 1}]', 5) == []


# -- programmatic fallback -----------------------------------------------------
def test_programmatic_proposer_diverse_and_valid():
    pit = PointInTimeAccessor(_trend())
    ev = gather_evidence(pit, "EUR/USD")
    hs = programmatic_proposer(ev, n=4)
    assert len(hs) == 4
    assert all(h.proposed_by == "programmatic" for h in hs)
    assert len({h.label for h in hs}) == 4            # all distinct
    for h in hs:                                       # all map to a runnable strategy
        factory, grid = map_to_strategy(h)
        assert factory(**grid[0]) is not None
