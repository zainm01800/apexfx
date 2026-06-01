"""Realised + EWMA volatility estimators.

These produce a forward volatility *estimate* (annualised) from a point-in-time
window of closes. They are fast and leakage-free, so they're the default in the
backtest loop; GARCH (in garch.py) is the heavier forward-looking model used for
the live estimate. All estimators share the ``VolForecast`` return type.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel

from apex_quant.config import get_config


class VolForecast(BaseModel):
    """A forward volatility estimate. ``annualized`` is the headline number used
    by sizing and regime; ``per_bar`` is the native (e.g. daily) std."""

    annualized: float
    per_bar: float
    horizon: int
    method: str
    converged: bool = True
    detail: str = ""


def _closes(data) -> np.ndarray:
    if isinstance(data, pd.DataFrame):
        data = data["close"]
    if isinstance(data, pd.Series):
        data = data.to_numpy()
    return np.asarray(data, dtype="float64")


def log_returns(data) -> np.ndarray:
    c = _closes(data)
    c = c[c > 0]
    return np.diff(np.log(c))


def realized_vol(data, window: int | None = None, annualization: int | None = None) -> VolForecast:
    """Close-to-close realised volatility over the last ``window`` returns."""
    cfg = get_config().volatility
    window = window or cfg.realized_windows[0]
    ann = annualization or cfg.annualization_factor

    r = log_returns(data)
    if len(r) < 2:
        return VolForecast(annualized=float("nan"), per_bar=float("nan"),
                           horizon=1, method="realized", converged=False,
                           detail="insufficient returns")
    r = r[-window:]
    per_bar = float(np.std(r, ddof=1))
    return VolForecast(
        annualized=per_bar * np.sqrt(ann),
        per_bar=per_bar,
        horizon=1,
        method=f"realized_{window}",
    )


def ewma_vol(data, lam: float = 0.94, annualization: int | None = None) -> VolForecast:
    """RiskMetrics EWMA volatility. ``lam`` is the decay (0.94 = ~RiskMetrics
    daily). More responsive to recent shocks than equal-weight realised vol."""
    ann = annualization or get_config().volatility.annualization_factor
    r = log_returns(data)
    if len(r) < 2:
        return VolForecast(annualized=float("nan"), per_bar=float("nan"),
                           horizon=1, method="ewma", converged=False,
                           detail="insufficient returns")
    # Recursive EWMA variance seeded with the sample variance.
    var = float(np.var(r, ddof=1))
    for x in r:
        var = lam * var + (1.0 - lam) * x * x
    per_bar = float(np.sqrt(var))
    return VolForecast(
        annualized=per_bar * np.sqrt(ann),
        per_bar=per_bar,
        horizon=1,
        method="ewma",
    )
