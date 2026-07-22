"""Unit tests for Paired Block-Bootstrap & Diebold-Mariano testing module (apex_quant/validation/paired_tests.py)."""

import pandas as pd
import numpy as np
import pytest
from apex_quant.validation.paired_tests import paired_block_bootstrap, diebold_mariano_test

def test_paired_block_bootstrap():
    rng = np.random.default_rng(42)
    idx = pd.date_range("2020-01-01", periods=500, freq="B")
    r_base = pd.Series(rng.normal(0.0002, 0.01, size=500), index=idx)
    r_new = pd.Series(rng.normal(0.0008, 0.01, size=500), index=idx)
    
    res = paired_block_bootstrap(r_base, r_new, block_size=21, n_bootstraps=1000, seed=42)
    assert "sharpe_delta" in res
    assert res["sharpe_delta"] > 0
    assert "p_value_one_sided" in res
    assert 0.0 <= res["p_value_one_sided"] <= 1.0
    assert res["ci_95_lower"] < res["ci_95_upper"]

def test_diebold_mariano_test():
    rng = np.random.default_rng(42)
    idx = pd.date_range("2020-01-01", periods=500, freq="B")
    r_base = pd.Series(rng.normal(0.0002, 0.01, size=500), index=idx)
    r_new = pd.Series(rng.normal(0.0008, 0.01, size=500), index=idx)
    
    res = diebold_mariano_test(r_base, r_new)
    assert "dm_stat" in res
    assert "p_value" in res
    assert 0.0 <= res["p_value"] <= 1.0
