"""Order-invariant risk allocation (W1, 2026-07-25; prereg
engine/data_store/order_invariant_prereg.md).

Certified defaults must be untouched (sequential cap, panel-order allocation);
simultaneous mode must (a) share the 6.5% portfolio-risk budget proportionally
across same-bar candidates instead of first-come-first-served, and (b) produce
results that are invariant to panel insertion order, on a scenario where the
certified sequential path demonstrably is not.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest import PortfolioBacktester
from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import (
    AccountState,
    Direction,
    MarketState,
    OpenPosition,
    Signal,
)


def _ohlc(rets, base=100.0, start="2016-01-01"):
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range(start, periods=len(rets), tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


class _AlwaysLong:
    """Deterministic stub: LONG every bar with fixed (p, b)."""

    holding_horizon = 10

    def generate(self, pit, t, instrument):
        return Signal(instrument=instrument, direction=Direction.LONG,
                      probability=0.60, reward_risk=1.5)


def _cfg(**over):
    """Risk config isolating the 6.5% portfolio-risk cap: no Kelly gate, no regime
    (use_regime=False at the backtester), and the vol-target/gross/correlation caps
    effectively disabled so the only binding book-level constraint is the one under
    test. max_risk_per_trade 4% so two raw candidates (8%) breach the 6.5% budget."""
    base = dict(
        kelly_fraction=0.0,
        max_risk_per_trade=0.04,
        max_portfolio_risk=0.065,
        target_portfolio_vol=1e9,
        max_total_exposure=1e9,
        max_correlated_exposure=1e9,
        correlation_threshold=0.999999,
    )
    base.update(over)
    return get_config().risk.model_copy(update=base)


def _run(pits, risk_cfg):
    names = list(pits.keys())
    pbt = PortfolioBacktester(
        risk_manager=RiskManager(cfg=risk_cfg),
        use_regime=False,
    )
    return pbt.run(
        pits, {nm: _AlwaysLong() for nm in names},
        timeframes={nm: "1d" for nm in names}, warmup=300, periods_per_year=252,
    )


# -- certified defaults ---------------------------------------------------------
def test_certified_defaults_preserved():
    rcfg = get_config().risk
    assert rcfg.portfolio_risk_cap_mode == "sequential"
    assert rcfg.slot_allocation == "order"
    rm = RiskManager(cfg=rcfg)
    assert rm.defer_portfolio_risk_cap is False
    assert PortfolioBacktester().slot_allocation == "order"


def test_slot_allocation_config_flows_and_arg_wins():
    rcfg = _cfg(slot_allocation="expected_value")
    pbt = PortfolioBacktester(risk_manager=RiskManager(cfg=rcfg))
    assert pbt.slot_allocation == "expected_value"          # config flows through
    pbt2 = PortfolioBacktester(risk_manager=RiskManager(cfg=rcfg),
                               slot_allocation="order")
    assert pbt2.slot_allocation == "order"                  # explicit arg wins


# -- the defer switch on RiskManager --------------------------------------------
def _permit_with_open_risk(open_risk: float, defer: bool):
    rm = RiskManager(cfg=_cfg())
    rm.defer_portfolio_risk_cap = defer
    sig = Signal(instrument="AAA", direction=Direction.LONG, probability=0.60,
                 reward_risk=1.5, timeframe="1d")
    acct = AccountState(
        equity=100_000.0, peak_equity=100_000.0,
        open_positions=[OpenPosition(instrument="ZZZ", direction=Direction.LONG,
                                     notional=50_000.0, risk=open_risk, timeframe="1d")],
    )
    mkt = MarketState(instrument="AAA", price=100.0, ann_vol=0.20, atr=1.0, correlations={})
    return rm.permit(sig, acct, mkt)


def test_sequential_cap_vetoes_when_budget_full():
    pos = _permit_with_open_risk(open_risk=6_600.0, defer=False)   # 6.6% > 6.5%
    assert not pos.permitted
    assert "max_portfolio_risk_exceeded" in pos.constraints_applied


def test_sequential_cap_clamps_to_remaining_budget():
    pos = _permit_with_open_risk(open_risk=4_000.0, defer=False)   # 2.5% left < 4%
    assert pos.permitted
    assert "portfolio_risk_cap" in pos.constraints_applied


def test_defer_skips_sequential_cap_entirely():
    pos = _permit_with_open_risk(open_risk=6_600.0, defer=True)
    assert pos.permitted
    assert "max_portfolio_risk_exceeded" not in pos.constraints_applied
    assert "portfolio_risk_cap" not in pos.constraints_applied


# -- proportional sharing vs sequential clamping (2 identical instruments) -------
def _first_trades(res):
    out = {}
    for tr in res.trades:
        out.setdefault(tr.instrument, tr)
    return out


def test_simultaneous_shares_budget_proportionally():
    df = _ohlc(np.random.default_rng(11).normal(0.0010, 0.006, 600))
    pits = {"AAA": PointInTimeAccessor(df), "BBB": PointInTimeAccessor(df)}

    seq = _run(pits, _cfg())                                    # certified path
    sim = _run(pits, _cfg(portfolio_risk_cap_mode="simultaneous",
                          slot_allocation="expected_value"))

    seq_t = _first_trades(seq)
    sim_t = _first_trades(sim)
    # Identical data -> identical stop distances, so units are proportional to the
    # allocated risk fraction (Trade.units is rounded to 2dp — tolerances allow it).
    # Sequential: first candidate gets the full 4%, the second is clamped to the
    # remaining 2.5%.
    assert seq_t["BBB"].units == pytest.approx(seq_t["AAA"].units * 0.025 / 0.04, rel=1e-4)
    # Simultaneous: gamma = 6.5/8 = 0.8125 applied to BOTH — equal units, and the
    # absolute level matches the certified-path first candidate scaled by gamma.
    assert sim_t["AAA"].units == pytest.approx(sim_t["BBB"].units, abs=0.02)
    assert sim_t["AAA"].units == pytest.approx(seq_t["AAA"].units * 0.8125, rel=1e-4)
    assert any(k.startswith("portfolio_risk_gamma=") for k in sim.constraint_log)


# -- order-invariance where the certified path is order-dependent ----------------
@pytest.fixture(scope="module")
def six_pits():
    pits = {}
    for k, nm in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]):
        rets = np.random.default_rng(100 + k).normal(0.0009, 0.006, 600)
        pits[nm] = PointInTimeAccessor(_ohlc(rets, base=100.0 + 13.0 * k))
    return pits


def test_shuffle_invariance(six_pits):
    order2 = {k: six_pits[k] for k in ["FFF", "DDD", "AAA", "EEE", "BBB", "CCC"]}
    sim_cfg = _cfg(portfolio_risk_cap_mode="simultaneous", slot_allocation="expected_value")
    r1 = _run(six_pits, sim_cfg)
    r2 = _run(order2, sim_cfg)
    assert r1.metrics["n_trades"] == r2.metrics["n_trades"]
    assert r1.metrics["sharpe"] == pytest.approx(r2.metrics["sharpe"], rel=1e-9)
    assert r1.metrics["final_equity"] == pytest.approx(r2.metrics["final_equity"], rel=1e-9)


def test_sequential_is_order_dependent_on_same_data(six_pits):
    # The control must differ across the same two orders — proving this scenario
    # actually exercises the artifact (otherwise the invariance test is vacuous).
    order2 = {k: six_pits[k] for k in ["FFF", "DDD", "AAA", "EEE", "BBB", "CCC"]}
    r1 = _run(six_pits, _cfg())
    r2 = _run(order2, _cfg())
    assert r1.metrics["final_equity"] != pytest.approx(r2.metrics["final_equity"], rel=1e-6)


def test_gamma_trims_open_positions(six_pits):
    sim_cfg = _cfg(portfolio_risk_cap_mode="simultaneous", slot_allocation="expected_value")
    res = _run(six_pits, sim_cfg)
    # Staggered exits/re-entries over 300 post-warmup bars: candidates appear while
    # positions are open, implied risk exceeds the budget, trims fire.
    assert res.constraint_log.get("portfolio_risk_gamma_trim", 0) > 0
    # Accounting integrity still holds with trims in the book.
    total_trades = sum(v["n_trades"] for v in res.per_instrument.values())
    assert total_trades == len(res.trades) == res.metrics["n_trades"]
