"""ML dataset construction via meta-labelling (Lopez de Prado, AFML ch. 3).

We do NOT ask a model to predict raw price direction (near-impossible). Instead:
  * a transparent PRIMARY rule picks the direction (regime-gated momentum - the
    same trades the Phase 1 baseline would take);
  * the ML model is the SECONDARY/meta layer that predicts P(this trade hits its
    target before its stop), trained on triple-barrier labels.

That P(win) is exactly what the risk layer Kelly-sizes on, with payoff
b = reward_risk. Features are computed vectorised but each row depends only on
data up to that bar (rolling = backward), so the matrix is point-in-time. The
leakage test confirms rows are unchanged when future bars are poisoned.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from apex_quant.config import AppConfig, get_config
from apex_quant.strategies.labeling import atr_series, triple_barrier_label


@dataclass
class MLDataset:
    X: pd.DataFrame                 # features at candidate (primary-trade) bars
    y: np.ndarray                   # 1 = target hit before stop, 0 = stop first
    directions: np.ndarray          # +1 long / -1 short (primary direction)
    index: pd.DatetimeIndex
    feature_names: list[str]

    def __len__(self) -> int:
        return len(self.y)


def compute_feature_frame(df: pd.DataFrame, cfg: AppConfig | None = None) -> pd.DataFrame:
    """Vectorised feature matrix mirroring the Phase 1 feature definitions.
    Row t uses only bars <= t (all rolling windows are backward-looking)."""
    cfg = cfg or get_config()
    f = cfg.features
    ann = cfg.volatility.annualization_factor
    c = df["close"]
    logret = np.log(c).diff()
    out: dict[str, pd.Series] = {}

    for L in f.momentum_lookbacks:
        out[f"mom_{L}"] = c / c.shift(L) - 1.0
    mid = f.momentum_lookbacks[len(f.momentum_lookbacks) // 2]
    out[f"mom_vs_{mid}"] = (c / c.shift(mid) - 1.0) / logret.rolling(mid).std(ddof=1)

    for w in f.vol_windows:
        out[f"rvol_{w}"] = logret.rolling(w).std(ddof=1) * np.sqrt(ann)
    w0 = f.vol_windows[0]
    k = 1.0 / (4.0 * np.log(2.0))
    log_hl = np.log(df["high"] / df["low"])
    out[f"pvol_{w0}"] = np.sqrt((k * (log_hl ** 2).rolling(w0).mean()) * ann)

    ma = c.rolling(f.trend_ma).mean()
    out[f"trend_slope_{f.trend_ma}"] = (ma - ma.shift(f.trend_slope_window)) / (f.trend_slope_window * c)
    out[f"dist_ma_{f.trend_ma}"] = (c / ma - 1.0) / logret.rolling(w0).std(ddof=1)

    return pd.DataFrame(out, index=df.index)


def primary_direction(features: pd.DataFrame, cfg: AppConfig | None = None) -> np.ndarray:
    """Regime-gated momentum direction: +1/-1 only when the trend is decisive AND
    momentum agrees with it; 0 (no trade) otherwise. Mirrors the Phase 1 gate."""
    cfg = cfg or get_config()
    eps = cfg.regime.rule_based.ranging_slope_eps
    slope = features[f"trend_slope_{cfg.features.trend_ma}"].to_numpy()
    mid = cfg.features.momentum_lookbacks[len(cfg.features.momentum_lookbacks) // 2]
    mom = features[f"mom_vs_{mid}"].to_numpy()

    trending = np.abs(slope) > eps
    aligned = np.sign(mom) == np.sign(slope)
    direction = np.where(trending & aligned & np.isfinite(mom) & np.isfinite(slope), np.sign(mom), 0)
    return direction.astype(int)


def build_dataset(
    pit,
    *,
    cfg: AppConfig | None = None,
    train_end=None,
    holding_horizon: int = 10,
    reward_risk: float = 1.5,
) -> MLDataset:
    """Assemble (X, y) for meta-labelling over the data known at ``train_end``
    (defaults to all available history)."""
    cfg = cfg or get_config()
    df = pit.as_of(train_end if train_end is not None else pit.end)

    feats = compute_feature_frame(df, cfg)
    direction = primary_direction(feats, cfg)
    atr = atr_series(df, cfg.risk.atr_window)
    high, low, close = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    stop_mult = cfg.risk.atr_stop_mult

    n = len(df)
    rows, ys, dirs, idx = [], [], [], []
    fvals = feats.to_numpy()
    feat_ok = np.isfinite(fvals).all(axis=1)

    for i in range(n):
        d = direction[i]
        if d == 0 or not feat_ok[i] or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        stop_dist = stop_mult * atr[i]
        lbl = triple_barrier_label(
            high, low, float(close[i]), int(d), stop_dist, reward_risk * stop_dist, i, holding_horizon
        )
        if lbl is None:
            continue
        rows.append(fvals[i])
        ys.append(lbl)
        dirs.append(d)
        idx.append(df.index[i])

    X = pd.DataFrame(rows, columns=list(feats.columns), index=pd.DatetimeIndex(idx, name="timestamp"))
    return MLDataset(
        X=X, y=np.array(ys, dtype=int), directions=np.array(dirs, dtype=int),
        index=X.index, feature_names=list(feats.columns),
    )
