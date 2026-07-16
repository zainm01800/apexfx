"""Portfolio-level CPCV/DSR/PBO harness.

The load-bearing test is that a pure-noise universe is REJECTED. A validation gate
that cannot reject noise is worse than no gate at all, because it manufactures
confidence.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.strategies import CrossSectionalMomentum
from apex_quant.validation import run_portfolio_validation
from apex_quant.validation.portfolio_report import run_portfolio_cpcv


def _panel(drifts, n=400, noise=0.006, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n, tz="UTC", name="timestamp")
    panel = {}
    for i, dr in enumerate(drifts):
        close = 1.0 * np.exp(np.cumsum(rng.normal(dr, noise, n)))
        op = np.concatenate([[1.0], close[:-1]])
        hi = np.maximum(op, close) * 1.002
        lo = np.minimum(op, close) * 0.998
        panel[f"P{i}/USD"] = pd.DataFrame(
            {"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)
    return panel


def _factory(panel, **kw):
    return CrossSectionalMomentum(panel, **kw)


def _grid():
    return [
        {"lookback": 21, "long_frac": 0.30, "short_frac": 0.30, "min_universe": 4},
        {"lookback": 63, "long_frac": 0.30, "short_frac": 0.30, "min_universe": 4},
    ]


@pytest.fixture(scope="module")
def noise_report():
    """A universe of pure random walks — there is no edge to find, by construction."""
    panel = _panel([0.0] * 5, n=400, noise=0.006, seed=11)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    return run_portfolio_validation(
        panel, pits, _factory, _grid(),
        strategy_name="cross_sectional_momentum", warmup=150,
        timeframes={k: "1d" for k in panel},
    )


# -- the gate must reject noise ------------------------------------------------
def test_pure_noise_is_rejected(noise_report):
    assert noise_report.verdict["passed"] is False
    # DSR is the multiple-testing gate; noise must not clear 0.95.
    assert noise_report.dsr["dsr"] <= 0.95


def test_report_is_well_formed(noise_report):
    r = noise_report
    assert len(r.universe) == 5
    assert r.n_trials >= 2
    assert r.cpcv["n_paths"] > 0
    assert len(r.cpcv["oos_sharpe_paths"]) == r.cpcv["n_paths"]
    assert set(["dsr_pass", "pbo_pass", "cpcv_pass", "passed", "reasons"]) <= set(r.verdict)
    assert isinstance(r.summary(), str) and "cross_sectional_momentum" in r.summary()


def test_verdict_requires_all_three_gates(noise_report):
    v = noise_report.verdict
    assert v["passed"] == (v["dsr_pass"] and v["pbo_pass"] and v["cpcv_pass"])


# -- honest trial counting threads through -------------------------------------
def test_honest_trial_count_raises_the_bar():
    panel = _panel([0.0] * 5, n=400, noise=0.006, seed=12)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    kw = dict(strategy_name="cs", warmup=150, timeframes={k: "1d" for k in panel})
    small = run_portfolio_validation(panel, pits, _factory, _grid(), **kw)
    honest = run_portfolio_validation(panel, pits, _factory, _grid(), n_trials=60, **kw)
    assert honest.n_trials == 60
    assert honest.dsr["sr0"] >= small.dsr["sr0"]      # more trials -> higher benchmark
    assert honest.dsr["dsr"] <= small.dsr["dsr"]      # -> a less flattering DSR


# -- CPCV mechanics ------------------------------------------------------------
def test_cpcv_returns_a_distribution():
    panel = _panel([0.002, 0.001, 0.0, -0.001, -0.002], n=400, seed=3)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    out = run_portfolio_cpcv(
        panel, pits, _factory,
        {"lookback": 21, "long_frac": 0.30, "short_frac": 0.30, "min_universe": 4},
        warmup=150, timeframes={k: "1d" for k in panel},
    )
    assert out["n_paths"] > 1                      # a distribution, not one path
    assert 0.0 <= out["frac_positive"] <= 1.0
    assert np.isfinite(out["oos_sharpe_median"])
