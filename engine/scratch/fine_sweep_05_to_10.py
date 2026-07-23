"""Fine-Grained Risk Sweep from 0.50% to 1.00% (Steps of 0.05%).

Goal: Find the exact optimal sweet spot between 0.50% and 1.00% risk per trade
on 1 single £100k account.
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


def run_fine_sweep():
    bars = load_bars()
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    print("=" * 70)
    print("FINE-GRAINED RISK SWEEP (0.50% TO 1.00% IN 0.05% STEPS)")
    print("=" * 70)
    
    results = []
    risk_steps = [0.0050, 0.0055, 0.0060, 0.0065, 0.0070, 0.0075, 0.0080, 0.0085, 0.0090, 0.0095, 0.0100]
    
    for rpt in risk_steps:
        cfg = get_config()
        cfg.risk.max_risk_per_trade = rpt
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
        res = bt.run(pits, strats)
        
        r = res.returns
        ann_r = float(r.mean() * 252)
        monthly_ret_pct = ann_r / 12
        monthly_gbp = float(100000 * monthly_ret_pct)
        max_dd = float(res.metrics.get("max_drawdown", 0))
        sh = float(res.metrics.get("sharpe", 0))
        
        results.append({
            "risk_pct": rpt * 100,
            "sharpe": sh,
            "monthly_gbp": monthly_gbp,
            "monthly_pct": monthly_ret_pct * 100,
            "ann_return_pct": ann_r * 100,
            "max_dd_pct": max_dd * 100,
        })
        
        print(f"Risk {rpt*100:.2f}% -> Sharpe: {sh:.3f}, Monthly: £{monthly_gbp:.2f}/mo ({monthly_ret_pct*100:.2f}%/mo), MaxDD: {max_dd*100:.2f}%")
        
    print("=" * 70)


if __name__ == "__main__":
    run_fine_sweep()
