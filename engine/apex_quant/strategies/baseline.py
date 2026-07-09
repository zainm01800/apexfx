"""Baseline strategy: regime-gated momentum with conformal-calibrated probability,
plus Bollinger Band mean-reversion for ranging regimes.

Logic:
  * In TRENDING regimes: take momentum trades in the direction of the trend.
  * In RANGING regimes (mean-reversion enabled): trade Bollinger Band bounces.
    - Price touches/crosses below lower band -> LONG (buy the dip)
    - Price touches/crosses above upper band -> SHORT (sell the rally)
  * Crypto: mean-reversion disabled (crypto ranging is unpredictable).
  * The probability is the conformal-calibrated P(target hit before stop).

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


def _bollinger_signal(df: pd.DataFrame, window: int = 20, n_std: float = 2.0) -> tuple[int, float]:
    """Compute a Bollinger Band mean-reversion signal.

    Returns:
        (direction, strength) where direction is +1 (long), -1 (short), or 0 (neutral),
        and strength is the normalised distance from the band (0.0 to 1.0).
    """
    if len(df) < window + 2:
        return 0, 0.0

    close = df["close"]
    mid = close.rolling(window).mean()
    std = close.rolling(window).std(ddof=1)

    latest_close = float(close.iloc[-1])
    latest_mid = float(mid.iloc[-1])
    latest_std = float(std.iloc[-1])

    if not (np.isfinite(latest_mid) and np.isfinite(latest_std) and latest_std > 0):
        return 0, 0.0

    lower = latest_mid - n_std * latest_std
    upper = latest_mid + n_std * latest_std
    band_width = upper - lower

    if latest_close <= lower:
        # Price at or below lower band: buy the dip
        strength = min(1.0, (lower - latest_close) / (latest_std + 1e-10))
        return 1, float(strength)
    elif latest_close >= upper:
        # Price at or above upper band: sell the rally
        strength = min(1.0, (latest_close - upper) / (latest_std + 1e-10))
        return -1, float(strength)

    return 0, 0.0


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
        instrument: str | None = None,
        enable_mean_reversion: bool = True,
    ):
        self.bypass_calibration = bypass_calibration
        self.momentum_lookback = momentum_lookback
        self.vol_window = vol_window
        self.holding_horizon = holding_horizon
        self.reward_risk = reward_risk
        self.regime_method = regime_method
        self.instrument = instrument or ""
        self._mom = VolScaledMomentum(momentum_lookback, vol_window)

        # Determine asset class for per-class tuning
        self._asset_class = "equity"
        if instrument:
            self._asset_class = get_config().asset_class_of(instrument)

        # Mean reversion: enabled for forex and equity on 1h/1d only.
        # On 5m/15m, Bollinger Bands fire on every wiggle (too noisy) → pure momentum only.
        # Crypto is always excluded from MR (gap-prone, regime breaks unpredictably).
        mr_tf_allowed = timeframe in ("1h", "1d")
        self.enable_mean_reversion = enable_mean_reversion and (self._asset_class != "crypto") and mr_tf_allowed

        # Scale slope epsilon dynamically based on timeframe & asset class volatility
        base_cfg = get_config().regime.rule_based
        timeframe_scale = 1.0
        if timeframe == "5m":
            timeframe_scale = 0.02
        elif timeframe == "15m":
            timeframe_scale = 0.05
        elif timeframe == "1h":
            timeframe_scale = 0.15

        # Asset class multiplier:
        # - crypto is ~8x more volatile than forex on scalp (raised from 5x to reduce false breaks)
        # - equities ~1.5x
        ac_multiplier = 1.0
        if self._asset_class == "crypto":
            ac_multiplier = 8.0 if timeframe in ("5m", "15m") else 5.0
        elif self._asset_class == "equity":
            ac_multiplier = 1.5

        final_eps = base_cfg.ranging_slope_eps * timeframe_scale * ac_multiplier

        custom_regime_cfg = RuleBasedConfig(
            ma_window=base_cfg.ma_window,
            slope_window=base_cfg.slope_window,
            vol_percentile_window=base_cfg.vol_percentile_window,
            vol_high_pct=base_cfg.vol_high_pct,
            vol_low_pct=base_cfg.vol_low_pct,
            ranging_slope_eps=final_eps
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

            # Conditional calibration: align training with strategy's trending-only gating
            regime = self._regime.classify(pit, ts)
            if not regime.is_trending:
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
               "reason": "", "mode": "momentum"}

        if not np.isfinite(score):
            out["reason"] = "insufficient history for momentum"
            return out

        # ── TRENDING REGIME: standard momentum trade ──────────────────────────
        if regime.is_trending:
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
            out["mode"] = "momentum"
            return out

        # ── RANGING REGIME: Bollinger Band mean-reversion trade ───────────────
        if not self.enable_mean_reversion:
            out["reason"] = f"regime {regime.name} not trending (MR disabled for {self._asset_class})"
            return out

        # Get recent bars for Bollinger Band calculation
        df_window = pit.window(t, 60)
        if len(df_window) < 22:
            out["reason"] = "insufficient bars for Bollinger Band MR"
            return out

        bb_dir, bb_strength = _bollinger_signal(df_window, window=20, n_std=2.0)
        if bb_dir == 0:
            out["reason"] = "ranging regime: price inside Bollinger Bands (no MR signal)"
            return out

        mr_direction = Direction.LONG if bb_dir == 1 else Direction.SHORT
        # Use a fixed moderate probability for MR trades (no calibration model for MR yet)
        cal = CalibratedProb(probability=0.52, lower=0.40, upper=0.65)
        out["direction"] = mr_direction
        out["prob"] = cal
        out["mode"] = "mean_reversion"
        out["reason"] = f"MR: BB signal strength={bb_strength:.3f}"
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
        # Use tighter R:R for mean-reversion (targets are bounded by the band midline)
        rr = 1.2 if ev.get("mode") == "mean_reversion" else self.reward_risk
        return Signal(
            instrument=instrument,
            direction=ev["direction"],
            probability=cal.probability,
            reward_risk=rr,
            confidence=cal.confidence,
            rationale=(
                f"{ev['direction'].value} | mode={ev.get('mode','momentum')} | "
                f"mom={ev['score']:.2f} | regime={regime.name} "
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
            "mode": ev.get("mode", "momentum"),
            "contributing_features": {
                "vol_scaled_momentum": None if not np.isfinite(ev["score"]) else round(float(ev["score"]), 4),
                "regime": regime.name,
                "regime_confidence": round(regime.confidence, 3),
            },
            "fitted": self._fitted,
        }
