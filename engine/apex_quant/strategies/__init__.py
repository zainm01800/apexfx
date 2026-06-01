"""Strategies: probabilistic, calibrated signal generators (test-harness baseline)."""

from apex_quant.strategies.base import Strategy
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.strategies.calibration import CalibratedProb, ConformalCalibrator
from apex_quant.strategies.labeling import atr_series, triple_barrier_label

__all__ = [
    "Strategy",
    "RegimeGatedMomentum",
    "MLStrategy",
    "ConformalCalibrator",
    "CalibratedProb",
    "triple_barrier_label",
    "atr_series",
]


# MLStrategy lives in the `ml` subsystem, which imports strategies.calibration /
# .labeling. Importing it lazily keeps `from apex_quant.strategies import MLStrategy`
# working without creating a strategies <-> ml import cycle at package-load time.
def __getattr__(name):
    if name == "MLStrategy":
        from apex_quant.strategies.ml_strategy import MLStrategy
        return MLStrategy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
