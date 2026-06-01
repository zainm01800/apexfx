"""Probabilistic models for meta-labelling: a linear baseline and a regularised
gradient-boosting ensemble, both wrapped in conformal calibration.

Why both: the linear model is the honesty check - if the GBM can't beat a
regularised linear model out-of-sample, its extra complexity is just overfitting.
The GBM is deliberately shallow and regularised (depth 3, few leaves, subsampling,
L2) because, per the brief, overfitting is the default failure mode on noisy
financial data. Raw model probabilities are NOT trusted - they pass through a
split-conformal calibrator so the P(win) handed to the risk layer is honest, with
an uncertainty band.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from apex_quant.strategies.calibration import CalibratedProb, ConformalCalibrator


class ProbModel(ABC):
    name: str = "model"

    @abstractmethod
    def fit(self, X, y, sample_weight=None) -> "ProbModel": ...

    @abstractmethod
    def raw_proba(self, X) -> np.ndarray:
        """Uncalibrated P(win) per row in [0,1]."""


class LinearModel(ProbModel):
    name = "linear"

    def __init__(self, C: float = 1.0, penalty: str = "l2", seed: int = 42):
        self.C, self.penalty, self.seed = C, penalty, seed
        self._pipe = None
        self._single = None

    def fit(self, X, y, sample_weight=None):
        y = np.asarray(y, dtype=int)
        if len(np.unique(y)) < 2:
            self._single = float(np.mean(y)) if len(y) else 0.5
            return self
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        kwargs = dict(C=self.C, penalty=self.penalty, max_iter=2000, random_state=self.seed)
        if self.penalty == "elasticnet":
            kwargs.update(solver="saga", l1_ratio=0.5)  # l1_ratio only valid for elasticnet
        else:
            kwargs.update(solver="lbfgs")
        clf = LogisticRegression(**kwargs)
        self._pipe = make_pipeline(StandardScaler(), clf)
        self._pipe.fit(np.asarray(X), y, **({"logisticregression__sample_weight": sample_weight} if sample_weight is not None else {}))
        self._single = None
        return self

    def raw_proba(self, X) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype="float64"))
        if self._pipe is None:
            return np.full(len(X), self._single if self._single is not None else 0.5)
        return self._pipe.predict_proba(X)[:, 1]


class GBMModel(ProbModel):
    name = "gbm"

    def __init__(self, seed: int = 42, n_estimators: int = 300, max_depth: int = 3,
                 num_leaves: int = 7, learning_rate: float = 0.03, subsample: float = 0.8,
                 colsample_bytree: float = 0.8, reg_lambda: float = 1.0,
                 min_child_samples: int = 30):
        self.params = dict(
            n_estimators=n_estimators, max_depth=max_depth, num_leaves=num_leaves,
            learning_rate=learning_rate, subsample=subsample, subsample_freq=1,
            colsample_bytree=colsample_bytree, reg_lambda=reg_lambda,
            min_child_samples=min_child_samples, random_state=seed, n_jobs=1, verbose=-1,
        )
        self._model = None
        self._single = None

    def fit(self, X, y, sample_weight=None):
        y = np.asarray(y, dtype=int)
        if len(np.unique(y)) < 2 or len(y) < 40:
            self._single = float(np.mean(y)) if len(y) else 0.5
            return self
        from lightgbm import LGBMClassifier

        self._model = LGBMClassifier(**self.params)
        self._model.fit(np.asarray(X), y, sample_weight=sample_weight)
        self._single = None
        return self

    def raw_proba(self, X) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype="float64"))
        if self._model is None:
            return np.full(len(X), self._single if self._single is not None else 0.5)
        return self._model.predict_proba(X)[:, 1]

    def feature_importance(self) -> np.ndarray | None:
        return None if self._model is None else self._model.feature_importances_


class CalibratedModel:
    """Fit a base model, then split-conformal calibrate its probability output."""

    def __init__(self, base: ProbModel, alpha: float = 0.1, calib_frac: float = 0.3, seed: int = 42):
        self.base = base
        self.alpha, self.calib_frac, self.seed = alpha, calib_frac, seed
        self._cal = ConformalCalibrator(alpha=alpha, calib_frac=calib_frac, seed=seed)
        self.fitted = False

    def fit(self, X, y, sample_weight=None) -> "CalibratedModel":
        X = np.asarray(X, dtype="float64")
        y = np.asarray(y, dtype=int)
        n = len(y)
        if n < 40 or len(np.unique(y)) < 2:
            self.base.fit(X, y, sample_weight)
            self._cal.fit(self.base.raw_proba(X), y)  # degenerate -> base rate, wide band
            self.fitted = True
            return self

        cut = max(20, int(n * (1 - self.calib_frac)))
        cut = min(cut, n - 10)
        sw = None if sample_weight is None else np.asarray(sample_weight)[:cut]
        self.base.fit(X[:cut], y[:cut], sw)
        cal_scores = self.base.raw_proba(X[cut:])
        self._cal.fit(cal_scores, y[cut:])
        self.fitted = True
        return self

    def predict_one(self, x_row) -> CalibratedProb:
        raw = float(self.base.raw_proba(np.atleast_2d(x_row))[0])
        return self._cal.predict(raw)

    def predict(self, X):
        return [self._cal.predict(float(s)) for s in self.base.raw_proba(X)]


def make_model(kind: str, seed: int = 42) -> ProbModel:
    if kind in ("gbm", "lightgbm"):
        return GBMModel(seed=seed)
    if kind in ("linear", "logistic"):
        return LinearModel(seed=seed)
    raise ValueError(f"unknown model '{kind}'")
