"""ML meta-labelling strategy (Phase 2 signal expansion).

A drop-in ``Strategy``: the PRIMARY direction is regime-gated momentum (identical
to the Phase 1 baseline's gate), and a calibrated ML model (LightGBM or linear)
is the SECONDARY layer that predicts P(this trade wins). That calibrated P(win)
is handed to the SAME risk layer for sizing - the ML model never sizes anything
and never overrides the risk veto.

It plugs straight into the existing backtester and the CPCV/DSR/PBO validation
harness, so the ML signal is held to exactly the same evidentiary bar as the
baseline. The feature frame is cached per data object so backtests stay O(n).
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.ml.dataset import build_dataset, compute_feature_frame, primary_direction
from apex_quant.ml.models import CalibratedModel, make_model
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy


class MLStrategy(Strategy):
    def __init__(
        self,
        model: str = "gbm",
        holding_horizon: int = 10,
        reward_risk: float = 1.5,
        alpha: float = 0.1,
        seed: int | None = None,
    ):
        self.cfg = get_config()
        self.model_kind = model
        self.holding_horizon = holding_horizon
        self.reward_risk = reward_risk
        self.name = f"ml_{model}"
        seed = self.cfg.seed if seed is None else seed
        self._model = CalibratedModel(make_model(model, seed=seed), alpha=alpha, seed=seed)
        self._fitted = False
        self._fm: pd.DataFrame | None = None
        self._dir: np.ndarray | None = None
        self._fm_id: int | None = None
        self._eps = self.cfg.regime.rule_based.ranging_slope_eps
        f = self.cfg.features
        self._slope_col = f"trend_slope_{f.trend_ma}"
        self._mom_col = f"mom_vs_{f.momentum_lookbacks[len(f.momentum_lookbacks)//2]}"

    # -- feature frame cache (keyed by data identity; leakage-safe rolling) ----
    def _frame(self, pit: PointInTimeAccessor) -> pd.DataFrame:
        if self._fm_id != id(pit):
            self._fm = compute_feature_frame(pit.as_of(pit.end), self.cfg)
            self._dir = primary_direction(self._fm, self.cfg)
            self._fm_id = id(pit)
        return self._fm

    # -- training -------------------------------------------------------------
    def fit(self, pit: PointInTimeAccessor, train_timestamps: Iterable[pd.Timestamp]) -> None:
        ds = build_dataset(
            pit, cfg=self.cfg, train_end=pit.end,
            holding_horizon=self.holding_horizon, reward_risk=self.reward_risk,
        )
        train_set = {pd.Timestamp(s) for s in train_timestamps}
        mask = ds.index.isin(list(train_set))   # restrict to (purged) training bars only
        Xtr, ytr = ds.X[mask], ds.y[mask]
        self._model.fit(Xtr.to_numpy(), ytr)
        self._frame(pit)                          # warm the cache
        self._fitted = True

    def is_fitted(self) -> bool:
        return self._fitted

    # -- inference ------------------------------------------------------------
    def _direction_at(self, fm: pd.DataFrame, t) -> tuple[Direction, float]:
        row = fm.loc[t]
        slope, mom = row[self._slope_col], row[self._mom_col]
        if not (np.isfinite(slope) and np.isfinite(mom)) or abs(slope) <= self._eps:
            return Direction.FLAT, mom
        if np.sign(mom) != np.sign(slope):
            return Direction.FLAT, mom
        return (Direction.LONG if mom > 0 else Direction.SHORT), mom

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        fm = self._frame(pit)
        t = pd.Timestamp(t)
        if t not in fm.index:
            return self._flat(instrument, "no feature row at t")
        row = fm.loc[t]
        if row.isna().any():
            return self._flat(instrument, "insufficient history for features")

        direction, mom = self._direction_at(fm, t)
        if direction == Direction.FLAT:
            return self._flat(instrument, "primary gate: not a regime-aligned momentum trade")
        if not self._fitted:
            return self._flat(instrument, "model not fitted")

        cal = self._model.predict_one(row.to_numpy())
        return Signal(
            instrument=instrument, direction=direction, probability=cal.probability,
            reward_risk=self.reward_risk, confidence=cal.confidence,
            rationale=(
                f"{direction.value} | {self.model_kind} P(win)={cal.probability:.2f} "
                f"[{cal.lower:.2f},{cal.upper:.2f}] | mom={mom:.2f}"
            ),
        )

    def explain(self, pit: PointInTimeAccessor, t, instrument: str = "") -> dict:
        fm = self._frame(pit)
        t = pd.Timestamp(t)
        direction, mom = (Direction.FLAT, np.nan)
        cal = None
        reason = ""
        if t in fm.index and not fm.loc[t].isna().any():
            direction, mom = self._direction_at(fm, t)
            if direction != Direction.FLAT and self._fitted:
                cal = self._model.predict_one(fm.loc[t].to_numpy())
            elif direction == Direction.FLAT:
                reason = "primary gate: not a regime-aligned momentum trade"
        else:
            reason = "insufficient history"
        return {
            "instrument": instrument,
            "model": self.model_kind,
            "direction": direction.value,
            "probability": cal.probability if cal else 0.5,
            "uncertainty": {"lower": cal.lower, "upper": cal.upper} if cal else None,
            "confidence": cal.confidence if cal else 0.0,
            "reward_risk": self.reward_risk,
            "reason": reason,
            "contributing_features": {
                k: (None if (t not in fm.index or pd.isna(fm.loc[t, k])) else round(float(fm.loc[t, k]), 4))
                for k in (self._mom_col, self._slope_col, f"rvol_{self.cfg.features.vol_windows[0]}")
            },
            "fitted": self._fitted,
        }

    def _flat(self, instrument: str, reason: str) -> Signal:
        return Signal(instrument=instrument, direction=Direction.FLAT, probability=0.5,
                      reward_risk=self.reward_risk, confidence=0.0, rationale=reason)
