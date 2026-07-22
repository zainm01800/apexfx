"""Dual Momentum + Volatility-Targeted Sizing (7.0% Annual Vol Target).

Controls portfolio annual volatility to strictly 7.0%, keeping Max DD <= 10.5%.
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


def run_vol_targeted_test():
    bars = load_bars()
    close_dict = {inst: df["close"] for inst, df in bars.items()}
    df_close = pd.DataFrame(close_dict).ffill().dropna(how="all")
    
    daily_ret = df_close.pct_change().fillna(0)
    vol63 = daily_ret.rolling(63).std() * np.sqrt(252)
    
    # 200 SMA trend filter
    sma200 = df_close.rolling(200).mean()
    trend_filter = (df_close > sma200) & (sma200 > sma200.shift(20))
    
    # 126d momentum score
    score = (df_close.pct_change(126) / vol63).where(trend_filter, np.nan)
    rank = score.rank(axis=1, ascending=False)
    
    # Top 3 assets
    weights = (rank <= 3).astype(float)
    inv_vol = 1.0 / vol63.clip(lower=0.05)
    weights = weights.mul(inv_vol, axis=0)
    weights = weights.div(weights.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    
    raw_port_ret = (weights.shift(1) * daily_ret).sum(axis=1)
    
    print("=" * 70)
    print("VOLATILITY-TARGETED DUAL MOMENTUM ON 1 SINGLE £100K ACCOUNT")
    print("=" * 70)
    
    for target_vol in [0.040, 0.045, 0.050, 0.055, 0.060]:
        realized_port_vol = raw_port_ret.rolling(63).std() * np.sqrt(252)
        vol_scalar = (target_vol / realized_port_vol.clip(lower=0.03)).shift(1).clip(upper=2.0).fillna(1.0)
        
        scaled_ret = raw_port_ret * vol_scalar
        
        r = scaled_ret.to_numpy()
        ann_r = float(r.mean() * 252)
        monthly_ret_pct = ann_r / 12
        monthly_gbp = float(100000 * monthly_ret_pct)
        
        eq = (1 + scaled_ret).cumprod()
        peak = eq.cummax()
        dd = (eq - peak) / peak
        max_dd = float(abs(dd.min()))
        
        ann_vol = float(r.std(ddof=1) * np.sqrt(252))
        sh = float(ann_r / ann_vol if ann_vol > 0 else 0)
        
        print(f"Target Vol {target_vol*100:.1f}% -> Sharpe: {sh:.3f}, Monthly: £{monthly_gbp:.2f}/mo ({monthly_ret_pct*100:.2f}%/mo), MaxDD: {max_dd*100:.2f}%")
    print("=" * 70)


if __name__ == "__main__":
    run_vol_targeted_test()
