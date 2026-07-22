"""Unit tests for COT Speculator Crowding Reversal Strategy (apex_quant/strategies/cot_reversal.py)."""

import pandas as pd
import numpy as np
import pytest
from apex_quant.strategies.cot_reversal import COTReversalBook, COTReversalStrategy
from apex_quant.risk.types import Direction, Signal

def test_cot_reversal_book_initialization():
    dates = pd.date_range("2020-01-01", "2022-01-01", freq="B", tz="UTC")
    df_eur = pd.DataFrame({
        "open": 1.10, "high": 1.11, "low": 1.09, "close": 1.10, "volume": 1000
    }, index=dates)
    
    panel = {"EUR/USD": df_eur}
    book = COTReversalBook(panel, z_threshold=2.0, horizon=10, cot_years=range(2020, 2023))
    
    strats = book.strategies()
    assert isinstance(strats, dict)
    if "EUR/USD" in strats:
        strat = strats["EUR/USD"]
        assert isinstance(strat, COTReversalStrategy)
        assert strat.instrument == "EUR/USD"
        
        # Test generate method
        sig = strat.generate(None, dates[10], "EUR/USD")
        assert isinstance(sig, Signal)
        assert sig.instrument == "EUR/USD"
        assert sig.direction in (Direction.LONG, Direction.SHORT, Direction.FLAT)
