"""Test for the live scanner timeframe filter."""

from __future__ import annotations

import pytest
import scripts.run_live_paper_trading as scanner


def test_live_scanner_timeframe_filter(monkeypatch, capsys):
    # Mock portfolio to have items with 15m, 1h, 1d timeframes
    test_portfolio = [
        {"instrument": "EUR/USD", "timeframe": "15m", "style": "scalp"},
        {"instrument": "GBP/USD", "timeframe": "1h", "style": "intraday"},
        {"instrument": "AUD/USD", "timeframe": "1d", "style": "swing"},
        {"instrument": "USD/JPY", "timeframe": "1w", "style": "swing"},
    ]
    monkeypatch.setattr(scanner, "ROBUST_CORE_PORTFOLIO", test_portfolio)
    
    # Mock active session check to always return True
    monkeypatch.setattr(scanner, "is_asset_in_active_session", lambda sym: True)
    
    # Mock executor submit to do nothing and count how many times it was called
    submitted_items = []
    
    class FakeExecutor:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
        def submit(self, func, item, active_trades_map, corr_matrix):
            submitted_items.append(item)
            # Return a fake future that can be passed to as_completed
            class FakeFuture:
                pass
            return FakeFuture()
            
    # Mock ThreadPoolExecutor in scripts.run_live_paper_trading
    monkeypatch.setattr(scanner, "ThreadPoolExecutor", lambda max_workers: FakeExecutor())
    # Mock as_completed to do nothing
    monkeypatch.setattr(scanner, "as_completed", lambda futures: [])
    # Mock get_portfolio_correlation_matrix to return empty dict
    monkeypatch.setattr(scanner, "get_portfolio_correlation_matrix", lambda: {})
    
    # 1. Test when live_timeframes is ["1d", "1w"]
    scanner.cfg.data.live_timeframes = ["1d", "1w"]
    submitted_items.clear()
    capsys.readouterr() # Clear output
    
    scanner.scan_robust_core(open_trades=[])
    
    # Should only submit 1d and 1w items
    assert len(submitted_items) == 2
    assert {item["timeframe"] for item in submitted_items} == {"1d", "1w"}
    
    # Should print skip info for 15m and 1h once per cycle
    captured = capsys.readouterr().out
    assert "Skipping scan for timeframe '15m'" in captured
    assert "Skipping scan for timeframe '1h'" in captured
    # Should not duplicate the print for the same timeframe
    assert captured.count("Skipping scan for timeframe '15m'") == 1
    
    # 2. Test when live_timeframes is None (backward compatible, scans all)
    scanner.cfg.data.live_timeframes = None
    submitted_items.clear()
    
    scanner.scan_robust_core(open_trades=[])
    
    assert len(submitted_items) == 4
    assert {item["timeframe"] for item in submitted_items} == {"15m", "1h", "1d", "1w"}
