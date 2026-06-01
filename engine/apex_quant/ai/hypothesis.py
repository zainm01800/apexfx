"""Hypotheses + the constrained, validatable config space.

This module is the safety boundary of the AI layer. An LLM emits free text; we
NEVER execute that. Instead every proposal is forced through ``sanitize_config``,
which whitelists keys and clamps every value into a safe range, then
``map_to_strategy`` turns it into a runnable (factory, grid) for the EXISTING
validation harness. The worst a malicious/hallucinated proposal can do is be a
slightly different momentum lookback that then fails CPCV/DSR/PBO.
"""

from __future__ import annotations

from pydantic import BaseModel

from apex_quant.ai.client import extract_json
from apex_quant.ai.retrieval import EvidencePack

STRATEGIES = ("baseline", "ml_gbm", "ml_linear")
REGIME_METHODS = ("rule_based", "hmm")


def _clamp_int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _clamp_float(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def sanitize_config(raw: dict | None) -> dict:
    """Whitelist + clamp a proposed config into a guaranteed-runnable one."""
    raw = raw or {}
    strat = raw.get("strategy", "baseline")
    if strat not in STRATEGIES:
        strat = "baseline"
    rm = raw.get("regime_method", "rule_based")
    if rm not in REGIME_METHODS:
        rm = "rule_based"
    return {
        "strategy": strat,
        "momentum_lookback": _clamp_int(raw.get("momentum_lookback"), 10, 252, 63),
        "vol_window": _clamp_int(raw.get("vol_window"), 10, 252, 63),
        "holding_horizon": _clamp_int(raw.get("holding_horizon"), 3, 40, 10),
        "reward_risk": round(_clamp_float(raw.get("reward_risk"), 0.5, 4.0, 1.5), 2),
        "regime_method": rm,
    }


class Hypothesis(BaseModel):
    thesis: str
    config: dict
    rationale: str = ""
    proposed_by: str = "llm"

    @classmethod
    def create(cls, thesis: str, config: dict, rationale: str = "", proposed_by: str = "llm"):
        return cls(thesis=str(thesis)[:300], config=sanitize_config(config),
                   rationale=str(rationale)[:600], proposed_by=proposed_by)

    @property
    def label(self) -> str:
        c = self.config
        if c["strategy"].startswith("ml"):
            return f"{c['strategy']} h{c['holding_horizon']} rr{c['reward_risk']}"
        return f"{c['strategy']} mom{c['momentum_lookback']} h{c['holding_horizon']} rr{c['reward_risk']} {c['regime_method']}"


def map_to_strategy(hypo: Hypothesis):
    """Return (strategy_factory, param_grid) for the existing validation harness.
    grid[0] is the hypothesis itself; the rest are a small neighbourhood that
    forms the multiple-testing set for DSR/PBO."""
    c = hypo.config
    if c["strategy"] in ("ml_gbm", "ml_linear"):
        from apex_quant.strategies.ml_strategy import MLStrategy

        model = "gbm" if c["strategy"] == "ml_gbm" else "linear"
        base = {"model": model, "holding_horizon": c["holding_horizon"], "reward_risk": c["reward_risk"]}
        grid = [
            base,
            {**base, "holding_horizon": max(3, base["holding_horizon"] - 5)},
            {**base, "model": "linear" if model == "gbm" else "gbm"},
        ]
        return (lambda **p: MLStrategy(**p)), grid

    from apex_quant.strategies.baseline import RegimeGatedMomentum

    base = {
        "momentum_lookback": c["momentum_lookback"], "vol_window": c["vol_window"],
        "holding_horizon": c["holding_horizon"], "reward_risk": c["reward_risk"],
        "regime_method": c["regime_method"],
    }
    grid = [
        base,
        {**base, "momentum_lookback": max(10, base["momentum_lookback"] // 2)},
        {**base, "momentum_lookback": min(252, base["momentum_lookback"] * 2)},
    ]
    return (lambda **p: RegimeGatedMomentum(**p)), grid


def parse_llm_hypotheses(text: str | None, n: int) -> list[Hypothesis]:
    """Parse a JSON array of {thesis, config, rationale}; drop anything invalid."""
    data = extract_json(text)
    out: list[Hypothesis] = []
    if isinstance(data, dict):
        data = [data]
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("thesis"):
                out.append(Hypothesis.create(item["thesis"], item.get("config", {}),
                                             item.get("rationale", ""), "llm"))
            if len(out) >= n:
                break
    return out


def programmatic_proposer(evidence: EvidencePack, n: int = 4) -> list[Hypothesis]:
    """Heuristic, evidence-aware hypothesis set used when no LLM is available, so
    the pipeline is always functional. Diversity over cleverness."""
    hi_vol = (evidence.rvol_ann or 0.0) > 0.10
    base_h = 7 if hi_vol else 12
    cands = [
        ("Short-lookback momentum reacts faster to the live move",
         {"strategy": "baseline", "momentum_lookback": 21, "vol_window": 21, "holding_horizon": base_h, "reward_risk": 1.5}),
        ("Slower momentum filters chop in a noisy tape",
         {"strategy": "baseline", "momentum_lookback": 126, "vol_window": 63, "holding_horizon": base_h + 5, "reward_risk": 2.0}),
        ("GBM meta-model may capture nonlinear feature interactions",
         {"strategy": "ml_gbm", "holding_horizon": base_h, "reward_risk": 1.5}),
        ("HMM-gated momentum adapts to latent regime shifts",
         {"strategy": "baseline", "momentum_lookback": 63, "vol_window": 63, "holding_horizon": base_h, "reward_risk": 1.5, "regime_method": "hmm"}),
        ("Linear meta-model as a low-variance benchmark",
         {"strategy": "ml_linear", "holding_horizon": base_h, "reward_risk": 1.5}),
    ]
    return [Hypothesis.create(t, c, proposed_by="programmatic") for t, c in cands[:n]]
