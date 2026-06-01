"""Transparent, rule-based regime classifier - the sanity baseline.

No latent variables, nothing to overfit: trend comes from the sign of a long-MA
slope, vol from where current realised vol sits in its own trailing distribution.
If the HMM ever disagrees wildly with this, treat the HMM with suspicion.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.config import RuleBasedConfig, get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.features.trend import TrendSlope
from apex_quant.regime.base import RegimeClassifier, RegimeLabel
from apex_quant.volatility.realized import log_returns


class RuleBasedRegime(RegimeClassifier):
    def __init__(self, cfg: RuleBasedConfig | None = None):
        self.cfg = cfg or get_config().regime.rule_based
        self._slope = TrendSlope(self.cfg.ma_window, self.cfg.slope_window)

    def classify(self, pit: PointInTimeAccessor, t: pd.Timestamp | str) -> RegimeLabel:
        cfg = self.cfg
        slope = self._slope.compute(pit, t)

        # --- trend axis ---
        eps = cfg.ranging_slope_eps
        if not np.isfinite(slope):
            trend, trend_conf = "ranging", 0.0
        elif slope > eps:
            trend = "up"
            trend_conf = min(1.0, (slope - eps) / eps + 0.5)
        elif slope < -eps:
            trend = "down"
            trend_conf = min(1.0, (-slope - eps) / eps + 0.5)
        else:
            trend = "ranging"
            trend_conf = min(1.0, 1.0 - abs(slope) / eps) if eps > 0 else 1.0

        # --- vol axis: percentile of current vol within its trailing history ---
        hist = pit.as_of(t)
        vol, vol_conf, vpercent = self._vol_state(hist)

        confidence = float(np.clip(0.5 * trend_conf + 0.5 * vol_conf, 0.0, 1.0))
        detail = (
            f"slope={slope:.5f} (eps={eps}); vol_pctile={vpercent:.2f}"
            if np.isfinite(slope)
            else f"slope=nan; vol_pctile={vpercent:.2f}"
        )
        return RegimeLabel(
            trend=trend, vol=vol, confidence=confidence, method="rule_based", detail=detail
        )

    def _vol_state(self, hist: pd.DataFrame) -> tuple[str, float, float]:
        cfg = self.cfg
        r = log_returns(hist)
        w = max(2, min(len(r), 21))
        if len(r) < w + 5:
            return "normal", 0.0, float("nan")
        # rolling realised vol series, then percentile rank of the latest value
        s = pd.Series(r)
        roll = s.rolling(w).std(ddof=1).dropna()
        lookback = roll.iloc[-cfg.vol_percentile_window:]
        if len(lookback) < 5:
            return "normal", 0.0, float("nan")
        current = roll.iloc[-1]
        pct = float((lookback < current).mean())
        if pct >= cfg.vol_high_pct:
            return "high", min(1.0, (pct - cfg.vol_high_pct) / max(1e-9, 1 - cfg.vol_high_pct)), pct
        if pct <= cfg.vol_low_pct:
            return "low", min(1.0, (cfg.vol_low_pct - pct) / max(1e-9, cfg.vol_low_pct)), pct
        # normal: most confident in the middle of the band
        mid = 0.5 * (cfg.vol_low_pct + cfg.vol_high_pct)
        half = 0.5 * (cfg.vol_high_pct - cfg.vol_low_pct)
        return "normal", float(max(0.0, 1.0 - abs(pct - mid) / max(1e-9, half))), pct
