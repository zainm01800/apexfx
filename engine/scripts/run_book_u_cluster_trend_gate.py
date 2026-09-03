#!/usr/bin/env python3
"""Run the frozen Book U cluster-balanced trend research gate.

The protocol predates the implementation and lives at
``data_store/book_u_cluster_trend_prereg_2026-09-03.md``.  This runner is
deliberately isolated from every live/paper book.  It reads the frozen
adjusted-ETF snapshot, observes the shared trial ledger read-only for spent
trial accounting, and can consume an optional explicit Sharpe-history artifact.

Daily adjusted ETF OHLC can support a conservative research and shadow-paper
decision only.  It cannot establish executable FTMO CFD fills, stops, margin,
or funded-account readiness.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import gzip
import hashlib
from itertools import combinations
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

import exchange_calendars as xcals
import numpy as np
import pandas as pd


ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = ENGINE_DIR.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.research.book_u_cluster_trend import (  # noqa: E402
    BookURun,
    BookUSpec,
    CLUSTERS,
    USD_ETF_UNIVERSE,
    common_book_u_panel,
    run_book_u,
)
from apex_quant.validation.funded_simulator import (  # noqa: E402
    DayRecord,
    FundedRules,
    firm_session_key,
    replay_funded_rules,
)
from apex_quant.validation.metrics import (  # noqa: E402
    deflated_sharpe_ratio,
    sharpe_ratio,
)


PROTOCOL_DATE = "2026-09-03"
PROTOCOL = ENGINE_DIR / "data_store" / "book_u_cluster_trend_prereg_2026-09-03.md"
SNAPSHOT = ENGINE_DIR / "data_store" / "validation" / "book_u_inputs_2026-09-03.parquet"
MANIFEST = (
    ENGINE_DIR / "data_store" / "validation" / "book_u_inputs_2026-09-03.manifest.json"
)
DEFAULT_JSON = (
    ENGINE_DIR / "data_store" / "validation" / "book_u_cluster_trend_gate_2026-09-03.json"
)
DEFAULT_REPORT = (
    ENGINE_DIR / "data_store" / "validation" / "book_u_cluster_trend_gate_2026-09-03.md"
)
DEFAULT_BOOK_U_LEDGER = (
    ENGINE_DIR
    / "data_store"
    / "validation"
    / "book_u_cluster_trend_trial_ledger_2026-09-03.json"
)
SHARED_TRIAL_LEDGER = ENGINE_DIR / "data_store" / "validation" / "trial_ledger.json"

# This digest was frozen from the committed protocol, not derived from the
# mutable snapshot manifest at run time.
PROTOCOL_SHA256 = "bcf4c94cdd2c1ecf0afa42c558a43c8b29bf1706dfcb7883fcc4b876a1f700cc"
ACCOUNT = 100_000.0
PERIODS_PER_YEAR = 252

MAIN_SEGMENTS: tuple[tuple[str, str, str | None], ...] = (
    ("sealed_historical_robustness", "2010-01-04", "2015-12-31"),
    ("development", "2016-01-04", "2022-12-30"),
    ("retrospective_validation", "2023-01-03", "2024-12-31"),
    ("known_data_replication", "2025-01-02", None),
    ("full_available_history", "2010-01-04", None),
)
TWO_YEAR_BLOCKS: tuple[tuple[str, str, str], ...] = (
    ("2016_2017", "2016-01-04", "2017-12-29"),
    ("2018_2019", "2018-01-02", "2019-12-31"),
    ("2020_2021", "2020-01-02", "2021-12-31"),
    ("2022_2023", "2022-01-03", "2023-12-29"),
    ("2024_2025", "2024-01-02", "2025-12-31"),
)
RISK_CELLS: dict[str, tuple[float, float]] = {
    "U075": (0.0075, 0.0225),
    "U085": (0.0085, 0.0255),
    "U100": (0.0100, 0.0300),
}
ALLOWED_RESEARCH_STATUSES = (
    "NO_RESEARCH_CANDIDATE",
    "DATA_BLOCKED",
    "SHADOW_ELIGIBLE",
)
STATUS_CEILING = "SHADOW_ELIGIBLE"
SNAPSHOT_END_EXCLUSIVE = "2026-09-03"


RunFunction = Callable[..., BookURun]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _valid_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _utc(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return (
        timestamp.tz_localize("UTC")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("UTC")
    )


def _date(value: Any) -> str:
    return _utc(value).strftime("%Y-%m-%d")


def _jsonable(value: Any) -> Any:
    """Return deterministic strict-JSON data, mapping non-finite values to null."""

    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _strict_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        _jsonable(payload),
        indent=2,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ": "),
    ) + "\n"


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_frozen_panel(
    snapshot: Path = SNAPSHOT,
    manifest_path: Path = MANIFEST,
    protocol: Path = PROTOCOL,
    *,
    expected_protocol_sha256: str = PROTOCOL_SHA256,
    repo_root: Path = REPO_DIR,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Verify immutable input metadata, then load the exact common USD panel."""

    if not protocol.is_file():
        raise FileNotFoundError(f"missing frozen Book U protocol: {protocol}")
    if not snapshot.is_file():
        raise FileNotFoundError(f"missing frozen Book U snapshot: {snapshot}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing frozen Book U manifest: {manifest_path}")

    protocol_hash = _sha256(protocol)
    if protocol_hash != expected_protocol_sha256:
        raise RuntimeError("Book U frozen protocol hash mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise RuntimeError("Book U snapshot manifest schema mismatch")
    if manifest.get("kind") != "book_u_adjusted_usd_etf_snapshot":
        raise RuntimeError("Book U snapshot manifest kind mismatch")
    if manifest.get("protocol_sha256") != protocol_hash:
        raise RuntimeError("Book U manifest does not bind the frozen protocol")
    manifest_snapshot_hash = manifest.get("snapshot_sha256")
    if not _valid_digest(manifest_snapshot_hash):
        raise RuntimeError("Book U manifest has no valid snapshot digest")
    snapshot_hash = _sha256(snapshot)
    if snapshot_hash != manifest_snapshot_hash:
        raise RuntimeError("Book U frozen snapshot hash mismatch")
    if list(manifest.get("instruments") or []) != list(USD_ETF_UNIVERSE):
        raise RuntimeError("Book U manifest universe differs from the frozen protocol")
    download = manifest.get("download") or {}
    if download.get("requested_end_exclusive") != SNAPSHOT_END_EXCLUSIVE:
        raise RuntimeError(
            "Book U snapshot must end exclusively at the frozen 2026-09-03 boundary"
        )
    if set((manifest.get("sources") or {}).keys()) != set(USD_ETF_UNIVERSE):
        raise RuntimeError("Book U manifest does not record every raw vendor source")
    repository = repo_root.expanduser().resolve()
    verified_raw: dict[str, dict[str, Any]] = {}
    for instrument, source in (manifest.get("sources") or {}).items():
        response_hash = source.get("raw_response_sha256")
        gzip_hash = source.get("raw_gzip_sha256")
        relative_name = source.get("raw_gzip_path")
        if not _valid_digest(response_hash) or not _valid_digest(gzip_hash):
            raise RuntimeError(f"Book U manifest lacks valid raw digests for {instrument}")
        if not isinstance(relative_name, str) or not relative_name:
            raise RuntimeError(f"Book U manifest lacks the raw gzip path for {instrument}")
        relative_path = Path(relative_name)
        if relative_path.is_absolute():
            raise RuntimeError(f"Book U raw path must be repository-relative for {instrument}")
        raw_path = (repository / relative_path).resolve()
        try:
            raw_path.relative_to(repository)
        except ValueError as exc:
            raise RuntimeError(f"Book U raw path escapes the repository for {instrument}") from exc
        if not raw_path.is_file():
            raise RuntimeError(f"Book U raw gzip is missing for {instrument}")
        compressed = raw_path.read_bytes()
        if hashlib.sha256(compressed).hexdigest() != gzip_hash:
            raise RuntimeError(f"Book U raw gzip hash mismatch for {instrument}")
        try:
            response = gzip.decompress(compressed)
        except (OSError, EOFError) as exc:
            raise RuntimeError(f"Book U raw gzip is invalid for {instrument}") from exc
        if hashlib.sha256(response).hexdigest() != response_hash:
            raise RuntimeError(f"Book U decompressed response hash mismatch for {instrument}")
        verified_raw[instrument] = {
            "path": relative_path.as_posix(),
            "raw_gzip_sha256": gzip_hash,
            "raw_response_sha256": response_hash,
            "raw_gzip_bytes": len(compressed),
            "raw_response_bytes": len(response),
        }

    frame = pd.read_parquet(snapshot)
    expected_columns = {
        "instrument",
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adjustment_factor",
    }
    if set(frame.columns) != expected_columns:
        raise RuntimeError("Book U frozen snapshot schema mismatch")
    if int(manifest.get("snapshot_rows", -1)) != len(frame):
        raise RuntimeError("Book U frozen snapshot row count mismatch")
    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    if frame[["instrument", "timestamp"]].duplicated().any():
        raise RuntimeError("Book U frozen snapshot contains duplicate instrument sessions")
    adjustment = frame["adjustment_factor"].to_numpy(dtype=float)
    if not np.isfinite(adjustment).all() or np.any(adjustment <= 0.0):
        raise RuntimeError("Book U snapshot contains an invalid adjustment factor")

    panel: dict[str, pd.DataFrame] = {}
    for instrument in USD_ETF_UNIVERSE:
        part = frame.loc[frame["instrument"] == instrument].copy()
        if part.empty:
            raise RuntimeError(f"Book U snapshot is missing {instrument}")
        panel[instrument] = part.set_index("timestamp")[
            ["open", "high", "low", "close", "volume", "adjustment_factor"]
        ].sort_index()
    checked = common_book_u_panel(panel)
    common_index = next(iter(checked.values())).index
    expected_sessions = int(manifest.get("common_sessions", -1))
    if expected_sessions != len(common_index):
        raise RuntimeError("Book U common-session count differs from its manifest")
    if len(frame) != expected_sessions * len(USD_ETF_UNIVERSE):
        raise RuntimeError("Book U snapshot is not an exact common-session panel")
    for instrument, part in panel.items():
        if len(part) != expected_sessions or not part.index.equals(common_index):
            raise RuntimeError(f"Book U snapshot forward-fill/common-date defect for {instrument}")
    if manifest.get("common_start") != _date(common_index.min()):
        raise RuntimeError("Book U common start differs from its manifest")
    if manifest.get("common_end") != _date(common_index.max()):
        raise RuntimeError("Book U common end differs from its manifest")
    exclusive_end = _utc(SNAPSHOT_END_EXCLUSIVE)
    if common_index.max() >= exclusive_end:
        raise RuntimeError("Book U common panel contains data on/after the frozen exclusive end")

    integrity = {
        "protocol_sha256": protocol_hash,
        "snapshot_sha256": snapshot_hash,
        "manifest_sha256": _sha256(manifest_path),
        "manifest_snapshot_sha256_match": True,
        "manifest_protocol_sha256_match": True,
        "raw_vendor_response_hashes_recorded": True,
        "raw_vendor_files_verified": len(verified_raw),
        "raw_vendor_sources": verified_raw,
        "requested_end_exclusive": SNAPSHOT_END_EXCLUSIVE,
        "snapshot_rows": int(len(frame)),
        "common_sessions": int(len(common_index)),
        "common_start": _date(common_index.min()),
        "common_end": _date(common_index.max()),
        "account_and_quote_currency": "USD",
        "adjustment_policy": manifest.get("adjustment_policy"),
    }
    return checked, integrity


def _spec(cell: str, *, stressed: bool = False) -> BookUSpec:
    risk, aggregate = RISK_CELLS[cell]
    return BookUSpec(
        name=f"{cell}_STRESS" if stressed else cell,
        risk_per_leg=risk,
        aggregate_risk=aggregate,
        cost_bps_per_side=10.0 if stressed else 5.0,
        stop_slippage_bps=25.0 if stressed else 0.0,
    )


def _series_digest(series: pd.Series) -> str:
    rows = [[_date(date), float(value)] for date, value in series.sort_index().items()]
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _object_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _result_fingerprint(run: BookURun) -> str:
    """Bind local outcomes to core spec, protocol, and consumed-input identity."""

    required = (
        "run_fingerprint_sha256",
        "outcome_sha256",
        "consumed_panel_sha256",
        "protocol_sha256",
    )
    missing = [name for name in required if not _valid_digest(run.metrics.get(name))]
    if missing:
        raise RuntimeError("Book U core result lacks fingerprint fields: " + ", ".join(missing))
    if run.metrics["protocol_sha256"] != PROTOCOL_SHA256:
        raise RuntimeError("Book U core result is bound to a different protocol")
    local_outcome = {
        "start": _date(run.start),
        "end": _date(run.end),
        "equity": [[_date(t), float(v)] for t, v in run.equity.items()],
        "events": run.events,
        "decisions": run.decisions,
        "trace": run.trace,
        "episodes": run.episodes,
        "cluster_attribution": run.cluster_attribution,
    }
    return _object_digest(
        {
            "spec": run.spec.to_dict(),
            "core_run_fingerprint_sha256": run.metrics["run_fingerprint_sha256"],
            "core_outcome_sha256": run.metrics["outcome_sha256"],
            "consumed_panel_sha256": run.metrics["consumed_panel_sha256"],
            "protocol_sha256": run.metrics["protocol_sha256"],
            "local_outcome_sha256": _object_digest(local_outcome),
        }
    )


def _deterministic_metrics(run: BookURun) -> dict[str, Any]:
    equity = run.equity.astype(float).sort_index()
    if len(equity) < 2 or not np.isfinite(equity.to_numpy()).all():
        raise RuntimeError("Book U run has an invalid equity path")
    returns = equity.pct_change().dropna()
    standard_deviation = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    annualized_return = (
        float((equity.iloc[-1] / ACCOUNT) ** (PERIODS_PER_YEAR / len(returns)) - 1.0)
        if len(returns) and equity.iloc[-1] > 0.0
        else 0.0
    )
    drawdown = equity / equity.cummax() - 1.0
    maximum_drawdown = float(-drawdown.min())
    return {
        "initial_equity_usd": ACCOUNT,
        "final_equity_usd": float(equity.iloc[-1]),
        "net_pnl_usd": float(equity.iloc[-1] - ACCOUNT),
        "total_return": float(equity.iloc[-1] / ACCOUNT - 1.0),
        "annualized_return": annualized_return,
        "annualized_volatility": standard_deviation * math.sqrt(PERIODS_PER_YEAR),
        "sharpe": (
            float(returns.mean() / standard_deviation * math.sqrt(PERIODS_PER_YEAR))
            if standard_deviation > 0.0
            else 0.0
        ),
        "max_drawdown": maximum_drawdown,
        "calmar": annualized_return / maximum_drawdown if maximum_drawdown > 0.0 else 0.0,
        "worst_close_day": float(returns.min()) if len(returns) else 0.0,
        "worst_conservative_intraday_day": min(
            (float(row["conservative_intraday_return"]) for row in run.trace),
            default=0.0,
        ),
        "sessions": int(len(equity)),
    }


def _fresh_segment_checks(run: BookURun) -> dict[str, Any]:
    first = run.trace[0] if run.trace else {}
    last = run.trace[-1] if run.trace else {}
    checks = {
        "started_with_fresh_100k_balance": abs(float(first.get("day_start_balance_usd", 0.0)) - ACCOUNT) <= 1e-6,
        "started_with_fresh_100k_equity": abs(float(first.get("day_start_equity_usd", 0.0)) - ACCOUNT) <= 1e-6,
        "terminally_flat": bool(run.metrics.get("verified_flat_at_end")) and bool(last.get("verified_flat_at_end")),
        "no_terminal_positions": not list(last.get("positions") or []),
        "cluster_attribution_reconciles": bool(run.metrics.get("cluster_attribution_reconciles")),
        "no_cash_borrowing": int(run.metrics.get("borrow_breach_count", 1)) == 0,
    }
    return {"checks": checks, "passed": bool(all(checks.values()))}


def _summary(run: BookURun) -> dict[str, Any]:
    independent = _deterministic_metrics(run)
    engine = run.metrics
    comparison_keys = (
        "initial_equity_usd",
        "final_equity_usd",
        "net_pnl_usd",
        "total_return",
        "annualized_return",
        "annualized_volatility",
        "sharpe",
        "max_drawdown",
        "calmar",
        "worst_close_day",
        "worst_conservative_intraday_day",
        "sessions",
    )
    differences: dict[str, float] = {}
    for key in comparison_keys:
        if key not in engine:
            differences[key] = float("inf")
            continue
        differences[key] = abs(float(independent[key]) - float(engine[key]))
    metrics_reconcile = all(
        difference <= max(1e-10, abs(float(independent[key])) * 1e-10)
        for key, difference in differences.items()
    )
    return {
        "spec": run.spec.to_dict(),
        "window": {"start": _date(run.start), "end": _date(run.end)},
        "metrics": independent,
        "engine_metrics": engine,
        "metric_reconciliation": {
            "passed": bool(metrics_reconcile),
            "absolute_differences": differences,
        },
        "events": len(run.events),
        "decisions": len(run.decisions),
        "episodes": len(run.episodes),
        "equity_sha256": _series_digest(run.equity),
        "outcome_sha256": _result_fingerprint(run),
        "fresh_segment": _fresh_segment_checks(run),
    }


def _run_pair(
    panel: dict[str, pd.DataFrame],
    *,
    cell: str,
    start: str,
    end: str,
    run_fn: RunFunction = run_book_u,
) -> dict[str, Any]:
    base = run_fn(panel, _spec(cell), start=start, end=end, initial_equity_usd=ACCOUNT)
    stress = run_fn(
        panel,
        _spec(cell, stressed=True),
        start=start,
        end=end,
        initial_equity_usd=ACCOUNT,
    )
    return {
        "window": {"start": start, "end": end},
        "base": _summary(base),
        "stress_10bps_plus_25bps_stop_slippage": _summary(stress),
        "_base_run": base,
        "_stress_run": stress,
    }


def _strip_private(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_private(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, list):
        return [_strip_private(item) for item in value]
    return value


def _cost_and_cap_diagnostics(
    run: BookURun,
    panel: Mapping[str, pd.DataFrame],
) -> dict[str, Any]:
    tolerance = 1e-9
    spec = run.spec
    cost_rate = spec.cost_bps_per_side / 10_000.0
    slip_rate = spec.stop_slippage_bps / 10_000.0
    event_cost_checks = [
        abs(float(row["cost_usd"]) - float(row["notional_usd"]) * cost_rate)
        <= max(1e-7, abs(float(row["cost_usd"])) * 1e-10)
        for row in run.events
    ]
    stop_fill_checks: list[bool] = []
    for row in run.events:
        reason = str(row.get("reason"))
        if reason == "stop_gap":
            expected = float(row["gap_open_price_usd"]) * (1.0 - slip_rate)
            stop_fill_checks.append(abs(float(row["price_usd"]) - expected) <= max(1e-9, abs(expected) * 1e-10))
        elif reason == "stop_intraday":
            expected = float(row["resting_stop_price_usd"]) * (1.0 - slip_rate)
            stop_fill_checks.append(abs(float(row["price_usd"]) - expected) <= max(1e-9, abs(expected) * 1e-10))

    planned_loss_checks: list[bool] = []
    rotation_reconstructions: list[dict[str, Any]] = []
    for decision in run.decisions:
        execution = decision.get("execution")
        if not isinstance(execution, Mapping):
            continue
        fill_date = _utc(decision["fill_date"])
        decision_date = _date(decision["decision_date"])
        fill_date_string = _date(fill_date)
        immediate_rotation_cost = sum(
            float(event["cost_usd"])
            for event in run.events
            if event.get("reason") == "monthly_rebalance"
            and str(event.get("date")) == fill_date_string
            and str(event.get("decision_date")) == decision_date
        )
        target_stressed_stop_loss = 0.0
        for instrument, target in (execution.get("targets") or {}).items():
            entry = float(panel[instrument].loc[fill_date, "open"])
            stop = float(target["stop_price_usd"])
            stressed_stop = stop * (1.0 - slip_rate)
            expected = max(0.0, entry - stressed_stop) + entry * cost_rate + stressed_stop * cost_rate
            actual = float(target["planned_loss_per_unit_usd"])
            planned_loss_checks.append(abs(actual - expected) <= max(1e-9, abs(expected) * 1e-10))
            units = float(target["units"])
            target_stressed_stop_loss += units * max(0.0, entry - stressed_stop)
            target_stressed_stop_loss += units * stressed_stop * cost_rate

        rotation = execution.get("rotation_inclusive_planned_loss")
        if not isinstance(rotation, Mapping):
            rotation_reconstructions.append({"passed": False, "reason": "missing core field"})
            continue
        total = immediate_rotation_cost + target_stressed_stop_loss
        capital = float(execution["pre_trade_capital_usd"])
        expected_fraction = total / capital if capital > 0.0 else 0.0
        differences = {
            "immediate_rotation_cost_usd": abs(
                immediate_rotation_cost - float(rotation["immediate_rotation_cost_usd"])
            ),
            "target_mark_to_stressed_stop_loss_usd": abs(
                target_stressed_stop_loss
                - float(rotation["target_mark_to_stressed_stop_loss_usd"])
            ),
            "total_pretrade_to_stressed_stops_usd": abs(
                total - float(rotation["total_pretrade_to_stressed_stops_usd"])
            ),
            "fraction_capital": abs(expected_fraction - float(rotation["fraction_capital"])),
        }
        within_cap = total <= spec.aggregate_risk * capital + 1e-6
        passed = bool(
            all(
                difference <= max(1e-7, abs(total) * 1e-10)
                for difference in differences.values()
            )
            and abs(float(rotation["aggregate_budget_usd"]) - spec.aggregate_risk * capital)
            <= max(1e-7, abs(spec.aggregate_risk * capital) * 1e-10)
            and bool(rotation["within_cap"]) == within_cap
            and within_cap
        )
        rotation_reconstructions.append(
            {
                "decision_date": decision_date,
                "fill_date": fill_date_string,
                "independent_immediate_rotation_cost_usd": immediate_rotation_cost,
                "independent_target_stressed_stop_loss_usd": target_stressed_stop_loss,
                "independent_total_pretrade_to_stressed_stops_usd": total,
                "independent_fraction_capital": expected_fraction,
                "absolute_differences": differences,
                "passed": passed,
            }
        )

    trace_max_positions = max((len(row.get("positions") or []) for row in run.trace), default=0)
    marked_close = {
        "max_leg_planned_risk_fraction": max(
            (float(row["max_leg_planned_loss_fraction_capital"]) for row in run.trace), default=0.0
        ),
        "max_aggregate_planned_risk_fraction": max(
            (float(row["aggregate_planned_loss_fraction_capital"]) for row in run.trace), default=0.0
        ),
        "max_gross_fraction": max(
            (float(row["gross_exposure_fraction_equity"]) for row in run.trace), default=0.0
        ),
        "max_position_fraction": max(
            (float(row["max_position_fraction_equity"]) for row in run.trace), default=0.0
        ),
        "risk_trim_required_days": sum(bool(row.get("risk_trim_required_next_open")) for row in run.trace),
    }
    checks = {
        "frozen_base_or_stress_cost": spec.cost_bps_per_side in (5.0, 10.0),
        "frozen_stop_slippage": spec.stop_slippage_bps in (0.0, 25.0),
        "every_fill_charged_at_configured_rate": bool(run.events) and all(event_cost_checks),
        "stop_slippage_formula_exact": all(stop_fill_checks),
        "planned_loss_per_unit_includes_entry_and_exit_costs": bool(planned_loss_checks) and all(planned_loss_checks),
        "rotation_inclusive_planned_loss_independently_reconstructed": bool(rotation_reconstructions) and all(row["passed"] for row in rotation_reconstructions),
        "rotation_inclusive_aggregate_cap": float(run.metrics["max_rotation_inclusive_planned_risk_fraction"]) <= spec.aggregate_risk + tolerance and int(run.metrics["rotation_inclusive_cap_breach_count"]) == 0,
        "execution_leg_risk_cap": float(run.metrics["max_execution_leg_planned_risk_fraction"]) <= spec.risk_per_leg + tolerance,
        "execution_aggregate_risk_cap": float(run.metrics["max_execution_aggregate_planned_risk_fraction"]) <= spec.aggregate_risk + tolerance,
        "execution_gross_cap": float(run.metrics["max_execution_gross_fraction"]) <= spec.gross_cap + tolerance,
        "execution_position_cap": float(run.metrics["max_execution_position_fraction"]) <= spec.position_cap + tolerance,
        "six_position_cluster_cap": trace_max_positions <= 6,
        "every_open_execution_satisfies_caps": bool(run.trace) and all(bool(row.get("open_execution_caps_satisfied")) for row in run.trace),
        "open_cap_breach_count_zero": int(run.metrics.get("open_cap_breach_count", -1)) == 0,
        "cash_only": int(run.metrics.get("borrow_breach_count", 1)) == 0 and float(run.metrics.get("minimum_cash_usd", -1.0)) >= -1e-6,
        "terminal_liquidation": bool(run.metrics.get("verified_flat_at_end")),
        "cost_and_cluster_pnl_reconcile": bool(run.metrics.get("cluster_attribution_reconciles")),
    }
    return {
        "checks": checks,
        "passed": bool(all(checks.values())),
        "execution_maxima": {
            "leg_risk": run.metrics["max_execution_leg_planned_risk_fraction"],
            "aggregate_risk": run.metrics["max_execution_aggregate_planned_risk_fraction"],
            "rotation_inclusive_risk": run.metrics["max_rotation_inclusive_planned_risk_fraction"],
            "gross": run.metrics["max_execution_gross_fraction"],
            "position": run.metrics["max_execution_position_fraction"],
            "positions": trace_max_positions,
        },
        "configured_caps": {
            "leg_risk": spec.risk_per_leg,
            "aggregate_risk": spec.aggregate_risk,
            "rotation_inclusive_risk": spec.aggregate_risk,
            "gross": spec.gross_cap,
            "position": spec.position_cap,
            "positions": 6,
        },
        "marked_close_diagnostic": marked_close,
        "rotation_inclusive_reconstruction": rotation_reconstructions,
        "marked_close_semantics": (
            "Market moves can create a close-observed overrun; the frozen engine flags it "
            "for reduction at the next executable open. Binding target/execution caps are separate."
        ),
    }


def _winner_haircut(run: BookURun) -> dict[str, Any]:
    original = sum(float(row["net_pnl_usd"]) for row in run.episodes)
    adjusted = sum(
        0.5 * float(row["net_pnl_usd"])
        if float(row["net_pnl_usd"]) > 0.0
        else float(row["net_pnl_usd"])
        for row in run.episodes
    )
    net = float(run.metrics["net_pnl_usd"])
    reconciles = abs(original - net) <= max(1e-6, abs(net) * 1e-10)
    return {
        "completed_episode_net_pnl_usd": original,
        "haircut_net_pnl_usd": adjusted,
        "haircut_return": adjusted / ACCOUNT,
        "positive": bool(adjusted > 0.0),
        "episode_pnl_reconciles": reconciles,
        "passed": bool(adjusted > 0.0 and reconciles),
        "rule": "multiply every positive completed-episode P&L by 0.50; leave losses unchanged",
    }


def _correlated_gap_arithmetic(run: BookURun) -> dict[str, Any]:
    """Pure exposure arithmetic; deliberately not a calibrated tail model."""

    max_open_gross = float(run.metrics.get("max_open_gross_fraction", 0.0))
    scenarios = {
        f"{int(shock * 100)}pct_simultaneous_adverse_gap": {
            "gap_fraction": shock,
            "gross_loss_fraction_initial_equity": max_open_gross * shock,
            "gross_loss_usd_on_100k": ACCOUNT * max_open_gross * shock,
        }
        for shock in (0.05, 0.10)
    }
    return {
        "status": "NON_BINDING_ARITHMETIC_ONLY",
        "preregistered_gate_input": False,
        "empirical_probability": None,
        "max_open_gross_fraction": max_open_gross,
        "scenarios": scenarios,
        "limitation": (
            "This multiplies maximum observed opening gross by a simultaneous gap size. "
            "It is not an empirical probability, correlated-tail estimate, fill simulation, "
            "or architecture/frontier pass-fail input."
        ),
    }


def _cluster_concentration(run: BookURun) -> dict[str, Any]:
    by_cluster = {
        cluster: float(row["net_pnl_usd"])
        for cluster, row in sorted(run.cluster_attribution.items())
    }
    total = float(run.metrics["net_pnl_usd"])
    positive = {cluster: value for cluster, value in by_cluster.items() if value > 0.0}
    top_cluster = min(positive, key=lambda name: (-positive[name], name)) if positive else None
    top_pnl = positive[top_cluster] if top_cluster is not None else 0.0
    share_of_net = top_pnl / total if total > 0.0 and top_cluster is not None else None
    sum_positive = sum(positive.values())
    share_of_positive = top_pnl / sum_positive if sum_positive > 0.0 else None
    after_removal = total - top_pnl if top_cluster is not None else total
    reconciles = bool(run.metrics.get("cluster_attribution_reconciles")) and abs(sum(by_cluster.values()) - total) <= 1e-6
    passed = bool(
        reconciles
        and total > 0.0
        and share_of_positive is not None
        and share_of_positive <= 0.35 + 1e-12
        and after_removal > 0.0
    )
    return {
        "by_cluster_net_pnl_usd": by_cluster,
        "portfolio_net_pnl_usd": total,
        "top_profit_cluster": top_cluster,
        "top_profit_cluster_pnl_usd": top_pnl,
        "top_cluster_share_of_portfolio_net_pnl": share_of_net,
        "top_cluster_share_of_positive_cluster_pnl": share_of_positive,
        "share_cap": 0.35,
        "binding_share_denominator": "sum_of_positive_cluster_pnl",
        "net_pnl_after_removing_top_cluster_usd": after_removal,
        "positive_after_removal": bool(after_removal > 0.0),
        "cluster_attribution_reconciles": reconciles,
        "passed": passed,
    }


def _trace_to_day_records(run: BookURun) -> tuple[DayRecord, ...]:
    records: list[DayRecord] = []
    for row in run.trace:
        # The adjusted daily bar has no executable event timestamp.  Noon UTC
        # keeps the proxy session on the same CE(S)T calendar date; this choice
        # is disclosed and cannot qualify a funded account.
        timestamp = _utc(row["date"]) + pd.Timedelta(hours=12)
        records.append(
            DayRecord(
                session=firm_session_key(timestamp, timezone="Europe/Prague"),
                timestamp=timestamp,
                day_start_balance=float(row["day_start_balance_usd"]),
                day_start_equity=float(row["day_start_equity_usd"]),
                intraday_min_equity=float(row["conservative_intraday_min_equity_usd"]),
                end_balance=float(row["day_end_balance_usd"]),
                end_equity=float(row["day_end_equity_usd"]),
                closed_pnl=float(row["day_end_balance_usd"] - row["day_start_balance_usd"]),
                source_risk_base=max(
                    1e-9,
                    min(float(row["day_start_equity_usd"]), ACCOUNT),
                ),
                intraday_min_timestamp=timestamp,
                positions_opened=len(row.get("positions_opened") or []),
                verified_flat_at_end=bool(row.get("verified_flat_at_end")),
            )
        )
    return tuple(records)


def _funded_replay_proxy(run: BookURun) -> dict[str, Any]:
    records = _trace_to_day_records(run)
    rules = FundedRules(
        initial_balance=ACCOUNT,
        profit_target_pct=None,
        daily_loss_pct=0.05,
        max_loss_pct=0.10,
        max_loss_mode="static",
        daily_loss_basis="initial_balance",
        minimum_trading_days=4,
        session_timezone="Europe/Prague",
    )
    replay = replay_funded_rules(records, rules)
    worst_daily = max(
        (
            max(0.0, (record.day_start_balance - record.intraday_min_equity) / ACCOUNT)
            for record in records
        ),
        default=0.0,
    )
    worst_static = max(
        (max(0.0, (ACCOUNT - record.intraday_min_equity) / ACCOUNT) for record in records),
        default=0.0,
    )
    return {
        "status": "DAILY_OHLC_STATIC_PROXY_ONLY",
        "binding_funded_evidence": False,
        "rules": asdict(rules),
        "replay": asdict(replay),
        "breach": replay.status == "breached",
        "worst_daily_loss_fraction_initial": worst_daily,
        "worst_static_loss_fraction_initial": worst_static,
        "records": len(records),
        "session_timestamp_assumption": "12:00 UTC mapped into Europe/Prague firm-day dates",
        "limitation": (
            "Adjusted daily ETF OHLC co-extremes are conservative marks, not executable "
            "CFD bid/ask paths or acknowledged stop/flatten events."
        ),
    }


def _cpcv_sign_diagnostic(returns: pd.Series) -> dict[str, Any]:
    values = returns.sort_index().astype(float)
    values = values[np.isfinite(values.to_numpy())]
    groups = np.array_split(np.arange(len(values), dtype=int), 6)
    paths: list[dict[str, Any]] = []
    if len(values) < 12 or any(len(group) == 0 for group in groups):
        return {
            "status": "INSUFFICIENT_OBSERVATIONS",
            "groups": 6,
            "test_groups": 2,
            "n_paths": 0,
            "positive_paths": 0,
            "required_positive_paths": 12,
            "paths": [],
            "passed": False,
        }
    for pair in combinations(range(6), 2):
        indices = np.sort(np.concatenate([groups[pair[0]], groups[pair[1]]]))
        selected = values.iloc[indices]
        per_period = sharpe_ratio(selected.to_numpy(dtype=float), periods_per_year=1)
        paths.append(
            {
                "test_groups": list(pair),
                "observations": len(selected),
                "start": _date(selected.index.min()),
                "end": _date(selected.index.max()),
                "per_period_sharpe": per_period,
                "positive": bool(per_period > 0.0),
            }
        )
    positive = sum(bool(row["positive"]) for row in paths)
    return {
        "status": "EVALUATED_SINGLE_FROZEN_RULE_STREAM",
        "groups": 6,
        "test_groups": 2,
        "n_paths": len(paths),
        "positive_paths": positive,
        "required_positive_paths": 12,
        "paths": paths,
        "training_or_refit_performed": False,
        "semantics": (
            "The one causal, terminally reconciled stressed U075 return stream is "
            "restricted to each fixed choose-two group combination; no parameter is fitted."
        ),
        "passed": bool(len(paths) == 15 and positive >= 12),
    }


def _load_trial_sharpe_history(path: Path | None) -> dict[str, Any] | None:
    """Load an explicit compatible artifact without reading the shared ledger."""

    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    sharpes = payload.get("sharpes")
    count = payload.get("n_trials")
    units = payload.get("units")
    periods = payload.get("periods_per_year", PERIODS_PER_YEAR)
    if (
        not isinstance(sharpes, list)
        or not isinstance(count, int)
        or count < 2
        or len(sharpes) != count
        or units not in {"annualized", "per_period"}
        or int(periods) != PERIODS_PER_YEAR
    ):
        raise ValueError("incompatible project trial-Sharpe dispersion artifact")
    converted: list[float] = []
    for value in sharpes:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("trial-Sharpe history contains a non-finite value")
        converted.append(number / math.sqrt(PERIODS_PER_YEAR) if units == "annualized" else number)
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "n_trials": count,
        "per_period_sharpes": converted,
        "source_units": units,
        "periods_per_year": PERIODS_PER_YEAR,
    }


def _observe_shared_trial_ledger(path: Path = SHARED_TRIAL_LEDGER) -> dict[str, Any]:
    """Count the spent trials without taking a lock or ever writing the ledger."""

    if not path.is_file():
        return {
            "exists": False,
            "sha256": None,
            "object_entry_count": 0,
            "finite_compatible_sharpes": 0,
            "expected_finite_compatible_sharpes": 0,
            "matches_expected_missing_dispersion": True,
            "read_only": True,
            "_annualized_sharpes": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("shared trial ledger is not a JSON object")
    sharpes: list[float] = []
    for value in payload.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            sharpes.append(number)
    return {
        "exists": True,
        "sha256": _sha256(path),
        "object_entry_count": len(payload),
        "finite_compatible_sharpes": len(sharpes),
        "expected_finite_compatible_sharpes": 0,
        "matches_expected_missing_dispersion": len(sharpes) == 0,
        "read_only": True,
        # Kept private so the report records counts/hash, not a duplicate of
        # the shared ledger.  It is usable only if the history is complete.
        "_annualized_sharpes": sharpes,
    }


def _dsr_diagnostic(
    target_returns: pd.Series,
    candidate_runs: Sequence[BookURun],
    trial_history: Mapping[str, Any] | None,
    shared_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_sharpes = [
        sharpe_ratio(run.equity.pct_change().dropna().to_numpy(dtype=float), periods_per_year=1)
        for run in candidate_runs
    ]
    spent_trials = int(shared_ledger.get("object_entry_count", 0))
    finite_shared = int(shared_ledger.get("finite_compatible_sharpes", 0))
    effective_trials = spent_trials + len(candidate_sharpes)
    compatible_history: list[float] | None = None
    history_hash: str | None = None
    history_source: str | None = None
    if trial_history is not None:
        if int(trial_history["n_trials"]) == spent_trials:
            compatible_history = list(trial_history["per_period_sharpes"])
            history_hash = str(trial_history.get("sha256"))
            history_source = "explicit_complete_artifact"
    elif spent_trials >= 2 and finite_shared == spent_trials:
        compatible_history = [
            float(value) / math.sqrt(PERIODS_PER_YEAR)
            for value in shared_ledger.get("_annualized_sharpes", [])
        ]
        history_hash = str(shared_ledger.get("sha256"))
        history_source = "complete_shared_ledger"

    if compatible_history is None:
        return {
            "status": "DATA_BLOCKED",
            "passed": False,
            "dsr": None,
            "spent_project_trial_count": spent_trials,
            "finite_compatible_project_sharpes": finite_shared,
            "book_u_cells_in_dispersion": len(candidate_sharpes),
            "effective_trial_count": effective_trials,
            "reason": (
                "The shared ledger supplies the spent trial count but not a complete "
                "compatible Sharpe dispersion; it was observed read-only and never mutated."
            ),
        }
    dispersion = compatible_history + candidate_sharpes
    evaluated = deflated_sharpe_ratio(
        target_returns.to_numpy(dtype=float),
        dispersion,
        periods_per_year=PERIODS_PER_YEAR,
        n_trials=effective_trials,
    )
    return {
        "status": "EVALUATED",
        "passed": bool(float(evaluated.get("dsr", 0.0)) >= 0.95),
        **evaluated,
        "spent_project_trial_count": spent_trials,
        "finite_compatible_project_sharpes": finite_shared,
        "book_u_cells_in_dispersion": len(candidate_sharpes),
        "effective_trial_count": effective_trials,
        "trial_history_sha256": history_hash,
        "trial_history_source": history_source,
        "dispersion_units": "per_period",
    }


def _gate(name: str, threshold: str, value: Any, passed: bool, *, status: str = "EVALUATED") -> dict[str, Any]:
    return {
        "name": name,
        "threshold": threshold,
        "value": value,
        "status": status,
        "passed": bool(passed),
    }


def _architecture_gate(
    segments: Mapping[str, Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
    cpcv: Mapping[str, Any],
    dsr: Mapping[str, Any],
    concentration: Mapping[str, Any],
    haircut: Mapping[str, Any],
    caps: Mapping[str, Mapping[str, Any]],
    replays: Mapping[str, Mapping[str, Any]],
    engineering: Mapping[str, Any],
) -> dict[str, Any]:
    full = segments["full_available_history"]
    validation = segments["retrospective_validation"]
    sealed = segments["sealed_historical_robustness"]
    fb = full["base"]["metrics"]
    fs = full["stress_10bps_plus_25bps_stop_slippage"]["metrics"]
    vb = validation["base"]["metrics"]
    vs = validation["stress_10bps_plus_25bps_stop_slippage"]["metrics"]
    sb = sealed["base"]["metrics"]
    ss = sealed["stress_10bps_plus_25bps_stop_slippage"]["metrics"]
    positive_blocks = sum(
        float(row["stress_10bps_plus_25bps_stop_slippage"]["metrics"]["total_return"]) > 0.0
        for row in blocks
    )
    fresh_segments = all(
        bool(segment[variant]["fresh_segment"]["passed"])
        and bool(segment[variant]["metric_reconciliation"]["passed"])
        for segment in segments.values()
        for variant in ("base", "stress_10bps_plus_25bps_stop_slippage")
    ) and all(
        bool(row["stress_10bps_plus_25bps_stop_slippage"]["fresh_segment"]["passed"])
        for row in blocks
    )
    checks = [
        _gate("full_base_annualized_return", ">= 5.0%", fb["annualized_return"], fb["annualized_return"] >= 0.05),
        _gate("full_base_sharpe", ">= 0.90", fb["sharpe"], fb["sharpe"] >= 0.90),
        _gate("full_base_max_drawdown", "<= 8.0%", fb["max_drawdown"], fb["max_drawdown"] <= 0.08),
        _gate("full_base_worst_conservative_day", ">= -1.50%", fb["worst_conservative_intraday_day"], fb["worst_conservative_intraday_day"] >= -0.015),
        _gate("full_stress_annualized_return", ">= 2.5%", fs["annualized_return"], fs["annualized_return"] >= 0.025),
        _gate("full_stress_sharpe", ">= 0.65", fs["sharpe"], fs["sharpe"] >= 0.65),
        _gate("full_stress_max_drawdown", "<= 9.0%", fs["max_drawdown"], fs["max_drawdown"] <= 0.09),
        _gate("full_stress_total_return", "> 0", fs["total_return"], fs["total_return"] > 0.0),
        _gate("validation_base_annualized_return", ">= 4.0%", vb["annualized_return"], vb["annualized_return"] >= 0.04),
        _gate("validation_base_sharpe", ">= 0.75", vb["sharpe"], vb["sharpe"] >= 0.75),
        _gate("validation_base_max_drawdown", "<= 8.0%", vb["max_drawdown"], vb["max_drawdown"] <= 0.08),
        _gate("validation_stress_total_return", "> 0", vs["total_return"], vs["total_return"] > 0.0),
        _gate("sealed_base_total_return", "> 0", sb["total_return"], sb["total_return"] > 0.0),
        _gate("sealed_stress_total_return", "> 0", ss["total_return"], ss["total_return"] > 0.0),
        _gate("sealed_stress_sharpe", "> 0", ss["sharpe"], ss["sharpe"] > 0.0),
        _gate("sealed_max_drawdown", "base and stress <= 10.0%", max(sb["max_drawdown"], ss["max_drawdown"]), max(sb["max_drawdown"], ss["max_drawdown"]) <= 0.10),
        _gate("fixed_two_year_blocks", ">= 4 of 5 stressed returns positive", positive_blocks, positive_blocks >= 4),
        _gate("six_group_choose_two_cpcv", ">= 12 of 15 Sharpe signs positive", cpcv, bool(cpcv.get("passed")), status=str(cpcv.get("status"))),
        _gate("deflated_sharpe_ratio", ">= 0.95", dsr.get("dsr"), bool(dsr.get("passed")), status=str(dsr.get("status"))),
        _gate("cluster_concentration_and_removal", "top <= 35%; removal positive", concentration, bool(concentration.get("passed"))),
        _gate("winner_haircut", "positive completed-episode P&L", haircut, bool(haircut.get("passed"))),
        _gate("base_and_stress_positive", "both total returns > 0", {"base": fb["total_return"], "stress": fs["total_return"]}, fb["total_return"] > 0.0 and fs["total_return"] > 0.0),
        _gate("cost_risk_gross_position_caps", "base and stress exact checks pass", caps, all(bool(row.get("passed")) for row in caps.values())),
        _gate("conservative_funded_rule_replay", "base and stress zero breaches", replays, all(not bool(row.get("breach")) for row in replays.values())),
        _gate("fresh_segment_reconciliation", "every segment/cell starts fresh and ends flat", fresh_segments, fresh_segments),
        _gate("causal_execution_engineering_controls", "all frozen controls pass", engineering, bool(engineering.get("passed"))),
    ]
    evaluated_failures = [
        row for row in checks if row["status"] != "DATA_BLOCKED" and not row["passed"]
    ]
    blocked = [row for row in checks if row["status"] == "DATA_BLOCKED"]
    status = "FAILED" if evaluated_failures else ("DATA_BLOCKED" if blocked else "PASSED")
    return {
        "status": status,
        "passed": status == "PASSED",
        "checks": checks,
        "failed_checks": [row["name"] for row in evaluated_failures],
        "blocked_checks": [row["name"] for row in blocked],
    }


def _synthetic_panel(n: int = 820) -> dict[str, pd.DataFrame]:
    sessions = pd.DatetimeIndex(
        xcals.get_calendar("XNYS").sessions_in_range("2017-01-03", "2025-12-31")
    )[:n]
    index = (
        sessions.tz_localize("UTC") if sessions.tz is None else sessions.tz_convert("UTC")
    ).rename("timestamp")
    x = np.arange(n, dtype=float)
    panel: dict[str, pd.DataFrame] = {}
    for number, instrument in enumerate(USD_ETF_UNIVERSE):
        daily = 0.00025 + number * 0.000015 + 0.0015 * np.sin(x / (9.0 + number))
        close = (80.0 + number * 3.0) * np.exp(np.cumsum(daily))
        open_ = close * (1.0 + 0.0008 * np.cos(x / (7.0 + number)))
        panel[instrument] = pd.DataFrame(
            {
                "open": open_,
                "high": np.maximum(open_, close) * 1.006,
                "low": np.minimum(open_, close) * 0.994,
                "close": close,
                "volume": 1_000_000.0,
                "adjustment_factor": 1.0,
            },
            index=index,
        )
    return panel


def _engineering_control_suite(run_fn: RunFunction = run_book_u) -> dict[str, Any]:
    """Execute small deterministic synthetic controls before historical evidence."""

    panel = _synthetic_panel()
    index = next(iter(panel.values())).index
    start, end = index[252], index[700]
    reference = run_fn(panel, _spec("U075"), start=start, end=end, initial_equity_usd=ACCOUNT)
    repeated = run_fn(panel, _spec("U075"), start=start, end=end, initial_equity_usd=ACCOUNT)

    poisoned = {instrument: frame.copy() for instrument, frame in panel.items()}
    for frame in poisoned.values():
        mask = frame.index > end
        multipliers = np.linspace(10.0, 1_000.0, int(mask.sum()))
        for column in ("open", "high", "low", "close"):
            frame.loc[mask, column] = frame.loc[mask, column].to_numpy(dtype=float) * multipliers
    poisoned_run = run_fn(poisoned, _spec("U075"), start=start, end=end, initial_equity_usd=ACCOUNT)
    reversed_run = run_fn(
        dict(reversed(list(panel.items()))),
        _spec("U075"),
        start=start,
        end=end,
        initial_equity_usd=ACCOUNT,
    )

    buy = next((row for row in reference.events if row["side"] == "buy"), None)
    gap_pass = entry_pass = False
    if buy is not None:
        instrument = str(buy["instrument"])
        fill_date = _utc(buy["date"])
        location = int(panel[instrument].index.get_loc(fill_date))
        if location + 1 < len(index):
            gap_date = index[location + 1]
            gap_panel = {name: frame.copy() for name, frame in panel.items()}
            gap_open = float(buy["stop_price_usd"]) * 0.80
            gap_panel[instrument].loc[gap_date, ["open", "high", "low", "close"]] = [
                gap_open,
                gap_open * 1.01,
                gap_open * 0.99,
                gap_open,
            ]
            gap_run = run_fn(gap_panel, _spec("U075", stressed=True), start=start, end=end, initial_equity_usd=ACCOUNT)
            gap_pass = any(
                row["reason"] == "stop_gap"
                and row["instrument"] == instrument
                and row["date"] == _date(gap_date)
                and abs(float(row["price_usd"]) - gap_open * (1.0 - 0.0025)) <= 1e-8
                for row in gap_run.events
            )

        entry_panel = {name: frame.copy() for name, frame in panel.items()}
        stop = float(buy["stop_price_usd"])
        entry_panel[instrument].loc[fill_date, "low"] = min(
            float(entry_panel[instrument].loc[fill_date, "low"]), stop * 0.90
        )
        entry_run = run_fn(entry_panel, _spec("U075"), start=start, end=end, initial_equity_usd=ACCOUNT)
        entry_pass = any(
            row["reason"] == "stop_intraday"
            and row["instrument"] == instrument
            and row["date"] == _date(fill_date)
            for row in entry_run.events
        )

    cap_checks = _cost_and_cap_diagnostics(reference, panel)
    checks = {
        "future_poison": _result_fingerprint(reference) == _result_fingerprint(poisoned_run),
        "input_order_permutation": _result_fingerprint(reference) == _result_fingerprint(reversed_run),
        "deterministic_repeat": _result_fingerprint(reference) == _result_fingerprint(repeated),
        "gap_stop_worse_open_plus_slippage": gap_pass,
        "entry_day_stop": entry_pass,
        "terminal_liquidation": bool(reference.metrics.get("verified_flat_at_end")),
        "cost_inclusive_sizing_and_caps": bool(cap_checks.get("passed")),
        "fresh_segment": bool(_fresh_segment_checks(reference).get("passed")),
    }
    return {
        "status": "EVALUATED_ON_DETERMINISTIC_SYNTHETIC_PANEL",
        "historical_outcomes_used": False,
        "checks": checks,
        "passed": bool(all(checks.values())),
    }


def _frontier(
    panel: dict[str, pd.DataFrame],
    full_pair: Mapping[str, Any],
    *,
    start: str,
    end: str,
    run_fn: RunFunction = run_book_u,
) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    for cell in RISK_CELLS:
        pair = full_pair if cell == "U075" else _run_pair(
            panel, cell=cell, start=start, end=end, run_fn=run_fn
        )
        base: BookURun = pair["_base_run"]
        stress: BookURun = pair["_stress_run"]
        base_return = float(base.metrics["annualized_return"])
        stress_return = float(stress.metrics["annualized_return"])
        retention = stress_return / base_return if base_return > 0.0 else None
        haircut = _winner_haircut(base)
        replay = _funded_replay_proxy(stress)
        cap_base = _cost_and_cap_diagnostics(base, panel)
        cap_stress = _cost_and_cap_diagnostics(stress, panel)
        eligible = bool(
            float(stress.metrics["max_drawdown"]) <= 0.08
            and float(stress.metrics["worst_conservative_intraday_day"]) >= -0.015
            and not replay["breach"]
            and haircut["passed"]
            and retention is not None
            and retention >= 0.70
            and cap_base["passed"]
            and cap_stress["passed"]
        )
        cells[cell] = {
            "risk_per_leg": RISK_CELLS[cell][0],
            "aggregate_risk": RISK_CELLS[cell][1],
            "base": _strip_private(pair["base"]),
            "stress": _strip_private(pair["stress_10bps_plus_25bps_stop_slippage"]),
            "stressed_calmar": float(stress.metrics["calmar"]),
            "annualized_stress_base_return_retention": retention,
            "winner_haircut": haircut,
            "funded_replay_proxy": replay,
            "caps": {"base": cap_base, "stress": cap_stress},
            "eligibility": {
                "max_drawdown_le_8pct": float(stress.metrics["max_drawdown"]) <= 0.08,
                "worst_conservative_day_ge_minus_1_5pct": float(stress.metrics["worst_conservative_intraday_day"]) >= -0.015,
                "zero_conservative_rule_breaches": not replay["breach"],
                "positive_winner_haircut": bool(haircut["passed"]),
                "return_retention_ge_70pct": retention is not None and retention >= 0.70,
                "all_caps_pass": bool(cap_base["passed"] and cap_stress["passed"]),
                "eligible": eligible,
            },
            "_base_run": base,
            "_stress_run": stress,
        }
    eligible_cells = [name for name, row in cells.items() if row["eligibility"]["eligible"]]
    selected: str | None = None
    if eligible_cells:
        best = max(float(cells[name]["stressed_calmar"]) for name in eligible_cells)
        within_five_percent = [
            name for name in eligible_cells
            if float(cells[name]["stressed_calmar"]) >= best * 0.95 - 1e-12
        ]
        selected = min(within_five_percent, key=lambda name: RISK_CELLS[name][0])
    return {
        "status": "EVALUATED" if selected is not None else "NO_ELIGIBLE_CELL",
        "selection_rule": (
            "highest stressed Calmar; if within 5% of the best, choose the lower-risk cell"
        ),
        "selected_cell": selected,
        "cells": cells,
    }


def _conditional_frontier(
    architecture: Mapping[str, Any],
    panel: dict[str, pd.DataFrame],
    full_pair: Mapping[str, Any],
    *,
    start: str,
    end: str,
    run_fn: RunFunction = run_book_u,
) -> dict[str, Any]:
    """Keep the higher-risk cells genuinely unobserved until U075 passes."""

    if not bool(architecture.get("passed")):
        return {
            "status": "NOT_RUN_ARCHITECTURE_GATE",
            "selected_cell": None,
            "reason": (
                "The frozen protocol forbids inspecting U085/U100 unless every U075 "
                "architecture requirement, including DSR, passes."
            ),
            "cells": {},
        }
    return _frontier(
        panel,
        full_pair,
        start=start,
        end=end,
        run_fn=run_fn,
    )


def _configuration_id(run: BookURun) -> str:
    name = str(run.spec.name)
    mode = "STRESS_10BPS_25BPS_STOP" if name.endswith("_STRESS") else "BASE_5BPS"
    cell = name.removesuffix("_STRESS")
    return f"{cell}_{mode}"


def _build_book_u_trial_ledger(
    segments: Mapping[str, Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
    frontier: Mapping[str, Any],
    *,
    integrity: Mapping[str, Any],
    shared_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Record only distinct market configurations actually evaluated by this run."""

    evaluations: list[tuple[str, BookURun]] = []
    for segment_name, segment in segments.items():
        evaluations.append((f"segment:{segment_name}:base", segment["_base_run"]))
        evaluations.append((f"segment:{segment_name}:stress", segment["_stress_run"]))
    for row in blocks:
        evaluations.append((f"two_year_block:{row['name']}:stress", row["_stress_run"]))
    for cell_name, row in frontier.get("cells", {}).items():
        if cell_name == "U075":
            continue  # Reuses the already recorded full-history U075 objects.
        evaluations.append((f"frontier:{cell_name}:base", row["_base_run"]))
        evaluations.append((f"frontier:{cell_name}:stress", row["_stress_run"]))

    cells: dict[str, dict[str, Any]] = {}
    seen_evaluations: set[tuple[str, str]] = set()
    for scope, run in evaluations:
        configuration_id = _configuration_id(run)
        spec = run.spec.to_dict()
        spec_sha = _object_digest(spec)
        result_sha = _result_fingerprint(run)
        dedupe_key = (scope, result_sha)
        if dedupe_key in seen_evaluations:
            continue
        seen_evaluations.add(dedupe_key)
        cell = cells.setdefault(
            configuration_id,
            {
                "configuration_id": configuration_id,
                "spec": spec,
                "spec_sha256": spec_sha,
                "market_outcomes": [],
            },
        )
        if cell["spec_sha256"] != spec_sha:
            raise RuntimeError("Book U configuration identifier mapped to different specs")
        cell["market_outcomes"].append(
            {
                "scope": scope,
                "start": _date(run.start),
                "end": _date(run.end),
                "outcome_sha256": result_sha,
                "core_run_fingerprint_sha256": run.metrics.get("run_fingerprint_sha256"),
                "consumed_panel_sha256": run.metrics.get("consumed_panel_sha256"),
                "input_panel_sha256": run.metrics.get("input_panel_sha256"),
            }
        )
    ordered_cells = []
    for configuration_id in sorted(cells):
        cell = cells[configuration_id]
        cell["market_outcomes"] = sorted(
            cell["market_outcomes"], key=lambda row: (row["scope"], row["outcome_sha256"])
        )
        ordered_cells.append(cell)
    return {
        "schema_version": 1,
        "ledger_id": "book_u_cluster_trend_trials_2026_09_03",
        "protocol_sha256": integrity.get("protocol_sha256"),
        "snapshot_sha256": integrity.get("snapshot_sha256"),
        "shared_trial_ledger_observation": _strip_private(shared_ledger),
        "configuration_cell_count": len(ordered_cells),
        "market_evaluation_count": sum(len(row["market_outcomes"]) for row in ordered_cells),
        "configuration_cells": ordered_cells,
        "synthetic_engineering_controls_count_as_market_trials": False,
        "shared_trial_ledger_modified": False,
    }


def _render_report(payload: Mapping[str, Any]) -> str:
    def pct(value: Any) -> str:
        return "n/a" if value is None else f"{float(value) * 100.0:.2f}%"

    def number(value: Any) -> str:
        return "n/a" if value is None else f"{float(value):.3f}"

    lines = [
        "# Book U cluster-balanced trend gate",
        "",
        f"**Research verdict: {payload['research_status']}**",
        "",
        f"**Funded-readiness status: {payload['funded_readiness']['status']}**",
        "",
        "The strongest status this historical daily-ETF study can produce is "
        "`SHADOW_ELIGIBLE`; `FUNDED_READY` is forbidden by the frozen protocol.",
        "",
        "## Fresh-segment results",
        "",
        "| Segment | Run | CAGR | Sharpe | Max DD | Total return | Conservative worst day |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for segment_name, segment in payload["segments"].items():
        for label, key in (
            ("Base", "base"),
            ("10 bps + 25 bps stop slip", "stress_10bps_plus_25bps_stop_slippage"),
        ):
            metrics = segment[key]["metrics"]
            lines.append(
                f"| {segment_name.replace('_', ' ')} | {label} | "
                f"{pct(metrics['annualized_return'])} | {number(metrics['sharpe'])} | "
                f"{pct(metrics['max_drawdown'])} | {pct(metrics['total_return'])} | "
                f"{pct(metrics['worst_conservative_intraday_day'])} |"
            )
    lines += ["", "## Architecture gate", "", "| Check | Threshold | Status | Pass |", "|---|---|---|---:|"]
    for check in payload["architecture_gate"]["checks"]:
        lines.append(
            f"| {check['name']} | {check['threshold']} | {check['status']} | "
            f"{'yes' if check['passed'] else 'no'} |"
        )
    frontier = payload["conditional_risk_frontier"]
    lines += ["", "## Conditional risk frontier", ""]
    if frontier.get("status") == "NOT_RUN_ARCHITECTURE_GATE":
        lines.append("Not run: U075 did not clear every architecture requirement.")
    else:
        lines += [
            "| Cell | Risk/leg | Aggregate | Stress Calmar | Retention | Eligible |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for name, row in frontier.get("cells", {}).items():
            lines.append(
                f"| {name} | {pct(row['risk_per_leg'])} | {pct(row['aggregate_risk'])} | "
                f"{number(row['stressed_calmar'])} | "
                f"{pct(row['annualized_stress_base_return_retention'])} | "
                f"{'yes' if row['eligibility']['eligible'] else 'no'} |"
            )
        lines += ["", f"Selected cell: `{frontier.get('selected_cell') or 'none'}`."]
    shared = payload["shared_trial_ledger_observation"]
    lines += [
        "",
        "## Trial accounting",
        "",
        f"Shared ledger SHA-256: `{shared.get('sha256') or 'missing'}`; "
        f"{shared['object_entry_count']} spent entries; "
        f"{shared['finite_compatible_sharpes']} finite compatible Sharpes. It was read-only.",
        "",
        f"Separate Book U ledger: {payload['book_u_trial_ledger']['configuration_cell_count']} "
        f"evaluated configuration cells; SHA-256 `{payload['book_u_trial_ledger_sha256']}`.",
        "",
        "## Non-binding simultaneous-gap arithmetic",
        "",
        "| Full-history run | Maximum open gross | 5% gap loss | 10% gap loss |",
        "|---|---:|---:|---:|",
    ]
    full_gap = payload["correlated_gap_arithmetic"]["segments"]["full_available_history"]
    for label in ("base", "stress"):
        row = full_gap[label]
        lines.append(
            f"| {label} | {pct(row['max_open_gross_fraction'])} | "
            f"{pct(row['scenarios']['5pct_simultaneous_adverse_gap']['gross_loss_fraction_initial_equity'])} | "
            f"{pct(row['scenarios']['10pct_simultaneous_adverse_gap']['gross_loss_fraction_initial_equity'])} |"
        )
    lines += [
        "",
        "This is arithmetic only—not an empirical probability, fill simulation, or pass/fail gate.",
    ]
    lines += ["", "## Evidence limitations", ""]
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines += [
        "",
        "No broker order, paid challenge, paper-book state, website strategy, or shared trial ledger was changed.",
        "",
    ]
    return "\n".join(lines)


def run_gate(
    panel: dict[str, pd.DataFrame],
    integrity: Mapping[str, Any],
    *,
    trial_history: Mapping[str, Any] | None = None,
    shared_ledger: Mapping[str, Any] | None = None,
    run_fn: RunFunction = run_book_u,
    engineering: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    checked = common_book_u_panel(panel)
    index = next(iter(checked.values())).index
    required_start = _utc("2010-01-04")
    required_end = _utc("2025-12-31")
    if index.min() > required_start or index.max() < required_end:
        raise RuntimeError("Book U snapshot does not cover every frozen evidence block")
    terminal = _date(index.max())
    raw_segments: dict[str, Any] = {}
    for name, start, fixed_end in MAIN_SEGMENTS:
        end = terminal if fixed_end is None else fixed_end
        raw_segments[name] = _run_pair(
            checked, cell="U075", start=start, end=end, run_fn=run_fn
        )

    raw_blocks: list[dict[str, Any]] = []
    for name, start, end in TWO_YEAR_BLOCKS:
        stress = run_fn(
            checked,
            _spec("U075", stressed=True),
            start=start,
            end=end,
            initial_equity_usd=ACCOUNT,
        )
        raw_blocks.append(
            {
                "name": name,
                "window": {"start": start, "end": end},
                "stress_10bps_plus_25bps_stop_slippage": _summary(stress),
                "_stress_run": stress,
            }
        )

    full = raw_segments["full_available_history"]
    validation = raw_segments["retrospective_validation"]
    full_base: BookURun = full["_base_run"]
    full_stress: BookURun = full["_stress_run"]
    validation_base: BookURun = validation["_base_run"]
    validation_stress: BookURun = validation["_stress_run"]
    shared_ledger_result = (
        dict(shared_ledger)
        if shared_ledger is not None
        else _observe_shared_trial_ledger()
    )
    cpcv = _cpcv_sign_diagnostic(full_stress.equity.pct_change().dropna())
    dsr = _dsr_diagnostic(
        validation_stress.equity.pct_change().dropna(),
        (validation_base, validation_stress),
        trial_history,
        shared_ledger_result,
    )
    concentration = _cluster_concentration(full_base)
    haircut = _winner_haircut(full_base)
    caps = {
        "base": _cost_and_cap_diagnostics(full_base, checked),
        "stress": _cost_and_cap_diagnostics(full_stress, checked),
    }
    replays = {
        "base": _funded_replay_proxy(full_base),
        "stress": _funded_replay_proxy(full_stress),
    }
    engineering_result = dict(engineering) if engineering is not None else _engineering_control_suite(run_fn)
    architecture = _architecture_gate(
        raw_segments,
        raw_blocks,
        cpcv,
        dsr,
        concentration,
        haircut,
        caps,
        replays,
        engineering_result,
    )

    frontier = _conditional_frontier(
        architecture,
        checked,
        full,
        start="2010-01-04",
        end=terminal,
        run_fn=run_fn,
    )

    correlated_gap = {
        "segments": {
            name: {
                "base": _correlated_gap_arithmetic(segment["_base_run"]),
                "stress": _correlated_gap_arithmetic(segment["_stress_run"]),
            }
            for name, segment in raw_segments.items()
        },
        "fixed_two_year_blocks_stress": {
            row["name"]: _correlated_gap_arithmetic(row["_stress_run"])
            for row in raw_blocks
        },
        "frontier": {
            name: {
                "base": _correlated_gap_arithmetic(row["_base_run"]),
                "stress": _correlated_gap_arithmetic(row["_stress_run"]),
            }
            for name, row in frontier.get("cells", {}).items()
        },
        "binding": False,
    }
    book_u_ledger = _build_book_u_trial_ledger(
        raw_segments,
        raw_blocks,
        frontier,
        integrity=integrity,
        shared_ledger=shared_ledger_result,
    )

    if architecture["status"] == "FAILED":
        research_status = "NO_RESEARCH_CANDIDATE"
    elif architecture["status"] == "DATA_BLOCKED":
        research_status = "DATA_BLOCKED"
    elif frontier.get("selected_cell") is None:
        research_status = "NO_RESEARCH_CANDIDATE"
    else:
        research_status = "SHADOW_ELIGIBLE"
    if research_status not in ALLOWED_RESEARCH_STATUSES:
        raise RuntimeError("Book U status exceeded its frozen evidence ceiling")

    payload = {
        "schema_version": 1,
        "audit_id": "book_u_cluster_trend_gate_2026_09_03",
        "protocol_date": PROTOCOL_DATE,
        "research_status": research_status,
        "status_ceiling": STATUS_CEILING,
        "funded_ready_forbidden": True,
        "funded_readiness": {
            "status": "DATA_BLOCKED",
            "ready": False,
            "reason": (
                "Exact FTMO CFD symbols/contracts, executable bid/ask and intraday paths, "
                "lot/margin rules, swaps/financing/dividends, rejected orders, and stop/guard "
                "acknowledgements are unavailable."
            ),
            "promotion_requirements": (
                "Exact platform integration plus at least six unchanged forward months and "
                "100 closed holding episodes with zero rule or internal-guard breaches."
            ),
        },
        "frozen_architecture": {
            "universe": list(USD_ETF_UNIVERSE),
            "clusters": CLUSTERS,
            "base_u075": _spec("U075").to_dict(),
            "stress_u075": _spec("U075", stressed=True).to_dict(),
            "risk_cells": {
                name: {"risk_per_leg": values[0], "aggregate_risk": values[1]}
                for name, values in RISK_CELLS.items()
            },
        },
        "integrity": {
            **dict(integrity),
            "core_source_sha256": _sha256(
                ENGINE_DIR / "apex_quant" / "research" / "book_u_cluster_trend.py"
            ),
            "runner_source_sha256": _sha256(Path(__file__).resolve()),
        },
        "segments": _strip_private(raw_segments),
        "fixed_two_year_blocks": _strip_private(raw_blocks),
        "cpcv_sign_diagnostic": cpcv,
        "deflated_sharpe_ratio": dsr,
        "shared_trial_ledger_observation": _strip_private(shared_ledger_result),
        "book_u_trial_ledger_sha256": hashlib.sha256(
            _strict_json(book_u_ledger).encode("utf-8")
        ).hexdigest(),
        "book_u_trial_ledger": book_u_ledger,
        "cluster_concentration": concentration,
        "winner_haircut": haircut,
        "cost_risk_gross_position_caps": caps,
        "funded_rule_replay_proxy": replays,
        "engineering_controls": engineering_result,
        "correlated_gap_arithmetic": correlated_gap,
        "architecture_gate": architecture,
        "conditional_risk_frontier": _strip_private(frontier),
        "evidence_labels": {
            "sealed_historical_robustness": "one-shot reverse-time robustness; not forward OOS",
            "development": "engineering and architecture diagnosis",
            "retrospective_validation": "unchanged pass/fail but already research-contaminated",
            "known_data_replication": "descriptive only",
            "true_blind": "sessions strictly after the 2026-09-03 protocol freeze",
        },
        "limitations": [
            "The sealed 2010-2015 block precedes design and is not forward out-of-sample evidence.",
            "The development, retrospective-validation, and known-data periods were already accessible to the project; none is honestly blind.",
            "Adjusted Yahoo ETF OHLC is a USD total-return proxy, not the exact FTMO CFD price or contract feed.",
            "Daily OHLC cannot identify the executable intraday order of portfolio lows, stop acknowledgements, rejected orders, or guard flattening.",
            "Bid/ask quotes, platform hours, contract multipliers, lot steps, margin, swaps, financing, and dividend cash treatment are unavailable.",
            "Opening gaps can exceed the planned stop-loss budget; a stop is not a guaranteed maximum loss.",
            "The conservative funded replay is a daily/static proxy and is never binding evidence for a paid account.",
            "Only unchanged post-freeze forward evidence can add genuinely blind observations.",
        ],
    }
    return _jsonable(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument(
        "--trial-sharpe-history",
        type=Path,
        default=None,
        help=(
            "Optional isolated JSON with n_trials, sharpes, units (annualized or per_period), "
            "and periods_per_year=252. The shared ledger is still observed read-only for "
            "the spent-trial count and is never modified."
        ),
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--book-u-ledger",
        type=Path,
        default=DEFAULT_BOOK_U_LEDGER,
        help="Separate deterministic Book U-only evaluated-cell ledger",
    )
    args = parser.parse_args(argv)

    panel, integrity = _load_frozen_panel(args.snapshot, args.manifest)
    trial_history = _load_trial_sharpe_history(args.trial_sharpe_history)
    shared_ledger = _observe_shared_trial_ledger()
    payload = run_gate(
        panel,
        integrity,
        trial_history=trial_history,
        shared_ledger=shared_ledger,
    )
    encoded = _strict_json(payload)
    book_u_ledger = payload["book_u_trial_ledger"]
    ledger_encoded = _strict_json(book_u_ledger)
    # Reparse before any write: this proves the artifact contains no NaN or
    # Infinity tokens and is stable under canonical strict serialization.
    json.loads(encoded, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    json.loads(
        ledger_encoded,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    _atomic_text(args.out, encoded)
    _atomic_text(args.report, _render_report(payload))
    _atomic_text(args.book_u_ledger, ledger_encoded)
    print(
        _strict_json(
            {
                "research_status": payload["research_status"],
                "funded_readiness": payload["funded_readiness"]["status"],
                "architecture_gate": payload["architecture_gate"]["status"],
                "selected_cell": payload["conditional_risk_frontier"].get("selected_cell"),
                "json": str(args.out),
                "report": str(args.report),
                "book_u_ledger": str(args.book_u_ledger),
            }
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
