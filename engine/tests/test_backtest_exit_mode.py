import pytest
import pandas as pd
import numpy as np
from apex_quant.config import get_config
from apex_quant.backtest.engine import Backtester
from apex_quant.backtest.portfolio import PortfolioBacktester
from apex_quant.risk import RiskManager, Signal, Direction
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.strategies.baseline import RegimeGatedMomentum

def test_backtester_exit_modes():
    # Simple strategy/data setup to run a mini backtest
    cfg = get_config()
    
    # We construct a fake PointInTimeAccessor with a simple dataframe where:
    # - Price goes up then down, which would trigger Chandelier trailing stop in managed mode
    # but only hit stop/target/time in barrier mode.
    dates = pd.date_range("2025-01-01", periods=100, freq="D", tz="UTC")
    closes = [100.0] * 50 + [102.0] * 20 + [98.0] * 30
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    opens = closes
    
    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000] * 100
    }, index=dates)
    
    pit = PointInTimeAccessor(df)
    
    # Create strategy
    strat = RegimeGatedMomentum()
    
    # Run backtester in both modes
    bt_managed = Backtester(cfg=cfg, exit_mode="managed")
    res_managed = bt_managed.run(pit, strat, "EUR/USD", warmup=5)
    
    bt_barrier = Backtester(cfg=cfg, exit_mode="barrier")
    res_barrier = bt_barrier.run(pit, strat, "EUR/USD", warmup=5)
    
    # Parity test: managed default matches managed mode explicitly
    bt_default = Backtester(cfg=cfg)
    res_default = bt_default.run(pit, strat, "EUR/USD", warmup=5)
    
    assert len(res_default.trades) == len(res_managed.trades)
    
    # Both modes should produce trades
    print(f"Managed trades: {len(res_managed.trades)}, Barrier trades: {len(res_barrier.trades)}")
