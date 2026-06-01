"""Event-driven backtesting with realistic costs (consumes the PIT accessor)."""

from apex_quant.backtest.engine import Backtester
from apex_quant.backtest.result import BacktestResult, Trade, compute_metrics

__all__ = ["Backtester", "BacktestResult", "Trade", "compute_metrics"]
