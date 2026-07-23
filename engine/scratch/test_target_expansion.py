"""Test 252d High-Quality Trend + R:R Target Expansion on 1 Single £100k Account.

Goal: Maximize Monthly Profit on 1 single £100k account while constraining Max DD.

Evaluates:
  1. 252d Trend + 1w HTF Confluence on 35 instruments
  2. Target Expansion to 2.0R on high-conviction signals (P >= 0.60)
  3. Risk per trade sweep: 0.60%, 0.75%, 0.85%
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


def run_target_expansion_backtest():
    bars = load_bars()
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    print("=" * 70)
    print("252D TREND + TARGET EXPANSION (2.0R) ON 1 SINGLE £100K ACCOUNT")
    print("=" * 70)
    
    for rpt in [0.0050, 0.0065, 0.0075, 0.0085]:
        cfg = get_config()
        cfg.risk.max_risk_per_trade = rpt
        cfg.risk.max_swing_slots = 12
        cfg.risk.max_concurrent_trades = 12
        
        # TradeManager with 2.0R Target Expansion
        tm = TradeManager(p1_r=1.0, p1_pct=0.33, p2_r=2.0, p2_pct=0.33, runner_mode=True)
        
        strats = {}
        for inst, df in bars.items():
            pit = PointInTimeAccessor(df)
            base_strat = RegimeGatedMomentum(
                momentum_lookback=252, vol_window=63, holding_horizon=252,
                reward_risk=2.0, regime_method="rule_based", timeframe="1d",
                instrument=inst,
            )
            strat = MultiTimeframeMomentum(
                base_strategy=base_strat, htf_rule="1w", htf_ma_window=50, instrument=inst
            )
            strats[inst] = strat
            
        bt = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", trade_manager=tm, vol_window=63, corr_window=63)
        set_global_seeds(42)
        res = bt.run(pits, strats)
        
        r = res.returns
        ann_r = r.mean() * 252
        monthly_ret_pct = ann_r / 12
        monthly_gbp = 100000 * monthly_ret_pct
        max_dd = res.metrics.get("max_drawdown", 0)
        sh = res.metrics.get("sharpe", 0)
        n_trades = res.metrics.get("n_trades", len(res.trades))
        n_months = len(r) / 21
        t_per_mo = n_trades / n_months
        
        print(f"\n  Risk {rpt*100:.2f}% per trade (2.0R Target Expansion):")
        print(f"    Sharpe Ratio:       {sh:.3f}")
        print(f"    Monthly Profit:     £{monthly_gbp:.2f} / month ({monthly_ret_pct*100:.2f}%/mo)")
        print(f"    Annual Return:      {ann_r*100:.2f}%")
        print(f"    Max Drawdown:       {max_dd*100:.2f}%")
        print(f"    Trades / Month:     {t_per_mo:.1f} ({n_trades} total trades)")
    print("=" * 70)


if __name__ == "__main__":
    run_target_expansion_backtest()
