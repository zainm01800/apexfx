"""Synthetic-only tests for the frozen Book U research gate runner."""

from __future__ import annotations

import hashlib
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from apex_quant.research.book_u_cluster_trend import CLUSTER_MEMBERS, USD_ETF_UNIVERSE
from scripts import run_book_u_cluster_trend_gate as gate


def test_frozen_risk_cells_and_stress_are_exact() -> None:
    assert gate.RISK_CELLS == {
        "U075": (0.0075, 0.0225),
        "U085": (0.0085, 0.0255),
        "U100": (0.0100, 0.0300),
    }
    base = gate._spec("U075")
    stress = gate._spec("U075", stressed=True)
    assert (base.cost_bps_per_side, base.stop_slippage_bps) == (5.0, 0.0)
    assert (stress.cost_bps_per_side, stress.stop_slippage_bps) == (10.0, 25.0)
    assert base.momentum_lookback == 252
    assert base.vol_window == 63
    assert base.atr_window == 20
    assert base.stop_atr_multiple == 2.5
    assert base.portfolio_vol_target == 0.06
    assert base.gross_cap == 0.95
    assert base.position_cap == 0.25


def test_strict_json_is_deterministic_and_never_emits_nonfinite_tokens() -> None:
    payload = {
        "z": np.float64(np.inf),
        "a": [np.float64(np.nan), np.int64(2), -np.inf],
    }
    first = gate._strict_json(payload)
    second = gate._strict_json(payload)
    assert first == second
    assert "NaN" not in first
    assert "Infinity" not in first
    assert json.loads(first) == {"a": [None, 2, None], "z": None}


def _write_frozen_fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    panel = gate._synthetic_panel(300)
    rows = []
    for instrument in USD_ETF_UNIVERSE:
        part = panel[instrument].reset_index()
        part.insert(0, "instrument", instrument)
        rows.append(part)
    snapshot_frame = pd.concat(rows, ignore_index=True)
    snapshot = tmp_path / "book_u.parquet"
    snapshot_frame.to_parquet(snapshot, index=False)
    protocol = tmp_path / "protocol.md"
    protocol.write_text("frozen synthetic protocol\n", encoding="utf-8")
    protocol_hash = hashlib.sha256(protocol.read_bytes()).hexdigest()
    snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    index = next(iter(panel.values())).index
    sources = {}
    for number, instrument in enumerate(USD_ETF_UNIVERSE, start=1):
        response = json.dumps({"instrument": instrument, "number": number}).encode()
        compressed = gzip.compress(response, mtime=0)
        relative = Path("raw") / f"{instrument}.json.gz"
        raw_path = tmp_path / relative
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(compressed)
        sources[instrument] = {
            "raw_gzip_path": relative.as_posix(),
            "raw_gzip_sha256": hashlib.sha256(compressed).hexdigest(),
            "raw_response_sha256": hashlib.sha256(response).hexdigest(),
        }
    manifest = {
        "schema_version": 1,
        "kind": "book_u_adjusted_usd_etf_snapshot",
        "protocol_sha256": protocol_hash,
        "snapshot_sha256": snapshot_hash,
        "snapshot_rows": len(snapshot_frame),
        "instruments": list(USD_ETF_UNIVERSE),
        "common_sessions": len(index),
        "common_start": index.min().strftime("%Y-%m-%d"),
        "common_end": index.max().strftime("%Y-%m-%d"),
        "adjustment_policy": "synthetic adjusted OHLC",
        "download": {"requested_end_exclusive": "2026-09-03"},
        "sources": sources,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return snapshot, manifest_path, protocol, protocol_hash


def test_loader_binds_protocol_manifest_and_snapshot_hashes(tmp_path: Path) -> None:
    snapshot, manifest, protocol, protocol_hash = _write_frozen_fixture(tmp_path)
    panel, integrity = gate._load_frozen_panel(
        snapshot,
        manifest,
        protocol,
        expected_protocol_sha256=protocol_hash,
        repo_root=tmp_path,
    )
    assert tuple(panel) == tuple(sorted(USD_ETF_UNIVERSE))
    assert integrity["manifest_snapshot_sha256_match"] is True
    assert integrity["manifest_protocol_sha256_match"] is True

    with snapshot.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(RuntimeError, match="snapshot hash mismatch"):
        gate._load_frozen_panel(
            snapshot,
            manifest,
            protocol,
            expected_protocol_sha256=protocol_hash,
            repo_root=tmp_path,
        )


def test_loader_rejects_raw_vendor_path_escape(tmp_path: Path) -> None:
    snapshot, manifest, protocol, protocol_hash = _write_frozen_fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sources"]["SPY"]["raw_gzip_path"] = "../escape.json.gz"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="escapes the repository"):
        gate._load_frozen_panel(
            snapshot,
            manifest,
            protocol,
            expected_protocol_sha256=protocol_hash,
            repo_root=tmp_path,
        )


def test_loader_rejects_raw_vendor_gzip_hash_mismatch(tmp_path: Path) -> None:
    snapshot, manifest, protocol, protocol_hash = _write_frozen_fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    raw_path = tmp_path / payload["sources"]["TLT"]["raw_gzip_path"]
    raw_path.write_bytes(raw_path.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError, match="raw gzip hash mismatch for TLT"):
        gate._load_frozen_panel(
            snapshot,
            manifest,
            protocol,
            expected_protocol_sha256=protocol_hash,
            repo_root=tmp_path,
        )


def test_cpcv_is_exactly_six_groups_choose_two_and_uses_sharpe_sign() -> None:
    index = pd.bdate_range("2020-01-02", periods=180, tz="UTC")
    values = np.resize(np.array([0.0010, 0.0020, 0.0005]), len(index))
    result = gate._cpcv_sign_diagnostic(pd.Series(values, index=index))
    assert result["n_paths"] == 15
    assert result["positive_paths"] == 15
    assert result["passed"] is True
    assert all(row["per_period_sharpe"] > 0.0 for row in result["paths"])
    assert result["training_or_refit_performed"] is False


def test_dsr_is_data_blocked_without_compatible_project_dispersion() -> None:
    index = pd.bdate_range("2023-01-03", periods=40, tz="UTC")
    returns = pd.Series(np.linspace(-0.001, 0.002, len(index)), index=index)
    run = SimpleNamespace(equity=(1.0 + returns).cumprod() * 100_000.0)
    shared = {
        "object_entry_count": 362,
        "finite_compatible_sharpes": 0,
        "_annualized_sharpes": [],
    }
    result = gate._dsr_diagnostic(returns, [run], None, shared)
    assert result["status"] == "DATA_BLOCKED"
    assert result["passed"] is False
    assert result["dsr"] is None
    assert result["spent_project_trial_count"] == 362
    assert result["effective_trial_count"] == 363
    assert "shared ledger" in result["reason"]


def test_shared_trial_ledger_observation_is_exact_and_read_only(tmp_path: Path) -> None:
    path = tmp_path / "trial_ledger.json"
    path.write_text(json.dumps({"trial-a": None, "trial-b": None}), encoding="utf-8")
    before = path.read_bytes()
    result = gate._observe_shared_trial_ledger(path)
    assert result["sha256"] == hashlib.sha256(before).hexdigest()
    assert result["object_entry_count"] == 2
    assert result["finite_compatible_sharpes"] == 0
    assert result["matches_expected_missing_dispersion"] is True
    assert result["read_only"] is True
    assert path.read_bytes() == before


def test_episode_haircut_and_cluster_removal_use_reconciled_net_pnl() -> None:
    cluster_names = tuple(CLUSTER_MEMBERS)
    contributions = dict(zip(cluster_names, (40.0, 30.0, 30.0, 20.0, -20.0, 0.0), strict=True))
    episodes = [
        {"cluster": cluster, "net_pnl_usd": value}
        for cluster, value in contributions.items()
    ]
    run = SimpleNamespace(
        episodes=episodes,
        metrics={"net_pnl_usd": 100.0, "cluster_attribution_reconciles": True},
        cluster_attribution={
            cluster: {"net_pnl_usd": value}
            for cluster, value in contributions.items()
        },
    )
    concentration = gate._cluster_concentration(run)
    haircut = gate._winner_haircut(run)
    assert concentration["top_cluster_share_of_portfolio_net_pnl"] == pytest.approx(0.40)
    assert concentration["top_cluster_share_of_positive_cluster_pnl"] == pytest.approx(1.0 / 3.0)
    assert concentration["binding_share_denominator"] == "sum_of_positive_cluster_pnl"
    assert concentration["net_pnl_after_removing_top_cluster_usd"] == pytest.approx(60.0)
    assert concentration["passed"] is True
    assert haircut["haircut_net_pnl_usd"] == pytest.approx(40.0)
    assert haircut["passed"] is True


def test_daily_static_funded_replay_is_explicitly_only_a_proxy() -> None:
    trace = []
    balance = 100_000.0
    for number, date in enumerate(pd.bdate_range("2024-01-02", periods=5, tz="UTC")):
        end = balance + 100.0
        trace.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "day_start_balance_usd": balance,
                "day_start_equity_usd": balance,
                "conservative_intraday_min_equity_usd": balance - 250.0,
                "day_end_balance_usd": end,
                "day_end_equity_usd": end,
                "positions_opened": ["SPY"] if number == 0 else [],
                "verified_flat_at_end": number == 4,
            }
        )
        balance = end
    result = gate._funded_replay_proxy(SimpleNamespace(trace=trace))
    assert result["status"] == "DAILY_OHLC_STATIC_PROXY_ONLY"
    assert result["binding_funded_evidence"] is False
    assert result["breach"] is False
    assert result["rules"]["daily_loss_pct"] == 0.05
    assert result["rules"]["max_loss_pct"] == 0.10
    assert result["rules"]["max_loss_mode"] == "static"


def test_correlated_gap_is_nonbinding_arithmetic_not_probability() -> None:
    run = SimpleNamespace(metrics={"max_open_gross_fraction": 0.60})
    result = gate._correlated_gap_arithmetic(run)
    assert result["status"] == "NON_BINDING_ARITHMETIC_ONLY"
    assert result["preregistered_gate_input"] is False
    assert result["empirical_probability"] is None
    assert result["scenarios"]["5pct_simultaneous_adverse_gap"][
        "gross_loss_fraction_initial_equity"
    ] == pytest.approx(0.03)
    assert result["scenarios"]["10pct_simultaneous_adverse_gap"][
        "gross_loss_usd_on_100k"
    ] == pytest.approx(6_000.0)


def _fingerprinted_fake_run(spec):
    index = pd.DatetimeIndex([pd.Timestamp("2024-01-02", tz="UTC")])
    digest = "1" * 64
    return SimpleNamespace(
        spec=spec,
        start=index[0],
        end=index[0],
        equity=pd.Series([100_000.0], index=index),
        events=[],
        decisions=[],
        trace=[],
        episodes=[],
        cluster_attribution={},
        metrics={
            "run_fingerprint_sha256": digest,
            "outcome_sha256": "2" * 64,
            "consumed_panel_sha256": "3" * 64,
            "protocol_sha256": gate.PROTOCOL_SHA256,
            "input_panel_sha256": "5" * 64,
        },
    )


def test_result_fingerprint_and_separate_ledger_bind_core_spec_and_consumed_hash() -> None:
    base = _fingerprinted_fake_run(gate._spec("U075"))
    stress = _fingerprinted_fake_run(gate._spec("U075", stressed=True))
    first = gate._result_fingerprint(base)
    base.metrics["consumed_panel_sha256"] = "6" * 64
    assert gate._result_fingerprint(base) != first

    segments = {
        "synthetic_scope": {
            "_base_run": base,
            "_stress_run": stress,
        }
    }
    shared = {
        "sha256": "7" * 64,
        "object_entry_count": 362,
        "finite_compatible_sharpes": 0,
        "read_only": True,
    }
    ledger = gate._build_book_u_trial_ledger(
        segments,
        [],
        {"cells": {}},
        integrity={"protocol_sha256": gate.PROTOCOL_SHA256, "snapshot_sha256": "8" * 64},
        shared_ledger=shared,
    )
    assert ledger["configuration_cell_count"] == 2
    assert ledger["market_evaluation_count"] == 2
    assert ledger["shared_trial_ledger_modified"] is False
    assert {row["configuration_id"] for row in ledger["configuration_cells"]} == {
        "U075_BASE_5BPS",
        "U075_STRESS_10BPS_25BPS_STOP",
    }
    assert "NaN" not in gate._strict_json(ledger)


def test_conditional_frontier_does_not_touch_higher_risk_cells_when_blocked() -> None:
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("higher-risk frontier was inspected")

    result = gate._conditional_frontier(
        {"passed": False},
        {},
        {},
        start="2010-01-04",
        end="2025-12-31",
        run_fn=forbidden,
    )
    assert result["status"] == "NOT_RUN_ARCHITECTURE_GATE"
    assert result["cells"] == {}
    assert called is False


def test_frontier_uses_stressed_calmar_and_lower_risk_five_percent_tie(monkeypatch) -> None:
    calmars = {"U075": 0.96, "U085": 0.90, "U100": 1.00}

    def run_for(cell: str, stressed: bool):
        metrics = {
            "annualized_return": 0.075 if stressed else 0.10,
            "calmar": calmars[cell] if stressed else 1.1,
            "max_drawdown": 0.05,
            "worst_conservative_intraday_day": -0.01,
        }
        return SimpleNamespace(metrics=metrics, spec=gate._spec(cell, stressed=stressed))

    def pair(cell: str):
        return {
            "base": {"metrics": {"total_return": 0.10}},
            "stress_10bps_plus_25bps_stop_slippage": {"metrics": {"total_return": 0.08}},
            "_base_run": run_for(cell, False),
            "_stress_run": run_for(cell, True),
        }

    monkeypatch.setattr(
        gate,
        "_run_pair",
        lambda panel, *, cell, start, end, run_fn: pair(cell),
    )
    monkeypatch.setattr(gate, "_winner_haircut", lambda run: {"passed": True})
    monkeypatch.setattr(gate, "_funded_replay_proxy", lambda run: {"breach": False})
    monkeypatch.setattr(gate, "_cost_and_cap_diagnostics", lambda run, panel: {"passed": True})

    result = gate._frontier(
        {},
        pair("U075"),
        start="2010-01-04",
        end="2025-12-31",
        run_fn=lambda *args, **kwargs: None,
    )
    assert all(row["eligibility"]["eligible"] for row in result["cells"].values())
    # U075 is within 4% of U100, so the frozen tie-break must prefer lower risk.
    assert result["selected_cell"] == "U075"


def test_embedded_engineering_suite_uses_only_synthetic_data_and_passes() -> None:
    result = gate._engineering_control_suite()
    assert result["historical_outcomes_used"] is False
    assert result["passed"] is True
    assert all(result["checks"].values())
