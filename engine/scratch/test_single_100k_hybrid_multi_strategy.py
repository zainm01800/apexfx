"""Sweep Hybrid Multi-Strategy on 1 Single £100k Account.

Finds the exact Trend risk level that produces £700/month and checks its Max Drawdown.
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
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.trade_manager import TradeManager
from scratch.correlation_screen_tom_seasonality import simulate_tom_sleeve


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


def run_sweep():
    bars = load_bars()
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    r_tom = simulate_tom_sleeve()
    if r_tom.index.tz is None: r_tom.index = r_tom.index.tz_localize("UTC")
    
    print("=" * 70)
    print("SWEEPING HYBRID MULTI-STRATEGY ON 1 SINGLE £100K ACCOUNT")
    print("=" * 70)
    
    for trend_rpt in [0.70, 0.90, 1.10, 1.30, 1.50]:
        cfg = get_config()
        cfg.risk.max_risk_per_trade = trend_rpt / 100
        cfg.risk.max_swing_slots = 10
        cfg.risk.max_concurrent_trades = 10
        
        tm = TradeManager(runner_mode=True)
        strats = {}
        for inst, df in bars.items():
            pit = PointInTimeAccessor(df)
            b = RegimeGatedMomentum(
                momentum_lookback=252, vol_window=63, holding_horizon=252,
                reward_risk=1.5, regime_method="rule_based", timeframe="1d",
                instrument=inst,
            )
            strats[inst] = MultiTimeframeMomentum(base_strategy=b, htf_rule="1w", htf_ma_window=50, instrument=inst)
            
        bt = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", trade_manager=tm, vol_window=63, corr_window=63)
        set_global_seeds(42)
        res_trend = bt.run(pits, strats)
        
        r_trend = res_trend.returns
        if r_trend.index.tz is None: r_trend.index = r_trend.index.tz_localize("UTC")
        
        df_comb = pd.DataFrame({"trend": r_trend, "tom": r_tom}).fillna(0)
        p_ret = 0.70 * df_comb["trend"] + 0.30 * df_comb["tom"]
        
        r = p_ret.to_numpy()
        ann_r = float(r.mean() * 252)
        monthly_ret_pct = ann_r / 12
        monthly_gbp = 100000 * monthly_ret_pct
        
        eq = (1 + p_ret).cumprod()
        peak = eq.cummax()
        dd = (eq - peak) / peak
        max_dd = float(abs(dd.min()))
        
        ann_vol = float(r.std(ddof=1) * np.sqrt(252))
        sh = float(ann_r / ann_vol if ann_vol > 0 else 0)
        
        print(f"Trend Risk {trend_rpt:.2f}% + TOM -> Sharpe: {sh:.3f}, Monthly: £{monthly_gbp:.2f}/mo ({monthly_ret_pct*100:.2f}%/mo), MaxDD: {max_dd*100:.2f}%")
    print("=" * 70)


if __name__ == "__main__":
    run_sweep()
