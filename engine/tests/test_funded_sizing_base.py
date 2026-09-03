"""Opt-in funded capital base for new-position sizing."""

from __future__ import annotations

import pytest

from apex_quant.config import get_config
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import (
    AccountState,
    Direction,
    MarketState,
    OpenPosition,
    Signal,
)


def _cfg(**updates):
    values = {
        "kelly_fraction": 0.0,
        "max_risk_per_trade": 0.01,
        "target_portfolio_vol": 100.0,
        "max_portfolio_risk": 1.0,
        "max_total_exposure": 100.0,
        "max_correlated_exposure": 100.0,
        "max_position_notional_pct": 0.0,
        "drawdown_breaker": 1.0,
        "drawdown_reducing_limit": 0.99,
        "min_position": 0.0,
    }
    values.update(updates)
    return get_config().risk.model_copy(update=values)


def _signal() -> Signal:
    return Signal(
        instrument="TEST",
        direction=Direction.LONG,
        probability=0.9,
        reward_risk=2.0,
        stop_price=99.0,
        target_price=102.0,
    )


def _market() -> MarketState:
    return MarketState(
        instrument="TEST", price=100.0, ann_vol=0.1, atr=1.0,
    )


def _account(
    *,
    base=None,
    positions=None,
    candidate_cap=None,
    aggregate_cap=None,
    equity=100_000.0,
) -> AccountState:
    return AccountState(
        equity=equity,
        peak_equity=max(100_000.0, equity),
        open_positions=positions or [],
        risk_sizing_base=base,
        candidate_stop_risk_cap_dollars=candidate_cap,
        aggregate_stop_risk_cap_dollars=aggregate_cap,
    )


def test_none_and_explicit_equity_base_are_exactly_equivalent():
    manager = RiskManager(_cfg())
    default = manager.permit(_signal(), _account(), _market())
    explicit = manager.permit(_signal(), _account(base=100_000.0), _market())

    assert default.model_dump() == explicit.model_dump() | {
        "sizing_detail": default.sizing_detail
    }
    assert default.units == pytest.approx(explicit.units)
    assert default.notional == pytest.approx(explicit.notional)
    assert default.risk_fraction == pytest.approx(explicit.risk_fraction)
    assert default.sizing_detail["risk_sizing_base_applied"] is False
    assert explicit.sizing_detail["risk_sizing_base_applied"] is True


def test_smaller_funded_base_scales_only_new_risk_and_vol_capital():
    manager = RiskManager(_cfg())
    full = manager.permit(_signal(), _account(), _market())
    funded = manager.permit(_signal(), _account(base=3_000.0), _market())

    assert full.permitted and funded.permitted
    assert funded.units == pytest.approx(full.units * 0.03)
    assert funded.notional == pytest.approx(full.notional * 0.03)
    assert funded.risk_fraction == pytest.approx(0.0003)
    assert funded.sizing_detail["risk_sizing_base"] == pytest.approx(3_000.0)


def test_portfolio_risk_cap_remains_a_fraction_of_actual_equity():
    cfg = _cfg(max_portfolio_risk=0.0105)
    existing = OpenPosition(
        instrument="HELD",
        direction=Direction.LONG,
        notional=10_000.0,
        risk=900.0,
    )
    position = RiskManager(cfg).permit(
        _signal(), _account(base=30_000.0, positions=[existing]), _market()
    )

    # The funded-base proposal is £300; only £150 of the £1,050 actual-equity
    # portfolio budget remains, so the final actual-equity risk is 0.15%.
    assert position.permitted
    assert "portfolio_risk_cap" in position.constraints_applied
    assert position.risk_fraction == pytest.approx(0.0015)


def test_exhausted_funded_buffer_vetoes_new_risk():
    position = RiskManager(_cfg()).permit(
        _signal(), _account(base=0.0), _market()
    )

    assert not position.permitted
    assert "risk_sizing_base_exhausted" in position.constraints_applied


def test_absolute_candidate_cash_cap_applies_after_percentage_sizing():
    position = RiskManager(_cfg()).permit(
        _signal(),
        _account(base=100_000.0, candidate_cap=350.0),
        _market(),
    )

    assert position.permitted
    assert "candidate_stop_risk_cash_cap" in position.constraints_applied
    assert position.units == pytest.approx(350.0)
    assert position.risk_fraction == pytest.approx(0.0035)
    assert position.sizing_detail[
        "candidate_stop_risk_before_cash_cap_dollars"
    ] == pytest.approx(1_000.0)


def test_absolute_aggregate_cash_cap_deducts_existing_open_risk():
    existing = OpenPosition(
        instrument="HELD",
        direction=Direction.LONG,
        notional=10_000.0,
        risk=700.0,
    )
    position = RiskManager(_cfg()).permit(
        _signal(),
        _account(
            base=100_000.0,
            positions=[existing],
            candidate_cap=350.0,
            aggregate_cap=900.0,
        ),
        _market(),
    )

    assert position.permitted
    assert "candidate_stop_risk_cash_cap" in position.constraints_applied
    assert "aggregate_stop_risk_cash_cap" in position.constraints_applied
    assert position.units == pytest.approx(200.0)
    assert position.risk_fraction == pytest.approx(0.002)
    assert position.sizing_detail[
        "remaining_stop_risk_cap_dollars"
    ] == pytest.approx(200.0)


@pytest.mark.parametrize(
    ("candidate_cap", "aggregate_cap", "reason"),
    [
        (0.0, 900.0, "candidate_stop_risk_cash_exhausted"),
        (350.0, 0.0, "aggregate_stop_risk_cash_exhausted"),
    ],
)
def test_exhausted_absolute_cash_caps_veto(candidate_cap, aggregate_cap, reason):
    position = RiskManager(_cfg()).permit(
        _signal(),
        _account(
            base=100_000.0,
            candidate_cap=candidate_cap,
            aggregate_cap=aggregate_cap,
        ),
        _market(),
    )

    assert not position.permitted
    assert reason in position.constraints_applied
