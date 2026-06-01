"""Combinatorial Purged Cross-Validation (Lopez de Prado, AFML ch. 12).

Splits the timeline into N contiguous groups and tests every combination of k
groups, training on the rest. Two protections against leakage:
  * Purge: drop training observations whose forward-looking labels (horizon bars)
    overlap a test block.
  * Embargo: additionally drop a fraction of training bars right after each test
    block, to defeat serial-correlation leakage.

Across the C(N,k) combinations every bar is tested multiple times along different
training histories, yielding a *distribution* of out-of-sample results rather
than one fragile path.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from apex_quant.backtest.engine import Backtester
from apex_quant.config import AppConfig, get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.validation.metrics import sharpe_ratio


def cpcv_splits(
    n_obs: int, n_groups: int, n_test_groups: int, embargo_pct: float, purge: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Yield (train_idx, test_idx) for every C(n_groups, n_test_groups) combo,
    with purge + embargo applied to the training set."""
    groups = np.array_split(np.arange(n_obs), n_groups)
    embargo = int(round(n_obs * embargo_pct))
    splits = []
    for combo in combinations(range(n_groups), n_test_groups):
        test_idx = np.concatenate([groups[g] for g in combo])
        forbidden = set(test_idx.tolist())
        for g in combo:
            block = groups[g]
            lo, hi = int(block[0]), int(block[-1])
            forbidden.update(range(lo - purge, lo))             # purge before
            forbidden.update(range(hi + 1, hi + 1 + purge + embargo))  # purge+embargo after
        train_idx = np.array([i for i in range(n_obs) if i not in forbidden], dtype=int)
        splits.append((train_idx, np.sort(test_idx)))
    return splits


def run_cpcv(
    pit: PointInTimeAccessor,
    instrument: str,
    strategy_factory,
    params: dict,
    *,
    cfg: AppConfig | None = None,
    horizon: int = 10,
) -> dict:
    """Run CPCV for one configuration. Returns the OOS Sharpe distribution across
    paths (per-period Sharpes), plus aggregate stats."""
    cfg = cfg or get_config()
    c = cfg.validation.cpcv
    df = pit.as_of(pit.end)
    idx = df.index
    n = len(df)

    splits = cpcv_splits(n, c.n_groups, c.n_test_groups, c.embargo_pct, purge=horizon)
    bt = Backtester(cfg)
    oos_sharpes: list[float] = []

    for train_idx, test_idx in splits:
        if len(train_idx) < 60 or len(test_idx) < 30:
            continue
        strat = strategy_factory(**params)
        strat.fit(pit, idx[train_idx])
        test_start, test_end = idx[int(test_idx[0])], idx[int(test_idx[-1])]
        res = bt.run(pit, strat, instrument, start=test_start, end=test_end, warmup=0)
        rets = res.returns
        test_dates = idx[test_idx]
        rets = rets[rets.index.isin(test_dates)]
        oos_sharpes.append(sharpe_ratio(rets, periods_per_year=1))

    arr = np.array(oos_sharpes) if oos_sharpes else np.array([0.0])
    return {
        "n_paths": len(oos_sharpes),
        "oos_sharpe_mean": float(arr.mean()),
        "oos_sharpe_std": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        "oos_sharpe_median": float(np.median(arr)),
        "frac_positive": float(np.mean(arr > 0)),
        "oos_sharpe_paths": [round(float(x), 4) for x in arr],
    }
