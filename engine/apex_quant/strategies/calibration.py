"""Split-conformal probability calibration.

Maps a raw signal score to a calibrated probability with an honest uncertainty
band. We deliberately do NOT trust a model's raw output as a probability:
  1. Platt scaling (logistic regression) maps score -> probability, fit on a
     proper-training slice.
  2. A held-out calibration slice yields conformal nonconformity scores
     |outcome - phat|; their (1-alpha) quantile is the band half-width.

If there is no signal in the data, the logistic slope collapses toward zero and
the probability sits near the base rate - so a no-edge feature honestly produces
a no-edge probability (and the risk layer's Kelly gate then declines to bet).
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel


class CalibratedProb(BaseModel):
    probability: float
    lower: float
    upper: float

    @property
    def band_width(self) -> float:
        return self.upper - self.lower

    @property
    def confidence(self) -> float:
        """1 - band width: a wide conformal band => low confidence."""
        return float(max(0.0, 1.0 - self.band_width))


class ConformalCalibrator:
    def __init__(self, alpha: float = 0.1, calib_frac: float = 0.3, seed: int = 42):
        self.alpha = alpha
        self.calib_frac = calib_frac
        self.seed = seed
        self._model = None
        self._q = 0.5            # band half-width; 0.5 = maximally uncertain
        self._base_rate = 0.5
        self._fitted = False

    def fit(self, scores: np.ndarray, outcomes: np.ndarray) -> "ConformalCalibrator":
        scores = np.asarray(scores, dtype="float64").reshape(-1, 1)
        outcomes = np.asarray(outcomes, dtype="int")

        # Filter out NaN/Inf rows to prevent sklearn solver overflows
        valid = np.isfinite(scores).all(axis=1)
        scores = scores[valid]
        outcomes = outcomes[valid]

        n = len(scores)
        self._base_rate = float(np.mean(outcomes)) if n else 0.5

        # Not enough data, or only one class present -> honest "no information".
        if n < 30 or len(np.unique(outcomes)) < 2:
            self._fitted = True
            self._model = None
            self._q = 0.5
            return self

        # time-ordered split: proper-train then calibration (no shuffling)
        cut = int(n * (1 - self.calib_frac))
        cut = max(10, min(cut, n - 10))
        x_tr, y_tr = scores[:cut], outcomes[:cut]
        x_cal, y_cal = scores[cut:], outcomes[cut:]

        if len(np.unique(y_tr)) < 2:
            self._fitted = True
            self._model = None
            self._q = 0.5
            return self

        from sklearn.linear_model import LogisticRegression

        self._model = LogisticRegression(max_iter=1000, random_state=self.seed)
        self._model.fit(x_tr, y_tr)

        phat_cal = self._model.predict_proba(x_cal)[:, 1]
        nonconf = np.abs(y_cal - phat_cal)
        # conformal quantile with finite-sample correction
        k = int(np.ceil((len(nonconf) + 1) * (1 - self.alpha)))
        k = min(k, len(nonconf))
        self._q = float(np.sort(nonconf)[k - 1]) if len(nonconf) else 0.5
        self._fitted = True
        return self

    def predict(self, score: float) -> CalibratedProb:
        if not self._fitted:
            raise RuntimeError("calibrator not fitted")
        if self._model is None:
            p = self._base_rate
        else:
            p = float(self._model.predict_proba(np.array([[score]]))[:, 1][0])
        p = float(np.clip(p, 0.02, 0.98))
        lo = float(np.clip(p - self._q, 0.0, 1.0))
        hi = float(np.clip(p + self._q, 0.0, 1.0))
        return CalibratedProb(probability=p, lower=lo, upper=hi)
