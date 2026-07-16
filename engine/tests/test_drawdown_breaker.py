"""Three-state drawdown breaker: ACTIVE / REDUCING / HALTED.

Regression guard for the amber-zone deadlock: REDUCING used to veto every entry
(the engine only signals instruments it is FLAT on, so the "reduces exposure"
branch was unreachable), which made reducing_limit a silent second hard halt with
no recovery path — no entries, flat book, frozen equity, drawdown never falls back.
"""

from __future__ import annotations

import pytest

from apex_quant.config import get_config
from apex_quant.risk.circuit_breaker import BreakerState, breaker_state, reducing_scale
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import AccountState, Direction, MarketState, Signal


def mk_account(equity, peak=100_000.0):
    return AccountState(equity=equity, peak_equity=peak)


def mk_signal():
    return Signal(instrument="EUR/USD", direction=Direction.LONG, probability=0.7, reward_risk=2.0)


def mk_market():
    return MarketState(instrument="EUR/USD", price=1.10, ann_vol=0.08, atr=0.01)


def _cfg(halt=0.20, reducing=0.10):
    return get_config().risk.model_copy(
        update={"drawdown_breaker": halt, "drawdown_reducing_limit": reducing}
    )


# -- state machine -------------------------------------------------------------
def test_states_by_drawdown():
    assert breaker_state(mk_account(98_000), 0.20, 0.10) == BreakerState.ACTIVE     # 2%
    assert breaker_state(mk_account(88_000), 0.20, 0.10) == BreakerState.REDUCING   # 12%
    assert breaker_state(mk_account(78_000), 0.20, 0.10) == BreakerState.HALTED     # 22%


def test_reducing_scale_ramps_linearly():
    assert reducing_scale(mk_account(98_000), 0.20, 0.10) == 1.0          # ACTIVE -> full
    assert reducing_scale(mk_account(90_000), 0.20, 0.10) == pytest.approx(1.0)   # at the edge
    assert reducing_scale(mk_account(85_000), 0.20, 0.10) == pytest.approx(0.5)   # halfway
    assert reducing_scale(mk_account(80_000), 0.20, 0.10) == 0.0          # at the halt
    assert reducing_scale(mk_account(70_000), 0.20, 0.10) == 0.0          # beyond


# -- the deadlock regression ---------------------------------------------------
def test_amber_zone_still_permits_entries_on_a_flat_book():
    """THE regression test. A flat book in the amber zone must still be able to
    trade — otherwise nothing opens, equity never moves, and the drawdown can
    never recover: a permanent freeze."""
    rm = RiskManager(cfg=_cfg())
    pos = rm.permit(mk_signal(), mk_account(88_000), mk_market())  # 12% dd, no open positions
    assert pos.permitted, "amber zone deadlocked: flat book can never re-enter"
    assert "drawdown_breaker_reducing" not in pos.constraints_applied


def test_amber_zone_scales_size_down_not_off():
    rm = RiskManager(cfg=_cfg())
    full = rm.permit(mk_signal(), mk_account(99_000), mk_market())   # 1% dd  -> ACTIVE
    amber = rm.permit(mk_signal(), mk_account(85_000), mk_market())  # 15% dd -> halfway
    assert full.permitted and amber.permitted
    assert amber.risk_fraction < full.risk_fraction
    assert amber.sizing_detail["drawdown_reducing_scale"] == pytest.approx(0.5)
    assert amber.sizing_detail["circuit_breaker_reducing_active"] is True


def test_deeper_drawdown_sizes_smaller():
    rm = RiskManager(cfg=_cfg())
    shallow = rm.permit(mk_signal(), mk_account(88_000), mk_market())  # 12%
    deep = rm.permit(mk_signal(), mk_account(82_000), mk_market())     # 18%
    assert shallow.permitted and deep.permitted
    assert deep.risk_fraction < shallow.risk_fraction


def test_halt_still_vetoes():
    rm = RiskManager(cfg=_cfg())
    pos = rm.permit(mk_signal(), mk_account(75_000), mk_market())  # 25% dd
    assert not pos.permitted
    assert "drawdown_breaker" in pos.constraints_applied


def test_hard_halt_at_ten_percent_is_expressible():
    """If you want trading to stop at 10%, set the halt to 10% — each knob does
    what its name says, rather than reducing_limit acting as a secret halt."""
    rm = RiskManager(cfg=_cfg(halt=0.10, reducing=0.05))
    pos = rm.permit(mk_signal(), mk_account(88_000), mk_market())  # 12% dd
    assert not pos.permitted
    assert "drawdown_breaker" in pos.constraints_applied
