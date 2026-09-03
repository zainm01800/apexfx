"""Opt-in funded-risk trace for the portfolio backtester.

The trace is deliberately diagnostic: daily OHLC can bound an adverse intraday
equity state, but cannot reconstruct the true path or an exact prop-firm breach.
These tests pin the useful safety properties without changing certified results.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import apex_quant.backtest.portfolio as portfolio_module
from apex_quant.backtest.portfolio import (
    PortfolioBacktester,
    _funded_cash_risk_limits,
)
from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import Direction, Position, Signal
from apex_quant.strategies.base import Strategy


SYMBOL = "TRACE"


class _OneShotLong(Strategy):
    """Emit one long decision; position size remains the risk layer's job."""

    holding_horizon = 100

    def __init__(
        self,
        decision: pd.Timestamp,
        stop_distance: float,
        target_distance: float = 100.0,
    ):
        self.decision = decision
        self.stop_distance = stop_distance
        self.target_distance = target_distance

    def generate(self, pit, t, instrument="") -> Signal:
        t = pd.Timestamp(t)
        if t != self.decision:
            return Signal(
                instrument=instrument, direction=Direction.FLAT,
                probability=0.5, reward_risk=1.0, timeframe="1d",
            )
        close = float(pit.latest(t)["close"])
        return Signal(
            instrument=instrument, direction=Direction.LONG,
            probability=0.9, reward_risk=5.0, timeframe="1d",
            stop_price=close - self.stop_distance,
            target_price=close + self.target_distance,
        )


class _FixedRiskManager:
    """Minimal deterministic risk boundary for trace-mechanics tests."""

    def __init__(self, cfg, units: float = 100.0):
        self.cfg = cfg
        self.units = units
        self.risk_scalar = 1.0
        self.defer_portfolio_risk_cap = False

    def permit(self, signal, account, market, regime=None, t=None) -> Position:
        stop = float(signal.stop_price)
        planned = self.units * abs(float(market.price) - stop)
        return Position(
            instrument=signal.instrument,
            direction=signal.direction,
            units=self.units,
            notional=self.units * float(market.price),
            risk_fraction=planned / float(account.equity),
            stop_price=stop,
            target_price=float(signal.target_price),
            permitted=True,
            constraints_applied=[],
        )


def _config(*, commission_per_trade: float = 0.0):
    """Small-window, zero-cost config so trace values have exact hand checks."""
    base = get_config().model_copy(deep=True)
    risk = base.risk.model_copy(update={
        "atr_window": 2,
        "drawdown_breaker": 1.0,
        "drawdown_reducing_limit": 0.99,
        "max_total_exposure": 100.0,
        "max_correlated_exposure": 100.0,
        "max_portfolio_risk": 1.0,
    })
    equity = base.asset_classes.equity.model_copy(update={
        "spread_bps": 0.0,
        "slippage_bps": 0.0,
        "commission_per_trade": commission_per_trade,
        "short_borrow_bps_annual": 0.0,
    })
    asset_classes = base.asset_classes.model_copy(update={"equity": equity})
    return base.model_copy(update={"risk": risk, "asset_classes": asset_classes})


def _frame(
    *,
    entry_low: float = 99.0,
    entry_high: float | None = None,
    next_open: float = 100.0,
    next_low: float = 90.0,
    next_high: float | None = None,
    next_close: float = 101.0,
) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-02", periods=6, tz="UTC", name="timestamp")
    open_ = np.array([98.0, 98.0, 99.0, 100.0, next_open, next_close])
    close = np.array([98.0, 99.0, 100.0, 100.0, next_close, next_close])
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    low[3] = entry_low
    low[4] = next_low
    high[4] = max(high[4], next_open, next_close)
    if entry_high is not None:
        high[3] = entry_high
    if next_high is not None:
        high[4] = next_high
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": 1.0,
    }, index=idx)


def _run(
    df: pd.DataFrame,
    *,
    stop_distance: float,
    capture: bool,
    target_distance: float = 100.0,
    enforce_entry_bar_exits: bool | None = None,
    exit_mode: str = "managed",
    funded_sizing_limits: tuple[float, float] | None = None,
    funded_cash_risk_mode: str | None = None,
    funded_cash_max_loss_mode: str = "eod_trailing",
    entry_fill: str = "open",
    commission_per_trade: float = 0.0,
):
    cfg = _config(commission_per_trade=commission_per_trade)
    pit = PointInTimeAccessor(df)
    strat = _OneShotLong(df.index[2], stop_distance, target_distance)
    risk = _FixedRiskManager(cfg.risk)
    kwargs = {}
    if enforce_entry_bar_exits is not None:
        kwargs["enforce_entry_bar_exits"] = enforce_entry_bar_exits
    return PortfolioBacktester(
        cfg, risk_manager=risk, use_regime=False, vol_window=2,
        corr_window=2, capture_funded_trace=capture, exit_mode=exit_mode,
        funded_sizing_limits=funded_sizing_limits,
        funded_cash_risk_mode=funded_cash_risk_mode,
        funded_cash_max_loss_mode=funded_cash_max_loss_mode,
        entry_fill=entry_fill,
        **kwargs,
    ).run(
        {SYMBOL: pit}, {SYMBOL: strat}, timeframes={SYMBOL: "1d"},
        warmup=2, max_hold=100,
    )


def _assert_metrics_equal(left: dict, right: dict) -> None:
    assert left.keys() == right.keys()
    for key in left:
        if isinstance(left[key], (int, float)) and isinstance(right[key], (int, float)):
            assert left[key] == pytest.approx(right[key], nan_ok=True), key
        else:
            assert left[key] == right[key], key


def _run_v2_multi(order: list[str], *, mode: str = "evaluation"):
    df = _frame(entry_low=99.0, next_low=99.0, next_close=100.0)
    cfg = _config()
    risk = cfg.risk.model_copy(update={
        "kelly_fraction": 0.0,
        "max_risk_per_trade": 0.01,
        "target_portfolio_vol": 100.0,
        "max_portfolio_risk": 1.0,
        "max_total_exposure": 100.0,
        "max_correlated_exposure": 100.0,
        "max_position_notional_pct": 0.0,
        "max_concurrent_trades": 10,
        "max_swing_slots": 10,
        "portfolio_risk_cap_mode": "simultaneous",
    })
    cfg = cfg.model_copy(update={"risk": risk})
    panel = {name: PointInTimeAccessor(df.copy()) for name in order}
    strategies = {
        name: _OneShotLong(df.index[2], stop_distance=10.0)
        for name in order
    }
    return PortfolioBacktester(
        cfg,
        risk_manager=RiskManager(cfg.risk),
        use_regime=False,
        vol_window=2,
        corr_window=2,
        slot_allocation="expected_value",
        capture_funded_trace=True,
        funded_cash_risk_mode=mode,
    ).run(
        panel,
        strategies,
        timeframes={name: "1d" for name in order},
        warmup=2,
        max_hold=100,
    )


def test_trace_is_off_by_default_and_opt_in_is_result_parity():
    df = _frame(next_open=90.0, next_low=88.0, next_close=91.0)
    plain = _run(df, stop_distance=5.0, capture=False)
    explicitly_disabled = _run(
        df, stop_distance=5.0, capture=False,
        enforce_entry_bar_exits=False,
    )
    traced = _run(df, stop_distance=5.0, capture=True)

    assert PortfolioBacktester().capture_funded_trace is False
    assert PortfolioBacktester().enforce_entry_bar_exits is False
    assert plain.funded_trace is None
    assert traced.funded_trace is not None
    pd.testing.assert_series_equal(
        plain.equity, explicitly_disabled.equity, check_exact=True,
    )
    _assert_metrics_equal(plain.metrics, explicitly_disabled.metrics)
    assert [t.__dict__ for t in plain.trades] == [
        t.__dict__ for t in explicitly_disabled.trades
    ]
    pd.testing.assert_series_equal(plain.equity, traced.equity, check_exact=True)
    _assert_metrics_equal(plain.metrics, traced.metrics)
    assert [t.__dict__ for t in plain.trades] == [t.__dict__ for t in traced.trades]
    assert plain.per_instrument == traced.per_instrument
    assert plain.constraint_log == traced.constraint_log
    assert len(traced.funded_trace) == len(traced.equity)
    assert "risk_sizing_base" in traced.funded_trace.columns
    assert "positions_opened" in traced.funded_trace.columns
    assert int(traced.funded_trace["positions_opened"].sum()) == 1
    assert "verified_flat_at_end" in traced.funded_trace.columns
    assert traced.funded_trace.iloc[0]["risk_sizing_base"] == pytest.approx(
        traced.funded_trace.iloc[0]["end_equity"]
    )
    assert "not a reconstructed intraday path" in traced.funded_trace.attrs["semantics"]
    assert traced.funded_trace.attrs["account_currency_conversion_applied"] is False
    assert traced.funded_trace.attrs["currency_basis"] == (
        "UNCONVERTED_RAW_QUOTE_CURRENCY"
    )
    assert "not account-currency-safe" in (
        traced.funded_trace.attrs["account_currency_limitation"]
    )


def test_funded_trace_exposes_daily_decision_buffer_even_without_a_candidate():
    df = _frame()
    result = _run(
        df, stop_distance=20.0, capture=True,
        funded_sizing_limits=(0.03, 0.10),
    )

    # A fresh £100k account's nearest official floor is the £97k daily floor,
    # so the registered decision sizing capital is £3k.  The value is present
    # on every union day, not only the one that emits a signal.
    assert result.funded_trace["risk_sizing_base"].iloc[0] == pytest.approx(3_000.0)
    decision_row = result.funded_trace.loc[df.index[2]]
    assert decision_row["risk_sizing_base"] == pytest.approx(3_000.0)
    assert "carried" in result.funded_trace.attrs["semantics"]


def test_v2_cash_policy_keeps_literal_buffer_path_unchanged():
    df = _frame()
    literal = _run(
        df, stop_distance=20.0, capture=True,
        funded_sizing_limits=(0.03, 0.10),
    )
    v2 = _run(
        df, stop_distance=20.0, capture=True,
        funded_cash_risk_mode="evaluation",
    )

    decision = df.index[2]
    assert literal.funded_trace.loc[decision, "risk_sizing_base"] == pytest.approx(
        3_000.0
    )
    assert v2.funded_trace.loc[decision, "risk_sizing_base"] == pytest.approx(
        100_000.0
    )
    assert PortfolioBacktester().funded_cash_risk_mode is None
    assert literal.metrics.get("funded_cash_risk_status") is None
    assert v2.metrics["funded_cash_risk_status"] == "DATA_BLOCKED"
    assert (
        "planned_loss_excludes_ordinary_entry_exit_costs"
        in v2.metrics["funded_cash_risk_blockers"]
    )
    assert (
        "aggregate_carried_stop_risk_not_continuously_rebalanced"
        in v2.metrics["funded_cash_risk_blockers"]
    )
    assert (
        "atomic_open_pending_risk_reservation_not_integrated"
        in v2.metrics["funded_cash_risk_blockers"]
    )
    assert (
        "pending_next_open_not_revalidated_against_authoritative_opening_state"
        in v2.funded_trace.attrs["funded_cash_risk_blockers"]
    )
    assert v2.funded_trace.attrs["funded_cash_risk_status"] == "DATA_BLOCKED"

    with pytest.raises(ValueError, match="cannot be combined"):
        PortfolioBacktester(
            funded_sizing_limits=(0.03, 0.10),
            funded_cash_risk_mode="evaluation",
        )


def test_v2_preregistered_cash_limit_hand_checks_and_no_compounding():
    fresh_eval = _funded_cash_risk_limits(
        mode="evaluation",
        max_loss_mode="static",
        equity=100_000.0,
        initial_balance=100_000.0,
        day_start_balance=100_000.0,
        peak_eod_balance=100_000.0,
    )
    fresh_payout = _funded_cash_risk_limits(
        mode="payout",
        max_loss_mode="static",
        equity=100_000.0,
        initial_balance=100_000.0,
        day_start_balance=100_000.0,
        peak_eod_balance=100_000.0,
    )
    profitable = _funded_cash_risk_limits(
        mode="evaluation",
        max_loss_mode="static",
        equity=110_000.0,
        initial_balance=100_000.0,
        day_start_balance=110_000.0,
        peak_eod_balance=110_000.0,
    )
    below_initial = _funded_cash_risk_limits(
        mode="evaluation",
        max_loss_mode="static",
        equity=99_000.0,
        initial_balance=100_000.0,
        day_start_balance=99_000.0,
        peak_eod_balance=100_000.0,
    )

    assert fresh_eval.candidate_stop_risk_cap_dollars == pytest.approx(350.0)
    assert fresh_eval.aggregate_stop_risk_cap_dollars == pytest.approx(900.0)
    assert fresh_payout.candidate_stop_risk_cap_dollars == pytest.approx(250.0)
    assert fresh_payout.aggregate_stop_risk_cap_dollars == pytest.approx(600.0)
    assert profitable.capital_base == pytest.approx(100_000.0)
    assert profitable.candidate_stop_risk_cap_dollars == pytest.approx(350.0)
    assert below_initial.capital_base == pytest.approx(99_000.0)
    assert below_initial.candidate_stop_risk_cap_dollars == pytest.approx(346.50)
    assert below_initial.aggregate_stop_risk_cap_dollars == pytest.approx(891.0)


def test_v2_daily_and_trailing_max_buffers_cap_then_exhaust_risk():
    daily_near = _funded_cash_risk_limits(
        mode="evaluation",
        max_loss_mode="static",
        equity=97_010.0,
        initial_balance=100_000.0,
        day_start_balance=100_000.0,
        peak_eod_balance=100_000.0,
    )
    daily_at_floor = _funded_cash_risk_limits(
        mode="evaluation",
        max_loss_mode="static",
        equity=97_000.0,
        initial_balance=100_000.0,
        day_start_balance=100_000.0,
        peak_eod_balance=100_000.0,
    )
    max_near = _funded_cash_risk_limits(
        mode="evaluation",
        max_loss_mode="eod_trailing",
        equity=95_010.0,
        initial_balance=100_000.0,
        day_start_balance=95_010.0,
        peak_eod_balance=105_000.0,
    )

    assert daily_near.day_buffer == pytest.approx(10.0)
    assert daily_near.candidate_stop_risk_cap_dollars == pytest.approx(1.50)
    assert daily_at_floor.candidate_stop_risk_cap_dollars == 0.0
    assert max_near.max_floor == pytest.approx(95_000.0)
    assert max_near.max_buffer == pytest.approx(10.0)
    assert max_near.candidate_stop_risk_cap_dollars == pytest.approx(0.60)


def test_v2_same_bar_candidates_share_cash_budget_order_invariantly():
    forward = _run_v2_multi(["CCC", "AAA", "BBB"])
    reverse = _run_v2_multi(["BBB", "CCC", "AAA"])
    decision = forward.funded_trace.index[2]

    # Each raw candidate requests the registered £350 evaluation risk. Three
    # candidates therefore share the £900 aggregate cap at £300 apiece.
    assert forward.funded_trace.loc[
        decision, "post_pending_planned_stop_risk"
    ] == pytest.approx(900.0)
    assert forward.funded_trace.loc[
        forward.funded_trace.index[3], "positions_opened"
    ] == 3
    assert sum(
        count for label, count in forward.constraint_log.items()
        if label.startswith("aggregate_stop_risk_gamma=")
    ) == 3
    pd.testing.assert_series_equal(forward.equity, reverse.equity, check_exact=True)
    pd.testing.assert_frame_equal(
        forward.funded_trace, reverse.funded_trace, check_exact=True,
    )
    assert forward.constraint_log == reverse.constraint_log


@pytest.mark.parametrize(
    ("mode", "expected_risk"),
    [("evaluation", 350.0), ("payout", 250.0)],
)
def test_v2_portfolio_passes_fresh_cash_cap_to_real_risk_manager(
    mode, expected_risk,
):
    result = _run_v2_multi(["AAA"], mode=mode)
    decision = result.funded_trace.index[2]

    assert result.funded_trace.loc[decision, "risk_sizing_base"] == pytest.approx(
        100_000.0
    )
    assert result.funded_trace.loc[
        decision, "post_pending_planned_stop_risk"
    ] == pytest.approx(expected_risk)


def test_funded_next_session_buffer_does_not_expand_after_a_realized_gain():
    df = _frame(entry_high=102.0, next_low=100.0, next_close=101.0)
    result = _run(
        df,
        stop_distance=20.0,
        target_distance=1.0,
        capture=True,
        enforce_entry_bar_exits=True,
        exit_mode="barrier",
        funded_sizing_limits=(0.03, 0.10),
    )

    entry_row = result.funded_trace.loc[df.index[3]]
    following_row = result.funded_trace.loc[df.index[4]]
    assert entry_row["end_balance"] == pytest.approx(100_100.0)
    # The next session will reset from the new £100,100 closed balance.  The
    # fixed £3,000 allowance therefore remains £3,000 rather than expanding to
    # £3,100 by retaining the prior session's £100,000 anchor.
    assert following_row["risk_sizing_base"] == pytest.approx(3_000.0)


def test_v2_next_open_uses_prospective_balance_after_a_realized_gain(monkeypatch):
    df = _frame(entry_high=102.0, next_low=100.0, next_close=101.0)
    observed_day_anchors: list[float] = []
    original = portfolio_module._funded_cash_risk_limits

    def recording_limits(**kwargs):
        observed_day_anchors.append(float(kwargs["day_start_balance"]))
        return original(**kwargs)

    monkeypatch.setattr(
        portfolio_module, "_funded_cash_risk_limits", recording_limits,
    )
    result = _run(
        df,
        stop_distance=20.0,
        target_distance=1.0,
        capture=True,
        enforce_entry_bar_exits=True,
        exit_mode="barrier",
        funded_cash_risk_mode="evaluation",
    )

    entry_row = result.funded_trace.loc[df.index[3]]
    assert entry_row["day_start_balance"] == pytest.approx(100_000.0)
    assert entry_row["end_balance"] == pytest.approx(100_100.0)
    # This bar's decision queues for the next open, whose session would begin
    # from the newly realised £100,100 balance.  The V2 cushion calculation must
    # use that stricter prospective anchor rather than today's £100,000 anchor.
    assert observed_day_anchors[3] == pytest.approx(100_100.0)


def test_funded_next_session_buffer_keeps_stricter_current_floor_after_a_loss():
    df = _frame(entry_low=79.0, next_low=80.0, next_close=80.0)
    result = _run(
        df,
        stop_distance=20.0,
        capture=True,
        enforce_entry_bar_exits=True,
        exit_mode="barrier",
        funded_sizing_limits=(0.03, 0.10),
    )

    entry_row = result.funded_trace.loc[df.index[3]]
    following_row = result.funded_trace.loc[df.index[4]]
    assert entry_row["end_balance"] == pytest.approx(98_000.0)
    # On the loss day, retain the stricter current-session floor and leave only
    # the £1,000 cushion.  Once the following session actually begins, its
    # £98,000 anchor legitimately resets the fixed daily allowance to £3,000.
    assert entry_row["risk_sizing_base"] == pytest.approx(1_000.0)
    assert following_row["risk_sizing_base"] == pytest.approx(3_000.0)


def test_close_can_be_safe_while_conservative_intraday_bound_breaches():
    df = _frame(next_low=90.0, next_close=101.0)
    result = _run(df, stop_distance=20.0, capture=True)
    row = result.funded_trace.loc[df.index[4]]

    # 100 units entered at 100.  The close is +100, but the simultaneous-low
    # diagnostic reaches -1,000 and therefore crosses a hypothetical 99,500 floor.
    assert row["end_equity"] == pytest.approx(100_100.0)
    assert row["conservative_intraday_min_equity"] == pytest.approx(99_000.0)
    assert row["end_equity"] > 99_500.0
    assert row["conservative_intraday_min_equity"] < 99_500.0
    assert row["worst_symbol_adverse_loss"] == pytest.approx(1_000.0)


def test_pending_next_open_entry_bar_is_included_in_adverse_bound():
    df = _frame(entry_low=92.0, next_low=99.0, next_close=100.0)
    result = _run(df, stop_distance=20.0, capture=True)
    decision_row = result.funded_trace.loc[df.index[2]]
    entry_row = result.funded_trace.loc[df.index[3]]

    # At the decision close, there is no live position yet.  The next-open plan
    # must nevertheless expose the queued order's notional and stop budget.
    assert decision_row["actual_open_gross_exposure"] == 0.0
    assert decision_row["actual_open_stop_risk"] == 0.0
    assert decision_row["post_pending_planned_gross_exposure"] == pytest.approx(
        10_000.0
    )
    assert decision_row["post_pending_planned_stop_risk"] == pytest.approx(2_000.0)
    assert decision_row["gross_exposure"] == 0.0
    assert decision_row["planned_stop_risk"] == pytest.approx(2_000.0)

    # There was no carried position in the opening snapshot.  The pending order
    # then filled at 100, and the entry bar's low of 92 must still contribute.
    assert entry_row["opening_equity"] == pytest.approx(100_000.0)
    assert entry_row["day_start_balance"] == pytest.approx(100_000.0)
    assert entry_row["end_balance"] == pytest.approx(100_000.0)
    assert entry_row["closed_pnl"] == pytest.approx(0.0)
    assert entry_row["conservative_intraday_min_equity"] == pytest.approx(99_200.0)
    assert entry_row["worst_symbol_adverse_loss"] == pytest.approx(800.0)
    assert entry_row["actual_open_gross_exposure"] == pytest.approx(10_000.0)
    assert entry_row["positions_opened"] == 1
    assert bool(entry_row["verified_flat_at_end"]) is False
    assert entry_row["actual_open_stop_risk"] == pytest.approx(2_000.0)
    assert entry_row["post_pending_planned_gross_exposure"] == pytest.approx(
        10_000.0
    )
    assert entry_row["post_pending_planned_stop_risk"] == pytest.approx(2_000.0)
    assert entry_row["gross_exposure"] == pytest.approx(10_000.0)
    assert entry_row["planned_stop_risk"] == pytest.approx(2_000.0)


def test_gap_through_stop_is_visible_in_opening_and_adverse_trace():
    df = _frame(next_open=90.0, next_low=88.0, next_close=91.0)
    result = _run(df, stop_distance=5.0, capture=True)
    gap_row = result.funded_trace.loc[df.index[4]]

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop"
    assert result.trades[0].exit_price == pytest.approx(90.0)
    assert gap_row["day_start_equity"] == pytest.approx(100_000.0)
    assert gap_row["day_start_balance"] == pytest.approx(100_000.0)
    assert gap_row["opening_equity"] == pytest.approx(99_000.0)
    assert gap_row["conservative_intraday_min_equity"] == pytest.approx(99_000.0)
    assert gap_row["end_equity"] == pytest.approx(99_000.0)
    assert gap_row["end_balance"] == pytest.approx(99_000.0)
    assert gap_row["closed_pnl"] == pytest.approx(-1_000.0)
    assert gap_row["worst_symbol_adverse_loss"] == pytest.approx(1_000.0)
    assert gap_row["gross_exposure"] == 0.0
    assert gap_row["planned_stop_risk"] == 0.0


def test_pre_management_adverse_snapshot_preserves_original_units_before_partial():
    df = _frame(next_low=95.0, next_high=111.0, next_close=101.0)
    result = _run(df, stop_distance=10.0, capture=True)
    row = result.funded_trace.loc[df.index[4]]

    # P1 sells 50 units at 110 before the old trace was calculated.  Marking only
    # the remaining 50 units at 95 would report 100,250.  The pre-management
    # snapshot correctly preserves all 100 original units at the adverse low.
    assert len(result.trades) == 1  # remaining units stop at breakeven next bar
    assert row["end_balance"] == pytest.approx(100_500.0)
    assert row["end_equity"] == pytest.approx(100_550.0)
    assert row["conservative_intraday_min_equity"] == pytest.approx(99_500.0)
    assert row["worst_symbol_adverse_loss"] == pytest.approx(500.0)


def test_prior_close_day_start_equity_is_included_in_daily_minimum():
    df = _frame(next_low=90.0, next_close=90.0)
    # Recover at the following open without crossing the distant stop.  Opening,
    # raw adverse, and closing equity are all 100k; only the prior close is 99k.
    df.loc[df.index[5], ["open", "high", "low", "close"]] = [
        100.0, 101.0, 100.0, 100.0,
    ]
    result = _run(df, stop_distance=20.0, capture=True)
    row = result.funded_trace.loc[df.index[5]]

    assert row["day_start_equity"] == pytest.approx(99_000.0)
    assert row["opening_equity"] == pytest.approx(100_000.0)
    assert row["end_equity"] == pytest.approx(100_000.0)
    assert row["conservative_intraday_min_equity"] == pytest.approx(99_000.0)


@pytest.mark.parametrize("exit_mode", ["managed", "barrier"])
def test_opt_in_entry_bar_exit_is_stop_first_and_default_remains_disabled(exit_mode):
    df = _frame(
        entry_low=90.0,
        entry_high=110.0,
        next_low=99.0,
        next_close=100.0,
    )
    default = _run(
        df, stop_distance=5.0, target_distance=5.0,
        capture=True, exit_mode=exit_mode,
    )
    enforced = _run(
        df, stop_distance=5.0, target_distance=5.0,
        capture=True, exit_mode=exit_mode, enforce_entry_bar_exits=True,
    )

    # Entry bar reaches both 95 stop and 105 target.  Daily OHLC has no ordering,
    # so the opt-in mechanic must take the conservative stop; the default continues
    # to leave the position open for certified-result parity.
    assert default.trades == []
    assert len(enforced.trades) == 1
    trade = enforced.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.exit_price == pytest.approx(95.0)
    assert trade.exit_time == str(df.index[3].date())
    entry_row = enforced.funded_trace.loc[df.index[3]]
    assert entry_row["end_balance"] == pytest.approx(99_500.0)
    assert entry_row["end_equity"] == pytest.approx(99_500.0)
    assert entry_row["conservative_intraday_min_equity"] == pytest.approx(99_500.0)
    assert entry_row["actual_open_gross_exposure"] == 0.0
    assert entry_row["post_pending_planned_gross_exposure"] == 0.0


def test_managed_partials_charge_one_commission_per_close_fill():
    df = _frame(next_low=95.0, next_high=116.0, next_close=101.0)
    result = _run(
        df,
        stop_distance=10.0,
        target_distance=100.0,
        capture=True,
        commission_per_trade=10.0,
    )
    row = result.funded_trace.loc[df.index[4]]

    # Entry costs 10. On this bar P1 realizes 500 and P2 realizes 375; each
    # partial is a separate close transaction and therefore costs another 10.
    assert row["day_start_balance"] == pytest.approx(99_990.0)
    assert row["closed_pnl"] == pytest.approx(855.0)
    assert row["end_balance"] == pytest.approx(100_845.0)
    assert row["end_equity"] == pytest.approx(100_870.0)


def test_entry_bar_managed_partials_also_charge_each_close_commission():
    df = _frame(
        entry_low=95.0,
        entry_high=116.0,
        next_low=99.0,
        next_close=100.0,
    )
    result = _run(
        df,
        stop_distance=10.0,
        target_distance=100.0,
        capture=True,
        enforce_entry_bar_exits=True,
        commission_per_trade=10.0,
    )
    row = result.funded_trace.loc[df.index[3]]

    assert row["closed_pnl"] == pytest.approx(845.0)
    assert row["end_balance"] == pytest.approx(100_845.0)
    assert row["end_equity"] == pytest.approx(100_845.0)


def test_barrier_stop_gap_fills_at_worse_open_not_stale_stop():
    df = _frame(next_open=90.0, next_low=88.0, next_close=91.0)
    result = _run(
        df, stop_distance=5.0, capture=True, exit_mode="barrier",
    )
    row = result.funded_trace.loc[df.index[4]]

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop"
    assert result.trades[0].exit_price == pytest.approx(90.0)
    assert row["opening_equity"] == pytest.approx(99_000.0)
    assert row["end_balance"] == pytest.approx(99_000.0)
    assert row["end_equity"] == pytest.approx(99_000.0)


def test_barrier_target_gap_is_causally_first_but_not_credited_above_target():
    df = _frame(
        next_open=110.0,
        next_low=90.0,
        next_high=111.0,
        next_close=100.0,
    )
    result = _run(
        df,
        stop_distance=5.0,
        target_distance=5.0,
        capture=True,
        exit_mode="barrier",
    )

    # The opening print reaches the resting target before the later daily low.
    # Fill at the target (105), not the favourable 110 open and not the 95 stop.
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "target"
    assert result.trades[0].exit_price == pytest.approx(105.0)
    assert result.funded_trace.loc[df.index[4], "end_balance"] == pytest.approx(
        100_500.0
    )


@pytest.mark.parametrize(
    ("open_px", "expected_reason", "expected_fill"),
    [(110.0, "stop", 110.0), (90.0, "target", 95.0)],
)
def test_short_barrier_open_gap_ordering_is_symmetric(
    open_px, expected_reason, expected_fill,
):
    backtester = PortfolioBacktester(_config(), use_regime=False)
    position = {
        "direction": Direction.SHORT,
        "units": 100.0,
        "entry_price": 100.0,
        "entry_idx": 0,
        "stop": 105.0,
        "target": 95.0,
    }

    fill, reason = backtester._check_exit(
        position,
        hi=112.0,
        lo=88.0,
        close_px=100.0,
        i=1,
        max_hold=100,
        instrument=SYMBOL,
        timeframe="1d",
        open_px=open_px,
    )

    assert reason == expected_reason
    assert fill == pytest.approx(expected_fill)


def test_close_fill_commission_is_booked_on_entry_day_equity_and_balance():
    df = _frame(next_low=99.0, next_close=100.0)
    result = _run(
        df,
        stop_distance=20.0,
        capture=True,
        entry_fill="close",
        commission_per_trade=10.0,
    )
    entry_row = result.funded_trace.loc[df.index[2]]
    following_row = result.funded_trace.loc[df.index[3]]

    assert entry_row["closed_pnl"] == pytest.approx(-10.0)
    assert entry_row["end_balance"] == pytest.approx(99_990.0)
    assert entry_row["end_equity"] == pytest.approx(99_990.0)
    assert entry_row["conservative_intraday_min_equity"] == pytest.approx(99_990.0)
    assert entry_row["actual_open_gross_exposure"] == pytest.approx(10_000.0)
    assert entry_row["positions_opened"] == 1
    assert bool(entry_row["verified_flat_at_end"]) is False
    assert following_row["positions_opened"] == 0
    assert following_row["day_start_balance"] == pytest.approx(99_990.0)
    assert following_row["day_start_equity"] == pytest.approx(99_990.0)


def test_retain_pre_start_history_warms_indicators_without_inheriting_account_state():
    cfg = _config()
    idx = pd.bdate_range("2020-01-01", periods=270, tz="UTC", name="timestamp")
    close = 100.0 + (np.arange(len(idx)) % 3) * 0.1
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 2.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1.0,
        },
        index=idx,
    )
    start = idx[260]
    pit = PointInTimeAccessor(df)

    def run(retain: bool):
        return PortfolioBacktester(
            cfg,
            risk_manager=_FixedRiskManager(cfg.risk),
            use_regime=False,
            vol_window=2,
            corr_window=2,
            capture_funded_trace=True,
            enforce_entry_bar_exits=True,
            retain_pre_start_history=retain,
        ).run(
            {SYMBOL: pit},
            {SYMBOL: _OneShotLong(start, 5.0, 1.0)},
            timeframes={SYMBOL: "1d"},
            start=start,
            warmup=250,
            max_hold=100,
        )

    legacy_slice = run(False)
    retained = run(True)

    assert legacy_slice.trades == []  # start slicing resets i below warmup
    assert len(retained.trades) == 1
    assert retained.equity.index.min() == start
    first = retained.funded_trace.iloc[0]
    assert first["day_start_balance"] == pytest.approx(100_000.0)
    assert first["opening_equity"] == pytest.approx(100_000.0)
    assert first["actual_open_gross_exposure"] == 0.0
