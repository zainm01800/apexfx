"""Paired Statistical Significance Tests for Return Differences.

Implements:
1. Circular Block Bootstrap (block size ~21 trading days, seed=42) to compute
   p-values and 95% confidence intervals on the Sharpe ratio difference (Sharpe_new - Sharpe_base).
2. Diebold-Mariano test with Harvey-Leybourne-Newbold (HLN) finite-sample correction
   for paired strategy return streams.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.validation.metrics import sharpe_ratio


def paired_block_bootstrap(
    rets_base: pd.Series,
    rets_new: pd.Series,
    block_size: int = 21,
    n_bootstraps: int = 10000,
    seed: int = 42,
    periods_per_year: float = 252.0,
) -> dict:
    """Circular block bootstrap on paired return difference series.
    
    Returns 95% CI for (Sharpe_new - Sharpe_base), p-value of superiority (H0: delta <= 0).
    """
    aligned = pd.concat([rets_base, rets_new], axis=1).dropna()
    r_a = aligned.iloc[:, 0].to_numpy()
    r_b = aligned.iloc[:, 1].to_numpy()
    
    N = len(aligned)
    if N < block_size * 2:
        return {"error": "series too short for block bootstrap"}
        
    s_a_orig = sharpe_ratio(r_a, periods_per_year=periods_per_year)
    s_b_orig = sharpe_ratio(r_b, periods_per_year=periods_per_year)
    delta_orig = s_b_orig - s_a_orig
    
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(N / block_size))
    
    deltas = np.zeros(n_bootstraps)
    
    for b in range(n_bootstraps):
        # Sample block start indices uniformly
        start_indices = rng.integers(0, N, size=n_blocks)
        sampled_a = []
        sampled_b = []
        for idx in start_indices:
            # Wrap around circularly if block exceeds array length
            block_idx = (np.arange(idx, idx + block_size)) % N
            sampled_a.append(r_a[block_idx])
            sampled_b.append(r_b[block_idx])
            
        boot_a = np.concatenate(sampled_a)[:N]
        boot_b = np.concatenate(sampled_b)[:N]
        
        sa = sharpe_ratio(boot_a, periods_per_year=periods_per_year)
        sb = sharpe_ratio(boot_b, periods_per_year=periods_per_year)
        deltas[b] = sb - sa
        
    p_value = float(np.mean(deltas <= 0.0))
    ci_lower = float(np.percentile(deltas, 2.5))
    ci_upper = float(np.percentile(deltas, 97.5))
    
    return {
        "sharpe_base": float(s_a_orig),
        "sharpe_new": float(s_b_orig),
        "sharpe_delta": float(delta_orig),
        "p_value_one_sided": p_value,
        "ci_95_lower": ci_lower,
        "ci_95_upper": ci_upper,
        "n_bootstraps": n_bootstraps,
        "block_size": block_size,
    }


def diebold_mariano_test(
    rets_base: pd.Series,
    rets_new: pd.Series,
    h: int = 1,
) -> dict:
    """Diebold-Mariano test on mean-squared or mean-absolute return loss differences."""
    aligned = pd.concat([rets_base, rets_new], axis=1).dropna()
    r_a = aligned.iloc[:, 0].to_numpy()
    r_b = aligned.iloc[:, 1].to_numpy()
    
    N = len(aligned)
    # Loss function: negative returns as loss
    d = (r_b ** 2) - (r_a ** 2)
    mean_d = np.mean(d)
    
    # Auto-covariance estimation (Newey-West style)
    gamma_0 = np.var(d, ddof=0)
    gamma_sum = 0.0
    for k in range(1, h):
        gamma_k = np.mean((d[k:] - mean_d) * (d[:-k] - mean_d))
        gamma_sum += (1.0 - k / h) * gamma_k
        
    var_d = gamma_0 + 2.0 * gamma_sum
    if var_d <= 0:
        stat = 0.0
    else:
        stat = mean_d / np.sqrt(var_d / N)
        
    # Harvey-Leybourne-Newbold (HLN) correction
    hln_stat = stat * np.sqrt((N + 1 - 2 * h + h * (h - 1) / N) / N)
    
    from scipy.stats import norm
    p_val = float(1.0 - norm.cdf(hln_stat))
    
    return {
        "dm_stat": float(hln_stat),
        "p_value": p_val,
    }
