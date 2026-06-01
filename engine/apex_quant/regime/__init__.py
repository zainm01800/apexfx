"""Regime detection: rule-based baseline + HMM, behind one entry point."""

from __future__ import annotations

import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.regime.base import RegimeClassifier, RegimeLabel
from apex_quant.regime.hmm import HmmRegime
from apex_quant.regime.rule_based import RuleBasedRegime

__all__ = [
    "RegimeLabel",
    "RegimeClassifier",
    "RuleBasedRegime",
    "HmmRegime",
    "classify_regime",
]


def classify_regime(
    pit: PointInTimeAccessor,
    t: pd.Timestamp | str,
    *,
    method: str = "rule_based",
) -> RegimeLabel:
    """Classify the regime known at ``t``. ``method`` is ``"rule_based"`` (fast,
    transparent - the default) or ``"hmm"`` (latent-state, with fallback)."""
    if method == "hmm":
        return HmmRegime().classify(pit, t)
    if method == "rule_based":
        return RuleBasedRegime().classify(pit, t)
    raise ValueError(f"unknown regime method '{method}'")
