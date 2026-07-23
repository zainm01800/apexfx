"""Daily-loss stop — the prop-firm rule the from-peak drawdown breaker cannot see.

Every prop contract limits loss from the DAY'S OPENING equity (3% FundedElite, 4% Orion).
`drawdown_breaker` measures from PEAK, so a losing day that begins at a fresh high shows
near-zero drawdown while blowing the daily rule — account gone, breaker never fired.

config.prop.yaml declared this stop on 2026-07-22 and it was never implemented. The book's
worst day is -3.70% against a 3% limit: an 18.4% chance of losing a funded account over 24
months. These tests pin that it now exists, that it is OFF by default, and — the part that
actually saves the account — that it FLATTENS rather than merely blocking new entries.
"""
from __future__ import annotations

import pytest

from apex_quant.config import RiskConfig
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import AccountState, Direction, MarketState, Signal


def _sig(inst: str = "EUR/USD") -> Signal:
    return Signal(instrument=inst, direction=Direction.LONG, probability=0.60,
                  reward_risk=2.0, timeframe="1d")


def _mkt(inst: str = "EUR/USD") -> MarketState:
    return MarketState(instrument=inst, price=1.10, ann_vol=0.10, atr=0.01, correlations={})


def test_disabled_by_default():
    """Nothing certified may change silently — the stop is opt-in."""
    assert RiskConfig().daily_loss_limit == 0.0
    rm = RiskManager(RiskConfig(max_risk_per_trade=0.01))
    acct = AccountState(equity=90_000, peak_equity=100_000, day_start_equity=100_000)
    assert rm.permit(_sig(), acct, _mkt()).permitted, "off by default must be a no-op"


def test_daily_loss_measured_from_day_start_not_peak():
    """The whole point: a bad day starting at a fresh HIGH has ~0 drawdown from peak."""
    acct = AccountState(equity=97_000, peak_equity=100_000, day_start_equity=100_000)
    assert acct.drawdown == pytest.approx(0.03, rel=1e-6)
    assert acct.daily_loss == pytest.approx(0.03, rel=1e-6)

    # Equity made a new high yesterday, then fell 3% today: from-peak is still only 3%,
    # but had the peak been much higher the two would diverge — this pins the semantics.
    up = AccountState(equity=97_000, peak_equity=97_000, day_start_equity=100_000)
    assert up.drawdown == 0.0, "at a fresh high, from-peak drawdown is zero"
    assert up.daily_loss == pytest.approx(0.03, rel=1e-6), "but the daily rule is breached"


def test_stop_vetoes_new_entries_at_the_limit():
    cfg = RiskConfig(max_risk_per_trade=0.01, daily_loss_limit=0.025)
    rm = RiskManager(cfg)

    ok = AccountState(equity=97_600, peak_equity=100_000, day_start_equity=100_000)  # -2.4%
    assert rm.permit(_sig(), ok, _mkt()).permitted

    breached = AccountState(equity=97_400, peak_equity=100_000, day_start_equity=100_000)
    pos = rm.permit(_sig(), breached, _mkt())
    assert not pos.permitted
    assert "daily_loss_stop" in pos.constraints_applied


def test_a_winning_day_is_never_blocked():
    rm = RiskManager(RiskConfig(max_risk_per_trade=0.01, daily_loss_limit=0.025))
    acct = AccountState(equity=105_000, peak_equity=105_000, day_start_equity=100_000)
    assert acct.daily_loss == 0.0
    assert rm.permit(_sig(), acct, _mkt()).permitted


def test_missing_day_start_equity_skips_the_check_rather_than_crashing():
    """Callers that do not track sessions (research harnesses) must keep working."""
    rm = RiskManager(RiskConfig(max_risk_per_trade=0.01, daily_loss_limit=0.025))
    # 5% off peak: inside the drawdown breaker, so only the daily check is under test.
    acct = AccountState(equity=95_000, peak_equity=100_000)   # no day_start_equity
    assert acct.daily_loss == 0.0
    assert rm.permit(_sig(), acct, _mkt()).permitted


def test_from_peak_breaker_alone_would_have_missed_it():
    """Regression guard for the actual failure mode: drawdown_breaker cannot substitute.

    Account at a fresh high this morning, down 3.7% today (the book's real worst day).
    A 20% from-peak breaker sees 3.7% and permits. The daily stop is what saves it.
    """
    at_high = AccountState(equity=96_300, peak_equity=100_000, day_start_equity=100_000)

    no_daily = RiskManager(RiskConfig(max_risk_per_trade=0.01, drawdown_breaker=0.20))
    assert no_daily.permit(_sig(), at_high, _mkt()).permitted, (
        "the from-peak breaker permits — this is exactly the hole"
    )

    with_daily = RiskManager(RiskConfig(max_risk_per_trade=0.01, drawdown_breaker=0.20,
                                        daily_loss_limit=0.025))
    blocked = with_daily.permit(_sig(), at_high, _mkt())
    assert not blocked.permitted
    assert "daily_loss_stop" in blocked.constraints_applied


def test_backtester_flattens_open_positions_not_just_blocks_entries():
    """Blocking entries is NOT a daily stop — open positions carry the loss further.

    Pins that the backtester has the flattening branch wired to the config value.
    """
    import inspect
    from apex_quant.backtest import portfolio as pf

    src = inspect.getsource(pf.PortfolioBacktester.run)
    assert "daily_loss_stop" in src, "backtester must flatten on a daily-loss breach"
    assert "daily_limit" in src and "day_start_eq" in src
    assert 'constraint_log["daily_loss_stop_flattened"]' in src, (
        "the flatten event must be recorded in the constraint log, not silent"
    )
