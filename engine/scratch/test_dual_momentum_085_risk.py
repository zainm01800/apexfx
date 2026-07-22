"""Test Volatility-Targeted Dual Momentum at 0.85% Risk per Trade.

Computes exact figures for 0.85% Risk (6.2% Target Vol) on 1 Single £100k Account.
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


def run_085_risk():
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
    
    scaled_ret = raw_port_ret * vol_scalar
    r = scaled_ret.to_numpy()
    ann_r = float(r.mean() * 252)
    monthly_ret_pct = ann_r / 12
    monthly_gbp = float(100000 * monthly_ret_pct)
    
    eq = (1 + scaled_ret).cumprod() * 100000
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = float(abs(dd.min())) * 100
    
    ann_vol = float(r.std(ddof=1) * np.sqrt(252))
    sh = float(ann_r / ann_vol if ann_vol > 0 else 0)
    min_balance = float(eq.min())
    
    print("=" * 70)
    print("DUAL MOMENTUM AT 0.85% RISK PER TRADE ON 1 SINGLE £100K ACCOUNT")
    print("=" * 70)
    print(f"Risk per Trade: 0.85% (Target Vol: 6.2%):")
    print(f"  Sharpe Ratio:       {sh:.3f}")
    print(f"  Monthly Profit:     £{monthly_gbp:.2f} / month ({monthly_ret_pct*100:.2f}%/mo)")
    print(f"  Annual Return:      £{ann_r * 100000:.2f} / year ({ann_r*100:.2f}%)")
    print(f"  Max Drawdown:       {max_dd:.2f}%")
    print(f"  Lowest Equity:      £{min_balance:.2f} (Drawdown vs £100k Base = {max(0, 100000 - min_balance)/1000:.2f}%)")
    print("=" * 70)


if __name__ == "__main__":
    run_085_risk()
