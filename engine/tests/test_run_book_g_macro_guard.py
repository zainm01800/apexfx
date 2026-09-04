"""Offline contract tests for the two-stage Book G research runner.

No test in this module downloads market data or evaluates the sealed OOS period.
Synthetic Parquet rows and monkeypatched run results are used to prove that the
runner keeps IS selection physically separated from OOS and fails closed when a
frozen artifact changes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from scripts import run_book_g_macro_guard as gate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exact_request() -> dict[str, object]:
    return {
        "library": "yfinance",
        "library_version": "9.9.9-synthetic",
        "download_mode": "one_symbol_per_call_in_declared_order",
        "symbols": list(gate.ALL_SYMBOLS),
        "start": "2014-01-01",
        "end_exclusive": "2026-09-04",
        "interval": "1d",
        "auto_adjust": True,
        "actions": False,
        "repair": False,
        "threads": False,
        "progress": False,
    }


def _runtime_versions() -> dict[str, str]:
    return {
        "python": "3.12.0-synthetic",
        "numpy": "2.0.0-synthetic",
        "pandas": "3.0.0-synthetic",
        "pyarrow": "24.0.0-synthetic",
        "exchange-calendars": "4.13.0-synthetic",
        "yfinance": "9.9.9-synthetic",
    }


def _write_manifest(
    path: Path,
    *,
    snapshot: Path,
    protocol: Path,
    sessions: pd.DatetimeIndex,
) -> dict[str, object]:
    sessions = pd.DatetimeIndex(sessions).tz_localize(None).normalize()
    first_date = sessions[0].date().isoformat()
    last_date = sessions[-1].date().isoformat()
    session_count = len(sessions)
    row_counts = {symbol: session_count for symbol in gate.ALL_SYMBOLS}
    coverage = {
        symbol: {
            "first_date": first_date,
            "last_date": last_date,
            "rows": session_count,
        }
        for symbol in gate.ALL_SYMBOLS
    }
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": "book_g_yfinance_adjusted_ohlc_snapshot",
        "protocol_path": str(protocol),
        "retrieved_at_utc": "2026-09-04T08:30:00+00:00",
        "request": _exact_request(),
        "calendar": {
            "name": "XNYS",
            "expected_first_session": first_date,
            "expected_last_session": last_date,
            "expected_sessions": session_count,
            "missing_rows_allowed": 0,
            "forward_fill": False,
        },
        "coverage": coverage,
        "row_counts": row_counts,
        "snapshot": {
            "path": str(snapshot.resolve()),
            "columns": ["date", "symbol", "open", "high", "low", "close"],
            "rows": session_count * len(gate.ALL_SYMBOLS),
            "bytes": snapshot.stat().st_size,
            "sha256": _sha256(snapshot),
        },
        "protocol_sha256": _sha256(protocol),
        "limitations": "synthetic offline fixture",
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return payload


def _tiny_panel(sessions: pd.DatetimeIndex) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for rank, symbol in enumerate(gate.ALL_SYMBOLS):
        for offset, session in enumerate(sessions):
            close = 100.0 + rank + offset
            rows.append(
                {
                    "date": pd.Timestamp(session),
                    "symbol": symbol,
                    "open": close - 0.25,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                }
            )
    return pd.DataFrame(rows).sort_values(["date", "symbol"]).reset_index(drop=True)


def _minimal_metrics() -> dict[str, object]:
    return {
        "cagr": 0.09,
        "avg_monthly_profit": 750.0,
        "sharpe": 1.1,
        "profit_factor": 1.7,
        "max_drawdown": 0.04,
        "worst_day": -0.01,
        "total_return": 0.10,
        "win_rate": 0.55,
        "trades": 12,
        "annual_returns": {"2020": 0.08, "2026": 0.02},
    }


class _FakeResult:
    def __init__(self, label: str = "synthetic") -> None:
        self.label = label
        self.metrics = _minimal_metrics()

    def to_dict(self) -> dict[str, object]:
        return {"label": self.label, "metrics": self.metrics}


def test_is_loader_passes_a_parquet_predicate_that_excludes_2020_plus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sealed rows must never be materialized and sliced inside Python."""

    protocol = tmp_path / "protocol.md"
    protocol.write_text("frozen synthetic protocol\n", encoding="utf-8")
    snapshot = tmp_path / "book_g.parquet"
    sessions = pd.DatetimeIndex(
        ["2019-12-30", "2019-12-31", "2020-01-02", "2020-01-03"]
    )
    _tiny_panel(sessions).to_parquet(snapshot, index=False)
    manifest_path = tmp_path / "book_g.manifest.json"
    manifest = _write_manifest(
        manifest_path,
        snapshot=snapshot,
        protocol=protocol,
        sessions=sessions,
    )
    monkeypatch.setattr(gate, "PROTOCOL", protocol)
    monkeypatch.setattr(gate, "_expected_full_sessions", lambda: sessions)

    real_read_parquet = pd.read_parquet
    observed: dict[str, object] = {}

    def recording_read_parquet(path, *args, **kwargs):
        observed["filters"] = kwargs.get("filters")
        return real_read_parquet(path, *args, **kwargs)

    validated: list[pd.DataFrame] = []
    monkeypatch.setattr(gate.pd, "read_parquet", recording_read_parquet)
    monkeypatch.setattr(gate, "validate_panel", lambda frame: validated.append(frame.copy()))

    frame, loaded_manifest = gate._load_is_only(snapshot, manifest_path)

    assert observed["filters"] == [
        ("date", "<", pd.Timestamp("2020-01-01"))
    ]
    assert frame["date"].max() < pd.Timestamp("2020-01-01")
    assert len(frame) == 2 * len(gate.ALL_SYMBOLS)
    assert len(validated) == 1
    assert validated[0].equals(frame)
    assert loaded_manifest == manifest


@pytest.mark.parametrize("tampered", ["snapshot", "protocol"])
def test_manifest_binds_snapshot_and_protocol_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered: str,
) -> None:
    protocol = tmp_path / "protocol.md"
    protocol.write_text("frozen protocol\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot.parquet"
    snapshot.write_bytes(b"synthetic snapshot bytes")
    sessions = pd.DatetimeIndex(["2014-01-02", "2014-01-03"])
    manifest_path = tmp_path / "snapshot.manifest.json"
    expected = _write_manifest(
        manifest_path,
        snapshot=snapshot,
        protocol=protocol,
        sessions=sessions,
    )
    monkeypatch.setattr(gate, "PROTOCOL", protocol)
    monkeypatch.setattr(gate, "_expected_full_sessions", lambda: sessions)

    assert gate._manifest(snapshot, manifest_path) == expected
    if tampered == "snapshot":
        snapshot.write_bytes(snapshot.read_bytes() + b"tamper")
        message = "snapshot hash"
    else:
        protocol.write_text("changed protocol\n", encoding="utf-8")
        message = "protocol hash"

    with pytest.raises(RuntimeError, match=message):
        gate._manifest(snapshot, manifest_path)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("kind", "snapshot kind"),
        ("request", "request field"),
        ("calendar", "calendar field"),
        ("snapshot_rows", "snapshot row count"),
        ("row_counts", "row count for SPY"),
        ("coverage", "coverage for SPY"),
    ],
)
def test_manifest_rejects_incomplete_or_fabricated_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    message: str,
) -> None:
    protocol = tmp_path / "protocol.md"
    protocol.write_text("frozen protocol\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot.parquet"
    snapshot.write_bytes(b"synthetic snapshot bytes")
    sessions = pd.DatetimeIndex(["2014-01-02", "2014-01-03"])
    manifest_path = tmp_path / "snapshot.manifest.json"
    payload = _write_manifest(
        manifest_path,
        snapshot=snapshot,
        protocol=protocol,
        sessions=sessions,
    )
    if field == "kind":
        payload["kind"] = "untrusted_snapshot"
    elif field == "request":
        payload["request"]["library"] = "not-yfinance"  # type: ignore[index]
    elif field == "calendar":
        payload["calendar"]["expected_sessions"] = 999  # type: ignore[index]
    elif field == "snapshot_rows":
        payload["snapshot"]["rows"] = 999  # type: ignore[index]
    elif field == "row_counts":
        payload["row_counts"]["SPY"] = 1  # type: ignore[index]
    else:
        payload["coverage"]["SPY"] = {}  # type: ignore[index]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(gate, "PROTOCOL", protocol)
    monkeypatch.setattr(gate, "_expected_full_sessions", lambda: sessions)

    with pytest.raises(RuntimeError, match=message):
        gate._manifest(snapshot, manifest_path)


def test_manifest_hash_is_part_of_the_frozen_artifact_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = tmp_path / "protocol.md"
    core = tmp_path / "core.py"
    runner = tmp_path / "runner.py"
    fetcher = tmp_path / "fetcher.py"
    requirements = tmp_path / "requirements.txt"
    lockfile = tmp_path / "requirements.lock.txt"
    funded_simulator = tmp_path / "funded_simulator.py"
    test_a = tmp_path / "test_a.py"
    test_b = tmp_path / "test_b.py"
    manifest = tmp_path / "manifest.json"
    for number, path in enumerate(
        (
            protocol,
            core,
            runner,
            fetcher,
            requirements,
            lockfile,
            funded_simulator,
            test_a,
            test_b,
            manifest,
        )
    ):
        path.write_text(f"artifact {number}\n", encoding="utf-8")

    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "PROTOCOL", protocol)
    monkeypatch.setattr(gate, "CORE", core)
    monkeypatch.setattr(gate, "RUNNER", runner)
    monkeypatch.setattr(gate, "FETCHER", fetcher)
    monkeypatch.setattr(gate, "REQUIREMENTS", requirements)
    monkeypatch.setattr(gate, "LOCKFILE", lockfile)
    monkeypatch.setattr(gate, "FUNDED_SIMULATOR", funded_simulator)
    monkeypatch.setattr(gate, "TESTS", (test_a, test_b))

    hashes = gate._artifact_hashes(manifest)
    assert hashes == {
        "protocol_sha256": _sha256(protocol),
        "core_sha256": _sha256(core),
        "runner_sha256": _sha256(runner),
        "fetcher_sha256": _sha256(fetcher),
        "requirements_sha256": _sha256(requirements),
        "lockfile_sha256": _sha256(lockfile),
        "funded_simulator_sha256": _sha256(funded_simulator),
        "tests_sha256": gate._combined_sha256((test_a, test_b)),
        "manifest_sha256": _sha256(manifest),
    }


def test_select_is_writes_exactly_the_three_frozen_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "selection.json"
    snapshot = tmp_path / "snapshot.parquet"
    manifest_path = tmp_path / "manifest.json"
    chosen = _FakeResult("is-126")
    candidates = [
        {
            "lookback": lookback,
            "eligible": True,
            "metrics": _minimal_metrics(),
            "invariants": {"synthetic": True},
        }
        for lookback in (63, 126, 252)
    ]
    selection = {
        "selected_lookback": 126,
        "selected_result": chosen,
        "selected_config": gate.BacktestConfig(126),
        "forced_selection": False,
        "selection_rule": "frozen synthetic rule",
        "candidates": candidates,
    }
    monkeypatch.setattr(
        gate,
        "_load_is_only",
        lambda *_args: (
            pd.DataFrame({"synthetic": [1]}),
            {
                "snapshot": {"sha256": "a" * 64},
                "request": {"library_version": _runtime_versions()["yfinance"]},
            },
        ),
    )
    monkeypatch.setattr(gate, "select_is_candidate", lambda *_args, **_kwargs: selection)
    monkeypatch.setattr(gate, "_runtime_versions", _runtime_versions)
    monkeypatch.setattr(
        gate,
        "_artifact_hashes",
        lambda _path: {
            "protocol_sha256": "b" * 64,
            "core_sha256": "c" * 64,
            "runner_sha256": "d" * 64,
            "fetcher_sha256": "e" * 64,
            "requirements_sha256": "f" * 64,
            "lockfile_sha256": "1" * 64,
            "funded_simulator_sha256": "2" * 64,
            "tests_sha256": "3" * 64,
            "manifest_sha256": "4" * 64,
        },
    )

    payload = gate.select_is(snapshot, manifest_path, output)
    stored = json.loads(output.read_text(encoding="utf-8"))

    assert stored == payload
    assert payload["research_status"] == "IS_SELECTION_FROZEN_OOS_UNOPENED"
    assert payload["selected_lookback"] == 126
    assert payload["runtime_versions"] == _runtime_versions()
    assert len(payload["candidates"]) == 3
    assert [row["lookback"] for row in payload["candidates"]] == [63, 126, 252]


def _unlock_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Path], Path, Path, dict[Path, bytes]]:
    paths = {
        "protocol": tmp_path / "protocol.md",
        "core": tmp_path / "core.py",
        "runner": tmp_path / "runner.py",
        "fetcher": tmp_path / "fetcher.py",
        "requirements": tmp_path / "requirements.txt",
        "lockfile": tmp_path / "requirements.lock.txt",
        "funded_simulator": tmp_path / "funded_simulator.py",
        "test_a": tmp_path / "test_a.py",
        "test_b": tmp_path / "test_b.py",
        "manifest": tmp_path / "manifest.json",
        "selection": tmp_path / "selection.json",
    }
    for name, path in paths.items():
        if name != "selection":
            path.write_text(f"frozen {name}\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot.parquet"
    snapshot.write_bytes(b"synthetic frozen snapshot")

    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "PROTOCOL", paths["protocol"])
    monkeypatch.setattr(gate, "CORE", paths["core"])
    monkeypatch.setattr(gate, "RUNNER", paths["runner"])
    monkeypatch.setattr(gate, "FETCHER", paths["fetcher"])
    monkeypatch.setattr(gate, "REQUIREMENTS", paths["requirements"])
    monkeypatch.setattr(gate, "LOCKFILE", paths["lockfile"])
    monkeypatch.setattr(gate, "FUNDED_SIMULATOR", paths["funded_simulator"])
    monkeypatch.setattr(gate, "TESTS", (paths["test_a"], paths["test_b"]))
    monkeypatch.setattr(gate, "_runtime_versions", _runtime_versions)
    monkeypatch.setattr(
        gate,
        "_commit_chronology",
        lambda _path: {
            "selection_commit": "1" * 40,
            "head_commit": "2" * 40,
        },
    )
    selection = {
        "research_status": "IS_SELECTION_FROZEN_OOS_UNOPENED",
        "snapshot_sha256": _sha256(snapshot),
        "runtime_versions": _runtime_versions(),
        "artifacts": gate._artifact_hashes(paths["manifest"]),
        "selected_lookback": 126,
    }
    paths["selection"].write_text(json.dumps(selection), encoding="utf-8")
    committed = {path: path.read_bytes() for path in paths.values()}
    return paths, snapshot, paths["manifest"], committed


def test_commit_chronology_accepts_exactly_one_unchanged_selection_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection = tmp_path / "selection.json"
    selection_commit = "1" * 40
    head_commit = "2" * 40

    def fake_log(path: Path, *extra: str) -> list[str]:
        if path == selection:
            assert extra in ((), ("--diff-filter=A",))
            return [selection_commit]
        assert path in (gate.OOS_SEAL, gate.OOS_RESULT, gate.REPORT)
        assert extra == ("--all",)
        return []

    monkeypatch.setattr(gate, "_git_log_for_path", fake_log)
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=f"{head_commit}\n"),
    )

    assert gate._commit_chronology(selection) == {
        "selection_commit": selection_commit,
        "head_commit": head_commit,
    }


@pytest.mark.parametrize(
    ("changes", "additions"),
    [
        (["3" * 40, "2" * 40, "1" * 40], ["3" * 40, "1" * 40]),
        (["2" * 40, "1" * 40], ["1" * 40]),
    ],
    ids=("delete-and-readd", "modified-after-add"),
)
def test_commit_chronology_rejects_readded_or_changed_selection_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: list[str],
    additions: list[str],
) -> None:
    selection = tmp_path / "selection.json"

    def fake_log(path: Path, *extra: str) -> list[str]:
        assert path == selection
        return additions if extra == ("--diff-filter=A",) else changes

    monkeypatch.setattr(gate, "_git_log_for_path", fake_log)

    with pytest.raises(RuntimeError, match="not added once and left immutable"):
        gate._commit_chronology(selection)


def test_oos_unlock_verifies_runtime_and_commit_chronology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, snapshot, manifest, committed = _unlock_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(gate, "_git_bytes", lambda path: committed[path])

    selection = gate._verify_unlock(snapshot, manifest, paths["selection"])

    assert selection["runtime_versions"] == _runtime_versions()
    assert selection["_verified_chronology"] == {
        "selection_commit": "1" * 40,
        "head_commit": "2" * 40,
    }


def test_oos_unlock_rejects_runtime_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, snapshot, manifest, committed = _unlock_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(gate, "_git_bytes", lambda path: committed[path])
    changed = {**_runtime_versions(), "numpy": "different-runtime"}
    monkeypatch.setattr(gate, "_runtime_versions", lambda: changed)

    with pytest.raises(RuntimeError, match="runtime differs"):
        gate._verify_unlock(snapshot, manifest, paths["selection"])


@pytest.mark.parametrize(
    "target",
    [
        "selection",
        "core",
        "fetcher",
        "requirements",
        "lockfile",
        "funded_simulator",
        "test_a",
        "test_b",
        "manifest",
    ],
)
@pytest.mark.parametrize("mode", ["uncommitted", "modified"])
def test_oos_unlock_rejects_uncommitted_or_modified_research_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    mode: str,
) -> None:
    paths, snapshot, manifest, committed = _unlock_fixture(tmp_path, monkeypatch)
    target_path = paths[target]

    if mode == "modified":
        target_path.write_bytes(target_path.read_bytes() + b"working-tree change")

    def fake_git_bytes(path: Path) -> bytes:
        if mode == "uncommitted" and path == target_path:
            raise RuntimeError("required research artifact is not committed at HEAD")
        return committed[path]

    monkeypatch.setattr(gate, "_git_bytes", fake_git_bytes)
    expected = "not committed" if mode == "uncommitted" else "differs from committed HEAD"
    with pytest.raises(RuntimeError, match=expected):
        gate._verify_unlock(snapshot, manifest, paths["selection"])


@pytest.mark.parametrize(
    "target",
    [
        "core",
        "fetcher",
        "requirements",
        "lockfile",
        "funded_simulator",
        "test_a",
        "manifest",
    ],
)
def test_oos_unlock_rejects_newly_committed_artifact_not_bound_by_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    """A clean HEAD is insufficient when it is newer than the IS hash set."""

    paths, snapshot, manifest, committed = _unlock_fixture(tmp_path, monkeypatch)
    target_path = paths[target]
    target_path.write_bytes(target_path.read_bytes() + b"new committed contents")
    committed[target_path] = target_path.read_bytes()
    monkeypatch.setattr(gate, "_git_bytes", lambda path: committed[path])

    with pytest.raises(RuntimeError, match="research hashes differ"):
        gate._verify_unlock(snapshot, manifest, paths["selection"])


def test_run_oos_uses_only_the_exact_base_and_stress_cost_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _FakeResult()
    artifacts = {
        "protocol_sha256": "a" * 64,
        "core_sha256": "b" * 64,
        "runner_sha256": "c" * 64,
        "fetcher_sha256": "d" * 64,
        "requirements_sha256": "e" * 64,
        "lockfile_sha256": "f" * 64,
        "funded_simulator_sha256": "1" * 64,
        "tests_sha256": "2" * 64,
        "manifest_sha256": "3" * 64,
    }
    selection = {
        "selected_lookback": 126,
        "selected_run_fingerprint": gate._result_fingerprint(result),
        "candidate_table_fingerprint": hashlib.sha256(
            gate.canonical_json([])
        ).hexdigest(),
        "artifacts": artifacts,
        "_verified_chronology": {
            "selection_commit": "4" * 40,
            "head_commit": "5" * 40,
        },
    }
    calls: list[tuple[str, str, gate.BacktestConfig]] = []

    def fake_run(_panel, start: str, end: str, config: gate.BacktestConfig):
        calls.append((start, end, config))
        return result

    monkeypatch.setattr(gate, "_verify_unlock", lambda *_args: selection)
    monkeypatch.setattr(gate, "_runtime_versions", _runtime_versions)
    monkeypatch.setattr(
        gate,
        "_load_is_only",
        lambda *_args: (object(), {"snapshot": {"sha256": "a" * 64}}),
    )
    monkeypatch.setattr(
        gate,
        "select_is_candidate",
        lambda *_args, **_kwargs: {
            "selected_lookback": 126,
            "candidates": [],
            "selected_result": result,
        },
    )
    monkeypatch.setattr(
        gate,
        "_load_full",
        lambda *_args: (object(), {"snapshot": {"sha256": "a" * 64}}),
    )
    monkeypatch.setattr(gate, "run_backtest", fake_run)
    monkeypatch.setattr(gate, "_order_invariance", lambda *_args: True)
    monkeypatch.setattr(gate, "funded_replay", lambda _result: object())
    monkeypatch.setattr(gate, "_funded_dict", lambda _result: {"synthetic": True})
    monkeypatch.setattr(
        gate,
        "evaluate_final_gate",
        lambda *_args: {"status": "PASS", "checks": {}},
    )
    monkeypatch.setattr(gate, "_markdown_report", lambda _payload: "synthetic report\n")
    monkeypatch.setattr(gate, "OOS_SEAL", tmp_path / "oos-opened.json")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    snapshot = tmp_path / "snapshot.parquet"
    snapshot.write_bytes(b"synthetic snapshot")
    selection_path = tmp_path / "selection.json"
    selection_path.write_text("{}", encoding="utf-8")

    payload = gate.run_oos(
        snapshot,
        manifest,
        selection_path,
        tmp_path / "result.json",
        tmp_path / "report.md",
    )

    assert [(start, end) for start, end, _config in calls] == [
        (gate.OOS_START, gate.OOS_END),
        (gate.OOS_START, gate.OOS_END),
    ]
    base_cell, stress_cell = [config for _start, _end, config in calls]
    assert base_cell == gate.BacktestConfig(126, fee_bps=5.0, stop_slippage_bps=0.0)
    assert stress_cell == gate.BacktestConfig(126, fee_bps=10.0, stop_slippage_bps=25.0)
    assert base_cell.risk_per_trade == 0.0035
    assert base_cell.aggregate_risk_cap == 0.025
    assert base_cell.bull_gross_cap == 0.50
    assert base_cell.bear_gross_cap == 0.20
    assert payload["selected_lookback"] == 126
    assert payload["evidence"]["selection_commit"] == "4" * 40
    assert payload["evidence"]["head_commit"] == "5" * 40
    assert payload["evidence"]["artifact_hashes"] == artifacts
    assert payload["evidence"]["runtime_versions"] == _runtime_versions()


def test_run_oos_reselects_is_before_any_holdout_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection = {
        "selected_lookback": 63,
        "selected_run_fingerprint": "irrelevant",
        "candidate_table_fingerprint": "irrelevant",
        "artifacts": {"synthetic": "frozen"},
        "_verified_chronology": {
            "selection_commit": "6" * 40,
            "head_commit": "7" * 40,
        },
    }
    monkeypatch.setattr(gate, "_verify_unlock", lambda *_args: selection)
    monkeypatch.setattr(gate, "_runtime_versions", _runtime_versions)
    monkeypatch.setattr(gate, "_load_is_only", lambda *_args: (object(), {}))
    monkeypatch.setattr(
        gate,
        "select_is_candidate",
        lambda *_args, **_kwargs: {
            "selected_lookback": 126,
            "candidates": [],
            "selected_result": _FakeResult(),
        },
    )
    monkeypatch.setattr(gate, "OOS_SEAL", tmp_path / "oos-opened.json")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("holdout must stay sealed until IS selection reproduces")

    monkeypatch.setattr(gate, "_load_full", forbidden)
    with pytest.raises(RuntimeError, match="fails frozen IS reselection"):
        gate.run_oos(
            tmp_path / "snapshot.parquet",
            tmp_path / "manifest.json",
            tmp_path / "selection.json",
            tmp_path / "result.json",
            tmp_path / "report.md",
        )


def test_global_oos_seal_blocks_new_alternate_output_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seal = tmp_path / "oos-opened.json"
    seal.write_text("already opened\n", encoding="utf-8")
    monkeypatch.setattr(gate, "OOS_SEAL", seal)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("global seal must refuse before unlock or data access")

    monkeypatch.setattr(gate, "_verify_unlock", forbidden)
    monkeypatch.setattr(gate, "_load_is_only", forbidden)
    monkeypatch.setattr(gate, "_load_full", forbidden)
    with pytest.raises(FileExistsError, match="one-shot"):
        gate.run_oos(
            tmp_path / "snapshot.parquet",
            tmp_path / "manifest.json",
            tmp_path / "new-selection.json",
            tmp_path / "different-result.json",
            tmp_path / "different-report.md",
        )


def test_strict_json_replaces_every_nonfinite_number_with_null(tmp_path: Path) -> None:
    output = tmp_path / "strict.json"
    gate._write_json(
        output,
        {
            "nan": np.float64(np.nan),
            "positive_infinity": np.float64(np.inf),
            "negative_infinity": float("-inf"),
            "session": date(2026, 9, 3),
            "timestamp": datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc),
            "nested": [np.int64(7), {"finite": np.float64(1.25)}],
        },
    )
    raw = output.read_text(encoding="utf-8")

    assert "NaN" not in raw
    assert "Infinity" not in raw
    assert json.loads(raw) == {
        "nan": None,
        "negative_infinity": None,
        "nested": [7, {"finite": 1.25}],
        "positive_infinity": None,
        "session": "2026-09-03",
        "timestamp": "2026-09-03T20:00:00+00:00",
    }


@pytest.mark.parametrize("existing", ["output", "report"])
def test_one_shot_oos_refuses_existing_output_before_unlock_or_data_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: str,
) -> None:
    output = tmp_path / "result.json"
    report = tmp_path / "report.md"
    (output if existing == "output" else report).write_text(
        "already opened\n", encoding="utf-8"
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("one-shot refusal must happen before unlock or data access")

    monkeypatch.setattr(gate, "_verify_unlock", forbidden)
    monkeypatch.setattr(gate, "_load_full", forbidden)
    monkeypatch.setattr(gate, "OOS_SEAL", tmp_path / "oos-opened.json")

    with pytest.raises(FileExistsError, match="one-shot"):
        gate.run_oos(
            tmp_path / "snapshot.parquet",
            tmp_path / "manifest.json",
            tmp_path / "selection.json",
            output,
            report,
        )


def test_report_explicitly_marks_daily_data_and_funded_claim_limitations() -> None:
    payload = {
        "gate": {"status": "PASS", "checks": {"synthetic_check": True}},
        "oos_base": {"metrics": _minimal_metrics()},
        "oos_stress": {"metrics": _minimal_metrics()},
    }

    report = gate._markdown_report(payload)

    assert "retrospective, daily-OHLC research screen" in report
    assert "not proof of exact" in report
    assert "does not authorize live or funded deployment" in report
    assert "CE(S)T midnight mark" in report
    assert "broker-native forward paper run is required" in report
