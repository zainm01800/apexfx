"""Synthetic-only regression tests for the frozen Book U research core."""

from __future__ import annotations

from collections import OrderedDict
import json

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest

from apex_quant.research.book_u_cluster_trend import (
    CLUSTERS,
    USD_ETF_UNIVERSE,
    BookUSpec,
    build_book_u_decision,
    common_book_u_panel,
    run_book_u,
    select_book_u,
    validate_book_u_universe,
)


def _panel(n: int = 620, *, seed: int = 7401) -> dict[str, pd.DataFrame]:
    """Create causal, finite synthetic OHLC for every frozen ETF."""

    sessions = pd.DatetimeIndex(
        xcals.get_calendar("XNYS").sessions_in_range("2018-01-01", "2022-12-31")
    )[:n]
    index = sessions.tz_localize("UTC").rename("timestamp")
    # Distinct drifts make cluster winners deterministic while independent
    # oscillations keep covariance and ATR strictly positive.
    drift = {
        "SPY": 0.00045,
        "QQQ": 0.00075,
        "IWM": 0.00025,
        "XLK": 0.00055,
        "SMH": 0.00085,
        "SOXX": 0.00065,
        "GLD": 0.00030,
        "TLT": 0.00018,
        "XLE": 0.00038,
        "XBI": 0.00028,
    }
    panel: dict[str, pd.DataFrame] = {}
    for number, instrument in enumerate(USD_ETF_UNIVERSE):
        rng = np.random.default_rng(seed + number)
        noise = rng.normal(0.0, 0.004 + number * 0.00015, n)
        log_close = np.log(70.0 + 3.0 * number) + np.cumsum(drift[instrument] + noise)
        close = np.exp(log_close)
        overnight = rng.normal(0.0, 0.0012, n)
        open_ = close * np.exp(overnight)
        spread = np.abs(rng.normal(0.004, 0.001, n))
        high = np.maximum(open_, close) * (1.0 + spread)
        low = np.minimum(open_, close) * (1.0 - spread)
        panel[instrument] = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000_000.0,
            },
            index=index,
        )
    return panel


def _active_range(panel: dict[str, pd.DataFrame]) -> tuple[pd.Timestamp, pd.Timestamp]:
    index = next(iter(panel.values())).index
    return index[252], index[-1]


def _smooth_rotation_panel(n: int = 620) -> dict[str, pd.DataFrame]:
    """Up-trending panel whose positions cannot hit a stop before rotation."""

    base = _panel(n)
    for number, instrument in enumerate(USD_ETF_UNIVERSE):
        phase = number * 0.37
        time = np.arange(n, dtype=float)
        drift = 0.00045 + number * 0.000025
        wave = 0.00035 * np.sin(time / (6.0 + number * 0.3) + phase)
        close = (80.0 + number * 2.0) * np.exp(drift * time + wave)
        open_ = np.r_[close[0] * 0.9998, close[:-1]]
        base[instrument].loc[:, "open"] = open_
        base[instrument].loc[:, "close"] = close
        base[instrument].loc[:, "high"] = np.maximum(open_, close) * 1.0007
        base[instrument].loc[:, "low"] = np.minimum(open_, close) * 0.9993
    return base


def test_spec_locks_architecture_but_allows_declared_risk_and_cost_stress() -> None:
    stressed = BookUSpec(
        name="U100_STRESS",
        risk_per_leg=0.01,
        aggregate_risk=0.03,
        cost_bps_per_side=10.0,
        stop_slippage_bps=25.0,
    )
    assert stressed.risk_per_leg == 0.01
    assert BookUSpec(name="U085", risk_per_leg=0.0085, aggregate_risk=0.0255)
    with pytest.raises(ValueError, match="frozen U075/U085/U100"):
        BookUSpec(risk_per_leg=0.008, aggregate_risk=0.024)
    with pytest.raises(ValueError, match="base or binding-stress"):
        BookUSpec(cost_bps_per_side=5.0, stop_slippage_bps=25.0)
    with pytest.raises(ValueError, match="architecture is frozen"):
        BookUSpec(momentum_lookback=126)
    with pytest.raises(ValueError, match="fixed ten-ETF"):
        validate_book_u_universe([symbol for symbol in USD_ETF_UNIVERSE if symbol != "TLT"])


def test_common_panel_rejects_missing_expected_session_instead_of_forward_fill() -> None:
    raw = _panel(300)
    missing_date = raw["GLD"].index[100]
    raw["GLD"] = raw["GLD"].drop(index=missing_date)
    with pytest.raises(ValueError, match="missing expected XNYS sessions"):
        common_book_u_panel(raw)


def test_non_midnight_utc_vendor_labels_normalize_to_session_dates() -> None:
    raw = _panel(300)
    for frame in raw.values():
        frame.index = frame.index + pd.Timedelta(hours=21)
    panel = common_book_u_panel(raw)
    assert all(date.hour == 0 for date in panel["SPY"].index)
    duplicated = _panel(300)
    extra = duplicated["GLD"].iloc[[10]].copy()
    extra.index = extra.index + pd.Timedelta(hours=21)
    duplicated["GLD"] = pd.concat([duplicated["GLD"], extra])
    with pytest.raises(ValueError, match="duplicate session dates after UTC normalization"):
        common_book_u_panel(duplicated)


def test_missing_official_month_tail_cannot_create_false_month_end() -> None:
    raw = _panel(320)
    index = raw["SPY"].index
    month_tail = next(
        index[i] for i in range(260, len(index) - 1) if index[i].month != index[i + 1].month
    )
    for frame in raw.values():
        frame.drop(index=month_tail, inplace=True)
    with pytest.raises(ValueError, match="missing expected XNYS sessions"):
        common_book_u_panel(raw)


def test_selection_is_one_positive_winner_per_fixed_cluster() -> None:
    panel = common_book_u_panel(_panel())
    spec = BookUSpec()
    picked = select_book_u(panel, spec, len(next(iter(panel.values()))) - 1)
    clusters = [row["cluster"] for row in picked]
    symbols = {row["instrument"] for row in picked}
    assert len(clusters) == len(set(clusters))
    assert len({"SPY", "QQQ", "IWM"} & symbols) <= 1
    assert len({"XLK", "SMH", "SOXX"} & symbols) <= 1
    assert all(row["momentum_252"] > 0.0 for row in picked)
    assert all(row["score"] == pytest.approx(row["momentum_252"] / row["daily_volatility_63"]) for row in picked)


def test_negative_cluster_is_cash_and_symbol_tie_break_is_lexical() -> None:
    raw = _panel()
    # Exact equal OHLC makes broad-equity scores tie; lexical IWM must win.
    raw["QQQ"] = raw["IWM"].copy()
    raw["SPY"] = raw["IWM"].copy()
    # Give TLT a clean negative drift with non-zero oscillation/ATR.
    n = len(raw["TLT"])
    phase = np.sin(np.arange(n) / 7.0) * 0.002
    close = 100.0 * np.exp(-0.001 * np.arange(n) + phase)
    raw["TLT"].loc[:, "close"] = close
    raw["TLT"].loc[:, "open"] = close * 1.0005
    raw["TLT"].loc[:, "high"] = np.maximum(close, close * 1.0005) * 1.003
    raw["TLT"].loc[:, "low"] = np.minimum(close, close * 1.0005) * 0.997
    panel = common_book_u_panel(raw)
    picked = select_book_u(panel, BookUSpec(), len(next(iter(panel.values()))) - 1)
    by_cluster = {row["cluster"]: row["instrument"] for row in picked}
    assert by_cluster["broad_equity"] == "IWM"
    assert "rates" not in by_cluster


def test_weights_use_inverse_vol_covariance_target_and_hard_caps() -> None:
    panel = common_book_u_panel(_panel())
    i = len(next(iter(panel.values()))) - 1
    decision = build_book_u_decision(panel, BookUSpec(), i)
    selected = decision["selected"]
    inverse = np.asarray([1.0 / row["annualized_volatility_63"] for row in selected])
    inverse /= inverse.sum()
    assert [row["inverse_vol_weight"] for row in selected] == pytest.approx(inverse)
    assert sum(row["target_weight"] for row in selected) <= 0.95 + 1e-12
    assert max(row["target_weight"] for row in selected) <= 0.25 + 1e-12
    covariance = np.asarray(
        [
            [decision["covariance_annualized"][left][right] for right in [x["instrument"] for x in selected]]
            for left in [x["instrument"] for x in selected]
        ]
    )
    expected_projected = float(np.sqrt(inverse @ covariance @ inverse))
    assert decision["projected_volatility"] == pytest.approx(expected_projected)


def test_decision_close_fills_only_at_next_session_open() -> None:
    panel = common_book_u_panel(_panel())
    start, end = _active_range(panel)
    result = run_book_u(panel, BookUSpec(), start=start, end=end)
    buys = [event for event in result.events if event["side"] == "buy"]
    assert buys
    first = buys[0]
    assert first["date"] > first["decision_date"]
    date = pd.Timestamp(first["date"], tz="UTC")
    assert first["price_usd"] == pytest.approx(panel[first["instrument"]].loc[date, "open"])


def test_cost_inclusive_leg_and_aggregate_risk_caps_bind() -> None:
    panel = common_book_u_panel(_panel())
    start, end = _active_range(panel)
    spec = BookUSpec(cost_bps_per_side=10.0, stop_slippage_bps=25.0)
    result = run_book_u(panel, spec, start=start, end=end)
    executions = [row["execution"] for row in result.decisions if "execution" in row]
    assert executions
    assert max(row["max_leg_planned_loss_fraction_capital"] for row in executions) <= spec.risk_per_leg + 1e-10
    assert max(row["aggregate_planned_loss_fraction_capital"] for row in executions) <= spec.aggregate_risk + 1e-10
    assert max(row["gross_exposure_fraction_equity"] for row in executions) <= spec.gross_cap + 1e-10
    assert max(row["max_position_fraction_equity"] for row in executions) <= spec.position_cap + 1e-10
    assert all(leg["planned_loss_per_unit_usd"] > 0.0 for row in executions for leg in row["legs"])


def test_entry_day_stop_and_adverse_stop_slippage_are_charged() -> None:
    raw = _panel()
    panel = common_book_u_panel(raw)
    start, end = _active_range(panel)
    baseline = run_book_u(panel, BookUSpec(), start=start, end=end)
    first_buy = next(event for event in baseline.events if event["side"] == "buy")
    fill_date = pd.Timestamp(first_buy["date"], tz="UTC")
    instrument = first_buy["instrument"]
    stopped = {symbol: frame.copy() for symbol, frame in panel.items()}
    stopped[instrument].loc[fill_date, "low"] = first_buy["stop_price_usd"] * 0.95
    base = run_book_u(stopped, BookUSpec(), start=start, end=end)
    stress = run_book_u(
        stopped,
        BookUSpec(name="stress", cost_bps_per_side=10.0, stop_slippage_bps=25.0),
        start=start,
        end=end,
    )
    base_stop = next(
        event for event in base.events if event["instrument"] == instrument and event["reason"] == "stop_intraday"
    )
    stress_stop = next(
        event for event in stress.events if event["instrument"] == instrument and event["reason"] == "stop_intraday"
    )
    assert base_stop["date"] == first_buy["date"]
    assert stress_stop["price_usd"] < base_stop["price_usd"]
    assert stress_stop["cost_usd"] > base_stop["cost_usd"]


def test_gap_through_stop_fills_at_worse_open_before_slippage() -> None:
    panel = common_book_u_panel(_panel())
    start, end = _active_range(panel)
    first_run = run_book_u(panel, BookUSpec(), start=start, end=end)
    buy = next(event for event in first_run.events if event["side"] == "buy")
    buy_date = pd.Timestamp(buy["date"], tz="UTC")
    index = panel[buy["instrument"]].index
    next_date = index[index.get_loc(buy_date) + 1]
    altered = {symbol: frame.copy() for symbol, frame in panel.items()}
    gap_open = buy["stop_price_usd"] * 0.90
    altered[buy["instrument"]].loc[next_date, ["open", "low", "close"]] = [gap_open, gap_open * 0.99, gap_open * 1.01]
    altered[buy["instrument"]].loc[next_date, "high"] = gap_open * 1.02
    spec = BookUSpec(name="gap", cost_bps_per_side=10.0, stop_slippage_bps=25.0)
    result = run_book_u(altered, spec, start=start, end=end)
    gap = next(event for event in result.events if event["reason"] == "stop_gap")
    assert gap["gap_open_price_usd"] == pytest.approx(gap_open)
    assert gap["price_usd"] == pytest.approx(gap_open * (1.0 - 0.0025))
    assert gap["price_usd"] < gap["resting_stop_price_usd"]


def test_monthly_stop_never_loosens_for_retained_position() -> None:
    panel = common_book_u_panel(_panel())
    start, end = _active_range(panel)
    result = run_book_u(panel, BookUSpec(), start=start, end=end)
    by_episode: dict[int, list[float]] = {}
    for row in result.trace:
        for position in row["positions"]:
            by_episode.setdefault(position["episode_id"], []).append(position["stop_price_usd"])
    assert by_episode
    for stops in by_episode.values():
        assert all(later + 1e-12 >= earlier for earlier, later in zip(stops, stops[1:]))


def test_input_order_and_future_values_cannot_change_finished_result() -> None:
    raw = _panel()
    panel = common_book_u_panel(raw)
    index = next(iter(panel.values())).index
    start, end = index[252], index[510]
    clean = run_book_u(panel, BookUSpec(), start=start, end=end)

    reversed_panel = OrderedDict((symbol, raw[symbol]) for symbol in reversed(USD_ETF_UNIVERSE))
    reordered = run_book_u(reversed_panel, BookUSpec(), start=start, end=end)
    assert clean.metrics["input_panel_sha256"] == reordered.metrics["input_panel_sha256"]
    assert clean.metrics["order_invariant_result_sha256"] == reordered.metrics["order_invariant_result_sha256"]

    poisoned = {symbol: frame.copy() for symbol, frame in raw.items()}
    for frame in poisoned.values():
        frame.loc[frame.index > end, ["open", "high", "low", "close"]] *= 1000.0
    rerun = run_book_u(poisoned, BookUSpec(), start=start, end=end)
    assert clean.equity.equals(rerun.equity)
    assert clean.events == rerun.events
    assert clean.decisions == rerun.decisions
    assert clean.metrics["full_source_panel_sha256"] != rerun.metrics["full_source_panel_sha256"]
    assert clean.metrics["consumed_panel_sha256"] == rerun.metrics["consumed_panel_sha256"]
    assert clean.metrics["outcome_sha256"] == rerun.metrics["outcome_sha256"]
    assert clean.metrics["run_fingerprint_sha256"] == rerun.metrics["run_fingerprint_sha256"]


def test_direct_decision_is_invariant_to_all_suffix_values() -> None:
    panel = common_book_u_panel(_panel())
    i = 420
    clean = build_book_u_decision(panel, BookUSpec(), i)
    poisoned = {instrument: frame.copy() for instrument, frame in panel.items()}
    for frame in poisoned.values():
        frame.iloc[i + 1 :, frame.columns.get_indexer(["open", "high", "low", "close"])] *= 777.0
    assert build_book_u_decision(poisoned, BookUSpec(), i) == clean


def test_cash_never_borrows_terminal_liquidation_and_attribution_reconcile() -> None:
    panel = common_book_u_panel(_panel())
    start, end = _active_range(panel)
    result = run_book_u(panel, BookUSpec(), start=start, end=end)
    assert result.metrics["minimum_cash_usd"] >= -1e-6
    assert result.metrics["borrow_breach_count"] == 0
    assert result.metrics["verified_flat_at_end"] is True
    assert result.trace[-1]["positions"] == []
    assert result.trace[-1]["verified_flat_at_end"] is True
    assert any(event["reason"] == "final_liquidation" for event in result.events)
    assert result.metrics["cluster_attribution_reconciles"] is True
    assert sum(row["net_pnl_usd"] for row in result.cluster_attribution.values()) == pytest.approx(
        result.metrics["net_pnl_usd"], abs=1e-6
    )
    for row in result.trace:
        assert row["conservative_intraday_min_equity_usd"] <= row["day_start_equity_usd"] + 1e-8
        assert "aggregate_planned_loss_usd" in row
        assert "gross_exposure_fraction_equity" in row
        cash_holdings = row["day_end_cash_usd"] + sum(
            position["units"] * position["mark_price_usd"] for position in row["positions"]
        )
        balance_unrealized = row["day_end_balance_usd"] + sum(
            position["units"]
            * (position["mark_price_usd"] - position["average_entry_price_usd"])
            for position in row["positions"]
        )
        assert cash_holdings == pytest.approx(row["day_end_equity_usd"], abs=1e-6)
        assert balance_unrealized == pytest.approx(row["day_end_equity_usd"], abs=1e-6)
        assert row["daily_accounting_reconciles"] is True
    assert result.metrics["daily_accounting_reconciliation_failures"] == 0


def test_each_segment_starts_fresh_and_charges_terminal_exit_cost() -> None:
    panel = common_book_u_panel(_panel())
    index = next(iter(panel.values())).index
    start, end = index[300], index[-1]
    paid = run_book_u(panel, BookUSpec(), start=start, end=end)
    assert paid.trace[0]["day_start_equity_usd"] == 100_000.0
    assert paid.trace[0]["positions"] == []
    liquidation_cost = sum(
        event["cost_usd"] for event in paid.events if event["reason"] == "final_liquidation"
    )
    assert liquidation_cost > 0.0
    assert all(CLUSTERS[event["instrument"]] == event["cluster"] for event in paid.events)


def test_result_is_strict_json_serializable_even_without_losing_episode() -> None:
    panel = common_book_u_panel(_panel())
    start, end = _active_range(panel)
    result = run_book_u(panel, BookUSpec(), start=start, end=end)
    encoded = json.dumps(result.to_dict(), sort_keys=True, allow_nan=False)
    assert '"verified_flat_at_end": true' in encoded


def test_flat_start_uses_requested_first_index_and_terminal_discards_pending_order() -> None:
    panel = common_book_u_panel(_panel())
    index = next(iter(panel.values())).index
    # End on a common-session month end which has a later bar in the source
    # panel.  No decision at that terminal close may leak into a post-segment
    # fill, and earlier source history may warm indicators but not positions.
    month_ends = [index[i] for i in range(300, len(index) - 1) if index[i].month != index[i + 1].month]
    end = month_ends[-2]
    start = index[300]
    result = run_book_u(panel, BookUSpec(), start=start, end=end)
    assert result.equity.index[0] == start
    assert result.trace[0]["day_start_cash_usd"] == 100_000.0
    assert result.trace[0]["day_start_balance_usd"] == 100_000.0
    assert result.trace[0]["day_start_equity_usd"] == 100_000.0
    assert result.trace[0]["positions"] == []
    assert all(pd.Timestamp(event["date"], tz="UTC") <= end for event in result.events)
    assert all(decision["decision_date"] != end.strftime("%Y-%m-%d") for decision in result.decisions)
    assert result.metrics["verified_flat_at_end"] is True


def test_prior_month_end_decision_seeds_first_segment_open_from_flat_cash() -> None:
    panel = common_book_u_panel(_smooth_rotation_panel())
    index = next(iter(panel.values())).index
    prior_i = next(
        i for i in range(300, len(index) - 80) if index[i].month != index[i + 1].month
    )
    start, end = index[prior_i + 1], index[prior_i + 65]
    result = run_book_u(panel, BookUSpec(), start=start, end=end)
    seeded = result.decisions[0]
    assert seeded["segment_boundary_seed"] is True
    assert seeded["decision_date"] == index[prior_i].strftime("%Y-%m-%d")
    assert seeded["fill_date"] == start.strftime("%Y-%m-%d")
    assert result.trace[0]["day_start_cash_usd"] == 100_000.0
    assert result.trace[0]["day_start_balance_usd"] == 100_000.0
    assert result.trace[0]["day_start_equity_usd"] == 100_000.0
    assert result.trace[0]["positions_opened"]
    assert any(
        event["side"] == "buy" and event["date"] == start.strftime("%Y-%m-%d")
        for event in result.events
    )


def test_full_rotation_aggregate_loss_reconstructs_costs_and_all_stressed_stops() -> None:
    raw = _smooth_rotation_panel()
    panel = common_book_u_panel(raw)
    index = next(iter(panel.values())).index
    first_i = next(
        i for i in range(300, len(index) - 80) if index[i].month != index[i + 1].month
    )
    second_i = next(
        i
        for i in range(first_i + 1, len(index) - 20)
        if index[i].month != index[i + 1].month
    )
    initial_decision = build_book_u_decision(panel, BookUSpec(), first_i)
    carried = {row["instrument"] for row in initial_decision["selected"]}
    assert len(carried) >= 4

    # Poison only the old winners' 252-session denominator at the next
    # decision.  It predates the active segment and the 63/20-session windows,
    # so it forces every carried name out without causing an in-segment crash
    # or stop.  Multi-member clusters can replace them with untouched ETFs.
    denominator_i = second_i - 252
    altered = {instrument: frame.copy() for instrument, frame in panel.items()}
    for instrument in carried:
        current = float(altered[instrument]["close"].iloc[second_i])
        forced = current * 2.0
        altered[instrument].iloc[
            denominator_i,
            altered[instrument].columns.get_indexer(["open", "high", "low", "close"]),
        ] = [forced, forced * 1.001, forced * 0.999, forced]
    altered = common_book_u_panel(altered)

    start = index[first_i + 1]
    fill_date = index[second_i + 1]
    result = run_book_u(altered, BookUSpec(), start=start, end=index[second_i + 5])
    rotation_decision = next(
        row for row in result.decisions if row["decision_date"] == index[second_i].strftime("%Y-%m-%d")
    )
    execution = rotation_decision["execution"]
    targets = execution["targets"]
    assert carried.isdisjoint(targets)
    fill_events = [
        event
        for event in result.events
        if event["date"] == fill_date.strftime("%Y-%m-%d")
        and event["reason"] == "monthly_rebalance"
    ]
    assert carried <= {event["instrument"] for event in fill_events if event["side"] == "sell"}
    assert set(targets) <= {event["instrument"] for event in fill_events if event["side"] == "buy"}

    cost_rate = result.spec.cost_bps_per_side / 10_000.0
    independent_rotation_cost = sum(event["notional_usd"] * cost_rate for event in fill_events)
    independent_target_stop_loss = 0.0
    for instrument, target in targets.items():
        entry = float(altered[instrument].loc[fill_date, "open"])
        stressed_stop = target["stop_price_usd"] * (
            1.0 - result.spec.stop_slippage_bps / 10_000.0
        )
        independent_target_stop_loss += target["units"] * (entry - stressed_stop)
        independent_target_stop_loss += target["units"] * stressed_stop * cost_rate
    independent_total = independent_rotation_cost + independent_target_stop_loss
    recorded = execution["rotation_inclusive_planned_loss"]
    assert recorded["immediate_rotation_cost_usd"] == pytest.approx(independent_rotation_cost)
    assert recorded["target_mark_to_stressed_stop_loss_usd"] == pytest.approx(
        independent_target_stop_loss
    )
    assert recorded["total_pretrade_to_stressed_stops_usd"] == pytest.approx(independent_total)
    assert independent_total <= result.spec.aggregate_risk * execution["pre_trade_capital_usd"] + 1e-6
    assert recorded["within_cap"] is True


def test_first_segment_day_loss_is_in_all_return_drawdown_and_year_metrics() -> None:
    panel = common_book_u_panel(_smooth_rotation_panel())
    index = next(iter(panel.values())).index
    prior_i = next(
        i for i in range(300, len(index) - 30) if index[i].month != index[i + 1].month
    )
    start, end = index[prior_i + 1], index[prior_i + 10]
    decision = build_book_u_decision(panel, BookUSpec(), prior_i)
    assert decision["selected"]
    stopped = {instrument: frame.copy() for instrument, frame in panel.items()}
    for selected in decision["selected"]:
        instrument = selected["instrument"]
        stopped[instrument].loc[start, "low"] = (
            float(stopped[instrument].loc[start, "open"]) * 0.50
        )

    result = run_book_u(stopped, BookUSpec(), start=start, end=end)
    daily = np.asarray([row["daily_return"] for row in result.trace], dtype=float)
    assert len(daily) == result.metrics["sessions"]
    assert daily[0] < 0.0
    assert daily[0] == pytest.approx(daily.min())
    assert all(value == pytest.approx(0.0) for value in daily[1:])

    expected_std = float(pd.Series(daily).std(ddof=1))
    expected_sharpe = float(daily.mean() / expected_std * np.sqrt(252.0))
    expected_total = float(np.prod(1.0 + daily) - 1.0)
    expected_drawdown = float(-np.min(
        np.asarray([row["day_end_equity_usd"] for row in result.trace]) / 100_000.0 - 1.0
    ))
    assert result.metrics["worst_close_day"] == pytest.approx(daily[0])
    assert result.metrics["sharpe"] == pytest.approx(expected_sharpe)
    assert result.metrics["sharpe"] < 0.0
    assert result.metrics["max_drawdown"] == pytest.approx(expected_drawdown)
    assert result.metrics["max_drawdown"] == pytest.approx(-daily[0])
    assert result.metrics["total_return"] == pytest.approx(expected_total)
    assert result.metrics["annual_returns"][str(start.year)] == pytest.approx(expected_total)
    assert result.metrics["final_equity_usd"] / 100_000.0 - 1.0 == pytest.approx(expected_total)


def test_close_risk_overrun_is_reduced_within_every_cap_at_next_open() -> None:
    panel = common_book_u_panel(_panel())
    start, full_end = _active_range(panel)
    baseline = run_book_u(panel, BookUSpec(), start=start, end=full_end)
    index = next(iter(panel.values())).index
    candidate = next(
        row
        for row in baseline.trace
        if len(row["positions"]) >= 2
        and row["decision_formed"] is None
        and not row["positions_opened"]
        and pd.Timestamp(row["date"], tz="UTC") < full_end
    )
    spike_date = pd.Timestamp(candidate["date"], tz="UTC")
    next_date = index[index.get_loc(spike_date) + 1]
    altered = {symbol: frame.copy() for symbol, frame in panel.items()}
    held = [position["instrument"] for position in candidate["positions"]]
    for instrument in held:
        first_close = float(altered[instrument].loc[spike_date, "close"]) * 5.0
        altered[instrument].loc[spike_date, "close"] = first_close
        altered[instrument].loc[spike_date, "high"] = first_close * 1.001
        next_open = first_close
        altered[instrument].loc[next_date, ["open", "close"]] = [next_open, next_open]
        altered[instrument].loc[next_date, "high"] = next_open * 1.001
        altered[instrument].loc[next_date, "low"] = next_open * 0.999

    result = run_book_u(altered, BookUSpec(), start=start, end=next_date)
    spike_trace = next(row for row in result.trace if row["date"] == spike_date.strftime("%Y-%m-%d"))
    trim_trace = next(row for row in result.trace if row["date"] == next_date.strftime("%Y-%m-%d"))
    assert spike_trace["risk_trim_required_next_open"] is True
    trim_events = [
        event
        for event in result.events
        if event["date"] == next_date.strftime("%Y-%m-%d")
        and event["reason"] == "risk_budget_trim"
    ]
    assert trim_events
    opened = trim_trace["open_execution_risk"]
    spec = result.spec
    assert trim_trace["open_execution_caps_satisfied"] is True
    assert opened["max_leg_planned_loss_fraction_capital"] <= spec.risk_per_leg + 1e-12
    assert opened["aggregate_planned_loss_fraction_capital"] <= spec.aggregate_risk + 1e-12
    assert opened["gross_exposure_fraction_equity"] <= spec.gross_cap + 1e-12
    assert opened["max_position_fraction_equity"] <= spec.position_cap + 1e-12


def test_conservative_intraday_minimum_marks_two_positions_at_simultaneous_lows() -> None:
    panel = common_book_u_panel(_panel())
    start, full_end = _active_range(panel)
    baseline = run_book_u(panel, BookUSpec(), start=start, end=full_end)
    candidate = next(
        row
        for row in baseline.trace
        if len(row["positions"]) >= 2
        and row["decision_formed"] is None
        and not row["positions_opened"]
        and not row["positions_closed"]
        and not any(
            event["date"] == row["date"] for event in baseline.events
        )
    )
    date = pd.Timestamp(candidate["date"], tz="UTC")
    altered = {symbol: frame.copy() for symbol, frame in panel.items()}
    for position in candidate["positions"]:
        instrument = position["instrument"]
        previous_date = altered[instrument].index[altered[instrument].index.get_loc(date) - 1]
        flat = float(altered[instrument].loc[previous_date, "close"])
        stop = float(position["stop_price_usd"])
        low = max(stop * 1.01, flat * 0.985)
        assert low < flat
        altered[instrument].loc[date, ["open", "close"]] = [flat, flat]
        altered[instrument].loc[date, "high"] = flat * 1.002
        altered[instrument].loc[date, "low"] = low

    result = run_book_u(altered, BookUSpec(), start=start, end=full_end)
    row = next(item for item in result.trace if item["date"] == date.strftime("%Y-%m-%d"))
    assert not row["positions_opened"]
    assert not row["positions_closed"]
    assert not any(
        event["date"] == row["date"] and str(event["reason"]).startswith("stop")
        for event in result.events
    )
    assert len(row["positions"]) >= 2
    expected = row["day_end_cash_usd"] + sum(
        position["units"] * float(altered[position["instrument"]].loc[date, "low"])
        for position in row["positions"]
    )
    assert expected < row["day_start_equity_usd"]
    assert expected < row["day_end_equity_usd"]
    assert row["conservative_intraday_min_equity_usd"] == pytest.approx(expected)
