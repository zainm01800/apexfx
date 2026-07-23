"""Search for £700 - £1000/mo at <= 10% Max Drawdown.

Tests 3 specific quantitative levers:
  Lever 1: 1h + 1d Multi-Timeframe Trend Confluence (higher trade frequency + tighter stops)
  Lever 2: Trailing Stop Activation at 1.0R / 1.5R (protects open profits, reduces drawdown depth)
  Lever 3: RiskManager Drawdown Reducing Limit (drawdown_reducing_limit = 0.05) at 0.75%-0.85% risk
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

def load_data():
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

def test_lever_drawdown_ramp(bars):
    """Test RiskManager drawdown ramp (drawdown_reducing_limit = 0.05) at 0.75%-1.0% risk."""
    print("=" * 70)
    print("LEVER 1: RiskManager Dynamic Drawdown Ramp (5% Trigger Limit)")
    print("=" * 70)
    
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    for rpt in [0.0075, 0.0085, 0.0100]:
        cfg = get_config()
        cfg.risk.max_risk_per_trade = rpt
        cfg.risk.max_swing_slots = 12
        cfg.risk.drawdown_reducing_limit = 0.05  # de-risk when drawdown hits 5%
        
        strats = {}
        for inst, df in bars.items():
            pit = PointInTimeAccessor(df)
            b = RegimeGatedMomentum(
                momentum_lookback=252, vol_window=63, holding_horizon=21,
                reward_risk=1.5, regime_method="rule_based", timeframe="1d", instrument=inst,
            )
            strats[inst] = MultiTimeframeMomentum(base_strategy=b, htf_rule="1w", htf_ma_window=50, instrument=inst)
            
        bt = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", vol_window=63, corr_window=63)
        set_global_seeds(42)
        res = bt.run(pits, strats)
        
        r = res.returns
        ann_r = r.mean() * 252
        monthly_gbp = 100000 * (ann_r / 12)
        max_dd = res.metrics.get("max_drawdown", 0)
        sh = res.metrics.get("sharpe", 0)
        
        print(f"Risk {rpt*100:.2f}% + DD Ramp -> Sharpe: {sh:.3f}, Monthly: £{monthly_gbp:.2f}/mo ({ann_r/12*100:.2f}%/mo), MaxDD: {max_dd*100:.2f}%")

def test_lever_fast_trailing_stop(bars):
    """Test Fast Trailing Stop Activation (BE at 1.0R, Trail at 1.2R)."""
    print("\n" + "=" * 70)
    print("LEVER 2: Fast Trailing Stop Activation (BE at 1.0R, Trail at 1.2R)")
    print("=" * 70)
    
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    for rpt in [0.0075, 0.0085, 0.0100]:
        cfg = get_config()
        cfg.risk.max_risk_per_trade = rpt
        cfg.risk.max_swing_slots = 12
        
        tm = TradeManager(p1_r=1.0, p1_pct=0.50, p2_r=1.5, p2_pct=0.25, runner_mode=True)
        
        strats = {}
        for inst, df in bars.items():
            pit = PointInTimeAccessor(df)
            b = RegimeGatedMomentum(
                momentum_lookback=252, vol_window=63, holding_horizon=252,
                reward_risk=1.5, regime_method="rule_based", timeframe="1d", instrument=inst,
            )
            strats[inst] = MultiTimeframeMomentum(base_strategy=b, htf_rule="1w", htf_ma_window=50, instrument=inst)
            
        bt = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", trade_manager=tm, vol_window=63, corr_window=63)
        set_global_seeds(42)
        res = bt.run(pits, strats)
        
        r = res.returns
        ann_r = r.mean() * 252
        monthly_gbp = 100000 * (ann_r / 12)
        max_dd = res.metrics.get("max_drawdown", 0)
        sh = res.metrics.get("sharpe", 0)
        
        print(f"Risk {rpt*100:.2f}% + Fast Trailing -> Sharpe: {sh:.3f}, Monthly: £{monthly_gbp:.2f}/mo ({ann_r/12*100:.2f}%/mo), MaxDD: {max_dd*100:.2f}%")

def main():
    bars = load_data()
    test_lever_drawdown_ramp(bars)
    test_lever_fast_trailing_stop(bars)

if __name__ == "__main__":
    main()
