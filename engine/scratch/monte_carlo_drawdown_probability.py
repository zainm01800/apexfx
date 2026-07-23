"""10,000-Path Monte Carlo Block Bootstrap Drawdown Probability Analysis.

Calculates the exact empirical probability of hitting a 13.3% drawdown in a 1-year period (252 trading days).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

STORE = ENGINE_DIR / "data_store"
HOLDOUT = pd.Timestamp("2025-01-01", tz="UTC")

from scratch.run_runner_ev_test import ALL_INSTRUMENTS


def load_bars():
    bars = {}
    for inst in ALL_INSTRUMENTS:
        key = inst.replace("/", "_")
        p = STORE / f"{key}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                bars[inst] = df
    return bars


def run_monte_carlo():
    bars = load_bars()
    close_dict = {inst: df["close"] for inst, df in bars.items()}
    df_close = pd.DataFrame(close_dict).ffill().dropna(how="all")
    
    daily_ret = df_close.pct_change().fillna(0)
    vol63 = daily_ret.rolling(63).std() * np.sqrt(252)
    
    sma200 = df_close.rolling(200).mean()
    trend_filter = (df_close > sma200) & (sma200 > sma200.shift(20))
    
    score = (df_close.pct_change(126) / vol63).where(trend_filter, np.nan)
    rank = score.rank(axis=1, ascending=False)
    
    weights = (rank <= 3).astype(float)
    inv_vol = 1.0 / vol63.clip(lower=0.05)
    weights = weights.mul(inv_vol, axis=0)
    weights = weights.div(weights.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    
    raw_port_ret = (weights.shift(1) * daily_ret).sum(axis=1)
    
    target_vol = 0.0623
    realized_port_vol = raw_port_ret.rolling(63).std() * np.sqrt(252)
    vol_scalar = (target_vol / realized_port_vol.clip(lower=0.03)).shift(1).clip(upper=2.0).fillna(1.0)
    
    daily_returns = (raw_port_ret * vol_scalar).dropna().to_numpy()
    
    # 10,000 Path Block Bootstrap Simulation (Block Size = 21 trading days = 1 month)
    np.random.seed(42)
    n_paths = 10000
    path_len = 252  # 1 year
    block_size = 21  # 1 month
    n_blocks = path_len // block_size
    
    max_dds = []
    annual_returns = []
    
    n_obs = len(daily_returns)
    
    for _ in range(n_paths):
        # Sample random blocks
        block_starts = np.random.randint(0, n_obs - block_size + 1, size=n_blocks)
        path_ret = np.concatenate([daily_returns[start:start+block_size] for start in block_starts])
        
        # Calculate drawdown
        cum_ret = np.cumprod(1 + path_ret)
        peak = np.maximum.accumulate(cum_ret)
        dd = (cum_ret - peak) / peak
        max_dd = float(abs(np.min(dd))) * 100
        ann_r = float(cum_ret[-1] - 1) * 100
        
        max_dds.append(max_dd)
        annual_returns.append(ann_r)
        
    max_dds = np.array(max_dds)
    annual_returns = np.array(annual_returns)
    
    prob_13 = float(np.mean(max_dds >= 13.3)) * 100
    prob_10 = float(np.mean(max_dds >= 10.0)) * 100
    prob_8 = float(np.mean(max_dds >= 8.0)) * 100
    prob_5 = float(np.mean(max_dds >= 5.0)) * 100
    
    median_dd = float(np.median(max_dds))
    p95_dd = float(np.percentile(max_dds, 95))
    p99_dd = float(np.percentile(max_dds, 99))
    
    mean_ann_r = float(np.mean(annual_returns))
    prob_profitable_year = float(np.mean(annual_returns > 0)) * 100
    
    print("=" * 70)
    print("10,000-PATH MONTE CARLO BLOCK BOOTSTRAP DRAWDOWN PROBABILITY")
    print("=" * 70)
    print(f"1-Year Simulation Paths:           10,000 Paths (252 trading days/path)")
    print(f"Block Size (Autocorrelation):      21 Days (1 Month)")
    print("-" * 50)
    print(f"Probability of Hitting >= 13.3% DD in a Year:  {prob_13:.1f}%")
    print(f"Probability of Hitting >= 10.0% DD in a Year:  {prob_10:.1f}%")
    print(f"Probability of Hitting >= 8.0% DD in a Year:   {prob_8:.1f}%")
    print(f"Probability of Hitting >= 5.0% DD in a Year:   {prob_5:.1f}%")
    print("-" * 50)
    print(f"Median 1-Year Max Drawdown:                    {median_dd:.2f}%")
    print(f"95th Percentile 1-Year Max Drawdown (95% VaR):  {p95_dd:.2f}%")
    print(f"99th Percentile 1-Year Max Drawdown (99% VaR):  {p99_dd:.2f}%")
    print("-" * 50)
    print(f"Average Expected 1-Year Return:                +{mean_ann_r:.2f}% (£{mean_ann_r*1000:.0f}/yr)")
    print(f"Probability of a Profitable Year (> £0):      {prob_profitable_year:.1f}%")
    print("=" * 70)


if __name__ == "__main__":
    run_monte_carlo()
