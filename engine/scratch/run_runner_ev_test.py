"""Test Runner Exit Model (book_runner_252) under EV Slot Allocation + 0.5% Risk

Evaluates whether the runner exit mechanism (uncapped trailing stops allowing
winners to run past 21 bars) improves the baseline trend book Sharpe above 0.922.
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

EQUITY_CORE = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD",
    "PLTR", "TSM", "NFLX", "UBER",
    "ISWD.L", "ISDU.L", "ISDE.L",
    "XLK", "XLE", "XBI", "SMH", "SOXX",
]
GOLD_ETC = "SGLD.L"
CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD",
          "ADA/USD", "AVAX/USD", "DOGE/USD", "LINK/USD", "ARB/USD", "SUI/USD"]
FX_MAJORS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"]

ALL_INSTRUMENTS = EQUITY_CORE + [GOLD_ETC] + CRYPTO + FX_MAJORS


def run_runner_ev_backtest():
    from apex_quant.config import get_config, set_global_seeds
    from apex_quant.backtest.portfolio import PortfolioBacktester
    from apex_quant.strategies.baseline import RegimeGatedMomentum
    from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
    from apex_quant.data.point_in_time import PointInTimeAccessor
    from apex_quant.risk.trade_manager import TradeManager
    
    bars = {}
    for inst in ALL_INSTRUMENTS:
        key = inst.replace("/", "_")
        p = STORE / f"{key}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                bars[inst] = df
    
    print(f"Loaded {len(bars)} instruments before {HOLDOUT.date()}")
    
    # 1. Build strategies with runner holding horizon (holding_horizon=252 or uncapped)
    strats = {}
    pits = {}
    for inst, df in bars.items():
        pit = PointInTimeAccessor(df)
        base_strat = RegimeGatedMomentum(
            momentum_lookback=252, vol_window=63, holding_horizon=252,  # runner horizon
            reward_risk=1.5, regime_method="rule_based", timeframe="1d",
            instrument=inst,
        )
        strat = MultiTimeframeMomentum(
            base_strategy=base_strat, htf_rule="1w", htf_ma_window=50, instrument=inst
        )
        strats[inst] = strat
        pits[inst] = pit
    
    # Configure backtester with managed exits & TradeManager runner config
    cfg = get_config()
    cfg.risk.max_risk_per_trade = 0.005
    cfg.risk.max_swing_slots = 10
    
    tm = TradeManager(runner_mode=True)
    
    bt = PortfolioBacktester(
        cfg, slot_allocation="expected_value", exit_mode="managed",
        trade_manager=tm, vol_window=63, corr_window=63,
    )
    
    set_global_seeds(42)
    result = bt.run(pits, strats)
    
    print("\n" + "=" * 70)
    print("BOOK RUNNER + EV SLOT ALLOCATION + 0.5% RISK")
    print("=" * 70)
    print(f"  Sharpe:       {result.metrics.get('sharpe', 0):.3f}")
    print(f"  Ann return:   {result.metrics.get('ann_return', 0)*100:.2f}%")
    print(f"  Ann vol:      {result.metrics.get('ann_vol', 0)*100:.2f}%")
    print(f"  Max drawdown: {result.metrics.get('max_drawdown', 0)*100:.2f}%")
    print(f"  Trades:       {result.metrics.get('n_trades', 0)}")
    print(f"  Win rate:     {result.metrics.get('win_rate', 0)*100:.1f}%")
    print(f"  Profit factor:{result.metrics.get('profit_factor', 0):.2f}")
    print("=" * 70)


if __name__ == "__main__":
    run_runner_ev_backtest()
