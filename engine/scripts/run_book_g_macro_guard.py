#!/usr/bin/env python3
"""Two-stage, hash-bound research gate for Book G.

``select-is`` reads only rows before 2020 and writes the frozen selection.
``run-oos`` refuses to parse the holdout unless every research artifact is
byte-identical to its committed copy.  The historical block is one-shot for
this implementation and is never represented as genuinely unknown history.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np
import pandas as pd
import exchange_calendars as xcals


ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_DIR.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.research.book_g_macro_guard import (  # noqa: E402
    ACCOUNT_USD,
    ALL_SYMBOLS,
    BacktestConfig,
    canonical_json,
    evaluate_final_gate,
    funded_replay,
    run_backtest,
    select_is_candidate,
    validate_panel,
)


PROTOCOL = ENGINE_DIR / "data_store" / "book_g_macro_guard_prereg_2026-09-04.md"
SNAPSHOT = ENGINE_DIR / "data_store" / "validation" / "book_g_inputs_2026-09-04.parquet"
MANIFEST = SNAPSHOT.with_suffix(".manifest.json")
SELECTION = ENGINE_DIR / "data_store" / "validation" / "book_g_is_selection_2026-09-04.json"
OOS_RESULT = ENGINE_DIR / "data_store" / "validation" / "book_g_oos_result_2026-09-04.json"
REPORT = ENGINE_DIR / "data_store" / "validation" / "book_g_oos_report_2026-09-04.md"
OOS_SEAL = ENGINE_DIR / "data_store" / "validation" / "book_g_oos_opened_2026-09-04.json"
CORE = ENGINE_DIR / "apex_quant" / "research" / "book_g_macro_guard.py"
RUNNER = Path(__file__).resolve()
FETCHER = ENGINE_DIR / "scripts" / "fetch_book_g_snapshot.py"
REQUIREMENTS = ENGINE_DIR / "requirements.txt"
LOCKFILE = ENGINE_DIR / "requirements.lock.txt"
FUNDED_SIMULATOR = ENGINE_DIR / "apex_quant" / "validation" / "funded_simulator.py"
TESTS = (
    ENGINE_DIR / "tests" / "test_book_g_macro_guard.py",
    ENGINE_DIR / "tests" / "test_fetch_book_g_snapshot.py",
    ENGINE_DIR / "tests" / "test_run_book_g_macro_guard.py",
)
IS_START = "2015-01-01"
IS_END = "2019-12-31"
OOS_START = "2020-01-01"
OOS_END = "2026-09-03"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _combined_sha256(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _strict_value(value: Any) -> Any:
    if is_dataclass(value):
        return _strict_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _strict_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strict_value(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, payload: Any) -> None:
    _atomic_text(
        path,
        json.dumps(_strict_value(payload), indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def _runtime_versions() -> dict[str, str]:
    packages = ("numpy", "pandas", "pyarrow", "exchange-calendars", "yfinance")
    versions: dict[str, str] = {"python": sys.version.split()[0]}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(f"required Book G runtime package is missing: {package}") from exc
    return versions


def _expected_full_sessions() -> pd.DatetimeIndex:
    sessions = xcals.get_calendar("XNYS").sessions_in_range(
        pd.Timestamp("2014-01-01"), pd.Timestamp(OOS_END)
    )
    index = pd.DatetimeIndex(sessions)
    if index.tz is not None:
        index = index.tz_localize(None)
    return index.normalize()


def _manifest(snapshot: Path, manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise RuntimeError("Book G manifest has the wrong schema version")
    if payload.get("kind") != "book_g_yfinance_adjusted_ohlc_snapshot":
        raise RuntimeError("Book G manifest has the wrong snapshot kind")
    expected = payload.get("snapshot", {}).get("sha256")
    if expected != _sha256(snapshot):
        raise RuntimeError("Book G snapshot hash does not match its manifest")
    if payload.get("protocol_sha256") != _sha256(PROTOCOL):
        raise RuntimeError("Book G protocol hash does not match its manifest")
    request = payload.get("request", {})
    exact = {
        "library": "yfinance",
        "download_mode": "one_symbol_per_call_in_declared_order",
        "symbols": list(ALL_SYMBOLS),
        "start": "2014-01-01",
        "end_exclusive": "2026-09-04",
        "interval": "1d",
        "auto_adjust": True,
        "actions": False,
        "repair": False,
        "threads": False,
        "progress": False,
    }
    for key, expected_value in exact.items():
        if request.get(key) != expected_value:
            raise RuntimeError(f"Book G manifest has the wrong frozen request field: {key}")
    library_version = request.get("library_version")
    if not isinstance(library_version, str) or not library_version.strip():
        raise RuntimeError("Book G manifest lacks a yfinance library version")

    sessions = _expected_full_sessions()
    first_date = sessions[0].date().isoformat()
    last_date = sessions[-1].date().isoformat()
    calendar = payload.get("calendar", {})
    exact_calendar = {
        "name": "XNYS",
        "expected_first_session": first_date,
        "expected_last_session": last_date,
        "expected_sessions": len(sessions),
        "missing_rows_allowed": 0,
        "forward_fill": False,
    }
    for key, expected_value in exact_calendar.items():
        if calendar.get(key) != expected_value:
            raise RuntimeError(f"Book G manifest has the wrong calendar field: {key}")
    snapshot_meta = payload.get("snapshot", {})
    if snapshot_meta.get("columns") != ["date", "symbol", "open", "high", "low", "close"]:
        raise RuntimeError("Book G manifest has the wrong snapshot columns")
    expected_rows = len(sessions) * len(ALL_SYMBOLS)
    if int(snapshot_meta.get("rows", -1)) != expected_rows:
        raise RuntimeError("Book G manifest has the wrong snapshot row count")
    if int(snapshot_meta.get("bytes", -1)) != snapshot.stat().st_size:
        raise RuntimeError("Book G manifest has the wrong snapshot byte count")
    row_counts = payload.get("row_counts", {})
    coverage = payload.get("coverage", {})
    if set(row_counts) != set(ALL_SYMBOLS) or set(coverage) != set(ALL_SYMBOLS):
        raise RuntimeError("Book G manifest has incomplete symbol evidence")
    for symbol in ALL_SYMBOLS:
        if int(row_counts.get(symbol, -1)) != len(sessions):
            raise RuntimeError(f"Book G manifest has the wrong row count for {symbol}")
        expected_coverage = {
            "first_date": first_date,
            "last_date": last_date,
            "rows": len(sessions),
        }
        if coverage.get(symbol) != expected_coverage:
            raise RuntimeError(f"Book G manifest has the wrong coverage for {symbol}")
    return payload


def _assert_exact_frame_coverage(
    frame: pd.DataFrame, expected: pd.DatetimeIndex, *, label: str
) -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="raise"))
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    unique = dates.normalize().unique().sort_values()
    if not unique.equals(expected):
        raise RuntimeError(f"{label} has the wrong exact XNYS date coverage")
    counts = frame.groupby("symbol", sort=True)["date"].nunique()
    if set(counts.index.astype(str)) != set(ALL_SYMBOLS) or not (counts == len(expected)).all():
        raise RuntimeError(f"{label} has the wrong per-symbol date coverage")


def _load_is_only(snapshot: Path, manifest_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = _manifest(snapshot, manifest_path)
    # PyArrow predicate pushdown prevents IS selection from loading a 2020+
    # row into the process.  The raw file hash is checked without parsing it.
    frame = pd.read_parquet(
        snapshot,
        filters=[("date", "<", pd.Timestamp("2020-01-01"))],
    )
    if frame.empty or pd.to_datetime(frame["date"]).max() >= pd.Timestamp("2020-01-01"):
        raise RuntimeError("IS loader crossed the frozen 2020 boundary")
    validate_panel(frame)
    is_sessions = _expected_full_sessions()
    is_sessions = is_sessions[is_sessions < pd.Timestamp("2020-01-01")]
    _assert_exact_frame_coverage(frame, is_sessions, label="Book G IS panel")
    return frame, manifest


def _load_full(snapshot: Path, manifest_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = _manifest(snapshot, manifest_path)
    frame = pd.read_parquet(snapshot)
    validate_panel(frame)
    _assert_exact_frame_coverage(
        frame, _expected_full_sessions(), label="Book G full panel"
    )
    return frame, manifest


def _git_bytes(path: Path) -> bytes:
    relative = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    process = subprocess.run(
        ["git", "show", f"HEAD:{relative}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(f"required research artifact is not committed at HEAD: {relative}")
    return process.stdout


def _assert_committed_identical(path: Path) -> None:
    if _git_bytes(path) != path.read_bytes():
        raise RuntimeError(f"research artifact differs from committed HEAD: {path.relative_to(REPO_ROOT)}")


def _git_log_for_path(path: Path, *extra: str) -> list[str]:
    relative = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    process = subprocess.run(
        ["git", "log", *extra, "--format=%H", "--", relative],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(f"cannot inspect Git chronology for {relative}")
    return [line.strip() for line in process.stdout.splitlines() if line.strip()]


def _commit_chronology(selection_path: Path) -> dict[str, str]:
    changes = _git_log_for_path(selection_path)
    additions = _git_log_for_path(selection_path, "--diff-filter=A")
    if (
        len(changes) != 1
        or len(additions) != 1
        or changes[0] != additions[0]
    ):
        raise RuntimeError("Book G selection was not added once and left immutable")
    for artifact in (OOS_SEAL, OOS_RESULT, REPORT):
        if _git_log_for_path(artifact, "--all"):
            raise RuntimeError("a Book G OOS artifact already exists in Git history")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {"selection_commit": additions[0], "head_commit": head}


def _artifact_hashes(manifest_path: Path) -> dict[str, Any]:
    return {
        "protocol_sha256": _sha256(PROTOCOL),
        "core_sha256": _sha256(CORE),
        "runner_sha256": _sha256(RUNNER),
        "fetcher_sha256": _sha256(FETCHER),
        "requirements_sha256": _sha256(REQUIREMENTS),
        "lockfile_sha256": _sha256(LOCKFILE),
        "funded_simulator_sha256": _sha256(FUNDED_SIMULATOR),
        "tests_sha256": _combined_sha256(TESTS),
        "manifest_sha256": _sha256(manifest_path),
    }


def select_is(snapshot: Path, manifest_path: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"selection artifact already exists: {output}")
    panel, manifest = _load_is_only(snapshot, manifest_path)
    runtime_versions = _runtime_versions()
    if manifest["request"]["library_version"] != runtime_versions["yfinance"]:
        raise RuntimeError("snapshot yfinance version differs from the selection runtime")
    selection = select_is_candidate(panel, start=IS_START, end=IS_END)
    result = selection["selected_result"]
    payload = {
        "schema_version": 1,
        "research_status": "IS_SELECTION_FROZEN_OOS_UNOPENED",
        "claim_ceiling": "strategy-specific sealed retrospective holdout; not globally blind",
        "is_period": {"start": IS_START, "end": IS_END},
        "oos_period_locked": {"start": OOS_START, "end": OOS_END},
        "selected_lookback": int(selection["selected_lookback"]),
        "selected_config": BacktestConfig(int(selection["selected_lookback"])).to_dict(),
        "forced_selection": bool(selection["forced_selection"]),
        "selection_rule": selection["selection_rule"],
        "candidates": selection["candidates"],
        "candidate_table_fingerprint": hashlib.sha256(
            canonical_json(selection["candidates"])
        ).hexdigest(),
        "selected_run_fingerprint": hashlib.sha256(canonical_json(result.to_dict())).hexdigest(),
        "snapshot_sha256": manifest["snapshot"]["sha256"],
        "runtime_versions": runtime_versions,
        "artifacts": _artifact_hashes(manifest_path),
    }
    _write_json(output, payload)
    return payload


def _verify_unlock(
    snapshot: Path, manifest_path: Path, selection_path: Path
) -> dict[str, Any]:
    for path in (
        PROTOCOL,
        CORE,
        RUNNER,
        FETCHER,
        REQUIREMENTS,
        LOCKFILE,
        FUNDED_SIMULATOR,
        *TESTS,
        manifest_path,
        selection_path,
    ):
        _assert_committed_identical(path)
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    hashes = _artifact_hashes(manifest_path)
    if selection.get("artifacts") != hashes:
        raise RuntimeError("current Book G research hashes differ from the frozen selection")
    if selection.get("runtime_versions") != _runtime_versions():
        raise RuntimeError("current Book G runtime differs from the frozen selection")
    if selection.get("snapshot_sha256") != _sha256(snapshot):
        raise RuntimeError("current Book G snapshot differs from the frozen selection")
    if selection.get("research_status") != "IS_SELECTION_FROZEN_OOS_UNOPENED":
        raise RuntimeError("selection artifact does not authorize the one-shot OOS run")
    selection["_verified_chronology"] = _commit_chronology(selection_path)
    return selection


def _result_fingerprint(result: Any) -> str:
    return hashlib.sha256(canonical_json(result.to_dict())).hexdigest()


def _funded_dict(result: Any) -> dict[str, Any]:
    return _strict_value(asdict(result))


def _order_invariance(panel: pd.DataFrame, start: str, end: str, config: BacktestConfig, expected: Any) -> bool:
    shuffled = panel.sample(frac=1.0, random_state=20260904).reset_index(drop=True)
    rerun = run_backtest(shuffled, start, end, config)
    return _result_fingerprint(rerun) == _result_fingerprint(expected)


def _markdown_report(payload: dict[str, Any]) -> str:
    def pct(value: Any) -> str:
        return "n/a" if value is None else f"{100.0 * float(value):.2f}%"

    def num(value: Any) -> str:
        return "n/a" if value is None else f"{float(value):.2f}"

    base = payload["oos_base"]["metrics"]
    stress = payload["oos_stress"]["metrics"]
    lines = [
        "# Book G Macro Guard — Frozen Historical Audit",
        "",
        f"**Verdict:** `{payload['gate']['status']}`",
        "",
        "> This is a retrospective, daily-OHLC research screen. It is not proof of exact",
        "> FTMO compliance and does not authorize live or funded deployment.",
        "",
        "| Metric | OOS base | OOS stress |",
        "|---|---:|---:|",
        f"| CAGR | {pct(base['cagr'])} | {pct(stress['cagr'])} |",
        f"| Average monthly profit | ${num(base['avg_monthly_profit'])} | ${num(stress['avg_monthly_profit'])} |",
        f"| Sharpe | {num(base['sharpe'])} | {num(stress['sharpe'])} |",
        f"| Profit factor | {num(base['profit_factor'])} | {num(stress['profit_factor'])} |",
        f"| Max drawdown | {pct(base['max_drawdown'])} | {pct(stress['max_drawdown'])} |",
        f"| Worst regular-session proxy day | {pct(base['worst_day'])} | {pct(stress['worst_day'])} |",
        f"| Win rate | {pct(base['win_rate'])} | {pct(stress['win_rate'])} |",
        f"| Closed trades | {int(base['trades'])} | {int(stress['trades'])} |",
        "",
        "## Year-by-year OOS base returns",
        "",
        "| Year | Return |",
        "|---|---:|",
    ]
    for year, value in base["annual_returns"].items():
        label = f"{year} YTD" if str(year) == "2026" else str(year)
        lines.append(f"| {label} | {pct(value)} |")
    lines.extend(
        [
            "",
            "## Gate checks",
            "",
            *[f"- {'PASS' if passed else 'FAIL'} — `{name}`" for name, passed in payload["gate"]["checks"].items()],
            "",
            "## Funded-account evidence limit",
            "",
            "The replay uses simultaneous daily lows as a conservative regular-session proxy.",
            "Yahoo daily bars do not contain the CE(S)T midnight mark, executable bid/ask",
            "path, widened spreads, swaps, rejection latency, or broker stop acknowledgements.",
            "An unchanged broker-native forward paper run is required before any funded claim.",
            "",
        ]
    )
    return "\n".join(lines)


def run_oos(
    snapshot: Path,
    manifest_path: Path,
    selection_path: Path,
    output: Path,
    report: Path,
) -> dict[str, Any]:
    resolved = {output.resolve(), report.resolve(), OOS_SEAL.resolve()}
    if len(resolved) != 3:
        raise ValueError("Book G output, report and global OOS seal paths must differ")
    protected = {
        snapshot.resolve(),
        manifest_path.resolve(),
        selection_path.resolve(),
        PROTOCOL.resolve(),
        CORE.resolve(),
        RUNNER.resolve(),
        FETCHER.resolve(),
        REQUIREMENTS.resolve(),
        LOCKFILE.resolve(),
        FUNDED_SIMULATOR.resolve(),
    }
    if output.resolve() in protected or report.resolve() in protected:
        raise ValueError("Book G output paths cannot overwrite frozen research inputs")
    if output.exists() or report.exists() or OOS_SEAL.exists():
        raise FileExistsError("Book G OOS artifact already exists; the holdout is one-shot")
    selection = _verify_unlock(snapshot, manifest_path, selection_path)
    # Reproduce the entire three-candidate IS selection before parsing a single
    # holdout row.  A committed but hand-crafted selection file cannot unlock a
    # preferred horizon that the frozen tie-break would not have chosen.
    is_panel, _ = _load_is_only(snapshot, manifest_path)
    reproduced = select_is_candidate(is_panel, start=IS_START, end=IS_END)
    lookback = int(selection["selected_lookback"])
    if int(reproduced["selected_lookback"]) != lookback:
        raise RuntimeError("committed Book G lookback fails frozen IS reselection")
    reproduced_candidates = reproduced["candidates"]
    reproduced_table_hash = hashlib.sha256(
        canonical_json(reproduced_candidates)
    ).hexdigest()
    if selection.get("candidate_table_fingerprint") != reproduced_table_hash:
        raise RuntimeError("committed Book G candidate table fails frozen IS reproduction")
    base_config = BacktestConfig(lookback, fee_bps=5.0, stop_slippage_bps=0.0)
    stress_config = BacktestConfig(lookback, fee_bps=10.0, stop_slippage_bps=25.0)
    is_result = reproduced["selected_result"]
    if _result_fingerprint(is_result) != selection["selected_run_fingerprint"]:
        raise RuntimeError("recomputed IS outcome differs from the committed selection")
    OOS_SEAL.parent.mkdir(parents=True, exist_ok=True)
    seal_payload = {
        "state": "OOS_OPENED",
        "opened_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": _sha256(snapshot),
        "selection_sha256": _sha256(selection_path),
        "selection_commit": selection["_verified_chronology"]["selection_commit"],
        "head_commit": selection["_verified_chronology"]["head_commit"],
        "runtime_versions": _runtime_versions(),
        "claim": "one-shot strategy-specific retrospective holdout",
    }
    with OOS_SEAL.open("x", encoding="utf-8") as handle:
        json.dump(seal_payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    panel, manifest = _load_full(snapshot, manifest_path)
    oos_base = run_backtest(panel, OOS_START, OOS_END, base_config)
    oos_stress = run_backtest(panel, OOS_START, OOS_END, stress_config)
    if not _order_invariance(panel, OOS_START, OOS_END, base_config, oos_base):
        raise RuntimeError("Book G base OOS result failed input-order invariance")
    if not _order_invariance(panel, OOS_START, OOS_END, stress_config, oos_stress):
        raise RuntimeError("Book G stress OOS result failed input-order invariance")
    funded_base = funded_replay(oos_base)
    funded_stress = funded_replay(oos_stress)
    gate = evaluate_final_gate(is_result, oos_base, oos_stress, funded_base, funded_stress)
    payload = {
        "schema_version": 1,
        "status": gate["status"],
        "selected_lookback": lookback,
        "periods": {
            "is": {"start": IS_START, "end": IS_END},
            "oos": {"start": OOS_START, "end": OOS_END},
        },
        "data": {
            "snapshot_sha256": manifest["snapshot"]["sha256"],
            "manifest_sha256": _sha256(manifest_path),
            "adjustment_policy": "yfinance auto_adjust=True total-return-adjusted OHLC proxy",
        },
        "is_selected": is_result.to_dict(),
        "oos_base": oos_base.to_dict(),
        "oos_stress": oos_stress.to_dict(),
        "funded_base": _funded_dict(funded_base),
        "funded_stress": _funded_dict(funded_stress),
        "gate": gate,
        "limitations": [
            "2020-present is strategy-specific sealed retrospective OOS, not globally blind history.",
            "Daily adjusted Yahoo OHLC is a research proxy, not executable broker bid/ask evidence.",
            "Exact FTMO CE(S)T daily-loss compliance is data-blocked without timestamped broker marks.",
            "Historical passage can qualify only an unchanged forward paper trial.",
        ],
        "evidence": {
            "selection_sha256": _sha256(selection_path),
            "selection_commit": selection["_verified_chronology"]["selection_commit"],
            "head_commit": selection["_verified_chronology"]["head_commit"],
            "artifact_hashes": selection["artifacts"],
            "runtime_versions": _runtime_versions(),
            "oos_seal_sha256": _sha256(OOS_SEAL),
        },
    }
    report_text = _markdown_report(payload)
    payload["evidence"]["report_sha256"] = hashlib.sha256(
        report_text.encode("utf-8")
    ).hexdigest()
    _write_json(output, payload)
    _atomic_text(report, report_text)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the frozen two-stage Book G gate")
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select-is")
    select.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    select.add_argument("--manifest", type=Path, default=MANIFEST)
    select.add_argument("--output", type=Path, default=SELECTION)
    oos = sub.add_parser("run-oos")
    oos.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    oos.add_argument("--manifest", type=Path, default=MANIFEST)
    oos.add_argument("--selection", type=Path, default=SELECTION)
    oos.add_argument("--output", type=Path, default=OOS_RESULT)
    oos.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    if args.command == "select-is":
        payload = select_is(args.snapshot, args.manifest, args.output)
        print(json.dumps({"selected_lookback": payload["selected_lookback"], "output": str(args.output)}))
        return 0
    payload = run_oos(args.snapshot, args.manifest, args.selection, args.output, args.report)
    print(json.dumps({"status": payload["status"], "output": str(args.output), "report": str(args.report)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
