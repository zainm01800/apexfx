"""Find Exact Configuration for £700-£1000/mo at ~10% Max Drawdown.

Tests:
  1. Tighter ATR Stop (1.5 ATR stop instead of 2.0 ATR) -> smaller price distance = larger position size for same risk %
  2. Reward/Risk = 2.0 (2:1 target)
  3. Risk per trade = 0.70%, 0.75%, 0.80%
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


def run_optimal_config_search():
    bars = load_bars()
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    print("=" * 70)
    print("SEARCH FOR £700 - £1000/MO AT ~10% MAX DRAWDOWN")
    print("=" * 70)
    
    for stop_mult in [1.5, 1.75, 2.0]:
        for rpt in [0.0070, 0.0075, 0.0080]:
            cfg = get_config()
            cfg.risk.max_risk_per_trade = rpt
            cfg.risk.atr_stop_mult = stop_mult
            cfg.risk.max_swing_slots = 12
            cfg.risk.max_concurrent_trades = 12
            
            strats = {}
            for inst, df in bars.items():
                pit = PointInTimeAccessor(df)
                b = RegimeGatedMomentum(
                    momentum_lookback=252, vol_window=63, holding_horizon=21,
                    reward_risk=2.0, regime_method="rule_based", timeframe="1d",
                    instrument=inst, atr_stop_mult=stop_mult,
                )
                strats[inst] = MultiTimeframeMomentum(base_strategy=b, htf_rule="1w", htf_ma_window=50, instrument=inst)
                
            bt = PortfolioBacktester(cfg, slot_allocation="expected_value", exit_mode="managed", vol_window=63, corr_window=63)
            set_global_seeds(42)
            res = bt.run(pits, strats)
            
            r = res.returns
            ann_r = r.mean() * 252
            monthly_ret_pct = ann_r / 12
            monthly_gbp = 100000 * monthly_ret_pct
            max_dd = res.metrics.get("max_drawdown", 0)
            sh = res.metrics.get("sharpe", 0)
            
            print(f"Stop {stop_mult}x ATR, Risk {rpt*100:.2f}% -> Sharpe: {sh:.3f}, Monthly: £{monthly_gbp:.2f}/mo ({monthly_ret_pct*100:.2f}%/mo), MaxDD: {max_dd*100:.2f}%")


if __name__ == "__main__":
    run_optimal_config_search()
