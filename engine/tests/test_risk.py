"""Risk layer: proves Kelly, every cap, the breaker, and stops fire correctly.

Per the brief, this is where the supremacy of the risk layer is demonstrated:
a signal can only ever *propose* - these tests show the manager disposing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.config import get_config
from apex_quant.regime.base import RegimeLabel
from apex_quant.risk import (
    AccountState,
    Direction,
    MarketState,
    OpenPosition,
    RiskManager,
    Signal,
    atr,
    fractional_kelly,
    full_kelly,
    vol_target_notional,
)


def mk_account(equity=100_000, peak=100_000, positions=None):
    return AccountState(equity=equity, peak_equity=peak, open_positions=positions or [])


def mk_market(price=100.0, ann_vol=0.10, atr_val=1.0, correlations=None):
    return MarketState(
        instrument="EUR/USD", price=price, ann_vol=ann_vol, atr=atr_val,
        correlations=correlations or {},
    )


def mk_signal(direction="long", p=0.70, b=2.0):
    return Signal(instrument="EUR/USD", direction=Direction(direction), probability=p, reward_risk=b)


# -- fractional Kelly ----------------------------------------------------------
def test_full_kelly_formula():
    assert full_kelly(0.6, 1.0) == pytest.approx(0.2)
    assert full_kelly(0.5, 1.0) == pytest.approx(0.0)


def test_fractional_kelly_is_a_fraction_of_full():
    p, b, frac = 0.65, 1.5, 0.25
    assert fractional_kelly(p, b, frac) == pytest.approx(frac * full_kelly(p, b))


def test_kelly_never_negative_or_above_one():
    assert fractional_kelly(0.3, 1.0, 0.25) == 0.0          # negative edge -> 0
    assert fractional_kelly(0.999, 50.0, 1.0) <= 1.0        # clamped at 1


def test_no_edge_signal_is_vetoed():
    rm = RiskManager()
    pos = rm.permit(mk_signal(p=0.50, b=1.0), mk_account(), mk_market())
    assert not pos.permitted
    assert "no_edge" in pos.constraints_applied
    assert pos.risk_fraction == 0.0


# -- per-trade risk cap --------------------------------------------------------
def test_per_trade_risk_cap_binds():
    rm = RiskManager()
    # huge edge would imply a big Kelly bet; the 1% cap must clamp it
    pos = rm.permit(mk_signal(p=0.99, b=3.0), mk_account(), mk_market(ann_vol=0.10))
    assert pos.permitted
    assert "max_risk_per_trade" in pos.constraints_applied
    assert pos.risk_fraction == pytest.approx(get_config().risk.max_risk_per_trade, rel=1e-6)


# -- drawdown breaker ----------------------------------------------------------
def test_drawdown_breaker_halts_new_positions():
    rm = RiskManager()
    acct = mk_account(equity=80_000, peak=100_000)  # 20% drawdown == breaker
    pos = rm.permit(mk_signal(p=0.9, b=2.0), acct, mk_market())
    assert not pos.permitted
    assert "drawdown_breaker" in pos.constraints_applied


def test_just_below_breaker_allows_trade():
    rm = RiskManager()
    acct = mk_account(equity=85_000, peak=100_000)  # 15% < 20% breaker
    pos = rm.permit(mk_signal(p=0.9, b=2.0), acct, mk_market())
    assert pos.permitted


# -- flat ----------------------------------------------------------------------
def test_flat_signal_no_position():
    rm = RiskManager()
    pos = rm.permit(mk_signal(direction="flat"), mk_account(), mk_market())
    assert not pos.permitted
    assert "flat_signal" in pos.constraints_applied


# -- vol targeting -------------------------------------------------------------
def test_vol_target_ceiling_binds_in_high_vol():
    rm = RiskManager()
    # very high ann_vol -> vol-target notional small -> it, not the risk cap, binds
    pos = rm.permit(mk_signal(p=0.7, b=2.0), mk_account(), mk_market(ann_vol=0.60))
    assert pos.permitted
    assert "vol_target" in pos.constraints_applied
    assert pos.risk_fraction < get_config().risk.max_risk_per_trade


def test_vol_target_notional_formula():
    assert vol_target_notional(100_000, 0.10, 0.20) == pytest.approx(50_000)


# -- gross exposure cap --------------------------------------------------------
def test_gross_exposure_cap_binds():
    rm = RiskManager()
    cfg = get_config().risk
    ceiling = cfg.max_total_exposure * 100_000
    existing = OpenPosition(instrument="USD/JPY", direction=Direction.LONG, notional=ceiling - 10_000)
    acct = mk_account(positions=[existing])  # only 10k headroom
    pos = rm.permit(mk_signal(p=0.99, b=3.0), acct, mk_market(ann_vol=0.05))
    assert pos.permitted
    assert "max_total_exposure" in pos.constraints_applied
    assert pos.notional == pytest.approx(10_000, rel=1e-6)


# -- correlation cluster cap ---------------------------------------------------
def test_correlation_cap_binds():
    rm = RiskManager()
    cfg = get_config().risk
    ceiling = cfg.max_correlated_exposure * 100_000
    correlated = OpenPosition(instrument="GBP/USD", direction=Direction.LONG, notional=ceiling - 10_000)
    acct = mk_account(positions=[correlated])
    market = mk_market(ann_vol=0.05, correlations={"GBP/USD": 0.85})  # > 0.60 threshold
    pos = rm.permit(mk_signal(p=0.99, b=3.0), acct, market)
    assert pos.permitted
    assert "max_correlated_exposure" in pos.constraints_applied
    assert pos.notional == pytest.approx(10_000, rel=1e-6)


def test_uncorrelated_position_does_not_trip_cluster_cap():
    rm = RiskManager()
    cfg = get_config().risk
    big = OpenPosition(instrument="USD/JPY", direction=Direction.LONG, notional=cfg.max_correlated_exposure * 100_000)
    acct = mk_account(positions=[big])
    market = mk_market(ann_vol=0.05, correlations={"USD/JPY": 0.10})  # below threshold
    pos = rm.permit(mk_signal(p=0.99, b=3.0), acct, market)
    assert "max_correlated_exposure" not in pos.constraints_applied


# -- regime scaling ------------------------------------------------------------
def test_regime_scaling_shrinks_size():
    rm = RiskManager()
    sig, acct, mkt = mk_signal(p=0.8, b=2.0), mk_account(), mk_market(ann_vol=0.05)
    base = rm.permit(sig, acct, mkt)
    damp = RegimeLabel(trend="ranging", vol="high", confidence=1.0, method="x")  # aggression 0.25
    scaled = rm.permit(sig, acct, mkt, regime=damp)
    assert scaled.notional < base.notional
    assert any(c.startswith("regime_scale") for c in scaled.constraints_applied)


# -- min position floor --------------------------------------------------------
def test_min_position_floor_vetoes_dust():
    cfg = get_config().risk.model_copy(update={"min_position": 1e12})
    rm = RiskManager(cfg=cfg)
    pos = rm.permit(mk_signal(p=0.8, b=2.0), mk_account(), mk_market())
    assert not pos.permitted
    assert "below_min_position" in pos.constraints_applied


# -- stops + happy path --------------------------------------------------------
def test_long_stop_below_target_above():
    cfg = get_config().risk.model_copy(update={"atr_stop_mult": 2.0})
    rm = RiskManager(cfg=cfg)
    pos = rm.permit(mk_signal(direction="long", p=0.8, b=2.0), mk_account(), mk_market(price=100, atr_val=1.0))
    assert pos.permitted
    assert pos.stop_price == pytest.approx(98.0)        # 100 - 2*ATR
    assert pos.target_price == pytest.approx(104.0)     # 100 + b*stop_distance


def test_short_stop_above_target_below():
    cfg = get_config().risk.model_copy(update={"atr_stop_mult": 2.0})
    rm = RiskManager(cfg=cfg)
    pos = rm.permit(mk_signal(direction="short", p=0.8, b=2.0), mk_account(), mk_market(price=100, atr_val=1.0))
    assert pos.permitted
    assert pos.stop_price == pytest.approx(102.0)
    assert pos.target_price == pytest.approx(96.0)
    assert pos.signed_notional < 0


def test_position_carries_transparency_log():
    rm = RiskManager()
    pos = rm.permit(mk_signal(p=0.8, b=2.0), mk_account(), mk_market())
    assert pos.rationale
    assert "kelly_risk_fraction" in pos.sizing_detail
    assert "probability" in pos.sizing_detail


# -- ATR -----------------------------------------------------------------------
def test_atr_positive(clean_daily):
    assert atr(clean_daily, 14) > 0


def test_wider_atr_means_smaller_position():
    rm = RiskManager()
    sig, acct = mk_signal(p=0.99, b=3.0), mk_account()
    tight = rm.permit(sig, acct, mk_market(ann_vol=0.05, atr_val=0.5))
    wide = rm.permit(sig, acct, mk_market(ann_vol=0.05, atr_val=2.0))
    assert wide.units < tight.units  # wider stop -> fewer units for same risk


def test_max_concurrent_trades_exceeded():
    cfg = get_config().risk.model_copy(update={"max_concurrent_trades": 2})
    rm = RiskManager(cfg=cfg)
    p1 = OpenPosition(instrument="EUR/USD", direction=Direction.LONG, notional=1000.0, risk=100.0)
    p2 = OpenPosition(instrument="GBP/USD", direction=Direction.LONG, notional=1000.0, risk=100.0)
    acct = mk_account(positions=[p1, p2])
    pos = rm.permit(mk_signal(p=0.8, b=2.0), acct, mk_market())
    assert not pos.permitted
    # Per-timeframe-bucket refactor renamed this constraint: the global ceiling
    # (sum of buckets, == max_concurrent_trades) now vetoes via "global_trade_cap".
    assert "global_trade_cap" in pos.constraints_applied


def test_max_portfolio_risk_exceeded():
    # max_portfolio_risk = 0.035 (3.5% of 100k equity = 3,500)
    cfg = get_config().risk.model_copy(update={"max_portfolio_risk": 0.035})
    rm = RiskManager(cfg=cfg)
    # Existing trade already has 4% risk (4,000)
    p1 = OpenPosition(instrument="EUR/USD", direction=Direction.LONG, notional=10000.0, risk=4000.0)
    acct = mk_account(equity=100000.0, positions=[p1])
    pos = rm.permit(mk_signal(p=0.8, b=2.0), acct, mk_market())
    assert not pos.permitted
    assert "max_portfolio_risk_exceeded" in pos.constraints_applied


def test_portfolio_risk_cap_downsize():
    # max_portfolio_risk = 0.035 (3.5% of 100k equity = 3,500)
    cfg = get_config().risk.model_copy(update={"max_portfolio_risk": 0.035, "max_risk_per_trade": 0.02})
    rm = RiskManager(cfg=cfg)
    # Existing trade has 2% risk (2,000)
    p1 = OpenPosition(instrument="EUR/USD", direction=Direction.LONG, notional=10000.0, risk=2000.0)
    acct = mk_account(equity=100000.0, positions=[p1])
    
    # Proposed trade wants to risk 2% of equity (2,000), but remaining budget is 1.5% (1,500)
    pos = rm.permit(mk_signal(p=0.99, b=3.0), acct, mk_market(price=100.0, atr_val=1.0)) # Kelly/max cap suggests 2% risk
    assert pos.permitted
    assert "portfolio_risk_cap" in pos.constraints_applied
    # The final risk fraction must be capped at 1.5% (0.015)
    assert pos.sizing_detail["max_proposed_risk"] == pytest.approx(0.015)

