"""Quantitative Search for Sharpe >= 1.50 on 1 Single £100k Account.

Goal: £700 - £1000 / month WITH Max Drawdown <= 11.0%.

Mathematical Requirement:
  Annual Return: 8.4% - 12.0% (£700 - £1000/mo)
  Max Drawdown: <= 11.0%
  Required Sharpe Ratio: >= 1.50

Techniques Tested:
  1. Trend Strength Filter: Trade only high-conviction signals (|score| > 1.25)
  2. Asymmetric Payoff: Target = 3.0R on high-conviction trend signals
  3. Strict Volatility-Parity Risk Sizing across Equities, FX, Gold, Crypto
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


def run_high_sharpe_search():
    bars = load_bars()
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    print("=" * 70)
    print("SEARCH FOR SHARPE >= 1.50 (£700 - £1000/MO AT <= 11.0% MAX DD)")
    print("=" * 70)
    
    for rpt in [0.0050, 0.0060, 0.0070, 0.0080]:
        for rr in [2.0, 2.5, 3.0]:
            cfg = get_config()
            cfg.risk.max_risk_per_trade = rpt
            cfg.risk.max_swing_slots = 10
            cfg.risk.max_concurrent_trades = 10
            
            # Advanced TradeManager with 3.0R runner exit
            tm = TradeManager(p1_r=1.0, p1_pct=0.33, p2_r=rr, p2_pct=0.33, runner_mode=True)
            
            strats = {}
            for inst, df in bars.items():
                pit = PointInTimeAccessor(df)
                b = RegimeGatedMomentum(
                    momentum_lookback=252, vol_window=63, holding_horizon=252,
                    reward_risk=rr, regime_method="rule_based", timeframe="1d",
                    instrument=inst,
                )
                strats[inst] = MultiTimeframeMomentum(base_strategy=b, htf_rule="1w", htf_ma_window=50, instrument=inst)
                
            bt = PortfolioBacktester(cfg, slot_allocation="ev_regime", exit_mode="managed", use_regime=True, trade_manager=tm, vol_window=63, corr_window=63)
            set_global_seeds(42)
            res = bt.run(pits, strats)
            
            r = res.returns
            ann_r = r.mean() * 252
            monthly_ret_pct = ann_r / 12
            monthly_gbp = 100000 * monthly_ret_pct
            max_dd = res.metrics.get("max_drawdown", 0)
            sh = res.metrics.get("sharpe", 0)
            
            print(f"Risk {rpt*100:.2f}%, Target {rr:.1f}R -> Sharpe: {sh:.3f}, Monthly: £{monthly_gbp:.2f}/mo ({monthly_ret_pct*100:.2f}%/mo), MaxDD: {max_dd*100:.2f}%")
    print("=" * 70)


if __name__ == "__main__":
    run_high_sharpe_search()
