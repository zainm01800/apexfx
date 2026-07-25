"""Cornish-Fisher CVaR tail sizing (W2, 2026-07-25; prereg
engine/data_store/cf_cvar_prereg.md).

The flag defaults OFF (certified ATR/vol sizing byte-identical); when ON, units
shrink by the direction-aware tail multiplier tau >= 1 while stops, targets and
the recorded raw risk_fraction are untouched.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest import PortfolioBacktester
from apex_quant.backtest.portfolio import _cf_tau_arrays
from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.sizing import cornish_fisher_tau
from apex_quant.risk.types import AccountState, Direction, MarketState, Signal

Z = 2.326


# -- the formula ----------------------------------------------------------------
def test_gaussian_is_neutral():
    assert cornish_fisher_tau(0.0, 0.0, Z, direction_long=True) == pytest.approx(1.0)
    assert cornish_fisher_tau(0.0, 0.0, Z, direction_long=False) == pytest.approx(1.0)


def test_negative_skew_widens_the_left_tail_only():
    # S < 0: longs (left tail) pay, shorts (right tail) see a THINNER adverse tail.
    assert cornish_fisher_tau(-0.5, 0.0, Z, direction_long=True) > 1.0
    assert cornish_fisher_tau(-0.5, 0.0, Z, direction_long=False) < 1.0


def test_positive_kurtosis_widens_both_tails():
    assert cornish_fisher_tau(0.0, 3.0, Z, direction_long=True) > 1.0
    assert cornish_fisher_tau(0.0, 3.0, Z, direction_long=False) > 1.0


def test_vectorised_matches_scalar_and_nan_is_neutral():
    sw = pd.Series([np.nan, -0.5, 0.0, 0.8])
    ku = pd.Series([np.nan, 0.0, 3.0, 1.0])
    long_arr, short_arr = _cf_tau_arrays(sw, ku, Z, 1.0, 2.0)
    assert long_arr[0] == 1.0 and short_arr[0] == 1.0          # NaN -> no adjustment
    for k in range(1, 4):
        assert long_arr[k] == pytest.approx(
            float(np.clip(cornish_fisher_tau(sw[k], ku[k], Z, True), 1.0, 2.0)), rel=1e-12)
        assert short_arr[k] == pytest.approx(
            float(np.clip(cornish_fisher_tau(sw[k], ku[k], Z, False), 1.0, 2.0)), rel=1e-12)


# -- RiskManager integration ------------------------------------------------------
def _cfg(**over):
    base = dict(kelly_fraction=0.0, max_risk_per_trade=0.01, target_portfolio_vol=1e9,
                max_total_exposure=1e9, max_correlated_exposure=1e9,
                correlation_threshold=0.999999)
    base.update(over)
    return get_config().risk.model_copy(update=base)


def _permit(cfg, direction=Direction.LONG, cf_long=None, cf_short=None):
    rm = RiskManager(cfg=cfg)
    sig = Signal(instrument="AAPL", direction=direction, probability=0.60,
                 reward_risk=1.5, timeframe="1d")
    acct = AccountState(equity=100_000.0, peak_equity=100_000.0, open_positions=[])
    mkt = MarketState(instrument="AAPL", price=100.0, ann_vol=0.20, atr=1.0,
                      correlations={}, cf_tail_long=cf_long, cf_tail_short=cf_short)
    return rm.permit(sig, acct, mkt)


def test_flag_off_ignores_tail_fields_byte_identical():
    plain = _permit(_cfg())
    with_cf = _permit(_cfg(), cf_long=2.0, cf_short=2.0)
    assert plain.units == with_cf.units
    assert plain.notional == with_cf.notional
    assert not any(k.startswith("cf_cvar_tau") for k in with_cf.constraints_applied)


def test_flag_on_contracts_heavy_tailed_long():
    off = _permit(_cfg())
    on = _permit(_cfg(cf_cvar_enabled=True), cf_long=1.5, cf_short=1.0)
    assert on.permitted
    assert on.units == pytest.approx(off.units / 1.5, rel=1e-9)
    # Stops/targets untouched; recorded risk stays the raw planned loss at stop.
    assert on.stop_price == off.stop_price
    assert on.target_price == off.target_price
    assert on.stop_distance == off.stop_distance
    assert on.risk_fraction == pytest.approx(off.risk_fraction / 1.5, rel=1e-9)
    assert "cf_cvar_tau=1.50" in on.constraints_applied
    assert on.sizing_detail["cf_cvar_tau"] == pytest.approx(1.5)


def test_direction_selects_the_adverse_tail():
    long_pos = _permit(_cfg(cf_cvar_enabled=True), Direction.LONG, cf_long=1.4, cf_short=1.1)
    short_pos = _permit(_cfg(cf_cvar_enabled=True), Direction.SHORT, cf_long=1.4, cf_short=1.1)
    assert "cf_cvar_tau=1.40" in long_pos.constraints_applied
    assert "cf_cvar_tau=1.10" in short_pos.constraints_applied


def test_missing_or_thin_tail_is_noop():
    none_pos = _permit(_cfg(cf_cvar_enabled=True), cf_long=None, cf_short=None)
    thin_pos = _permit(_cfg(cf_cvar_enabled=True), cf_long=0.8, cf_short=0.8)  # tau < 1 -> no upsize
    off = _permit(_cfg())
    assert none_pos.units == off.units
    assert thin_pos.units == off.units
    assert not any(k.startswith("cf_cvar_tau=") for k in thin_pos.constraints_applied)


def test_tau_clipped_at_max():
    on = _permit(_cfg(cf_cvar_enabled=True), cf_long=99.0)
    off = _permit(_cfg())
    assert on.units == pytest.approx(off.units / 2.0, rel=1e-9)
    assert "cf_cvar_tau=2.00" in on.constraints_applied


# -- backtest integration ---------------------------------------------------------
def _ohlc(rets, base=100.0, start="2016-01-01"):
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range(start, periods=len(rets), tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


class _AlwaysLong:
    holding_horizon = 10

    def generate(self, pit, t, instrument):
        return Signal(instrument=instrument, direction=Direction.LONG,
                      probability=0.60, reward_risk=1.5)


def test_backtest_cf_labels_and_accounting():
    rng = np.random.default_rng(7)
    rets = rng.normal(0.0010, 0.006, 600)
    # Inject a cluster of violent down days -> negative rolling skew in-window.
    rets[350:360] = [-0.045, 0.010, -0.038, 0.012, -0.050, 0.008, -0.041, 0.011, -0.036, 0.009]
    pits = {"AAA": PointInTimeAccessor(_ohlc(rets))}

    def _run(cf_on):
        pbt = PortfolioBacktester(
            risk_manager=RiskManager(cfg=_cfg(cf_cvar_enabled=cf_on)),
            use_regime=False,
        )
        return pbt.run(pits, {"AAA": _AlwaysLong()}, timeframes={"AAA": "1d"},
                       warmup=300, periods_per_year=252)

    off = _run(False)
    on = _run(True)
    assert not any(k.startswith("cf_cvar_tau=") for k in off.constraint_log)
    assert any(k.startswith("cf_cvar_tau=") for k in on.constraint_log)
    total = sum(v["n_trades"] for v in on.per_instrument.values())
    assert total == len(on.trades) == on.metrics["n_trades"]
