"""Validation: CPCV split integrity, DSR & PBO statistical sanity, end-to-end."""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.validation import (
    cpcv_splits,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probability_of_backtest_overfitting,
    run_validation,
)


# -- CPCV split integrity -------------------------------------------------------
def test_cpcv_generates_all_combinations():
    splits = cpcv_splits(120, 6, 2, embargo_pct=0.0, purge=0)
    assert len(splits) == 15  # C(6,2)


def test_cpcv_train_test_disjoint_and_purged():
    purge = 5
    splits = cpcv_splits(180, 6, 2, embargo_pct=0.05, purge=purge)
    groups = np.array_split(np.arange(180), 6)
    for train_idx, test_idx in splits:
        train_set, test_set = set(train_idx.tolist()), set(test_idx.tolist())
        assert train_set.isdisjoint(test_set)
        # no training bar in the purge window immediately before any test block
        for g in range(6):
            block = groups[g]
            if block[0] in test_set:
                for j in range(block[0] - purge, block[0]):
                    assert j not in train_set


# -- DSR ------------------------------------------------------------------------
def test_expected_max_sharpe_grows_with_trials():
    assert expected_max_sharpe(0.1, 100) > expected_max_sharpe(0.1, 5) > 0


def test_dsr_penalises_many_trials():
    rng = np.random.default_rng(0)
    rets = rng.normal(0.001, 0.01, 1500)          # modest positive edge
    sr = rets.mean() / rets.std(ddof=1)
    few = [sr * 0.9, sr, sr * 1.1]                 # few trials, low dispersion
    many = list(rng.normal(0.0, 0.05, 300))        # many trials, high dispersion
    dsr_few = deflated_sharpe_ratio(rets, few)["dsr"]
    dsr_many = deflated_sharpe_ratio(rets, many)["dsr"]
    assert dsr_few > dsr_many


def test_dsr_zero_variance_is_zero():
    assert deflated_sharpe_ratio(np.zeros(100), [0.0])["dsr"] == 0.0


# -- PBO ------------------------------------------------------------------------
def test_pbo_low_for_genuine_edge():
    rng = np.random.default_rng(3)
    T = 600
    genuine = rng.normal(0.003, 0.01, T)           # consistent real edge
    noise = rng.normal(0.0, 0.01, (T, 3))
    M = np.column_stack([genuine, noise])
    pbo = probability_of_backtest_overfitting(M, n_splits=10)["pbo"]
    assert pbo < 0.5


def test_pbo_higher_for_pure_noise():
    rng = np.random.default_rng(4)
    M_noise = rng.normal(0.0, 0.01, (600, 4))
    M_genuine = np.column_stack([rng.normal(0.003, 0.01, 600), rng.normal(0.0, 0.01, (600, 3))])
    pbo_noise = probability_of_backtest_overfitting(M_noise, n_splits=10)["pbo"]
    pbo_genuine = probability_of_backtest_overfitting(M_genuine, n_splits=10)["pbo"]
    assert pbo_noise > pbo_genuine


# -- end-to-end -----------------------------------------------------------------
def _trend(n=560, drift=0.001, noise=0.004, seed=5):
    rng = np.random.default_rng(seed)
    close = 1.10 * np.exp(np.cumsum(rng.normal(drift, noise, n)))
    op = np.concatenate([[1.10], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range("2018-01-01", periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


def test_run_validation_produces_report():
    cfg = get_config().model_copy(deep=True)
    cfg.validation.cpcv.n_groups = 4          # C(4,2)=6 splits -> fast
    cfg.validation.cpcv.n_test_groups = 2
    pit = PointInTimeAccessor(_trend())
    report = run_validation(
        pit, "EUR/USD", param_grid=[{"momentum_lookback": 63, "vol_window": 63},
                                    {"momentum_lookback": 21, "vol_window": 21}],
        cfg=cfg, generated_for="2024-12-31",
    )
    assert report.n_trials == 2
    assert "passed" in report.verdict
    assert isinstance(report.verdict["reasons"], list) and report.verdict["reasons"]
    assert 0.0 <= report.dsr["dsr"] <= 1.0
    assert report.cpcv["n_paths"] >= 1
    assert report.summary()
