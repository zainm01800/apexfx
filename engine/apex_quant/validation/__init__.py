"""Validation: CPCV + Deflated Sharpe + PBO. Where fake edges go to die."""

from apex_quant.validation.cpcv import cpcv_splits, run_cpcv
from apex_quant.validation.metrics import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from apex_quant.validation.report import ValidationReport, run_validation

__all__ = [
    "cpcv_splits",
    "run_cpcv",
    "sharpe_ratio",
    "expected_max_sharpe",
    "deflated_sharpe_ratio",
    "probability_of_backtest_overfitting",
    "ValidationReport",
    "run_validation",
]
