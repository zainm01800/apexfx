"""Event-driven backtesting with realistic costs (consumes the PIT accessor)."""

from apex_quant.backtest.engine import Backtester
from apex_quant.backtest.result import BacktestResult, Trade, compute_metrics
from apex_quant.backtest.adaptive import AdaptiveBacktester, AdaptiveWrapperStrategy
from apex_quant.backtest.portfolio import PortfolioBacktester, PortfolioResult

__all__ = [
    "Backtester", "BacktestResult", "Trade", "compute_metrics",
    "AdaptiveBacktester", "AdaptiveWrapperStrategy",
    "PortfolioBacktester", "PortfolioResult",
]
