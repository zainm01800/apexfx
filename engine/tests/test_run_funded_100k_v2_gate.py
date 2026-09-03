import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import scripts.run_funded_100k_v2_gate as runner
from apex_quant.backtest.portfolio import PortfolioResult
from apex_quant.validation.funded_simulator import DayRecord, WilsonInterval


def _records() -> tuple[DayRecord, ...]:
    rows = []
    balance = runner.ACCOUNT
    for offset in range(3):
        timestamp = pd.Timestamp("2024-01-02", tz="UTC") + pd.Timedelta(days=offset)
        end = balance + 100.0
        rows.append(
            DayRecord(
                session=timestamp.date(),
                timestamp=timestamp,
                day_start_balance=balance,
                day_start_equity=balance,
                intraday_min_equity=balance - 50.0,
                end_balance=end,
                end_equity=end,
                closed_pnl=100.0,
                source_risk_base=min(balance, runner.ACCOUNT),
                intraday_min_timestamp=timestamp,
            )
        )
        balance = end
    return tuple(rows)


@pytest.mark.parametrize(
    ("mode", "risk", "aggregate", "gross", "correlated", "notional", "positions"),
    [
        ("evaluation", 0.0035, 0.0090, 0.60, 0.20, 0.08, 5),
        ("payout", 0.0025, 0.0060, 0.45, 0.15, 0.06, 4),
    ],
)
def test_v2_config_matches_preregistered_cells(
    mode, risk, aggregate, gross, correlated, notional, positions,
):
    cfg = runner.v2_config(mode=mode)

    assert cfg.backtest.initial_equity == runner.ACCOUNT
    assert cfg.risk.max_risk_per_trade == pytest.approx(risk)
    assert cfg.risk.max_portfolio_risk == pytest.approx(aggregate)
    assert cfg.risk.max_total_exposure == pytest.approx(gross)
    assert cfg.risk.max_correlated_exposure == pytest.approx(correlated)
    assert cfg.risk.max_position_notional_pct == pytest.approx(notional)
    assert cfg.risk.max_concurrent_trades == positions
    assert cfg.risk.max_swing_slots == positions
    assert cfg.risk.slot_allocation == "expected_value"
    assert cfg.risk.portfolio_risk_cap_mode == "simultaneous"


def test_v2_runner_opts_into_cash_policy_and_fresh_history(monkeypatch):
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

    monkeypatch.setattr(runner, "TrendBook", FakeModel)
    monkeypatch.setattr(runner, "PortfolioBacktester", FakeBacktester)
    frame = pd.DataFrame(
        {
            "open": [100.0], "high": [101.0], "low": [99.0],
            "close": [100.0], "volume": [1.0],
        },
        index=pd.DatetimeIndex(["2024-01-01"], tz="UTC"),
    )

    result = runner._run_v2(
        {"AAPL": frame},
        mode="payout",
        max_loss_mode="static",
        start="2023-01-01",
        end="2024-12-31",
    )

    assert result == "sentinel"
    assert captured["funded_cash_risk_mode"] == "payout"
    assert captured["funded_cash_max_loss_mode"] == "static"
    assert captured["enforce_entry_bar_exits"] is True
    assert captured["slot_allocation"] == "expected_value"
    assert captured["retain_pre_start_history"] is True


def test_guard_terminal_rules_use_mode_thresholds_and_peak_cycle():
    evaluation = runner._guard_terminal_rules(mode="evaluation")
    payout = runner._guard_terminal_rules(mode="payout")

    assert evaluation.daily_loss_pct == pytest.approx(0.015)
    assert evaluation.max_loss_pct == pytest.approx(0.05)
    assert evaluation.max_loss_mode == "eod_trailing"
    assert payout.daily_loss_pct == pytest.approx(0.012)
    assert payout.max_loss_pct == pytest.approx(0.04)
    assert payout.max_loss_mode == "eod_trailing"


def test_official_replay_requires_four_conservative_trading_days():
    rules = runner._official_rules(max_loss_mode="static", target=0.10)

    assert rules.minimum_trading_days == 4


def test_replay_cycle_drawdown_is_peak_cash_loss_over_initial_balance():
    first_t = pd.Timestamp("2024-01-02", tz="UTC")
    second_t = first_t + pd.Timedelta(days=1)
    records = (
        DayRecord(
            session=first_t.date(), timestamp=first_t,
            day_start_balance=100_000.0, day_start_equity=100_000.0,
            intraday_min_equity=100_000.0, end_balance=110_000.0,
            end_equity=110_000.0, closed_pnl=10_000.0,
            source_risk_base=100_000.0, intraday_min_timestamp=first_t,
        ),
        DayRecord(
            session=second_t.date(), timestamp=second_t,
            day_start_balance=110_000.0, day_start_equity=110_000.0,
            intraday_min_equity=104_900.0, end_balance=105_000.0,
            end_equity=105_000.0, closed_pnl=-5_000.0,
            source_risk_base=100_000.0, intraday_min_timestamp=second_t,
        ),
    )

    report = runner._replay(
        records, mode="evaluation", max_loss_mode="static",
    )

    assert report["guard_terminal_failure"] is True
    assert report["worst_peak_to_intraday_cash_drawdown_pct_initial"] == pytest.approx(
        0.051
    )


def test_bootstrap_is_explicitly_min_equity_initial(monkeypatch):
    captured = {}
    interval = WilsonInterval(
        estimate=1.0, lower=0.9, upper=1.0, successes=3, trials=3, confidence=0.95,
    )
    block = SimpleNamespace(
        mean_block_length=5,
        pass_probability=interval,
        breach_probability=interval,
        survival_probability=interval,
        median_sessions_to_pass=None,
        breach_reasons=(),
    )
    fake = SimpleNamespace(
        spec=SimpleNamespace(
            n_paths=3, sample_length=3, mean_block_lengths=(5,), chunk_size=2,
        ),
        strategies=(
            SimpleNamespace(
                name="evaluation::static",
                report=SimpleNamespace(blocks=(block,)),
            ),
        ),
    )

    def fake_bootstrap(*args, **kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(runner, "chunked_synchronized_funded_bootstrap", fake_bootstrap)
    output = runner._bootstrap(
        {"evaluation::static": _records()},
        target=0.10,
        sample_length=3,
        n_paths=3,
        seed=7,
        chunk_size=2,
    )

    assert captured["sizing_mode"] == "min_equity_initial"
    assert output["sizing_mode"] == "min_equity_initial"
    assert output["binding_eligible"] is False
    assert output["status"] == "DIAGNOSTIC_ONLY_POLICY_REPLAY_INCOMPLETE"


def test_exact_inputs_fail_closed_when_missing_or_merely_present(tmp_path: Path):
    missing = runner.audit_exact_inputs({})
    assert missing["passed"] is False
    assert missing["status"] == "DATA_BLOCKED_MISSING_EXACT_INPUTS"
    assert all(row["status"] == "MISSING" for row in missing["required"].values())

    paths = {}
    for name in runner.REQUIRED_EXACT_INPUTS:
        path = tmp_path / f"{name}.json"
        path.write_text("{}\n", encoding="utf-8")
        paths[name] = str(path)
    present = runner.audit_exact_inputs(paths)
    assert present["all_present"] is True
    assert present["integrated_into_executable_replay"] is False
    assert present["passed"] is False
    assert present["status"] == "DATA_PRESENT_NOT_INTEGRATED"


def test_green_numbers_and_present_files_cannot_cross_readiness_boundary(tmp_path: Path):
    paths = {}
    for name in runner.REQUIRED_EXACT_INPUTS:
        path = tmp_path / f"{name}.json"
        path.write_text("{}\n", encoding="utf-8")
        paths[name] = str(path)
    present_but_unintegrated = runner.audit_exact_inputs(paths)
    green_cells = {
        runner._cell(mode, max_mode): {
            "passed_all_binding_gates": True,
            "engine_cash_risk_status": "READY",
            "engine_cash_risk_blockers": [],
        }
        for mode in runner.MODES for max_mode in runner.MAX_LOSS_MODES
    }

    exact_boundary = runner.qualification_decision(
        green_cells, present_but_unintegrated,
    )
    assert exact_boundary["all_cell_gates_pass"] is True
    assert exact_boundary["exact_inputs_integrated"] is False
    assert exact_boundary["passed"] is False
    assert exact_boundary["verdict"] == "NO_FUNDED_STRATEGY_V2"

    engine_blocked = {name: dict(value) for name, value in green_cells.items()}
    engine_blocked["evaluation::static"] = {
        "passed_all_binding_gates": True,
        "engine_cash_risk_status": "DATA_BLOCKED",
        "engine_cash_risk_blockers": ["planned_loss_excludes_costs"],
    }
    exact_integrated = {
        "passed": True, "integrated_into_executable_replay": True,
    }
    engine_boundary = runner.qualification_decision(engine_blocked, exact_integrated)
    assert engine_boundary["exact_inputs_integrated"] is True
    assert engine_boundary["engine_ready"] is False
    assert engine_boundary["passed"] is False
    assert engine_boundary["verdict"] == "NO_FUNDED_STRATEGY_V2"


def test_nondefault_invocation_cannot_cross_readiness_boundary():
    green_cells = {
        runner._cell(mode, max_mode): {
            "passed_all_binding_gates": True,
            "engine_cash_risk_status": "READY",
            "engine_cash_risk_blockers": [],
        }
        for mode in runner.MODES for max_mode in runner.MAX_LOSS_MODES
    }
    exact = {"passed": True, "integrated_into_executable_replay": True}
    nonbinding = {"passed": False, "status": "NON_BINDING_INVOCATION"}
    decision = runner.qualification_decision(green_cells, exact, nonbinding)

    assert decision["all_cell_gates_pass"] is True
    assert decision["exact_inputs_integrated"] is True
    assert decision["engine_ready"] is True
    assert decision["binding_invocation"] is False
    assert decision["passed"] is False


def test_binding_invocation_requires_every_frozen_default():
    base = SimpleNamespace(
        n_paths=runner.DEFAULT_N_PATHS,
        order_permutations=runner.DEFAULT_ORDER_PERMUTATIONS,
        skip_bootstrap=False,
        skip_cpcv=False,
        skip_deep_diagnostics=False,
        validation_only_smoke=False,
    )
    assert runner._binding_invocation(base)["passed"] is True
    reduced = SimpleNamespace(**{**vars(base), "n_paths": 1_000})
    assert runner._binding_invocation(reduced)["passed"] is False


def test_v2_ledger_freezes_sources_config_and_data_before_results(tmp_path: Path):
    path = tmp_path / "validation" / "funded_100k_v2_ledger.json"
    source_hashes = {"protocol": "a" * 64, "runner": "b" * 64}
    config = {"cells": {"evaluation::static": {"risk": 0.0035}}}
    data = {"AAPL": {"sha256": "c" * 64}}
    exact = {"passed": False, "status": "DATA_BLOCKED_MISSING_EXACT_INPUTS"}
    prior = {
        "sha256": "d" * 64,
        "n_trials": 362,
        "annualization_metadata_complete": False,
    }

    first = runner.initialize_v2_ledger(
        path,
        source_hashes=source_hashes,
        effective_config=config,
        data_manifest=data,
        exact_input_manifest=exact,
        prior_trial_reference=prior,
    )
    second = runner.initialize_v2_ledger(
        path,
        source_hashes=source_hashes,
        effective_config=config,
        data_manifest=data,
        exact_input_manifest=exact,
        prior_trial_reference=prior,
    )

    assert first == second == json.loads(path.read_text(encoding="utf-8"))
    assert first["frozen_before_first_backtest"] is True
    assert first["results"] == {}
    with pytest.raises(RuntimeError, match="conflicts"):
        runner.initialize_v2_ledger(
            path,
            source_hashes=source_hashes,
            effective_config={"cells": {}},
            data_manifest=data,
            exact_input_manifest=exact,
            prior_trial_reference=prior,
        )

    completed = dict(first)
    completed["results"] = {"evaluation::static": {"passed": False}}
    path.write_text(json.dumps(completed), encoding="utf-8")
    with pytest.raises(RuntimeError, match="already contains results"):
        runner.initialize_v2_ledger(
            path,
            source_hashes=source_hashes,
            effective_config=config,
            data_manifest=data,
            exact_input_manifest=exact,
            prior_trial_reference=prior,
        )


def test_v2_outputs_must_be_pairwise_distinct(tmp_path: Path):
    ledger = tmp_path / "ledger.json"
    with pytest.raises(ValueError, match="must be distinct"):
        runner._require_distinct_output_paths(ledger, ledger, tmp_path / "report.md")
    runner._require_distinct_output_paths(
        ledger, tmp_path / "result.json", tmp_path / "report.md",
    )


def test_dsr_is_blocked_when_prior_sharpe_units_are_unknown():
    index = pd.date_range("2024-01-01", periods=4, tz="UTC")
    returns = {"evaluation::static": pd.Series([0.0, 0.01, -0.005, 0.003], index=index)}
    prior = {
        "n_trials": 362,
        "sharpes": [1.0, 0.8],
        "annualization_metadata_complete": False,
    }

    report = runner._v2_dsr_reports(returns, prior)["evaluation::static"]

    assert report["dsr"] is None
    assert report["passed"] is False
    assert report["status"] == "DATA_BLOCKED_TRIAL_SHARPE_ANNUALIZATION"


def test_trace_cap_diagnostic_never_promotes_stop_only_raw_currency_data():
    index = pd.date_range("2024-01-01", periods=2, tz="UTC")
    trace = pd.DataFrame(
        {
            "end_equity": [100_000.0, 99_000.0],
            "post_pending_planned_gross_exposure": [59_000.0, 40_000.0],
            "post_pending_planned_stop_risk": [890.0, 500.0],
            "worst_symbol_adverse_loss": [400.0, 300.0],
        },
        index=index,
    )

    diagnostic = runner._trace_cap_diagnostics(trace, mode="evaluation")

    assert diagnostic["gross_cap_pass"] is True
    assert diagnostic["stop_only_cap_pass"] is True
    assert diagnostic["planned_loss_includes_entry_exit_costs"] is False
    assert diagnostic["exact_exposure_gate_pass"] is False
    assert diagnostic["status"].startswith("DATA_BLOCKED")

    overrun_trace = trace.copy()
    overrun_trace.loc[index[0], "post_pending_planned_stop_risk"] = 1_310.0
    overrun = runner._trace_cap_diagnostics(overrun_trace, mode="evaluation")
    assert overrun["stop_only_cap_pass"] is False
    assert overrun[
        "aggregate_stop_only_overrun_pct_capital_base"
    ] == pytest.approx(0.0041)


def test_monthly_lower_bound_is_reported_without_claiming_payouts():
    index = pd.date_range("2023-01-01", periods=400, freq="D", tz="UTC")
    equity = pd.Series(
        100_000.0 * (1.0001 ** pd.RangeIndex(len(index))), index=index,
    )
    result = PortfolioResult(instruments=[], equity=equity)

    report = runner._monthly_lower_95(result)

    assert report["status"] == "EVALUATED_STUDENT_T"
    assert report["n_months"] >= 12
    assert report["lower_95"] > 0.0
