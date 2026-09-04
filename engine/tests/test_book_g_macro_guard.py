"""Adversarial, synthetic-only contract tests for Book G.

These tests intentionally avoid vendor data and historical outcomes.  They pin the
causal and accounting behaviour that must hold before the sealed period is opened.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest

from apex_quant.research.book_g_macro_guard import (
    DEFENSIVE_SYMBOLS,
    SECTOR_SYMBOLS,
    BacktestConfig,
    build_signal_panel,
    evaluate_final_gate,
    run_backtest,
    select_is_candidate,
)


ACCOUNT = 100_000.0
ALL_SYMBOLS = ("SPY", *SECTOR_SYMBOLS, *DEFENSIVE_SYMBOLS)


def _panel(
    n: int = 330,
    *,
    start: str = "2018-01-02",
    bear: bool = False,
) -> pd.DataFrame:
    """Return deterministic long-form adjusted OHLC with usable SMA warm-up."""

    calendar = xcals.get_calendar("XNYS")
    sessions = calendar.sessions_in_range(start, "2027-09-03")[:n]
    dates = sessions.tz_localize("UTC")
    rows: list[pd.DataFrame] = []
    for rank, symbol in enumerate(ALL_SYMBOLS):
        t = np.arange(n, dtype=float)
        if symbol == "SPY" or symbol in SECTOR_SYMBOLS:
            drift = (-0.0010 if bear else 0.00055) + rank * 0.000015
        else:
            drift = 0.00065 + rank * 0.000012
        close = (80.0 + 2.5 * rank) * np.exp(drift * t + 0.001 * np.sin(t / 11 + rank))
        open_ = np.r_[close[0] * 0.997, close[:-1] * 1.004]
        high = np.maximum(open_, close) * 1.010
        low = np.minimum(open_, close) * 0.990
        rows.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "symbol": symbol,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                }
            )
        )
    return (
        pd.concat(rows, ignore_index=True)
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


def _frame(value) -> pd.DataFrame:
    frame = value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)
    if frame.index.name and frame.index.name not in frame.columns:
        frame = frame.reset_index()
    return frame


def _events(result) -> pd.DataFrame:
    events = _frame(result.events)
    for column in ("date", "decision_date"):
        if column in events:
            events[column] = pd.to_datetime(events[column], utc=True)
    return events


def _trades(result) -> pd.DataFrame:
    trades = _frame(result.trades)
    for column in ("entry_date", "exit_date"):
        if column in trades:
            trades[column] = pd.to_datetime(trades[column], utc=True)
    return trades


def _daily(result) -> pd.DataFrame:
    daily = _frame(result.daily)
    if "date" in daily:
        daily["date"] = pd.to_datetime(daily["date"], utc=True)
    return daily


def _dates(panel: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())


def _active_range(panel: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    dates = _dates(panel)
    return dates[205], dates[-1]


def _run(panel: pd.DataFrame, **config_kwargs):
    start, end = _active_range(panel)
    return run_backtest(panel, start, end, BacktestConfig(63, **config_kwargs))


def _first_buy(result) -> pd.Series:
    events = _events(result)
    return events.loc[events["side"].str.lower().eq("buy")].iloc[0]


def _row(panel: pd.DataFrame, symbol: str, date: pd.Timestamp) -> pd.Series:
    date = pd.to_datetime(date, utc=True)
    mask = panel["symbol"].eq(symbol) & pd.to_datetime(panel["date"], utc=True).eq(
        date
    )
    assert mask.sum() == 1
    return panel.loc[mask].iloc[0]


def _canonical_events(result, *, through: pd.Timestamp | None = None) -> pd.DataFrame:
    events = _events(result).copy()
    if through is not None:
        events = events[pd.to_datetime(events["date"], utc=True) <= through]
    columns = [
        "symbol",
        "side",
        "reason",
        "date",
        "decision_date",
        "price",
        "resting_stop",
        "quantity",
        "fee",
    ]
    events = events[[column for column in columns if column in events]].copy()
    for column in ("date", "decision_date"):
        if column in events:
            events[column] = pd.to_datetime(events[column], utc=True)
    sort_columns = [
        column
        for column in ("date", "symbol", "side", "reason")
        if column in events
    ]
    return events.sort_values(sort_columns).reset_index(drop=True)


def _selected_config(selection) -> BacktestConfig:
    if isinstance(selection, BacktestConfig):
        return selection
    if isinstance(selection, dict):
        if "selected_lookback" in selection:
            return BacktestConfig(selection["selected_lookback"])
        selection = selection.get("config", selection.get("selected_config", selection))
    else:
        selection = getattr(
            selection, "config", getattr(selection, "selected_config", selection)
        )
    return selection if isinstance(selection, BacktestConfig) else BacktestConfig(**selection)


def test_frozen_universe_and_config_defaults() -> None:
    assert tuple(SECTOR_SYMBOLS) == (
        "XLK",
        "XLE",
        "XLV",
        "XLI",
        "XLF",
        "XLP",
        "XLU",
    )
    assert tuple(DEFENSIVE_SYMBOLS) == ("GLD", "TLT", "IEF", "SHY", "UUP")
    assert not set(SECTOR_SYMBOLS) & set(DEFENSIVE_SYMBOLS)
    config = BacktestConfig(63)
    assert config.fee_bps == 5.0
    assert config.stop_slippage_bps == 0.0


def test_signal_close_executes_only_at_next_open() -> None:
    panel = _panel()
    result = _run(panel)
    buy = _first_buy(result)
    fill_date = pd.Timestamp(buy["date"])
    decision_date = pd.Timestamp(buy["decision_date"])
    market = _row(panel, buy["symbol"], fill_date)
    signal_bar = _row(panel, buy["symbol"], decision_date)

    assert fill_date > decision_date
    assert buy["price"] == pytest.approx(market["open"])
    assert market["open"] != pytest.approx(signal_bar["close"])
    assert result.invariants["no_same_close_fill"] is True


def test_gap_stop_uses_worse_open_not_theoretical_stop() -> None:
    panel = _panel()
    baseline = _run(panel)
    buy = _first_buy(baseline)
    symbol = buy["symbol"]
    fill_date = pd.Timestamp(buy["date"])
    next_date = _dates(panel)[_dates(panel).get_loc(fill_date) + 1]
    stop = float(buy["resting_stop"])
    gap_open = stop * 0.90
    changed = panel.copy()
    mask = changed["symbol"].eq(symbol) & changed["date"].eq(next_date)
    changed.loc[mask, ["open", "high", "low", "close"]] = [
        gap_open,
        gap_open * 1.01,
        gap_open * 0.99,
        gap_open,
    ]

    result = _run(changed)
    exits = _events(result)
    gap = exits.loc[
        exits["symbol"].eq(symbol)
        & exits["date"].eq(next_date)
        & exits["reason"].eq("stop_gap")
    ].iloc[0]
    assert gap["price"] == pytest.approx(gap_open)
    assert gap["price"] < gap["resting_stop"]
    assert result.invariants["gap_stops_at_worse_open"] is True


def test_five_bps_each_side_and_cost_inclusive_risk_sizing() -> None:
    result = _run(_panel())
    trades = _trades(result)
    assert len(trades)
    for trade in trades.itertuples(index=False):
        assert trade.entry_fee == pytest.approx(trade.quantity * trade.entry_price * 0.0005)
        assert trade.exit_fee == pytest.approx(trade.quantity * trade.exit_price * 0.0005)
        assert trade.gross_pnl == pytest.approx(
            trade.quantity * (trade.exit_price - trade.entry_price)
        )
        assert trade.net_pnl == pytest.approx(
            trade.gross_pnl - trade.entry_fee - trade.exit_fee
        )

    first = trades.iloc[0]
    cost_inclusive_loss = first["quantity"] * (
        first["entry_price"]
        - first["initial_stop"]
        + 0.0005 * (first["entry_price"] + first["initial_stop"])
    )
    assert cost_inclusive_loss <= 350.0 + 1e-8
    assert cost_inclusive_loss == pytest.approx(350.0, rel=2e-3)
    assert result.invariants["costs_reconcile"] is True


def test_plus_one_r_ratchet_activates_next_bar_and_ambiguous_bar_is_adverse() -> None:
    panel = _panel()
    buy = _first_buy(_run(panel))
    symbol = buy["symbol"]
    entry_date = pd.Timestamp(buy["date"])
    entry = float(buy["price"])
    old_stop = float(buy["resting_stop"])
    one_r = entry - old_stop

    ambiguous = panel.copy()
    mask = ambiguous["symbol"].eq(symbol) & ambiguous["date"].eq(entry_date)
    ambiguous.loc[mask, "high"] = entry + 1.10 * one_r
    ambiguous.loc[mask, "low"] = old_stop * 0.99
    adverse = _events(_run(ambiguous))
    stopped = adverse.loc[
        adverse["symbol"].eq(symbol)
        & adverse["date"].eq(entry_date)
        & adverse["side"].str.lower().eq("sell")
    ].iloc[0]
    assert stopped["reason"] == "stop_intraday"
    assert stopped["price"] == pytest.approx(old_stop)

    ratchet = panel.copy()
    trigger = ratchet["symbol"].eq(symbol) & ratchet["date"].eq(entry_date)
    ratchet.loc[trigger, "high"] = entry + 1.10 * one_r
    ratchet.loc[trigger, "low"] = entry - 0.20 * one_r  # above old stop
    next_date = _dates(panel)[_dates(panel).get_loc(entry_date) + 1]
    worse_open = entry - 0.30 * one_r
    next_bar = ratchet["symbol"].eq(symbol) & ratchet["date"].eq(next_date)
    ratchet.loc[next_bar, ["open", "high", "low", "close"]] = [
        worse_open,
        entry * 1.001,
        worse_open * 0.999,
        worse_open,
    ]
    events = _events(_run(ratchet))
    assert not (
        events["symbol"].eq(symbol)
        & events["date"].eq(entry_date)
        & events["side"].str.lower().eq("sell")
    ).any()
    stopped = events.loc[
        events["symbol"].eq(symbol)
        & events["date"].eq(next_date)
        & events["side"].str.lower().eq("sell")
    ].iloc[0]
    assert stopped["reason"] == "stop_gap"
    assert stopped["resting_stop"] == pytest.approx(entry)
    assert stopped["price"] == pytest.approx(worse_open)


def test_open_above_plus_one_r_arms_breakeven_before_intraday_low() -> None:
    panel = _panel()
    buy = _first_buy(_run(panel))
    symbol = buy["symbol"]
    entry_date = pd.Timestamp(buy["date"])
    entry = float(buy["price"])
    old_stop = float(buy["resting_stop"])
    one_r = entry - old_stop
    next_date = _dates(panel)[_dates(panel).get_loc(entry_date) + 1]
    changed = panel.copy()
    mask = changed["symbol"].eq(symbol) & changed["date"].eq(next_date)
    opening = entry + 1.10 * one_r
    changed.loc[mask, ["open", "high", "low", "close"]] = [
        opening,
        entry + 1.20 * one_r,
        entry - 0.20 * one_r,
        entry - 0.10 * one_r,
    ]

    events = _events(_run(changed))
    ratchet = events.loc[
        events["symbol"].eq(symbol)
        & events["date"].eq(next_date)
        & events["reason"].eq("breakeven_ratchet")
    ].iloc[0]
    stopped = events.loc[
        events["symbol"].eq(symbol)
        & events["date"].eq(next_date)
        & events["reason"].eq("stop_intraday")
    ].iloc[0]
    assert ratchet["effective_next_session"] is False or not bool(
        ratchet["effective_next_session"]
    )
    assert ratchet["triggered_at_open"] is True or bool(ratchet["triggered_at_open"])
    assert stopped["resting_stop"] == pytest.approx(entry)
    assert stopped["price"] == pytest.approx(entry)


def test_future_poison_cannot_change_prior_signals_or_events() -> None:
    panel = _panel()
    dates = _dates(panel)
    cutoff = dates[245]
    poisoned = panel.copy()
    future = poisoned["date"] > cutoff
    poisoned.loc[future, ["open", "high", "low", "close"]] *= 7.0

    left_signal = _frame(build_signal_panel(panel, 63))
    right_signal = _frame(build_signal_panel(poisoned, 63))
    date_column = "date" if "date" in left_signal else "timestamp"
    left_signal = left_signal[pd.to_datetime(left_signal[date_column], utc=True) <= cutoff]
    right_signal = right_signal[pd.to_datetime(right_signal[date_column], utc=True) <= cutoff]
    sort_columns = [
        column for column in (date_column, "symbol") if column in left_signal
    ]
    pd.testing.assert_frame_equal(
        left_signal.sort_values(sort_columns).reset_index(drop=True),
        right_signal.sort_values(sort_columns).reset_index(drop=True),
        check_dtype=False,
    )

    start = dates[205]
    left = run_backtest(panel, start, dates[-1], BacktestConfig(63))
    right = run_backtest(poisoned, start, dates[-1], BacktestConfig(63))
    pd.testing.assert_frame_equal(
        _canonical_events(left, through=cutoff),
        _canonical_events(right, through=cutoff),
        check_dtype=False,
    )


def test_input_row_order_cannot_change_results_or_is_selection() -> None:
    panel = _panel()
    shuffled = panel.sample(frac=1.0, random_state=73).reset_index(drop=True)
    left = _run(panel)
    right = _run(shuffled)
    pd.testing.assert_frame_equal(
        _canonical_events(left), _canonical_events(right), check_dtype=False
    )
    for metric in (
        "cagr",
        "avg_monthly_profit",
        "sharpe",
        "max_drawdown",
        "worst_day",
        "profit_factor",
        "win_rate",
        "total_return",
        "trades",
    ):
        left_value = left.metrics[metric]
        right_value = right.metrics[metric]
        if pd.isna(left_value) and pd.isna(right_value):
            continue
        assert left_value == pytest.approx(right_value)

    is_panel = _panel(1510, start="2014-01-02")
    first = select_is_candidate(is_panel)
    second = select_is_candidate(is_panel.sample(frac=1.0, random_state=91))
    first_config = _selected_config(first)
    second_config = _selected_config(second)
    assert first_config.momentum_lookback in {63, 126, 252}
    assert first_config.momentum_lookback == second_config.momentum_lookback


def test_segment_starts_from_fresh_100k_and_finishes_flat() -> None:
    panel = _panel()
    dates = _dates(panel)
    start, end = dates[240], dates[-7]
    result = run_backtest(panel, start, end, BacktestConfig(63))
    daily = _daily(result)
    start_balance = (
        "day_start_balance" if "day_start_balance" in daily else "start_balance"
    )
    assert daily.iloc[0][start_balance] == pytest.approx(ACCOUNT)
    assert pd.to_datetime(_events(result)["date"], utc=True).min() >= start
    assert result.invariants["segment_flat_start"] is True
    assert result.invariants["segment_flat_end"] is True


def test_requested_segment_cannot_be_silently_truncated_to_panel_coverage() -> None:
    panel = _panel()
    dates = _dates(panel)

    with pytest.raises(ValueError, match="every requested XNYS session"):
        run_backtest(
            panel,
            dates[205],
            dates[-1] + pd.Timedelta(days=10),
            BacktestConfig(63),
        )


def test_bear_regime_never_buys_sectors_and_respects_twenty_percent_gross() -> None:
    result = _run(_panel(bear=True))
    buys = _events(result)
    buys = buys.loc[buys["side"].str.lower().eq("buy")]
    assert len(buys)
    assert set(buys["symbol"]).issubset(set(DEFENSIVE_SYMBOLS))
    assert not set(buys["symbol"]) & set(SECTOR_SYMBOLS)
    assert result.invariants["max_gross_exposure_fraction"] <= 0.20 + 1e-10


def test_open_gap_sizing_enforces_execution_gross_and_risk_caps() -> None:
    panel = _panel()
    baseline = _run(panel)
    buys = _events(baseline)
    buys = buys.loc[buys["side"].str.lower().eq("buy")]
    first_date = buys["date"].min()
    selected = buys.loc[buys["date"].eq(first_date), "symbol"]
    changed = panel.copy()
    mask = changed["date"].eq(first_date) & changed["symbol"].isin(selected)
    changed.loc[mask, ["open", "high", "low", "close"]] *= 10.0

    result = _run(changed)
    entries = _events(result)
    entries = entries.loc[
        entries["date"].eq(first_date) & entries["side"].str.lower().eq("buy")
    ]
    assert set(entries["symbol"]) == set(selected)
    assert (entries["quantity"] * entries["price"]).sum() <= 0.50 * ACCOUNT + 1e-7
    planned_loss = (
        entries["quantity"]
        * (
            entries["price"]
            - entries["resting_stop"]
            + 0.0005 * (entries["price"] + entries["resting_stop"])
        )
    ).sum()
    assert planned_loss <= 0.025 * ACCOUNT + 1e-7
    assert result.invariants["max_gross_exposure_fraction"] <= 0.50 + 1e-10
    assert result.invariants["max_planned_risk_fraction"] <= 0.025 + 1e-10


def test_unfilled_bull_slots_remain_cash_instead_of_being_reallocated() -> None:
    panel = _panel()
    t = np.arange(len(_dates(panel)), dtype=float)
    for symbol in SECTOR_SYMBOLS:
        mask = panel["symbol"].eq(symbol)
        if symbol == "XLK":
            close = 100.0 * np.exp(0.0002 * t + 0.00002 * np.sin(t / 7.0))
        else:
            close = 100.0 * np.exp(-0.0003 * t + 0.00002 * np.sin(t / 7.0))
        open_ = np.r_[close[0], close[:-1]]
        panel.loc[mask, ["open", "high", "low", "close"]] = np.column_stack(
            [
                open_,
                np.maximum(open_, close) * 1.00001,
                np.minimum(open_, close) * 0.99999,
                close,
            ]
        )

    result = _run(panel)
    events = _events(result)
    first_fill = events.loc[events["side"].eq("buy"), "date"].min()
    entries = events.loc[events["date"].eq(first_fill) & events["side"].eq("buy")]

    assert entries["symbol"].tolist() == ["XLK"]
    assert (entries["quantity"] * entries["price"]).sum() <= 0.125 * ACCOUNT + 1e-7


def test_prior_close_passive_overrun_blocks_all_new_risk_on_repair_open() -> None:
    panel = _panel()
    for symbol in panel["symbol"].unique():
        mask = panel["symbol"].eq(symbol)
        open_ = panel.loc[mask, "open"].to_numpy()
        close = panel.loc[mask, "close"].to_numpy()
        panel.loc[mask, "high"] = np.maximum(open_, close) * 1.0001
        panel.loc[mask, "low"] = np.minimum(open_, close) * 0.9999

    friday = pd.Timestamp("2018-11-09", tz="UTC")
    monday = pd.Timestamp("2018-11-12", tz="UTC")
    shocked = panel["date"].eq(friday) & panel["symbol"].isin(SECTOR_SYMBOLS)
    panel.loc[shocked, "close"] *= 2.0
    panel.loc[shocked, "high"] = (
        panel.loc[shocked, ["open", "close"]].max(axis=1) * 1.0001
    )
    panel.loc[shocked, "low"] = (
        panel.loc[shocked, ["open", "close"]].min(axis=1) * 0.9999
    )

    result = _run(panel)
    daily = _daily(result)
    events = _events(result)
    friday_row = daily.loc[daily["date"].eq(friday)].iloc[0]
    before_repair = events.loc[events["date"] < monday].sort_values("date")
    held: set[str] = set()
    for event in before_repair.itertuples(index=False):
        if event.side == "buy":
            held.add(event.symbol)
        elif event.side == "sell":
            held.discard(event.symbol)
    decision = next(
        item
        for item in result.decisions
        if pd.to_datetime(item["fill_date"], utc=True) == monday
    )
    selected = {row["symbol"] for row in decision["selected"]}
    repair_events = events.loc[events["date"].eq(monday)]

    assert bool(friday_row["marked_cap_overrun"])
    assert selected - held  # New candidates really were waiting to be bought.
    assert repair_events["side"].eq("sell").any()
    assert not repair_events["side"].eq("buy").any()
    assert result.invariants["passive_overruns_trimmed_next_open"] is True


def test_risk_cap_liquidation_always_closes_the_entire_leg() -> None:
    panel = _panel()
    for symbol in panel["symbol"].unique():
        mask = panel["symbol"].eq(symbol)
        open_ = panel.loc[mask, "open"].to_numpy()
        close = panel.loc[mask, "close"].to_numpy()
        panel.loc[mask, "high"] = np.maximum(open_, close) * 1.0001
        panel.loc[mask, "low"] = np.minimum(open_, close) * 0.9999

    baseline_events = _events(_run(panel))
    first_fill = baseline_events.loc[baseline_events["side"].eq("buy"), "date"].min()
    first_entries = baseline_events.loc[
        baseline_events["date"].eq(first_fill) & baseline_events["side"].eq("buy")
    ]
    dates = _dates(panel)
    overrun_date = dates[dates.get_loc(first_fill) + 1]
    repair_date = dates[dates.get_loc(first_fill) + 2]
    for date in (overrun_date, repair_date):
        shocked = panel["date"].eq(date) & panel["symbol"].isin(
            first_entries["symbol"]
        )
        panel.loc[shocked, ["open", "close"]] *= 2.0
        panel.loc[shocked, "high"] = (
            panel.loc[shocked, ["open", "close"]].max(axis=1) * 1.0001
        )
        panel.loc[shocked, "low"] = (
            panel.loc[shocked, ["open", "close"]].min(axis=1) * 0.9999
        )

    result = _run(panel)
    daily = _daily(result)
    events = _events(result)
    through_repair = events.loc[events["date"] <= repair_date]
    risk_exits = through_repair.loc[
        through_repair["reason"].eq("risk_cap_liquidation")
    ]

    assert bool(daily.loc[daily["date"].eq(overrun_date), "marked_cap_overrun"].iloc[0])
    assert len(risk_exits)
    assert set(risk_exits["date"]) == {repair_date}
    outstanding: dict[str, float] = {}
    for event in through_repair.itertuples(index=False):
        if event.side == "buy":
            outstanding[event.symbol] = outstanding.get(event.symbol, 0.0) + event.quantity
        elif event.side == "sell":
            before = outstanding[event.symbol]
            if event.reason == "risk_cap_liquidation":
                assert event.quantity == pytest.approx(before)
                assert before - event.quantity == pytest.approx(0.0, abs=1e-10)
            outstanding[event.symbol] = before - event.quantity


def test_daily_minimum_uses_simultaneous_lows_not_only_close() -> None:
    panel = _panel()
    baseline = _run(panel)
    entries = _events(baseline)
    entries = entries.loc[entries["side"].str.lower().eq("buy")]
    entry_date = entries["date"].min()
    held = entries.loc[entries["date"].eq(entry_date), ["symbol", "quantity"]]
    next_date = _dates(panel)[_dates(panel).get_loc(entry_date) + 1]
    changed = panel.copy()
    for symbol in held["symbol"]:
        mask = changed["symbol"].eq(symbol) & changed["date"].eq(next_date)
        floor = float(changed.loc[mask, ["open", "close"]].min(axis=1).iloc[0])
        changed.loc[mask, "low"] = floor * 0.97

    result = _run(changed)
    daily = _daily(result)
    day = daily.loc[pd.to_datetime(daily["date"], utc=True).eq(next_date)].iloc[0]
    expected_drop = 0.0
    for row in held.itertuples(index=False):
        market = _row(changed, row.symbol, next_date)
        expected_drop += row.quantity * (market.close - market.low)
    assert day["intraday_min_equity"] < day["equity"]
    assert day["equity"] - day["intraday_min_equity"] == pytest.approx(expected_drop)


def test_terminal_liquidation_and_trade_ledger_reconcile_without_phantom_pnl() -> None:
    panel = _panel()
    start, end = _active_range(panel)
    result = run_backtest(panel, start, end, BacktestConfig(63))
    events = _events(result)
    trades = _trades(result)
    terminal = events.loc[events["reason"].eq("terminal")]
    assert len(terminal)
    assert set(pd.to_datetime(terminal["date"], utc=True)) == {end}
    for trade in trades.itertuples(index=False):
        assert trade.gross_pnl == pytest.approx(
            trade.quantity * (trade.exit_price - trade.entry_price)
        )
        assert trade.net_pnl == pytest.approx(
            trade.gross_pnl - trade.entry_fee - trade.exit_fee
        )
    final_equity = float(_daily(result).iloc[-1]["equity"])
    assert trades["net_pnl"].sum() == pytest.approx(final_equity - ACCOUNT)
    assert result.metrics["total_return"] == pytest.approx((final_equity - ACCOUNT) / ACCOUNT)
    assert result.invariants["accounting_reconciles"] is True
    assert result.invariants["segment_flat_end"] is True


def _gate_result(
    metrics: dict,
    invariants: dict | None = None,
    config: BacktestConfig | None = None,
):
    required = {
        "no_same_close_fill": True,
        "gap_stops_at_worse_open": True,
        "costs_reconcile": True,
        "accounting_reconciles": True,
        "segment_flat_start": True,
        "segment_flat_end": True,
        "gross_cap_enforced_at_executions": True,
        "aggregate_risk_cap_enforced_at_executions": True,
        "passive_overruns_trimmed_next_open": True,
        "cash_never_negative": True,
        "max_gross_exposure_fraction": 0.50,
        "max_planned_risk_fraction": 0.014,
    }
    required.update(invariants or {})
    return SimpleNamespace(
        metrics=metrics,
        invariants=required,
        config=config or BacktestConfig(126),
    )


def _passing_gate_inputs():
    base_metrics = {
        "cagr": 0.09,
        "avg_monthly_profit": 760.0,
        "sharpe": 1.05,
        "max_drawdown": 0.055,
        "worst_day": -0.020,
        "profit_factor": 1.65,
        "win_rate": 0.48,
        "total_return": 0.60,
        "annual_returns": {2020: 0.08},
        "trades": 120,
    }
    stress_metrics = {
        **base_metrics,
        "cagr": 0.02,
        "sharpe": 0.55,
        "profit_factor": 1.12,
        "max_drawdown": 0.075,
        "worst_day": -0.030,
        "total_return": 0.10,
    }
    return (
        _gate_result({"cagr": 0.10, "sharpe": 1.20}),
        _gate_result(base_metrics),
        _gate_result(
            stress_metrics,
            config=BacktestConfig(126, fee_bps=10.0, stop_slippage_bps=25.0),
        ),
        {"daily_loss_breaches": 0, "max_loss_breaches": 0},
    )


@pytest.mark.parametrize(
    ("is_config", "base_config", "stress_config"),
    [
        (
            BacktestConfig(63),
            BacktestConfig(126),
            BacktestConfig(126, fee_bps=10.0, stop_slippage_bps=25.0),
        ),
        (
            BacktestConfig(126),
            BacktestConfig(126, fee_bps=6.0),
            BacktestConfig(126, fee_bps=10.0, stop_slippage_bps=25.0),
        ),
        (
            BacktestConfig(126),
            BacktestConfig(126),
            BacktestConfig(126, fee_bps=10.0, stop_slippage_bps=0.0),
        ),
    ],
)
def test_final_gate_rejects_mismatched_lookback_or_cost_configs(
    is_config: BacktestConfig,
    base_config: BacktestConfig,
    stress_config: BacktestConfig,
) -> None:
    is_result, base, stress, funded = _passing_gate_inputs()
    is_result.config = is_config
    base.config = base_config
    stress.config = stress_config

    verdict = evaluate_final_gate(is_result, base, stress, funded, funded)

    assert verdict["passed"] is False
    assert verdict["checks"]["frozen_configs_match"] is False


@pytest.mark.parametrize(
    "invalid_evidence",
    [None, {}, {"daily_loss_breaches": 0}, {"breach_count": "unknown"}],
)
def test_final_gate_fails_closed_on_missing_or_malformed_funded_evidence(
    invalid_evidence,
) -> None:
    is_result, base, stress, funded = _passing_gate_inputs()

    invalid_base = evaluate_final_gate(
        is_result, base, stress, invalid_evidence, funded
    )
    invalid_stress = evaluate_final_gate(
        is_result, base, stress, funded, invalid_evidence
    )

    assert invalid_base["passed"] is False
    assert invalid_base["checks"]["base_no_modeled_funded_breach"] is False
    assert invalid_stress["passed"] is False
    assert invalid_stress["checks"]["stress_no_modeled_funded_breach"] is False


def test_final_gate_is_conjunctive_and_fails_one_bad_metric_or_invariant() -> None:
    is_result = _gate_result({"cagr": 0.10, "sharpe": 1.20})
    base_metrics = {
        "cagr": 0.09,
        "avg_monthly_profit": 760.0,
        "sharpe": 1.05,
        "max_drawdown": 0.055,
        "worst_day": -0.020,
        "profit_factor": 1.65,
        "win_rate": 0.48,
        "total_return": 0.60,
        "annual_returns": {2020: 0.08},
        "trades": 120,
    }
    stress_metrics = {
        **base_metrics,
        "cagr": 0.02,
        "sharpe": 0.55,
        "profit_factor": 1.12,
        "max_drawdown": 0.075,
        "worst_day": -0.030,
        "total_return": 0.10,
    }
    funded = {"daily_loss_breaches": 0, "max_loss_breaches": 0}

    passed = evaluate_final_gate(
        is_result,
        _gate_result(base_metrics),
        _gate_result(
            stress_metrics,
            config=BacktestConfig(126, fee_bps=10.0, stop_slippage_bps=25.0),
        ),
        funded,
        funded,
    )
    assert passed["passed"] is True
    assert passed["status"] == "HISTORICAL_GATE_PASS_DATA_LIMITED"

    bad_metrics = deepcopy(base_metrics)
    bad_metrics["profit_factor"] = 1.5999
    rejected = evaluate_final_gate(
        is_result,
        _gate_result(bad_metrics),
        _gate_result(
            stress_metrics,
            config=BacktestConfig(126, fee_bps=10.0, stop_slippage_bps=25.0),
        ),
        funded,
        funded,
    )
    assert rejected["passed"] is False
    assert rejected["status"] == "NO_RESEARCH_CANDIDATE"

    rejected = evaluate_final_gate(
        is_result,
        _gate_result(base_metrics, {"no_same_close_fill": False}),
        _gate_result(
            stress_metrics,
            config=BacktestConfig(126, fee_bps=10.0, stop_slippage_bps=25.0),
        ),
        funded,
        funded,
    )
    assert rejected["passed"] is False
