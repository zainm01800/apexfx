"""Per-position notional cap (W3, 2026-07-24): RiskManager step 8.5.

Default 0.0 must leave sizing byte-identical; 0.15 caps notional at 15% of equity
and shrinks the final risk_fraction with it (never re-levered).
"""

from __future__ import annotations

import pytest

from apex_quant.config import get_config
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import AccountState, Direction, MarketState, Signal


def _permit(cap_pct: float):
    cfg = get_config().risk.model_copy(update={"max_position_notional_pct": cap_pct})
    rm = RiskManager(cfg=cfg)
    sig = Signal(instrument="AAPL", direction=Direction.LONG, probability=0.60,
                 reward_risk=1.5, timeframe="1d")
    acct = AccountState(equity=100_000.0, peak_equity=100_000.0, open_positions=[])
    # Low vol + tight ATR stop: vol-scaled risk sizing reaches for ~30% notional.
    mkt = MarketState(instrument="AAPL", price=100.0, ann_vol=0.10, atr=1.0, correlations={})
    return rm.permit(sig, acct, mkt)


def test_cap_off_by_default():
    pos = _permit(0.0)
    assert pos.permitted
    assert pos.notional > 0.15 * 100_000.0          # the uncapped sizer exceeds 15%
    assert "max_position_notional" not in pos.constraints_applied


def test_cap_binds_and_shrinks_risk():
    uncapped = _permit(0.0)
    capped = _permit(0.15)
    assert capped.permitted
    assert capped.notional == pytest.approx(0.15 * 100_000.0, rel=1e-9)
    assert "max_position_notional" in capped.constraints_applied
    # Same stop distance, smaller notional -> strictly less risk, never re-levered.
    assert capped.risk_fraction < uncapped.risk_fraction
    assert capped.risk_fraction == pytest.approx(
        uncapped.risk_fraction * capped.notional / uncapped.notional, rel=1e-6)


def test_cap_below_existing_size_is_noop():
    pos = _permit(0.95)  # generous cap above the sizer's own output
    assert pos.permitted
    assert "max_position_notional" not in pos.constraints_applied
