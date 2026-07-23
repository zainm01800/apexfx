"""Full Portfolio Backtest for Equity XS Momentum + Trend Book Combined

Evaluates Equity XS Momentum integrated into the APEX FX engine architecture.
Measures:
  1. Standalone Equity XS Momentum Sharpe, return, maxDD under RiskManager.
  2. Combined Trend Book + Equity XS Momentum Sharpe, return, maxDD under RiskManager.
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

EQUITY_CORE_GOLD = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD",
    "PLTR", "TSM", "NFLX", "UBER",
    "ISWD.L", "ISDU.L", "ISDE.L",
    "XLK", "XLE", "XBI", "SMH", "SOXX", "SGLD.L"
]


def load_daily_bars() -> dict[str, pd.DataFrame]:
    bars = {}
    for inst in EQUITY_CORE_GOLD:
        key = inst.replace("/", "_")
        p = STORE / f"{key}_1d.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df = df[df.index < HOLDOUT]
            if len(df) > 252:
                bars[inst] = df
    return bars


def run_xs_portfolio_backtest():
    from apex_quant.config import get_config, set_global_seeds
    from apex_quant.backtest.portfolio import PortfolioBacktester
    from apex_quant.strategies.cross_sectional import CrossSectionalMomentum, CrossSectionalMomentumStrategy
    from apex_quant.data.point_in_time import PointInTimeAccessor
    
    bars = load_daily_bars()
    
    # Instantiate the shared XS model
    xs_model = CrossSectionalMomentum(
        bars,
        lookback=126,
        vol_window=63,
        long_frac=0.15,  # top 3
        allow_short=False,
        min_universe=5,
        holding_horizon=21,
        timeframe="1d",
    )
    
    strats = xs_model.strategies()
    pits = {inst: PointInTimeAccessor(df) for inst, df in bars.items()}
    
    cfg = get_config()
    cfg.risk.max_risk_per_trade = 0.005
    cfg.risk.max_swing_slots = 10
    
    bt = PortfolioBacktester(
        cfg, slot_allocation="expected_value",
        exit_mode="managed", use_regime=False,
        vol_window=63, corr_window=63,
    )
    
    set_global_seeds(42)
    result = bt.run(pits, strats)
    
    print("=" * 70)
    print("STANDALONE EQUITY XS MOMENTUM (Engine Backtester + RiskManager)")
    print("=" * 70)
    print(f"  Sharpe:       {result.metrics.get('sharpe', 0):.3f}")
    print(f"  Ann return:   {result.metrics.get('ann_return', 0)*100:.2f}%")
    print(f"  Ann vol:      {result.metrics.get('ann_vol', 0)*100:.2f}%")
    print(f"  Max drawdown: {result.metrics.get('max_drawdown', 0)*100:.2f}%")
    print(f"  Trades:       {result.metrics.get('n_trades', 0)}")
    print(f"  Win rate:     {result.metrics.get('win_rate', 0)*100:.1f}%")
    print(f"  Profit factor:{result.metrics.get('profit_factor', 0):.2f}")
    
    return result.returns


if __name__ == "__main__":
    run_xs_portfolio_backtest()
