"""Bayesian Beta-Binomial position sizer.

Covers posterior updates, exponential decay, the three uncertainty-aware sizing
modes (mean / lcb / thompson), the drawdown circuit breaker, reproducibility,
and end-to-end integration with the supreme RiskManager.
"""

from __future__ import annotations

import numpy as np
import pytest

from apex_quant.config import get_config
from apex_quant.risk.bayesian_sizer import BayesianRiskSizer, BetaBinomialWinRate
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import AccountState, Direction, MarketState, Signal


def mk_signal(instrument="EUR/USD", b=2.0):
    return Signal(instrument=instrument, direction=Direction.LONG, probability=0.6, reward_risk=b)


def mk_account(equity=100_000.0, peak=100_000.0):
    return AccountState(equity=equity, peak_equity=peak)


# -- BetaBinomialWinRate -------------------------------------------------------
def test_prior_is_centered_half():
    t = BetaBinomialWinRate()
    assert t.posterior_mean == pytest.approx(0.5)
    assert t.n_trades == 0


def test_wins_raise_losses_lower_mean():
    t = BetaBinomialWinRate(decay=1.0)
    for _ in range(5):
        t.record_outcome(True)
    assert t.posterior_mean > 0.5
    for _ in range(20):
        t.record_outcome(False)
    assert t.posterior_mean < 0.5
    assert t.n_trades == 25


def test_decay_weights_recent_more():
    decayed = BetaBinomialWinRate(decay=0.8)
    uniform = BetaBinomialWinRate(decay=1.0)
    for w in [True] * 10 + [False] * 10:
        decayed.record_outcome(w)
        uniform.record_outcome(w)
    # After a recent loss streak the forgetting tracker has discarded more of the
    # old wins, so it sits below the uniform tracker.
    assert decayed.posterior_mean < uniform.posterior_mean


def test_lcb_is_conservative_and_clipped():
    t = BetaBinomialWinRate(decay=1.0)
    for _ in range(8):
        t.record_outcome(True)
    for _ in range(2):
        t.record_outcome(False)
    assert t.lower_confidence_bound(1.0) <= t.posterior_mean
    assert 0.0 <= t.lower_confidence_bound(100.0) <= 1.0  # cannot escape [0,1]


def test_lcb_tightens_with_evidence():
    few = BetaBinomialWinRate(decay=1.0)
    many = BetaBinomialWinRate(decay=1.0)
    for _ in range(8):
        few.record_outcome(True)
    for _ in range(2):
        few.record_outcome(False)
    for _ in range(80):
        many.record_outcome(True)
    for _ in range(20):
        many.record_outcome(False)
    # More evidence at a similar win-rate -> tighter posterior -> higher LCB, and
    # crucially the penalty gap below the mean (k*sigma) shrinks: the sizer earns
    # its way up to full size as uncertainty resolves.
    assert many.posterior_std < few.posterior_std
    assert many.lower_confidence_bound(1.0) > few.lower_confidence_bound(1.0)
    assert (many.posterior_mean - many.lower_confidence_bound(1.0)) \
        < (few.posterior_mean - few.lower_confidence_bound(1.0))


def test_sample_in_unit_interval_and_seeded():
    t = BetaBinomialWinRate()
    for _ in range(10):
        t.record_outcome(True)
    a = t.sample(np.random.default_rng(0))
    b = t.sample(np.random.default_rng(0))
    assert 0.0 <= a <= 1.0
    assert a == b  # same seed -> same draw


# -- BayesianRiskSizer ---------------------------------------------------------
def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        BayesianRiskSizer(mode="bogus")  # type: ignore[arg-type]


def test_drawdown_breaker_vetoes():
    s = BayesianRiskSizer(max_drawdown=0.15)
    acct = mk_account(equity=80_000, peak=100_000)  # 20% drawdown
    assert s.risk_fraction(mk_signal(), acct) is None


def test_min_risk_before_adaptation():
    s = BayesianRiskSizer(min_risk=0.005, min_trades_for_adaptation=20)
    for _ in range(5):
        s.record_outcome("EUR/USD", True)
    assert s.risk_fraction(mk_signal(), mk_account()) == pytest.approx(0.005)


def test_strong_edge_clamps_to_max_risk():
    s = BayesianRiskSizer(max_risk=0.02, min_trades_for_adaptation=5)
    for _ in range(30):
        s.record_outcome("EUR/USD", True)
    assert s.risk_fraction(mk_signal(b=2.0), mk_account()) == pytest.approx(0.02)


def test_no_edge_floors_to_min_risk():
    s = BayesianRiskSizer(min_risk=0.005, min_trades_for_adaptation=5)
    for _ in range(30):
        s.record_outcome("EUR/USD", False)  # all losses -> negative Kelly
    assert s.risk_fraction(mk_signal(b=2.0), mk_account()) == pytest.approx(0.005)


def test_lcb_sizes_below_mean():
    # Unclamped regime (large max, zero floor) so the estimator choice shows through.
    outcomes = [True] * 14 + [False] * 8
    mean_s = BayesianRiskSizer(mode="mean", max_risk=1.0, min_risk=0.0, min_trades_for_adaptation=5)
    lcb_s = BayesianRiskSizer(mode="lcb", uncertainty_penalty=1.0, max_risk=1.0, min_risk=0.0,
                              min_trades_for_adaptation=5)
    for w in outcomes:
        mean_s.record_outcome("EUR/USD", w)
        lcb_s.record_outcome("EUR/USD", w)
    mean_rf = mean_s.risk_fraction(mk_signal(b=2.0), mk_account())
    lcb_rf = lcb_s.risk_fraction(mk_signal(b=2.0), mk_account())
    assert 0.0 < lcb_rf < mean_rf  # the conservative estimator bets strictly less
    assert lcb_s.win_rate_estimate("EUR/USD") < mean_s.win_rate_estimate("EUR/USD")


def test_thompson_is_reproducible_and_in_range():
    a = BayesianRiskSizer(mode="thompson", seed=7, min_trades_for_adaptation=5)
    b = BayesianRiskSizer(mode="thompson", seed=7, min_trades_for_adaptation=5)
    for w in [True, False, True, True, False, True]:
        a.record_outcome("EUR/USD", w)
        b.record_outcome("EUR/USD", w)
    ea, eb = a.win_rate_estimate("EUR/USD"), b.win_rate_estimate("EUR/USD")
    assert ea == pytest.approx(eb)  # same seed -> identical posterior draw
    assert 0.0 <= ea <= 1.0


def test_instruments_are_independent():
    s = BayesianRiskSizer(min_trades_for_adaptation=5)
    for _ in range(10):
        s.record_outcome("EUR/USD", True)
    assert s._trackers["EUR/USD"].n_trades == 10
    assert s._trackers["GBP/USD"].n_trades == 0


def test_describe_includes_mode_and_estimate():
    s = BayesianRiskSizer(mode="lcb", min_trades_for_adaptation=5)
    for _ in range(6):
        s.record_outcome("EUR/USD", True)
    d = s.describe("EUR/USD")
    assert d["mode"] == "lcb"
    assert "win_rate_estimate" in d
    assert d["n_trades"] == 6
    assert s.describe("XXX/YYY")["n_trades"] == 0  # unknown instrument


# -- Integration: RiskManager contract ----------------------------------------
def test_integrates_with_risk_manager():
    sizer = BayesianRiskSizer(min_trades_for_adaptation=5, min_risk=0.005, max_risk=0.02)
    for _ in range(30):
        sizer.record_outcome("EUR/USD", True)
    # Disable the manager's own 20% breaker so the sizer's breaker is what we test.
    cfg = get_config().risk.model_copy(update={"drawdown_breaker": 0.9})
    rm = RiskManager(cfg=cfg, bayesian_sizer=sizer)
    sig = mk_signal(b=2.0)
    mkt = MarketState(instrument="EUR/USD", price=1.10, ann_vol=0.08, atr=0.01)

    pos = rm.permit(sig, mk_account(), mkt)
    assert pos.permitted
    assert "bayesian_risk_fraction" in pos.sizing_detail

    # Sizer-level drawdown breaker (17% dd: under the manager's 90%, over the sizer's 15%).
    dd_acct = mk_account(equity=83_000, peak=100_000)
    vetoed = rm.permit(sig, dd_acct, mkt)
    assert not vetoed.permitted
    assert "bayesian_drawdown_breaker" in vetoed.constraints_applied
