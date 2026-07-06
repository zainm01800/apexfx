"""Baseline strategy: regime-gated momentum with conformal-calibrated probability.

This is a *test harness*, not the final strategy - its only job is to exercise
the whole pipeline (features -> regime -> calibrated signal -> risk sizing ->
backtest -> validation) end-to-end with something real.

Logic:
  * Only act when the regime is trending (flat in ranging regimes).
  * Take the trade only when momentum direction agrees with the regime trend.
  * The probability is the conformal-calibrated P(target hit before stop), where
    the barriers match the risk layer's ATR stop and reward:risk - so the p the
    risk layer Kelly-sizes on means exactly what it should.

Calibration is fit on training bars' triple-barrier labels (P(win | momentum
strength)); regime gating is applied at decision time.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from apex_quant.config import get_config, RuleBasedConfig
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.features.momentum import VolScaledMomentum
from apex_quant.regime.base import RegimeClassifier
from apex_quant.regime.hmm import HmmRegime
from apex_quant.regime.rule_based import RuleBasedRegime
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy
from apex_quant.strategies.calibration import CalibratedProb, ConformalCalibrator
from apex_quant.strategies.labeling import atr_series, triple_barrier_label


class RegimeGatedMomentum(Strategy):
    name = "regime_gated_momentum"

    def __init__(
        self,
        momentum_lookback: int = 63,
        vol_window: int = 63,
        holding_horizon: int = 10,
        reward_risk: float = 1.5,
        regime_method: str = "rule_based",
        alpha: float = 0.1,
        timeframe: str = "1d",
        bypass_calibration: bool = True,
    ):
        self.bypass_calibration = bypass_calibration
        self.momentum_lookback = momentum_lookback
        self.vol_window = vol_window
        self.holding_horizon = holding_horizon
        self.reward_risk = reward_risk
        self.regime_method = regime_method
        self._mom = VolScaledMomentum(momentum_lookback, vol_window)
        
        # Scale slope epsilon dynamically based on timeframe
        base_cfg = get_config().regime.rule_based
        scale = 1.0
        if timeframe == "15m":
            scale = 0.05
        elif timeframe == "1h":
            scale = 0.15
            
        custom_regime_cfg = RuleBasedConfig(
            ma_window=base_cfg.ma_window,
            slope_window=base_cfg.slope_window,
            vol_percentile_window=base_cfg.vol_percentile_window,
            vol_high_pct=base_cfg.vol_high_pct,
            vol_low_pct=base_cfg.vol_low_pct,
            ranging_slope_eps=base_cfg.ranging_slope_eps * scale
        )
        
        self._regime: RegimeClassifier = (
            HmmRegime() if regime_method == "hmm" else RuleBasedRegime(custom_regime_cfg)
        )
        self._cal = ConformalCalibrator(alpha=alpha, seed=get_config().seed)
        rc = get_config().risk
        self._stop_mult = rc.atr_stop_mult
        self._atr_window = rc.atr_window
        self._fitted = False

    # -- training: calibrate on triple-barrier labels --------------------------
    def fit(self, pit: PointInTimeAccessor, train_timestamps: Iterable[pd.Timestamp]) -> None:
        stamps = list(train_timestamps)
        if not stamps:
            self._cal.fit(np.array([]), np.array([]))
            self._fitted = True
            return

        df = pit.as_of(stamps[-1])
        if len(df) < self._mom.min_obs + self.holding_horizon + 5:
            self._cal.fit(np.array([]), np.array([]))
            self._fitted = True
            return

        close = df["close"]
        logc = np.log(close)
        ret = (close / close.shift(self.momentum_lookback) - 1.0)
        vol = logc.diff().rolling(self.vol_window).std(ddof=1)
        score = (ret / vol).to_numpy()
        atr = atr_series(df, self._atr_window)
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()

        train_set = {pd.Timestamp(s) for s in stamps}
        scores, outcomes = [], []
        for i, ts in enumerate(df.index):
            if ts not in train_set:
                continue
            s, a = score[i], atr[i]
            if not (np.isfinite(s) and np.isfinite(a) and a > 0):
                continue
            direction = 1 if s > 0 else -1
            stop_dist = self._stop_mult * a
            target_dist = self.reward_risk * stop_dist
            label = triple_barrier_label(
                high, low, float(close.iloc[i]), direction, stop_dist, target_dist,
                i, self.holding_horizon,
            )
            if label is not None:
                scores.append(abs(s))
                outcomes.append(label)

        self._cal.fit(np.array(scores), np.array(outcomes))
        self._fitted = True

    def is_fitted(self) -> bool:
        return self._fitted

    # -- inference -------------------------------------------------------------
    def _evaluate(self, pit: PointInTimeAccessor, t) -> dict:
        regime = self._regime.classify(pit, t)
        score = self._mom.compute(pit, t)
        out = {"regime": regime, "score": score, "direction": Direction.FLAT, "prob": None,
               "reason": ""}

        if not np.isfinite(score):
            out["reason"] = "insufficient history for momentum"
            return out
        if not regime.is_trending:
            out["reason"] = f"regime {regime.name} not trending"
            return out

        mom_dir = Direction.LONG if score > 0 else Direction.SHORT
        regime_dir = Direction.LONG if regime.trend == "up" else Direction.SHORT
        if mom_dir != regime_dir:
            out["reason"] = "momentum disagrees with regime trend"
            return out

        if self.bypass_calibration:
            cal = CalibratedProb(probability=0.50, lower=0.0, upper=1.0)
        else:
            cal = self._cal.predict(abs(score)) if self._fitted else CalibratedProb(
                probability=0.5, lower=0.0, upper=1.0
            )
        out["direction"] = mom_dir
        out["prob"] = cal
        return out

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        ev = self._evaluate(pit, t)
        if ev["direction"] == Direction.FLAT or ev["prob"] is None:
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=self.reward_risk, confidence=0.0,
                rationale=ev["reason"] or "no trade",
            )
        cal: CalibratedProb = ev["prob"]
        regime = ev["regime"]
        return Signal(
            instrument=instrument,
            direction=ev["direction"],
            probability=cal.probability,
            reward_risk=self.reward_risk,
            confidence=cal.confidence,
            rationale=(
                f"{ev['direction'].value} | mom={ev['score']:.2f} | regime={regime.name} "
                f"| p={cal.probability:.2f} [{cal.lower:.2f},{cal.upper:.2f}]"
            ),
        )

    def explain(self, pit: PointInTimeAccessor, t, instrument: str = "") -> dict:
        """Rich, API-friendly view: signal + band + contributing features."""
        ev = self._evaluate(pit, t)
        regime = ev["regime"]
        cal: CalibratedProb | None = ev["prob"]
        return {
            "instrument": instrument,
            "direction": ev["direction"].value,
            "probability": cal.probability if cal else 0.5,
            "uncertainty": {"lower": cal.lower, "upper": cal.upper} if cal else None,
            "confidence": cal.confidence if cal else 0.0,
            "reward_risk": self.reward_risk,
            "reason": ev["reason"],
            "contributing_features": {
                "vol_scaled_momentum": None if not np.isfinite(ev["score"]) else round(float(ev["score"]), 4),
                "regime": regime.name,
                "regime_confidence": round(regime.confidence, 3),
            },
            "fitted": self._fitted,
        }
