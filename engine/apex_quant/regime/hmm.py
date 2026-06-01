"""HMM regime detection (hmmlearn).

A Gaussian HMM over [log-return, rolling realised vol] discovers latent market
states. Each state is then labelled by its own statistics: the sign of its mean
return -> trend axis, its mean vol rank among states -> vol axis. The regime at
``t`` is the state of the last bar, and confidence is that state's posterior
probability - a principled, earned confidence rather than a hand-set number.

Fitting an HMM is comparatively expensive, so the fast rule-based classifier is
the default in tight loops; the HMM is for the live/API estimate (and any
periodic backtest re-label). On failure it falls back to the rule-based label.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from apex_quant.config import HmmConfig, get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.regime.base import RegimeClassifier, RegimeLabel
from apex_quant.regime.rule_based import RuleBasedRegime
from apex_quant.volatility.realized import log_returns


class HmmRegime(RegimeClassifier):
    def __init__(self, cfg: HmmConfig | None = None, *, vol_window: int = 21, lookback: int = 756):
        self.cfg = cfg or get_config().regime.hmm
        self.vol_window = vol_window
        self.lookback = lookback
        self.seed = get_config().seed
        self._fallback = RuleBasedRegime()

    def _features(self, hist: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        r = log_returns(hist)
        s = pd.Series(r)
        vol = s.rolling(self.vol_window).std(ddof=1)
        valid = vol.notna().to_numpy()
        r_aligned = r[valid]
        vol_aligned = vol.to_numpy()[valid]
        X = np.column_stack([r_aligned, vol_aligned])
        return X, r_aligned, vol_aligned

    def classify(self, pit: PointInTimeAccessor, t: pd.Timestamp | str) -> RegimeLabel:
        hist = pit.as_of(t).iloc[-self.lookback:]
        try:
            X, r_raw, vol_raw = self._features(hist)
            if len(X) < self.cfg.min_obs:
                return self._fallback_label(pit, t, f"only {len(X)} obs (<{self.cfg.min_obs})")

            # standardise for numerically stable fitting (labels use raw arrays)
            mu, sd = X.mean(axis=0), X.std(axis=0)
            sd[sd == 0] = 1.0
            Xs = (X - mu) / sd

            from hmmlearn.hmm import GaussianHMM

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = GaussianHMM(
                    n_components=self.cfg.n_states,
                    covariance_type=self.cfg.covariance_type,
                    n_iter=self.cfg.n_iter,
                    random_state=self.seed,
                )
                model.fit(Xs)
                states = model.predict(Xs)
                post = model.predict_proba(Xs)

            cur = int(states[-1])
            confidence = float(post[-1, cur])

            trend = self._trend_for_state(cur, states, r_raw)
            vol = self._vol_for_state(cur, states, vol_raw)
            converged = bool(getattr(model.monitor_, "converged", True))
            detail = (
                f"state={cur}/{self.cfg.n_states} post={confidence:.2f} "
                f"converged={converged}"
            )
            return RegimeLabel(
                trend=trend, vol=vol, confidence=confidence, method="hmm", detail=detail
            )
        except Exception as exc:  # noqa: BLE001 - any hmm failure -> safe fallback
            return self._fallback_label(pit, t, f"HMM failed ({type(exc).__name__})")

    # -- state -> label mapping -------------------------------------------------
    def _trend_for_state(self, state: int, states: np.ndarray, r_raw: np.ndarray) -> str:
        mask = states == state
        mean_ret = float(np.mean(r_raw[mask])) if mask.any() else 0.0
        eps = 0.1 * float(np.std(r_raw)) if len(r_raw) > 1 else 0.0
        if mean_ret > eps:
            return "up"
        if mean_ret < -eps:
            return "down"
        return "ranging"

    def _vol_for_state(self, state: int, states: np.ndarray, vol_raw: np.ndarray) -> str:
        # rank each state by its mean vol; current state's rank -> low/normal/high
        uniq = sorted(set(int(s) for s in states))
        means = {s: float(np.mean(vol_raw[states == s])) for s in uniq}
        order = sorted(uniq, key=lambda s: means[s])  # low -> high
        rank = order.index(state)
        if rank == 0:
            return "low"
        if rank == len(order) - 1:
            return "high"
        return "normal"

    def _fallback_label(self, pit, t, why: str) -> RegimeLabel:
        base = self._fallback.classify(pit, t)
        return RegimeLabel(
            trend=base.trend,
            vol=base.vol,
            confidence=base.confidence * 0.8,  # discount: this isn't the HMM's view
            method="hmm->rule_fallback",
            detail=f"{why}; {base.detail}",
        )
