"""Dual Momentum + Volatility Parity Strategy.

Tests Dual Momentum (Cross-Sectional RS Rank + Absolute Trend Filter)
combined with Volatility Parity Risk Sizing on 1 Single £100k Account.

Goal: Raise Portfolio Sharpe to >= 1.40+, maximizing Monthly Profit at ~10% Max DD.
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
from apex_quant.config import get_config, set_global_seeds
from apex_quant.backtest.portfolio import PortfolioBacktester
from apex_quant.data.point_in_time import PointInTimeAccessor


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


def simulate_dual_momentum(bars: dict[str, pd.DataFrame], risk_per_trade: float = 0.0075) -> dict:
    """Simulates Dual Momentum with Volatility Parity across all 35 instruments."""
    # Build aligned price matrix
    close_dict = {}
    vol_dict = {}
    
    for inst, df in bars.items():
        close_dict[inst] = df["close"]
        ret = df["close"].pct_change()
        vol_dict[inst] = ret.rolling(63).std() * np.sqrt(252)
        
    df_close = pd.DataFrame(close_dict).ffill().dropna(how="all")
    df_vol = pd.DataFrame(vol_dict).ffill().dropna(how="all")
    
    # 1. Absolute Trend Filter: Close > 200 SMA and 200 SMA slope > 0
    sma200 = df_close.rolling(200).mean()
    abs_trend = (df_close > sma200) & (sma200 > sma200.shift(20))
    
    # 2. Relative Strength Rank: 126-day return / 63-day vol
    ret126 = df_close.pct_change(126)
    rs_score = ret126 / df_vol
    
    # Select top 4 assets each day that pass absolute trend filter
    valid_score = rs_score.where(abs_trend, np.nan)
    rank = valid_score.rank(axis=1, ascending=False)
    target_weights = (rank <= 4).astype(float)
    
    # Normalize weights by Volatility Parity (1 / Vol)
    inv_vol = 1.0 / df_vol.clip(lower=0.05)
    vol_weight = target_weights.mul(inv_vol, axis=0)
    vol_weight = vol_weight.div(vol_weight.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    
    # Daily returns
    daily_ret = df_close.pct_change().fillna(0)
    strategy_ret = (vol_weight.shift(1) * daily_ret).sum(axis=1) * (risk_per_trade / 0.0050)
    
    r = strategy_ret.to_numpy()
    ann_r = float(r.mean() * 252)
    monthly_ret_pct = ann_r / 12
    monthly_gbp = float(100000 * monthly_ret_pct)
    
    eq = (1 + strategy_ret).cumprod()
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = float(abs(dd.min()))
    
    ann_vol = float(r.std(ddof=1) * np.sqrt(252))
    sh = float(ann_r / ann_vol if ann_vol > 0 else 0)
    
    return {
        "sharpe": sh,
        "monthly_gbp": monthly_gbp,
        "monthly_ret_pct": monthly_ret_pct,
        "ann_return": ann_r,
        "max_drawdown": max_dd,
        "returns": strategy_ret,
    }


def main():
    bars = load_bars()
    print("=" * 70)
    print("DUAL MOMENTUM + VOLATILITY PARITY (35 INSTRUMENTS)")
    print("=" * 70)
    
    for rpt in [0.0050, 0.0065, 0.0075, 0.0085, 0.0100]:
        res = simulate_dual_momentum(bars, rpt)
        print(f"Risk {rpt*100:.2f}% -> Sharpe: {res['sharpe']:.3f}, Monthly: £{res['monthly_gbp']:.2f}/mo ({res['monthly_ret_pct']*100:.2f}%/mo), MaxDD: {res['max_drawdown']*100:.2f}%")
    print("=" * 70)


if __name__ == "__main__":
    main()
