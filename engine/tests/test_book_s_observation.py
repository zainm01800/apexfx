"""Read-only/synthetic regressions: risk telemetry cannot rewrite paper P&L."""
import copy
import json

import pandas as pd
import pytest

from apex_quant.models.book_s_execution import advance_hours
from apex_quant.models.book_s_observation import (
    ensure_drawdown_tracker,
    observation_metadata,
    observe_hour_close,
)
from apex_quant.models.book_s_session_smc import _date_str, new_book_s_state, runtime_payload


def marked_state():
    state = new_book_s_state("2026-09-01 11:00:00Z")
    state["positions"]["GBP/USD"] = {
        "symbol": "GBP/USD", "direction": "long", "units": 100000.0,
        "entry_price": 1.0, "entry_time": "2026-09-01 11:00:00",
        "decision_time": "2026-09-01 10:00:00", "stop_loss": .5,
        "take_profit": 2.0, "unrealized_pnl": 0.0, "current_price": 1.0,
    }
    return state


def run(state, times=None):
    index = pd.date_range("2026-09-01 12:00", periods=3, freq="h", tz="UTC")
    frame = pd.DataFrame({"open": [1.02, .99, 1.03], "high": [1.025, .995, 1.035],
                          "low": [1.015, .985, 1.025], "close": [1.02, .99, 1.03]}, index=index)
    return advance_hours(state, {"GBP/USD": frame}, index if times is None else times,
                         universe=["GBP/USD"], risk=500, rr=1.8, max_positions=4,
                         daily_limit=1800, max_hours=16, pip_sizes={"GBP/USD": .0001},
                         spreads={"GBP/USD": 0}, stamp=_date_str)


def test_hourly_drawdown_survives_recovered_daily_close():
    state, _ = run(marked_state())
    tracker = state["drawdown_tracker"]
    assert state["equity_curve"][-1]["drawdown"] == 0
    assert tracker["max_drawdown_fraction"] == pytest.approx(3000 / 102000)
    assert tracker["max_drawdown_amount_usd"] == pytest.approx(3000)
    assert tracker["max_drawdown_at_utc"] == "2026-09-01T14:00:00+00:00"
    assert tracker["high_water_equity"] == 103000
    assert tracker["observation_count"] == 3
    assert state["equity_curve"][-1]["metrics"]["max_drawdown"] == tracker["max_drawdown_fraction"]
    assert state["cash"] == 100000 and not state["trades"]
    assert state["equity"] == 103000


def test_restart_and_json_roundtrip_have_identical_telemetry_and_money():
    batch, _ = run(marked_state())
    split = marked_state()
    for time in pd.date_range("2026-09-01 12:00", periods=3, freq="h", tz="UTC"):
        split, _ = run(json.loads(json.dumps(split)), [time])
    assert split == batch


def test_old_tracker_migration_preserves_all_existing_history():
    old = new_book_s_state("2026-09-01")
    old.pop("drawdown_tracker")
    old.update(equity=101000.0, cash=100500.0, peak=102000.0)
    old["equity_curve"].append({"date": "2026-09-02", "equity": 99000,
                                "cash": 100500, "drawdown": .02})
    before = copy.deepcopy(old)
    tracker = ensure_drawdown_tracker(old)
    assert tracker["complete_since_activation"] is False
    assert tracker["max_drawdown_fraction"] == .02
    assert tracker["high_water_equity"] == 102000
    assert {k: v for k, v in old.items() if k != "drawdown_tracker"} == before
    observe_hour_close(old, "2026-09-03 08:00Z", 104000, 104000)
    assert tracker["complete_since_activation"] is False
    assert tracker["max_drawdown_fraction"] == .02


def test_old_zero_daily_drawdown_is_never_certified_as_complete():
    old = marked_state()
    old.pop("drawdown_tracker")
    state, _ = run(old)
    row = state["equity_curve"][-1]
    assert row["drawdown"] == 0
    assert row["metrics"]["max_drawdown"] is None
    assert row["metrics"]["observed_max_drawdown"] == pytest.approx(3000 / 102000)
    assert row["metrics"]["drawdown_complete_since_activation"] is False


def test_read_only_projection_never_migrates_or_rewrites_the_input():
    old = marked_state()
    old.pop("drawdown_tracker")
    before = copy.deepcopy(old)
    metadata = observation_metadata(old)
    assert old == before
    assert metadata["drawdown"]["complete_since_activation"] is False


def test_seed_is_not_mislabeled_as_a_completed_market_hour():
    state = new_book_s_state("2026-09-05T01:51:20+01:00")
    metadata = observation_metadata(state)
    assert state["last_processed_time"] == "2026-09-05 00:51:20"
    assert metadata["market_data_through_utc"] is None
    assert metadata["last_processed_bar_start_utc"] is None
    assert metadata["drawdown"]["coverage_started_at_utc"] == "2026-09-05T00:51:20+00:00"


def test_activation_date_and_timestamp_use_the_same_utc_day():
    state = new_book_s_state("2026-09-05T00:30:00+01:00")
    assert state["last_processed_time"] == "2026-09-04 23:30:00"
    assert state["equity_curve"][0]["date"] == "2026-09-04"


def test_market_timestamp_means_the_hour_close_not_the_hour_start():
    state, _ = run(marked_state())
    metadata = observation_metadata(state)
    assert metadata["last_processed_bar_start_utc"] == "2026-09-01T14:00:00+00:00"
    assert metadata["market_data_through_utc"] == "2026-09-01T15:00:00+00:00"
    assert metadata["drawdown"]["observed_through_utc"] == metadata["market_data_through_utc"]


def test_legacy_runtime_observation_keeps_the_exact_original_state():
    state, _ = run(marked_state())
    before = copy.deepcopy(state)
    payload = runtime_payload(state)
    assert state == before and payload["state"] == before
    assert payload["observation"]["drawdown"]["max_drawdown_fraction"] > 0


def test_repeated_hour_is_rejected_instead_of_double_counted():
    state = marked_state()
    observe_hour_close(state, "2026-09-01T12:00Z", 99000, 100000)
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="strictly once"):
        observe_hour_close(state, "2026-09-01T12:00Z", 99000, 100000)
    assert state == before


def test_invalid_tracker_does_not_silently_reset_history():
    state = marked_state()
    state["drawdown_tracker"]["max_drawdown_fraction"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        ensure_drawdown_tracker(state)


def test_new_closed_trades_retain_the_saved_decision_bar_label():
    state = marked_state()
    state["positions"]["GBP/USD"]["entry_time"] = "2026-08-31 20:00:00"
    state["positions"]["GBP/USD"]["decision_time"] = "2026-08-31 19:00:00"
    state, _ = run(state)
    assert state["trades"][0]["decision_time"] == "2026-08-31 19:00:00"
    assert state["trades"][0]["exit_reason"] == "time_limit"
