"""Calculate Equity Buffer Compounding Effect on Monthly Profit & Drawdown.

Simulates leaving profit buffers in the account (£101k, £102.5k, £105k, £110k)
and computing the expanded risk capacity, monthly profit (£/mo), and max drawdown.
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


def run_buffer_simulation():
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
    
    target_vol = 0.055
    realized_port_vol = raw_port_ret.rolling(63).std() * np.sqrt(252)
    vol_scalar = (target_vol / realized_port_vol.clip(lower=0.03)).shift(1).clip(upper=2.0).fillna(1.0)
    base_ret = raw_port_ret * vol_scalar
    
    buffers = [
        ("£100,000 (Base)", 100000, 1.00, 0.0075),
        ("£101,000 (+£1k Buffer)", 101000, 1.01, 0.0076),
        ("£102,500 (+£2.5k Buffer)", 102500, 1.025, 0.0077),
        ("£105,000 (+£5k Buffer)", 105000, 1.05, 0.0079),
        ("£110,000 (+£10k Buffer)", 110000, 1.10, 0.0083),
    ]
    
    print("=" * 70)
    print("EQUITY BUFFER COMPOUNDING SIMULATION (DUAL MOMENTUM SHARPE 1.330)")
    print("=" * 70)
    
    for label, balance, scale, risk_per_trade in buffers:
        scaled_ret = base_ret * scale
        r = scaled_ret.to_numpy()
        ann_r = float(r.mean() * 252)
        monthly_ret_pct = ann_r / 12
        monthly_gbp = float(balance * monthly_ret_pct)
        
        eq = (1 + scaled_ret).cumprod() * balance
        peak = eq.cummax()
        dd_pct = float(abs(((eq - peak) / peak).min())) * 100
        
        # Max drawdown relative to initial £100k balance
        min_balance = float(eq.min())
        dd_from_100k = float(max(0, (100000 - min_balance) / 100000)) * 100
        
        print(f"\n{label}:")
        print(f"  Monthly Profit:     £{monthly_gbp:.2f} / month ({monthly_ret_pct*100:.2f}%/mo)")
        print(f"  Annual Return:      £{ann_r * balance:.2f} / year ({ann_r*100:.2f}%)")
        print(f"  Max DD (Peak-to-Trough): {dd_pct:.2f}%")
        print(f"  Max DD vs £100k Base:   {dd_from_100k:.2f}% (Buffer absorbs loss)")
        print(f"  Risk per Trade:     {risk_per_trade*100:.2f}%")
    print("=" * 70)


if __name__ == "__main__":
    run_buffer_simulation()
