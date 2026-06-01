"""Strategies: probabilistic, calibrated signal generators (test-harness baseline)."""

from apex_quant.strategies.base import Strategy
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.strategies.calibration import CalibratedProb, ConformalCalibrator
from apex_quant.strategies.labeling import atr_series, triple_barrier_label
from apex_quant.strategies.ml_strategy import MLStrategy

__all__ = [
    "Strategy",
    "RegimeGatedMomentum",
    "MLStrategy",
    "ConformalCalibrator",
    "CalibratedProb",
    "triple_barrier_label",
    "atr_series",
]
