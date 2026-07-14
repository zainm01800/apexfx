"""Meta-labelling wrapper (Lopez de Prado, AFML ch. 3) — generalised.

The built-in ``MLStrategy`` meta-labels one hard-coded primary (regime-gated
momentum). This wrapper meta-labels **any** base ``Strategy``, and — crucially —
adds the piece the plain ML strategy is missing: an explicit **decision gate**.

Meta-labelling separates two questions:

  1. *Which side?*  — answered by the PRIMARY strategy (the ``base``). It already
     decides long / short / flat however it likes.
  2. *Act on it, and how strongly?* — answered here by a SECONDARY calibrated
     classifier that predicts P(this particular primary trade hits its target
     before its stop), trained on triple-barrier labels at the bars where the
     base actually fired.

The secondary model does two useful things the base cannot:

  * It replaces the base's (often cosmetic) probability with a calibrated,
    out-of-sample P(win) that the risk layer can honestly Kelly-size on.
  * It **vetoes** low-conviction trades: when P(win) < ``threshold`` the signal
    is forced flat. This is the whole point of meta-labelling — improving
    precision by *not taking* the primary's weakest trades — and it is exactly
    what ``MLStrategy`` omits (it always emits a signal when the primary fires,
    so with ``kelly_fraction = 0`` the P(win) gates nothing).

Leakage safety: features come from the same point-in-time frame the rest of the
engine uses (row ``t`` depends only on bars ``<= t``); triple-barrier labels look
forward at most ``holding_horizon`` bars, which the CPCV harness purges/embargoes.
If too few clean labels exist to fit, the wrapper degrades to a transparent
pass-through of the base signal — never a crash.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.ml.dataset import compute_feature_frame
from apex_quant.ml.models import CalibratedModel, make_model
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy
from apex_quant.strategies.labeling import atr_series, triple_barrier_label


class MetaLabeledStrategy(Strategy):
    """Wrap a primary ``base`` strategy with a secondary meta-label gate.

    Parameters
    ----------
    base :
        The primary strategy. Decides the side (long/short/flat).
    model :
        Secondary model kind — ``"gbm"`` (LightGBM) or ``"linear"`` (logistic).
    threshold :
        Trade only when the calibrated P(win) is at least this. Below it, the
        signal is forced flat (the meta veto). ``0.5`` is a neutral default.
    holding_horizon :
        Bars for the triple-barrier vertical barrier used to label trades.
        Defaults to the base's ``holding_horizon`` if it has one, else 10.
    min_samples :
        Minimum clean meta-labels required to fit the secondary model; below
        this the wrapper passes the base signal through unchanged.
    alpha :
        Miscoverage for the conformal calibrator wrapping the secondary model.
    seed :
        RNG seed (defaults to the global config seed) for a reproducible model.
    """

    def __init__(
        self,
        base: Strategy,
        model: str = "gbm",
        threshold: float = 0.5,
        holding_horizon: int | None = None,
        min_samples: int = 40,
        alpha: float = 0.1,
        seed: int | None = None,
    ) -> None:
        self.base = base
        self.cfg = get_config()
        self.model_kind = model
        self.threshold = threshold
        self.min_samples = min_samples
        self.holding_horizon = holding_horizon or getattr(base, "holding_horizon", 10)
        self.reward_risk = getattr(base, "reward_risk", 1.5)
        self._instrument = getattr(base, "instrument", "") or ""
        seed = self.cfg.seed if seed is None else seed
        self._model = CalibratedModel(make_model(model, seed=seed), alpha=alpha, seed=seed)
        self.name = f"meta_{getattr(base, 'name', 'base')}_{model}"
        self._fitted = False
        self._fm: pd.DataFrame | None = None
        self._fm_id: int | None = None

    # -- feature frame cache (keyed by data identity; rolling => leakage-safe) --
    def _frame(self, pit: PointInTimeAccessor) -> pd.DataFrame:
        if self._fm_id != id(pit):
            self._fm = compute_feature_frame(pit.as_of(pit.end), self.cfg)
            self._fm_id = id(pit)
        return self._fm

    # -- training -------------------------------------------------------------
    def fit(self, pit: PointInTimeAccessor, train_timestamps: Iterable[pd.Timestamp]) -> None:
        stamps = [pd.Timestamp(s) for s in train_timestamps]
        # 1. fit the primary on the same training bars (no-op if stateless).
        self.base.fit(pit, stamps)

        # 2. meta-label the trades the primary would actually take on those bars.
        fm = self._frame(pit)
        df = pit.as_of(pit.end)
        high, low, close = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
        atr = atr_series(df, self.cfg.risk.atr_window)
        stop_mult = self.cfg.risk.atr_stop_mult
        pos = {ts: i for i, ts in enumerate(df.index)}

        rows: list[np.ndarray] = []
        labels: list[int] = []
        for ts in stamps:
            if ts not in fm.index:
                continue
            frow = fm.loc[ts]
            if frow.isna().any():
                continue
            i = pos.get(ts)
            if i is None or not np.isfinite(atr[i]) or atr[i] <= 0:
                continue
            sig = self.base.generate(pit, ts, self._instrument)
            if sig.direction == Direction.FLAT:
                continue
            d = 1 if sig.direction == Direction.LONG else -1
            stop_dist = stop_mult * atr[i]
            lbl = triple_barrier_label(
                high, low, float(close[i]), d, stop_dist, sig.reward_risk * stop_dist,
                i, self.holding_horizon,
            )
            if lbl is None:
                continue
            rows.append(frow.to_numpy())
            labels.append(int(lbl))

        # 3. fit the secondary model if we have enough clean, two-class labels.
        if len(rows) >= self.min_samples and len(set(labels)) > 1:
            self._model.fit(np.asarray(rows, dtype="float64"), np.asarray(labels, dtype=int))
            self._fitted = True
        else:
            self._fitted = False  # too little evidence -> transparent pass-through

    def is_fitted(self) -> bool:
        return self._fitted

    # -- inference ------------------------------------------------------------
    def _meta_prob(self, pit: PointInTimeAccessor, t: pd.Timestamp):
        """Calibrated P(win) for the primary trade at ``t``, or ``None`` if the
        secondary model can't be evaluated (unfitted / missing features)."""
        if not self._fitted:
            return None
        fm = self._frame(pit)
        if t not in fm.index:
            return None
        row = fm.loc[t]
        if row.isna().any():
            return None
        return self._model.predict_one(row.to_numpy())

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        sig = self.base.generate(pit, t, instrument)
        if sig.direction == Direction.FLAT:
            return sig  # nothing to gate

        cal = self._meta_prob(pit, pd.Timestamp(t))
        if cal is None:
            return sig  # secondary unavailable -> defer to the primary unchanged

        if cal.probability < self.threshold:
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=cal.probability,
                reward_risk=sig.reward_risk, confidence=0.0, timeframe=sig.timeframe,
                rationale=f"meta-gate: P(win)={cal.probability:.2f} < threshold {self.threshold:.2f}",
            )
        return Signal(
            instrument=instrument, direction=sig.direction, probability=cal.probability,
            reward_risk=sig.reward_risk, confidence=cal.confidence, timeframe=sig.timeframe,
            rationale=(
                f"{sig.direction.value} | meta P(win)={cal.probability:.2f} "
                f"[{cal.lower:.2f},{cal.upper:.2f}] | base: {sig.rationale}"
            ),
        )

    def explain(self, pit: PointInTimeAccessor, t, instrument: str = "") -> dict:
        sig = self.base.generate(pit, t, instrument)
        cal = self._meta_prob(pit, pd.Timestamp(t)) if sig.direction != Direction.FLAT else None
        gated = bool(cal is not None and cal.probability < self.threshold)
        direction = Direction.FLAT.value if gated else sig.direction.value
        return {
            "instrument": instrument,
            "strategy": self.name,
            "direction": direction,
            "primary_direction": sig.direction.value,
            "probability": cal.probability if cal else sig.probability,
            "uncertainty": {"lower": cal.lower, "upper": cal.upper} if cal else None,
            "confidence": cal.confidence if cal else 0.0,
            "reward_risk": sig.reward_risk,
            "threshold": self.threshold,
            "meta_gated": gated,
            "fitted": self._fitted,
            "reason": sig.rationale,
        }
