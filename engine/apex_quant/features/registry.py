"""Assemble features from config and build a leakage-free feature matrix.

The matrix is computed by evaluating each feature through the point-in-time
accessor at every decision timestamp, so by construction row ``t`` depends only
on data ``<= t``. Disabled/optional features (carry, COT) are included only when
enabled in config AND a data provider is supplied.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from apex_quant.config import AppConfig, get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.features.base import Feature
from apex_quant.features.carry import Carry, RateProvider
from apex_quant.features.cot import CotPositioning, CotProvider
from apex_quant.features.momentum import Momentum, VolScaledMomentum
from apex_quant.features.trend import DistanceFromMA, TrendSlope
from apex_quant.features.volatility_features import ParkinsonVol, RealizedVol


def default_features(
    cfg: AppConfig | None = None,
    *,
    instrument: str | None = None,
    rate_provider: RateProvider | None = None,
    cot_provider: CotProvider | None = None,
) -> list[Feature]:
    cfg = cfg or get_config()
    ann = cfg.volatility.annualization_factor
    feats: list[Feature] = []

    for lb in cfg.features.momentum_lookbacks:
        feats.append(Momentum(lb))
    # one vol-scaled momentum at the medium lookback as a regime-robust variant
    if cfg.features.momentum_lookbacks:
        mid = cfg.features.momentum_lookbacks[len(cfg.features.momentum_lookbacks) // 2]
        feats.append(VolScaledMomentum(mid))

    for w in cfg.features.vol_windows:
        feats.append(RealizedVol(w, ann))
    feats.append(ParkinsonVol(cfg.features.vol_windows[0], ann))

    feats.append(TrendSlope(cfg.features.trend_ma, cfg.features.trend_slope_window))
    feats.append(DistanceFromMA(cfg.features.trend_ma, cfg.features.vol_windows[0]))

    if cfg.features.carry_enabled and instrument and rate_provider is not None:
        feats.append(Carry(instrument, rate_provider))
    if cfg.features.cot_enabled and instrument and cot_provider is not None:
        feats.append(CotPositioning(instrument, cot_provider))

    return feats


def compute_feature_matrix(
    pit: PointInTimeAccessor,
    timestamps: Iterable[pd.Timestamp],
    features: list[Feature] | None = None,
    *,
    cfg: AppConfig | None = None,
) -> pd.DataFrame:
    """Feature matrix indexed by timestamp; columns are feature names.

    Every cell is evaluated via the PIT accessor, so the matrix contains no
    look-ahead. Insufficient-history cells are NaN.
    """
    features = features if features is not None else default_features(cfg)
    stamps = list(timestamps)
    data = {
        f.name: [f.compute(pit, t) for t in stamps]
        for f in features
    }
    out = pd.DataFrame(data, index=pd.DatetimeIndex(stamps, name="timestamp"))
    return out


def feature_catalog(features: list[Feature] | None = None, *, cfg: AppConfig | None = None) -> list[dict]:
    """Human/API-readable list of features + their economic rationale."""
    features = features if features is not None else default_features(cfg)
    return [f.describe() for f in features]
