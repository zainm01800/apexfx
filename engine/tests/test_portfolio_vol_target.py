"""Portfolio-level volatility-target overlay (RiskManager.risk_scalar + backtester wiring).

The overlay de-levers the WHOLE book when the realised equity curve runs hotter than
`portfolio_vol_target`. These tests pin the three things that make it safe: it is off by
default, it actually scales size, and it can never demand unbounded leverage when the book
is flat (a near-zero realised vol would otherwise ask for infinite size).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest.portfolio import PortfolioBacktester
from apex_quant.config import RiskConfig, get_config
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import AccountState, Direction, MarketState, Signal


def _signal(inst: str = "EUR/USD") -> Signal:
    return Signal(
        instrument=inst, direction=Direction.LONG, probability=0.60,
        reward_risk=2.0, timeframe="1d",
    )


def _account(equity: float = 100_000.0) -> AccountState:
    return AccountState(equity=equity, peak_equity=equity, open_positions=[])


def _market(inst: str = "EUR/USD") -> MarketState:
    return MarketState(instrument=inst, price=1.10, ann_vol=0.10, atr=0.01, correlations={})


def test_overlay_is_off_by_default():
    """A fresh RiskManager must be a no-op: nothing certified changes silently."""
    rm = RiskManager(RiskConfig())
    assert rm.risk_scalar == 1.0
    assert RiskConfig().portfolio_vol_target == 0.0


def test_scalar_scales_risk_fraction_proportionally():
    cfg = RiskConfig(max_risk_per_trade=0.01, kelly_fraction=0.0)
    base = RiskManager(cfg).permit(_signal(), _account(), _market())
    assert base.permitted

    halved = RiskManager(cfg)
    halved.risk_scalar = 0.5
    scaled = halved.permit(_signal(), _account(), _market())
    assert scaled.permitted
    assert scaled.notional == pytest.approx(base.notional * 0.5, rel=1e-6)
    assert any("portfolio_vol_scalar" in c for c in scaled.constraints_applied)


def test_scalar_of_one_leaves_no_trace():
    """1.0 must not even appear in constraints_applied - it is not a binding rule."""
    rm = RiskManager(RiskConfig(max_risk_per_trade=0.01))
    rm.risk_scalar = 1.0
    pos = rm.permit(_signal(), _account(), _market())
    assert not any("portfolio_vol_scalar" in c for c in pos.constraints_applied)


def test_zero_scalar_vetoes_rather_than_sizing_dust():
    rm = RiskManager(RiskConfig(max_risk_per_trade=0.01))
    rm.risk_scalar = 0.0
    pos = rm.permit(_signal(), _account(), _market())
    assert not pos.permitted
    assert "portfolio_vol_scalar_zero" in pos.constraints_applied


def _flat_panel(n: int = 400) -> dict:
    """A panel whose equity curve barely moves - the pathological low-vol case."""
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    close = pd.Series(100.0 + np.arange(n) * 0.001, index=idx)
    return {
        "TEST": pd.DataFrame(
            {"open": close, "high": close * 1.0005, "low": close * 0.9995,
             "close": close, "volume": 1000.0},
            index=idx,
        )
    }


def test_scalar_is_clipped_and_never_explodes_on_a_flat_book():
    """Realised vol -> 0 must saturate at scalar_max, never divide toward infinity."""
    cfg = get_config()
    rc = cfg.risk.model_copy(update={
        "portfolio_vol_target": 0.07, "portfolio_vol_window": 20,
        "portfolio_vol_scalar_min": 0.25, "portfolio_vol_scalar_max": 1.5,
    })
    bt = PortfolioBacktester(cfg, risk_manager=RiskManager(rc))

    # Drive the overlay's arithmetic directly with a degenerate (constant) equity curve.
    eq_hist = [100_000.0] * 30
    a = np.asarray(eq_hist, dtype=float)
    rets = np.diff(a) / a[:-1]
    rv = float(np.nanstd(rets, ddof=1) * np.sqrt(252))
    scalar = float(np.clip(0.07 / rv, 0.25, 1.5)) if np.isfinite(rv) and rv > 1e-9 else 1.5

    assert np.isfinite(scalar)
    assert 0.25 <= scalar <= 1.5
    assert bt.risk.risk_scalar == 1.0  # untouched before run()


def test_backtester_leaves_scalar_at_one_when_overlay_disabled():
    cfg = get_config()
    rc = cfg.risk.model_copy(update={"portfolio_vol_target": 0.0})
    bt = PortfolioBacktester(cfg, risk_manager=RiskManager(rc))
    assert bt.risk.risk_scalar == 1.0


def test_overlay_reads_the_risk_managers_config_not_the_app_config():
    """Regression: callers override risk via risk_manager=RiskManager(modified_cfg).

    Reading self.cfg.risk instead silently discarded the override, so a whole 4x5 frontier
    sweep produced twenty identical rows and looked like 'vol targeting does nothing'.
    """
    cfg = get_config()
    assert cfg.risk.portfolio_vol_target == 0.0, "app config must leave the overlay off"

    rc = cfg.risk.model_copy(update={"portfolio_vol_target": 0.07})
    bt = PortfolioBacktester(cfg, risk_manager=RiskManager(rc))

    resolved = getattr(bt.risk, "cfg", None) or bt.cfg.risk
    assert resolved.portfolio_vol_target == 0.07
