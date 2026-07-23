"""Test 1.5% Risk per Trade with Account Drawdown Protection (Breaker at 8%).

Goal: £700 - £1000 / month WITH Max DD around 10% on 1 Single £100k Account.

Mechanism:
  - Base Risk: 1.5% risk per trade during normal/trending periods
  - Drawdown Protection: As drawdown approaches 8.0%, risk per trade scales down linearly to 0.2%
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


def run_15_risk_breaker_backtest():
    bars = load_bars()
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    print("=" * 70)
    print("TESTING 1.5% RISK PER TRADE WITH DRAWDOWN PROTECTION (8% TRIGGER)")
    print("=" * 70)
    
    for dd_trigger in [0.06, 0.08, 0.10]:
        cfg = get_config()
        cfg.risk.max_risk_per_trade = 0.015  # 1.5% risk per trade
        cfg.risk.max_swing_slots = 10
        cfg.risk.max_concurrent_trades = 10
        cfg.risk.drawdown_reducing_limit = dd_trigger
        
        strats = {}
        for inst, df in bars.items():
            pit = PointInTimeAccessor(df)
            b = RegimeGatedMomentum(
                momentum_lookback=252, vol_window=63, holding_horizon=21,
                reward_risk=1.5, regime_method="rule_based", timeframe="1d",
                instrument=inst,
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
        n_trades = res.metrics.get("n_trades", len(res.trades))
        t_per_mo = n_trades / (len(r) / 21)
        
        print(f"Risk 1.50% + DD Trigger {dd_trigger*100:.1f}% -> Sharpe: {sh:.3f}, Monthly: £{monthly_gbp:.2f}/mo ({monthly_ret_pct*100:.2f}%/mo), MaxDD: {max_dd*100:.2f}%, Trades/mo: {t_per_mo:.1f}")
    print("=" * 70)


if __name__ == "__main__":
    run_15_risk_breaker_backtest()
