"""Feature / signal layer - deterministic, leakage-free, economically motivated."""

from apex_quant.features.base import Feature
from apex_quant.features.carry import Carry
from apex_quant.features.cot import CotPositioning
from apex_quant.features.momentum import Momentum, VolScaledMomentum
from apex_quant.features.registry import (
    compute_feature_matrix,
    default_features,
    feature_catalog,
)
from apex_quant.features.trend import DistanceFromMA, TrendSlope
from apex_quant.features.volatility_features import ParkinsonVol, RealizedVol

__all__ = [
    "Feature",
    "Momentum",
    "VolScaledMomentum",
    "RealizedVol",
    "ParkinsonVol",
    "TrendSlope",
    "DistanceFromMA",
    "Carry",
    "CotPositioning",
    "default_features",
    "compute_feature_matrix",
    "feature_catalog",
]
