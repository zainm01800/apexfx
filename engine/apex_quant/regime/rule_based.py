"""Transparent, rule-based regime classifier - the sanity baseline.

No latent variables, nothing to overfit: trend comes from the sign of a long-MA
slope, vol from where current realised vol sits in its own trailing distribution.
If the HMM ever disagrees wildly with this, treat the HMM with suspicion.
"""

from __future__ import annotations

import weakref

import numpy as np
import pandas as pd

from apex_quant.config import RuleBasedConfig, get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.features.trend import TrendSlope
from apex_quant.regime.base import RegimeClassifier, RegimeLabel
from apex_quant.volatility.realized import log_returns


def regime_config_for(timeframe: str, asset_class: str, base: RuleBasedConfig | None = None) -> RuleBasedConfig:
    """RuleBasedConfig with ``ranging_slope_eps`` scaled for timeframe & asset class.

    The raw config eps is calibrated on daily forex slopes; per-bar slopes on
    smaller timeframes are commensurately smaller, and crypto/equity vol differs
    from forex, so ONE unscaled eps misreads everywhere else (intraday reads
    "ranging" almost always). This scaling used to live inline in
    ``RegimeGatedMomentum`` — it is the single source of truth shared by the
    strategy gate AND the engine-level regime the risk layer scales by, so
    backtest risk-damping sees the same regime semantics as the signal (E4).
    """
    base = base or get_config().regime.rule_based
    tf = str(timeframe).lower().strip()
    tf_scale = {"5m": 0.02, "15m": 0.05, "1h": 0.15}.get(tf, 1.0)
    # Asset class multiplier:
    # - crypto is ~8x more volatile than forex on scalp (raised from 5x to reduce
    #   false breaks), ~5x otherwise
    # - equities ~1.5x
    if asset_class == "crypto":
        ac_multiplier = 8.0 if tf in ("5m", "15m") else 5.0
    elif asset_class == "equity":
        ac_multiplier = 1.5
    else:
        ac_multiplier = 1.0
    return base.model_copy(update={"ranging_slope_eps": base.ranging_slope_eps * tf_scale * ac_multiplier})


class RuleBasedRegime(RegimeClassifier):
    # Class-level cache sharing regime classifications across strategy instances,
    # scoped per data object (see classify) so instruments can never cross-read.
    _global_regime_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()

    def __init__(self, cfg: RuleBasedConfig | None = None):
        self.cfg = cfg or get_config().regime.rule_based
        self._slope = TrendSlope(self.cfg.ma_window, self.cfg.slope_window)

    def classify(self, pit: PointInTimeAccessor, t: pd.Timestamp | str) -> RegimeLabel:
        cfg = self.cfg
        
        # The cache MUST be scoped to the data object. The original key was only
        # (t, eps, ma, slope), so two different instruments at the same timestamp
        # shared one entry — EUR/USD's regime was served for GBP/USD, silently
        # corrupting the regime gate across the whole book. And keying by id(pit)
        # is NOT enough: Python reuses ids after GC, so a new pit landing on a
        # freed pit's address inherits its stale labels (this actually broke the
        # single-vs-portfolio parity test, 20 vs 18 trades). A WeakKeyDictionary
        # ties each sub-cache to the pit's LIFETIME — entries vanish when the pit
        # is collected, which also stops the live loop (new pit every cycle)
        # growing the cache without bound.
        per_pit = self._global_regime_cache.get(pit)
        if per_pit is None:
            per_pit = {}
            self._global_regime_cache[pit] = per_pit
        cache_key = (str(t), float(cfg.ranging_slope_eps),
                     int(cfg.ma_window), int(cfg.slope_window))
        if cache_key in per_pit:
            return per_pit[cache_key]

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
        res = RegimeLabel(
            trend=trend, vol=vol, confidence=confidence, method="rule_based", detail=detail
        )
        per_pit[cache_key] = res
        return res

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
