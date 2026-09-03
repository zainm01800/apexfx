import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scripts.run_funded_100k_gate as funded_runner
from scripts.run_funded_100k_gate import (
    ACCOUNT,
    _assert_isolated_write,
    _bootstrap_common_chunked,
    _dsr_reports,
    _jsonable,
    _replay_payload,
    _rules,
    _winner_cut_records,
    calibrate_scale,
    floor_to_005,
    initialize_funded_ledger,
    trace_to_day_records,
)


def _trace(n: int = 400) -> pd.DataFrame:
    index = pd.date_range("2016-01-01", periods=n, freq="D", tz="UTC")
    # A deterministic balance/equity path with non-zero daily, rolling-DD, and
    # adverse-symbol calibration denominators.
    daily = np.where(np.arange(n) % 17 == 0, -250.0, 45.0)
    end_balance = ACCOUNT + np.cumsum(daily)
    day_start_balance = np.r_[ACCOUNT, end_balance[:-1]]
    end_equity = end_balance + np.sin(np.arange(n)) * 25.0
    day_start_equity = np.r_[ACCOUNT, end_equity[:-1]]
    opening = day_start_equity - np.where(np.arange(n) % 29 == 0, 350.0, 20.0)
    low = np.minimum.reduce(
        [opening - 225.0, end_equity - 175.0, day_start_equity - 100.0]
    )
    return pd.DataFrame(
        {
            "day_start_balance": day_start_balance,
            "day_start_equity": day_start_equity,
            "opening_equity": opening,
            "conservative_intraday_min_equity": low,
            "end_balance": end_balance,
            "end_equity": end_equity,
            "closed_pnl": daily,
            "positions_opened": np.where(np.arange(n) % 5 == 0, 1, 0),
            "verified_flat_at_end": end_equity == end_balance,
            "risk_sizing_base": 3_000.0,
            "gross_exposure": 25_000.0,
            "planned_stop_risk": 700.0,
            "worst_symbol_adverse_loss": np.where(np.arange(n) % 31 == 0, 800.0, 100.0),
        },
        index=index,
    )


def test_floor_to_005_is_downward_and_scale_uses_frozen_formula():
    assert floor_to_005(0.999) == pytest.approx(0.95)
    assert floor_to_005(0.75) == pytest.approx(0.75)

    result = calibrate_scale(_trace())
    expected_raw = min(
        1.0,
        0.50 * 0.03 / result["L_day"],
        0.50 * 0.10 / result["D_1y"],
        0.35 * 0.03 / result["L_gap"],
    )
    assert result["raw_minimum"] == pytest.approx(expected_raw)
    assert result["scale"] == pytest.approx(floor_to_005(expected_raw))
    assert "proxy" in result["L_gap_semantics"]


def test_jsonable_serializes_firm_session_dates_and_numpy_booleans():
    assert _jsonable({"session": date(2024, 1, 2), "passed": np.bool_(True)}) == {
        "passed": True,
        "session": "2024-01-02",
    }


def test_trace_conversion_requires_real_balance_fields_and_preserves_them():
    trace = _trace(3)
    records = trace_to_day_records(trace)

    assert records[0].day_start_balance == ACCOUNT
    assert records[0].end_balance == pytest.approx(trace.iloc[0]["end_balance"])
    assert records[0].closed_pnl == pytest.approx(trace.iloc[0]["closed_pnl"])
    assert records[0].source_risk_base == pytest.approx(3_000.0)
    assert records[0].positions_opened == 1
    assert records[0].verified_flat_at_end is True

    with pytest.raises(ValueError, match="end_balance"):
        trace_to_day_records(trace.drop(columns="end_balance"))


@pytest.mark.parametrize("bad", [1.9, True, "1"])
def test_trace_conversion_rejects_malformed_opening_counts(bad):
    trace = _trace(3).astype({"positions_opened": object})
    trace.iloc[0, trace.columns.get_loc("positions_opened")] = bad

    with pytest.raises((TypeError, ValueError), match="positions_opened"):
        trace_to_day_records(trace)


def test_internal_replay_thresholds_are_terminal_and_stricter_than_official():
    trace = _trace(3)
    trace.iloc[1, trace.columns.get_loc("conservative_intraday_min_equity")] = 97_800.0
    records = trace_to_day_records(trace)
    result = _replay_payload(records)

    assert result["official_breach"] is False
    assert result["internal_terminal_failure"] is True
    assert _rules(max_loss_mode="static", target=None).daily_loss_pct == 0.03
    assert _rules(
        max_loss_mode="static", target=None, internal_terminal=True
    ).daily_loss_pct == 0.018


def test_winner_cut_proxy_keeps_valid_minimum_and_separate_balance_basis():
    trace = _trace(3)
    # Construct a day that starts with floating equity above closed balance and
    # remains profitable throughout.  The transformed minimum must still include
    # the opening equity observation rather than sit above it.
    trace.iloc[0, trace.columns.get_loc("day_start_equity")] = 100_300.0
    trace.iloc[0, trace.columns.get_loc("end_equity")] = 100_400.0
    trace.iloc[0, trace.columns.get_loc("conservative_intraday_min_equity")] = 100_200.0
    trace.iloc[0, trace.columns.get_loc("end_balance")] = 100_100.0
    trace.iloc[0, trace.columns.get_loc("closed_pnl")] = 100.0
    trace.iloc[0, trace.columns.get_loc("verified_flat_at_end")] = False
    # Keep later source records internally continuous for DayRecord construction.
    trace.iloc[1, trace.columns.get_loc("day_start_balance")] = 100_100.0
    trace.iloc[1, trace.columns.get_loc("day_start_equity")] = 100_400.0
    trace.iloc[1, trace.columns.get_loc("conservative_intraday_min_equity")] = 100_200.0

    transformed = _winner_cut_records(trace_to_day_records(trace.iloc[:1]))
    day = transformed[0]

    assert day.intraday_min_equity == pytest.approx(
        100_000.0 * (100_200.0 / 100_300.0)
    )
    assert day.intraday_min_equity <= day.day_start_equity
    assert day.end_balance == pytest.approx(100_050.0)
    assert day.end_equity == pytest.approx(
        100_000.0 * (1.0 + 0.5 * (100_400.0 / 100_300.0 - 1.0))
    )


def test_dedicated_ledger_freezes_declarations_before_results(tmp_path: Path):
    path = tmp_path / "funded-ledger.json"
    protocol_hash = "a" * 64
    first = initialize_funded_ledger(path, protocol_hash)
    again = initialize_funded_ledger(path, protocol_hash)

    assert first == again
    assert first["declarations_frozen_before_computation"] is True
    assert [row["candidate"] for row in first["declarations"]] == [
        "C_FUNDED",
        "C_FUNDED_075_SCALE",
        "R_FUNDED",
        "PLATFORM_NATIVE_DIVERSIFIED_TREND",
    ]
    assert first["results"] == {}
    assert json.loads(path.read_text()) == first


def test_shared_trial_ledger_is_a_protected_output():
    from scripts.run_funded_100k_gate import MAIN_LEDGER_PATH

    with pytest.raises(ValueError, match="never write"):
        _assert_isolated_write(MAIN_LEDGER_PATH)


def test_small_common_random_bootstrap_is_deterministic():
    records = trace_to_day_records(_trace(40))
    first = _bootstrap_common_chunked(
        {"a": records, "b": records},
        target=None,
        sample_length=20,
        n_paths=8,
        seed=123,
        chunk_size=3,
    )
    second = _bootstrap_common_chunked(
        {"a": records, "b": records},
        target=None,
        sample_length=20,
        n_paths=8,
        seed=123,
        chunk_size=3,
    )

    assert first == second
    assert first["candidates"]["a"] == first["candidates"]["b"]
    assert first["n_paths"] == 8


def test_candidate_runner_opts_into_entry_bar_stop_first(monkeypatch):
    captured = {}

    class FakeModel:
        def __init__(self, panel, **params):
            self.panel = panel

        def strategies(self):
            return {name: object() for name in self.panel}

    class FakeBacktester:
        def __init__(self, cfg, **kwargs):
            captured.update(kwargs)

        def run(self, *args, **kwargs):
            captured["run"] = kwargs
            return "sentinel"

    monkeypatch.setattr(funded_runner, "TrendBook", FakeModel)
    monkeypatch.setattr(funded_runner, "PortfolioBacktester", FakeBacktester)
    frame = pd.DataFrame(
        {
            "open": [100.0], "high": [101.0], "low": [99.0],
            "close": [100.0], "volume": [1.0],
        },
        index=pd.DatetimeIndex(["2020-01-01"], tz="UTC"),
    )

    assert funded_runner._run_c({"AAPL": frame}, scale=0.5) == "sentinel"
    assert captured["enforce_entry_bar_exits"] is True
    assert captured["slot_allocation"] == "expected_value"
    assert captured["funded_sizing_limits"] == (0.03, 0.10)


def test_started_candidate_run_retains_history_but_not_account_state(monkeypatch):
    captured = {}

    class FakeModel:
        def __init__(self, panel, **params):
            self.panel = panel

        def strategies(self):
            return {name: object() for name in self.panel}

    class FakeBacktester:
        def __init__(self, cfg, **kwargs):
            captured.update(kwargs)

        def run(self, *args, **kwargs):
            captured["run"] = kwargs
            return "sentinel"

    monkeypatch.setattr(funded_runner, "TrendBook", FakeModel)
    monkeypatch.setattr(funded_runner, "PortfolioBacktester", FakeBacktester)
    frame = pd.DataFrame(
        {
            "open": [100.0], "high": [101.0], "low": [99.0],
            "close": [100.0], "volume": [1.0],
        },
        index=pd.DatetimeIndex(["2020-01-01"], tz="UTC"),
    )

    funded_runner._run_c(
        {"AAPL": frame}, scale=0.5,
        start="2020-01-01", end="2020-01-01",
    )
    assert captured["retain_pre_start_history"] is True
    assert captured["run"]["start"] == pd.Timestamp("2020-01-01", tz="UTC")


def test_dsr_is_blocked_when_spent_trials_lack_sharpe_history():
    returns = pd.Series(
        [0.001, -0.0005, 0.0012, -0.0002] * 10,
        index=pd.date_range("2023-01-01", periods=40, tz="UTC"),
    )
    reports = _dsr_reports(
        {"C_FUNDED": returns, "C_FUNDED_075_SCALE": returns * 0.75},
        {"n_trials": 362, "sharpes": []},
        declaration_count=4,
    )

    report = reports["C_FUNDED"]
    assert report["status"] == "DATA_BLOCKED_TRIAL_SHARPE_HISTORY"
    assert report["passed"] is False
    assert report["dsr"] is None
    assert report["n_trials"] == 366
    assert report["observed_dispersion_count"] == 2
    assert "naive_two_related_configs_non_binding" in report
