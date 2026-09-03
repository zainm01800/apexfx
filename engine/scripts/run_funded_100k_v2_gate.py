"""Run the frozen C_FUNDED_V2 cash-risk qualification protocol.

This module is intentionally research-only.  It exercises Book C's unchanged
signals through the separately preregistered evaluation and payout cash-risk
policies, from fresh 100,000-unit accounts, without writing any Book or paper
state.  Missing execution, contract, FX, or stressed-loss evidence is a binding
failure: a daily-OHLC result can never silently become funded-ready.

Binding invocation::

    cd engine
    .venv-mac/bin/python scripts/run_funded_100k_v2_gate.py

Small non-binding engineering smoke::

    .venv-mac/bin/python scripts/run_funded_100k_v2_gate.py \
        --validation-only-smoke --n-paths 25 --order-permutations 0 \
        --skip-bootstrap --skip-cpcv --skip-deep-diagnostics \
        --ledger /tmp/funded-v2-smoke-ledger.json \
        --out-json /tmp/funded-v2-smoke.json \
        --out-report /tmp/funded-v2-smoke.md
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.stats import t as student_t


ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = ENGINE_DIR.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

from apex_quant.backtest.portfolio import PortfolioBacktester, PortfolioResult  # noqa: E402
from apex_quant.config import AppConfig, get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor  # noqa: E402
from apex_quant.data._filelock import file_lock  # noqa: E402
from apex_quant.risk.trade_manager import TradeManager  # noqa: E402
from apex_quant.validation.cpcv import cpcv_splits  # noqa: E402
from apex_quant.validation.funded_simulator import (  # noqa: E402
    DayRecord,
    FundedRules,
    chunked_synchronized_funded_bootstrap,
    replay_funded_rules,
)
from apex_quant.validation.metrics import sharpe_ratio  # noqa: E402

import run_funded_100k_gate as v1  # noqa: E402
from run_book_c_deep_audit import PARAMS, TrendBook  # noqa: E402
from run_portfolio_gate import HORIZON, WARMUP  # noqa: E402


SCHEMA_VERSION = 2
PROTOCOL_DATE = "2026-09-03"
PROTOCOL_PATH = ENGINE_DIR / "data_store" / "funded_100k_v2_prereg_2026-09-03.md"
DEFAULT_LEDGER_PATH = (
    ENGINE_DIR / "data_store" / "validation"
    / "funded_100k_v2_trial_ledger_2026-09-03.json"
)
DEFAULT_JSON_PATH = (
    ENGINE_DIR / "data_store" / "validation" / "funded_100k_v2_gate_2026-09-03.json"
)
DEFAULT_REPORT_PATH = (
    ENGINE_DIR / "data_store" / "validation" / "funded_100k_v2_gate_2026-09-03.md"
)

ACCOUNT = 100_000.0
SEED = 20260903
PERIODS_PER_YEAR = 365
MEAN_BLOCK_LENGTHS = (5, 10, 21)
DEFAULT_N_PATHS = 100_000
DEFAULT_ORDER_PERMUTATIONS = 100
DEFAULT_BOOTSTRAP_CHUNK = 250

PARTITIONS: dict[str, tuple[str, str]] = {
    "build": ("2016-01-01", "2020-12-31"),
    "interim": ("2021-01-01", "2022-12-31"),
    "validation": ("2023-01-01", "2024-12-31"),
}
CONFIRMATION_START = "2025-01-01"
MODES = ("evaluation", "payout")
MAX_LOSS_MODES = ("static", "eod_trailing")

POLICIES: dict[str, dict[str, float | int]] = {
    "evaluation": {
        "base_risk_fraction": 0.0035,
        "daily_buffer_fraction": 0.15,
        "max_buffer_fraction": 0.06,
        "stressed_symbol_fraction": 0.0045,
        "aggregate_risk_fraction": 0.0090,
        "gross_exposure": 0.60,
        "correlated_exposure": 0.20,
        "position_notional": 0.08,
        "max_positions": 5,
        "guard_block": 0.009,
        "guard_flatten": 0.015,
        "cycle_drawdown": 0.05,
    },
    "payout": {
        "base_risk_fraction": 0.0025,
        "daily_buffer_fraction": 0.10,
        "max_buffer_fraction": 0.04,
        "stressed_symbol_fraction": 0.0035,
        "aggregate_risk_fraction": 0.0060,
        "gross_exposure": 0.45,
        "correlated_exposure": 0.15,
        "position_notional": 0.06,
        "max_positions": 4,
        "guard_block": 0.006,
        "guard_flatten": 0.012,
        "cycle_drawdown": 0.04,
    },
}

REQUIRED_EXACT_INPUTS = {
    "contract_metadata": "contract multiplier, lot step, leverage and symbol map",
    "fx_conversion_data": "timestamped executable account-currency FX bid/ask",
    "bid_ask_data": "one-minute-or-better venue bid/ask and fill path",
    "stressed_loss_data": "registered doubled-gap/doubled-cost loss per unit",
}


def _cell(mode: str, max_loss_mode: str) -> str:
    return f"{mode}::{max_loss_mode}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    return v1._jsonable(value)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        _jsonable(payload), indent=2, sort_keys=True, allow_nan=False,
        separators=(",", ": "),
    ) + "\n"
    v1._atomic_write(path, encoded)


def _assert_isolated_write(path: Path) -> None:
    v1._assert_isolated_write(path)
    resolved = path.expanduser().resolve()
    if resolved == DEFAULT_LEDGER_PATH.resolve() or "funded_100k_v2" in resolved.name:
        return
    # Arbitrary /tmp paths are useful for non-binding engineering smoke runs.
    if str(resolved).startswith("/tmp/") or str(resolved).startswith("/private/tmp/"):
        return
    if "validation" not in {part.lower() for part in resolved.parts}:
        raise ValueError("V2 research output must be a validation artifact or /tmp smoke file")


def _multiply_costs(cfg: AppConfig, multiplier: float) -> None:
    v1._multiply_costs(cfg, multiplier)


def v2_config(*, mode: str, cost_multiplier: float = 1.0) -> AppConfig:
    """Return one exact, isolated preregistered V2 operating configuration."""

    if mode not in POLICIES:
        raise ValueError("mode must be 'evaluation' or 'payout'")
    policy = POLICIES[mode]
    cfg = copy.deepcopy(get_config())
    cfg.backtest.initial_equity = ACCOUNT
    cfg.risk.max_risk_per_trade = float(policy["base_risk_fraction"])
    cfg.risk.max_portfolio_risk = float(policy["aggregate_risk_fraction"])
    cfg.risk.max_total_exposure = float(policy["gross_exposure"])
    cfg.risk.max_correlated_exposure = float(policy["correlated_exposure"])
    cfg.risk.max_position_notional_pct = float(policy["position_notional"])
    cfg.risk.max_concurrent_trades = int(policy["max_positions"])
    cfg.risk.max_swing_slots = int(policy["max_positions"])
    cfg.risk.slot_allocation = "expected_value"
    cfg.risk.portfolio_risk_cap_mode = "simultaneous"
    # Guard replay is evaluated separately.  A close-only breaker must not be
    # misrepresented as the independent persistent intraday funded guard.
    cfg.risk.daily_loss_limit = 0.0
    cfg.risk.daily_loss_flatten = False
    cfg.risk.drawdown_breaker = 0.99
    cfg.risk.drawdown_reducing_limit = 0.99
    _multiply_costs(cfg, cost_multiplier)
    return cfg


def _effective_config(args: argparse.Namespace) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    for mode in MODES:
        cfg = v2_config(mode=mode)
        policy = POLICIES[mode]
        for max_mode in MAX_LOSS_MODES:
            cells[_cell(mode, max_mode)] = {
                "signal_family": "Book C frozen [63,126,252] ensemble",
                "funded_cash_risk_mode": mode,
                "funded_cash_max_loss_mode": max_mode,
                "max_risk_per_trade": cfg.risk.max_risk_per_trade,
                "max_portfolio_risk": cfg.risk.max_portfolio_risk,
                "max_total_exposure": cfg.risk.max_total_exposure,
                "max_correlated_exposure": cfg.risk.max_correlated_exposure,
                "max_position_notional_pct": cfg.risk.max_position_notional_pct,
                "max_concurrent_trades": cfg.risk.max_concurrent_trades,
                "stressed_symbol_fraction": policy["stressed_symbol_fraction"],
                "entry_bar_exits": True,
                "ambiguous_daily_bar_order": "stop_first",
                "slot_allocation": "expected_value",
                "portfolio_allocation": "simultaneous_remaining_cash_risk",
            }
    return {
        "cells": cells,
        "partitions": PARTITIONS,
        "confirmation_start": CONFIRMATION_START,
        "bootstrap": {
            "n_paths": args.n_paths,
            "mean_block_lengths": list(MEAN_BLOCK_LENGTHS),
            "sizing_mode": "min_equity_initial",
            "chunk_size": args.bootstrap_chunk,
        },
        "order_permutations": args.order_permutations,
        "skip_bootstrap": bool(args.skip_bootstrap),
        "skip_cpcv": bool(args.skip_cpcv),
        "skip_deep_diagnostics": bool(args.skip_deep_diagnostics),
        "validation_only_smoke": bool(args.validation_only_smoke),
    }


def _source_hashes() -> dict[str, str]:
    """Freeze the executable Python tree, entry scripts, protocol, and config.

    Hashing only the runner's most obvious imports is insufficient: changes in
    data cleaning, trade management, metrics, or validation helpers can alter a
    result without touching those files.  The candidate ledger therefore records
    every Python module in ``apex_quant`` plus the three executable research
    entry points used by this run.
    """

    paths = {
        "protocol": PROTOCOL_PATH,
        "runner": Path(__file__).resolve(),
        "v1_runner": ENGINE_DIR / "scripts" / "run_funded_100k_gate.py",
        "book_c_audit_runner": ENGINE_DIR / "scripts" / "run_book_c_deep_audit.py",
        "portfolio_gate_runner": ENGINE_DIR / "scripts" / "run_portfolio_gate.py",
        "config": ENGINE_DIR / "config.yaml",
    }
    for path in sorted((ENGINE_DIR / "apex_quant").rglob("*.py")):
        key = "source:" + str(path.relative_to(ENGINE_DIR))
        paths[key] = path
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing V2 source: " + ", ".join(missing))
    return {name: _sha256(path) for name, path in paths.items()}


def audit_exact_inputs(paths: Mapping[str, str | None]) -> dict[str, Any]:
    """Fail closed unless every execution-critical dataset is explicitly supplied.

    Presence alone is not integration.  The current daily backtester has no
    contract/FX/bid-ask/stress-per-unit ingestion interface, so even four present
    files remain DATA_PRESENT_NOT_INTEGRATED rather than a funded-ready pass.
    """

    evidence: dict[str, Any] = {}
    for name, description in REQUIRED_EXACT_INPUTS.items():
        raw = paths.get(name)
        if raw is None:
            evidence[name] = {
                "status": "MISSING", "description": description, "path": None,
            }
            continue
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            evidence[name] = {
                "status": "MISSING", "description": description, "path": str(path),
            }
            continue
        evidence[name] = {
            "status": "PRESENT_UNVERIFIED",
            "description": description,
            "path": str(path),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
    all_present = all(row["status"] != "MISSING" for row in evidence.values())
    return {
        "required": evidence,
        "all_present": all_present,
        "integrated_into_executable_replay": False,
        "passed": False,
        "status": (
            "DATA_PRESENT_NOT_INTEGRATED" if all_present else "DATA_BLOCKED_MISSING_EXACT_INPUTS"
        ),
    }


def initialize_v2_ledger(
    path: Path,
    *,
    source_hashes: Mapping[str, str],
    effective_config: Mapping[str, Any],
    data_manifest: Mapping[str, Any],
    exact_input_manifest: Mapping[str, Any],
    prior_trial_reference: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist hashes/config/data declarations before the first V2 backtest."""

    _assert_isolated_write(path)
    expected = {
        "schema_version": SCHEMA_VERSION,
        "kind": "funded_100k_v2_dedicated_trial_ledger",
        "protocol_path": str(PROTOCOL_PATH.relative_to(REPO_DIR)),
        "protocol_sha256": source_hashes["protocol"],
        "frozen_before_first_backtest": True,
        "source_hashes": dict(source_hashes),
        "effective_config": _jsonable(effective_config),
        "data_manifest": _jsonable(data_manifest),
        "exact_input_manifest": _jsonable(exact_input_manifest),
        "prior_trial_reference": _jsonable(prior_trial_reference),
        "declaration": {
            "candidate": "C_FUNDED_V2",
            "family": "frozen_book_c_63_126_252",
            "operating_modes": list(MODES),
            "maximum_loss_variants": list(MAX_LOSS_MODES),
            "parameter_grid": False,
            "winner_selection": False,
        },
        "results": {},
    }
    with file_lock(path):
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            immutable = tuple(key for key in expected if key != "results")
            drift = [key for key in immutable if existing.get(key) != expected.get(key)]
            if drift:
                raise RuntimeError(
                    "dedicated V2 ledger conflicts with frozen run inputs: "
                    + ", ".join(drift)
                )
            if existing.get("results"):
                raise RuntimeError(
                    "dedicated V2 ledger already contains results; retries and "
                    "result overwrites are prohibited"
                )
            return existing
        _write_json(path, expected)
        return expected


def _update_ledger(path: Path, ledger: Mapping[str, Any], results: Mapping[str, Any]) -> None:
    with file_lock(path):
        if not path.is_file():
            raise RuntimeError("dedicated V2 ledger disappeared during the run")
        current = json.loads(path.read_text(encoding="utf-8"))
        expected_empty = dict(ledger)
        expected_empty["results"] = {}
        if current != expected_empty:
            raise RuntimeError(
                "dedicated V2 ledger changed during the run or already has results"
            )
        updated = dict(expected_empty)
        updated["results"] = _jsonable(results)
        _write_json(path, updated)


def _require_distinct_output_paths(*paths: Path) -> None:
    resolved = [path.expanduser().resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("ledger, JSON output, and report output must be distinct paths")


def _prior_trial_reference() -> dict[str, Any]:
    """Freeze the repository trial reference without inventing Sharpe units."""

    observed = v1._main_ledger_observation()
    return {
        **observed,
        "annualization_metadata_complete": False,
        "eligible_for_dsr": False,
        "status": "DATA_BLOCKED_TRIAL_SHARPE_ANNUALIZATION",
        "reason": (
            "The legacy trial ledger does not record each Sharpe observation's "
            "annualization convention; mixed/unknown units cannot be normalized."
        ),
    }


def _v2_dsr_reports(
    validation_returns: Mapping[str, pd.Series],
    prior_trial_reference: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return fail-closed DSR records until a unit-complete trial ledger exists."""

    candidate_sharpes = {
        name: sharpe_ratio(series.to_numpy(dtype=float), periods_per_year=1)
        for name, series in validation_returns.items()
    }
    return {
        name: {
            "status": "DATA_BLOCKED_TRIAL_SHARPE_ANNUALIZATION",
            "dsr": None,
            "passed": False,
            "repository_reference": _jsonable(prior_trial_reference),
            "candidate_validation_per_period_sharpes": candidate_sharpes,
            "reason": (
                "DSR was not calculated because the frozen prior-trial Sharpes "
                "lack complete per-observation annualization metadata."
            ),
        }
        for name in validation_returns
    }


def _run_v2(
    panel: Mapping[str, pd.DataFrame],
    *,
    mode: str,
    max_loss_mode: str,
    cost_multiplier: float = 1.0,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    warmup: int = WARMUP,
    capture_trace: bool = True,
) -> PortfolioResult:
    ordered = dict(panel)
    cfg = v2_config(mode=mode, cost_multiplier=cost_multiplier)
    pits = {name: PointInTimeAccessor(frame) for name, frame in ordered.items()}
    model = TrendBook(ordered, **PARAMS)
    return PortfolioBacktester(
        cfg,
        exit_mode="managed",
        trade_manager=TradeManager(),
        slot_allocation="expected_value",
        capture_funded_trace=capture_trace,
        enforce_entry_bar_exits=True,
        funded_cash_risk_mode=mode,
        funded_cash_max_loss_mode=max_loss_mode,
        retain_pre_start_history=True,
    ).run(
        pits,
        model.strategies(),
        timeframes={name: "1d" for name in ordered},
        warmup=warmup,
        start=v1._utc(start),
        end=v1._utc(end),
        periods_per_year=PERIODS_PER_YEAR,
    )


def _official_rules(*, max_loss_mode: str, target: float | None) -> FundedRules:
    return FundedRules(
        initial_balance=ACCOUNT,
        profit_target_pct=target,
        daily_loss_pct=0.03,
        max_loss_pct=0.10,
        max_loss_mode=max_loss_mode,
        daily_loss_basis="initial_balance",
        # Product-fit research selects the FTMO 2-Step Swing shape.  A session
        # qualifies only when the funded trace explicitly records at least one
        # opening fill; closed P&L and elapsed time are never used as proxies.
        minimum_trading_days=4,
        session_timezone="Europe/Prague",
    )


def _guard_terminal_rules(*, mode: str, target: float | None = None) -> FundedRules:
    policy = POLICIES[mode]
    # The cycle guard is peak-to-current in both account-rule variants, hence an
    # EOD-trailing replay regardless of whether the official maximum is static.
    return FundedRules(
        initial_balance=ACCOUNT,
        profit_target_pct=target,
        daily_loss_pct=float(policy["guard_flatten"]),
        max_loss_pct=float(policy["cycle_drawdown"]),
        max_loss_mode="eod_trailing",
        daily_loss_basis="initial_balance",
        session_timezone="Europe/Prague",
    )


def _replay(records: Sequence[DayRecord], *, mode: str, max_loss_mode: str) -> dict[str, Any]:
    records = v1._validate_independent_records(records)
    official = replay_funded_rules(
        records, _official_rules(max_loss_mode=max_loss_mode, target=None)
    )
    guard_terminal = replay_funded_rules(
        records, _guard_terminal_rules(mode=mode)
    )
    peak = ACCOUNT
    worst_daily = 0.0
    worst_drawdown = 0.0
    for day in records:
        worst_daily = max(
            worst_daily,
            max(0.0, (day.day_start_balance - day.intraday_min_equity) / ACCOUNT),
        )
        worst_drawdown = max(
            worst_drawdown,
            # V2's cycle latch is a fixed cash amount derived from the initial
            # account, measured from the highest completed-EOD balance.
            max(0.0, (peak - day.intraday_min_equity) / ACCOUNT),
        )
        peak = max(peak, day.end_balance)
    return {
        "official": asdict(official),
        "guard_terminal_proxy": asdict(guard_terminal),
        "official_breach": official.status == "breached",
        "guard_terminal_failure": guard_terminal.status == "breached",
        "worst_intraday_daily_loss_pct_initial": worst_daily,
        "worst_peak_to_intraday_cash_drawdown_pct_initial": worst_drawdown,
        "records": len(records),
        "semantics": (
            "Daily-OHLC terminal threshold proxy; this is not an executable replay "
            "of block/cancel/flatten acknowledgements."
        ),
    }


def _serialize_bootstrap(report: Any) -> dict[str, Any]:
    candidates: dict[str, Any] = {}
    for strategy in report.strategies:
        candidates[strategy.name] = [
            {
                "mean_block_length": block.mean_block_length,
                "pass_probability": asdict(block.pass_probability),
                "breach_probability": asdict(block.breach_probability),
                "survival_probability": asdict(block.survival_probability),
                "median_sessions_to_pass": block.median_sessions_to_pass,
                "breach_reasons": dict(block.breach_reasons),
            }
            for block in strategy.report.blocks
        ]
    return {
        "status": "DIAGNOSTIC_ONLY_POLICY_REPLAY_INCOMPLETE",
        "binding_eligible": False,
        "n_paths": report.spec.n_paths,
        "sample_length": report.spec.sample_length,
        "mean_block_lengths": list(report.spec.mean_block_lengths),
        "chunk_size": report.spec.chunk_size,
        "common_random_numbers": True,
        "sizing_mode": "min_equity_initial",
        "sizing_semantics": (
            "Exposure-level day shock scaling by min(simulated equity, initial); "
            "this is not a V2 position/event replay and does not implement the "
            "block, flatten, or cycle-halt state machine."
        ),
        "candidates": candidates,
    }


def _bootstrap(
    records: Mapping[str, Sequence[DayRecord]],
    *,
    target: float | None,
    sample_length: int,
    n_paths: int,
    seed: int,
    chunk_size: int,
) -> dict[str, Any]:
    rules = {
        name: _official_rules(
            max_loss_mode=name.split("::", 1)[1], target=target,
        )
        for name in records
    }
    report = chunked_synchronized_funded_bootstrap(
        records,
        rules,
        sizing_mode="min_equity_initial",
        n_paths=n_paths,
        sample_length=sample_length,
        mean_block_lengths=MEAN_BLOCK_LENGTHS,
        seed=seed,
        chunk_size=chunk_size,
    )
    return _serialize_bootstrap(report)


def _bootstrap_extreme(
    report: Mapping[str, Any], cell: str, field: str, bound: str, operation: str,
) -> float | None:
    values = [
        float(block[field][bound])
        for block in report.get("candidates", {}).get(cell, [])
    ]
    if not values:
        return None
    return min(values) if operation == "min" else max(values)


def _monthly_lower_95(result: PortfolioResult) -> dict[str, Any]:
    daily = result.returns.sort_index()
    monthly = daily.resample("ME").apply(lambda values: float(np.prod(1.0 + values) - 1.0))
    monthly = monthly[np.isfinite(monthly.to_numpy(dtype=float))]
    if len(monthly) < 2:
        return {
            "status": "INSUFFICIENT_MONTHS", "n_months": len(monthly),
            "mean": None, "lower_95": None,
        }
    mean = float(monthly.mean())
    standard_error = float(monthly.std(ddof=1) / math.sqrt(len(monthly)))
    critical = float(student_t.ppf(0.975, df=len(monthly) - 1))
    return {
        "status": "EVALUATED_STUDENT_T",
        "n_months": len(monthly),
        "mean": mean,
        "standard_error": standard_error,
        "lower_95": mean - critical * standard_error,
    }


def _trace_cap_diagnostics(trace: pd.DataFrame, *, mode: str) -> dict[str, Any]:
    policy = POLICIES[mode]
    equity = trace["end_equity"].to_numpy(dtype=float)
    capital = np.maximum(0.0, np.minimum(equity, ACCOUNT))
    positive_equity = np.where(equity > 0.0, equity, np.nan)
    positive_capital = np.where(capital > 0.0, capital, np.nan)
    planned_gross = trace["post_pending_planned_gross_exposure"].to_numpy(dtype=float)
    planned_risk = trace["post_pending_planned_stop_risk"].to_numpy(dtype=float)
    observed_symbol = trace["worst_symbol_adverse_loss"].to_numpy(dtype=float)
    max_gross = float(np.nanmax(planned_gross / positive_equity))
    max_stop = float(np.nanmax(planned_risk / positive_capital))
    max_symbol = float(np.nanmax(observed_symbol / positive_capital))
    aggregate_cap = float(policy["aggregate_risk_fraction"])
    return {
        "max_post_pending_gross_pct_equity": max_gross,
        "gross_cap": policy["gross_exposure"],
        "gross_cap_pass": bool(max_gross <= float(policy["gross_exposure"]) + 1e-12),
        "max_post_pending_stop_risk_pct_capital_base": max_stop,
        "aggregate_stop_risk_cap": aggregate_cap,
        "aggregate_stop_only_overrun_pct_capital_base": max(
            0.0, max_stop - aggregate_cap,
        ),
        "stop_only_cap_pass": bool(
            max_stop <= aggregate_cap + 1e-12
        ),
        "max_observed_symbol_adverse_pct_capital_base": max_symbol,
        "stressed_symbol_cap": policy["stressed_symbol_fraction"],
        "configured_correlated_exposure_cap": policy["correlated_exposure"],
        "configured_position_notional_cap": policy["position_notional"],
        "configured_max_positions": policy["max_positions"],
        "planned_loss_includes_entry_exit_costs": False,
        "stressed_loss_per_unit_available": False,
        "position_cluster_concurrency_trace_available": False,
        "exact_exposure_gate_pass": False,
        "status": "DATA_BLOCKED_COST_STRESS_AND_POSITION_LEVEL_TRACE",
    }


def _winner_haircut_diagnostic(result: PortfolioResult) -> dict[str, Any]:
    pnls = [float(trade.pnl) for trade in result.trades]
    adjusted = [0.5 * pnl if pnl > 0.0 else pnl for pnl in pnls]
    return {
        "closed_trade_net_pnl_proxy": float(sum(adjusted)),
        "positive_proxy": bool(sum(adjusted) > 0.0),
        "status": "DATA_BLOCKED_RECONCILED_EQUITY_PATH",
        "binding_pass": False,
        "reason": (
            "Closed trades can be haircutted exactly, but daily floating equity and "
            "partial-fill timing cannot be reconciled from the aggregate trace."
        ),
    }


def _concentration(
    result: PortfolioResult,
    panel: Mapping[str, pd.DataFrame],
    *,
    mode: str,
    max_loss_mode: str,
) -> dict[str, Any]:
    cfg = get_config()
    by_instrument: dict[str, float] = defaultdict(float)
    by_cluster: dict[str, float] = defaultdict(float)
    for trade in result.trades:
        pnl = float(trade.pnl)
        by_instrument[trade.instrument] += pnl
        by_cluster[v1._cluster_for(trade.instrument, cfg)] += pnl
    total = float(sum(by_instrument.values()))
    positive_instruments = {key: value for key, value in by_instrument.items() if value > 0.0}
    positive_clusters = {key: value for key, value in by_cluster.items() if value > 0.0}
    instrument_share = (
        max(positive_instruments.values()) / total
        if total > 0.0 and positive_instruments else None
    )
    cluster_share = (
        max(positive_clusters.values()) / total
        if total > 0.0 and positive_clusters else None
    )
    top_cluster = max(positive_clusters, key=positive_clusters.get) if positive_clusters else None
    removal: dict[str, Any] | None = None
    if top_cluster is not None:
        reduced_panel = {
            name: frame for name, frame in panel.items()
            if v1._cluster_for(name, cfg) != top_cluster
        }
        reduced = _run_v2(
            reduced_panel,
            mode=mode,
            max_loss_mode=max_loss_mode,
            start=PARTITIONS["validation"][0],
            end=PARTITIONS["validation"][1],
            capture_trace=False,
        )
        removal = {
            "removed_cluster": top_cluster,
            "remaining_instruments": len(reduced_panel),
            "validation_metrics": reduced.metrics,
            "positive": bool(reduced.metrics.get("total_return", 0.0) > 0.0),
        }
    diagnostic_passed = bool(
        instrument_share is not None
        and cluster_share is not None
        and instrument_share <= 0.35
        and cluster_share <= 0.35
        and removal is not None
        and removal["positive"]
    )
    return {
        "status": "DATA_BLOCKED_UNRECONCILED_TERMINAL_POSITIONS",
        "by_instrument_net_pnl": dict(sorted(by_instrument.items())),
        "by_cluster_net_pnl": dict(sorted(by_cluster.items())),
        "validation_net_trade_pnl": total,
        "max_positive_instrument_share_of_net_profit": instrument_share,
        "max_positive_cluster_share_of_net_profit": cluster_share,
        "top_cluster_removal": removal,
        "diagnostic_closed_trade_pass": diagnostic_passed,
        "passed": False,
        "reason": (
            "The denominator contains completed Trade.pnl only, while final equity "
            "also contains terminal open-position MTM, entry costs, and partial "
            "realizations. Exact liquidation/attribution is required."
        ),
    }


def _fingerprint(result: PortfolioResult) -> str:
    return v1._decision_fingerprint(result)


def _future_poison(
    panel: Mapping[str, pd.DataFrame],
    *,
    mode: str,
    max_loss_mode: str,
    reference: PortfolioResult,
) -> dict[str, Any]:
    poisoned: dict[str, pd.DataFrame] = {}
    cutoff = v1._utc(PARTITIONS["validation"][1])
    rows = 0
    for name, frame in panel.items():
        changed = frame.copy()
        mask = changed.index > cutoff
        rows += int(mask.sum())
        if mask.any():
            multiples = np.linspace(10.0, 1000.0, int(mask.sum()))
            for column in ("open", "high", "low", "close"):
                changed.loc[mask, column] = changed.loc[mask, column].to_numpy(dtype=float) * multiples
            if "volume" in changed:
                changed.loc[mask, "volume"] = 9.99e18
        poisoned[name] = changed
    result = _run_v2(
        poisoned,
        mode=mode,
        max_loss_mode=max_loss_mode,
        start=PARTITIONS["validation"][0],
        end=PARTITIONS["validation"][1],
        capture_trace=False,
    )
    return {
        "status": "EVALUATED",
        "rows_poisoned": rows,
        "reference_fingerprint": _fingerprint(reference),
        "poisoned_fingerprint": _fingerprint(result),
        "passed": bool(rows > 0 and _fingerprint(reference) == _fingerprint(result)),
    }


def _order_permutations(
    panel: Mapping[str, pd.DataFrame],
    *,
    mode: str,
    max_loss_mode: str,
    reference: PortfolioResult,
    count: int,
) -> dict[str, Any]:
    if count <= 0:
        return {"status": "SKIPPED_NON_BINDING", "requested": count, "passed": False}
    names = list(panel)
    expected = _fingerprint(reference)
    rng = np.random.default_rng(SEED + (1 if mode == "evaluation" else 2))
    mismatches: list[dict[str, Any]] = []
    completed = 0
    for iteration in range(count):
        order = list(rng.permutation(names))
        result = _run_v2(
            {name: panel[name] for name in order},
            mode=mode,
            max_loss_mode=max_loss_mode,
            start=PARTITIONS["validation"][0],
            end=PARTITIONS["validation"][1],
            capture_trace=False,
        )
        completed += 1
        observed = _fingerprint(result)
        if observed != expected:
            mismatches.append({
                "permutation": iteration + 1,
                "fingerprint": observed,
                "first_five": order[:5],
            })
            if len(mismatches) >= 10:
                break
    return {
        "status": "EVALUATED",
        "requested": count,
        "completed": completed,
        "mismatches": mismatches,
        "engineering_smoke_equal": not mismatches,
        "passed": bool(not mismatches and count == DEFAULT_ORDER_PERMUTATIONS),
    }


def _cpcv_cell(
    panel: Mapping[str, pd.DataFrame],
    *,
    mode: str,
    max_loss_mode: str,
    skip: bool,
) -> dict[str, Any]:
    if skip:
        return {"status": "SKIPPED_NON_BINDING", "n_paths": 0, "passed": False}
    cutoff = v1._utc(PARTITIONS["validation"][1])
    evidence = {name: frame.loc[frame.index <= cutoff].copy() for name, frame in panel.items()}
    timeline = pd.DatetimeIndex(sorted(set().union(*(set(frame.index) for frame in evidence.values()))))
    cfg = v2_config(mode=mode)
    spec = cfg.validation.cpcv
    splits = cpcv_splits(
        len(timeline), spec.n_groups, spec.n_test_groups, spec.embargo_pct, purge=HORIZON,
    )
    sharpes: list[float] = []
    independent_blocks = 0
    for _train, test_indices in splits:
        breaks = np.flatnonzero(np.diff(test_indices) > 1) + 1
        returns: list[np.ndarray] = []
        for block in np.split(test_indices, breaks):
            if len(block) < 2:
                continue
            result = _run_v2(
                evidence,
                mode=mode,
                max_loss_mode=max_loss_mode,
                start=timeline[int(block[0])],
                end=timeline[int(block[-1])],
                capture_trace=False,
            )
            selected = result.returns[result.returns.index.isin(timeline[block])]
            if not selected.empty:
                returns.append(selected.to_numpy(dtype=float))
            independent_blocks += 1
        combined = np.concatenate(returns) if returns else np.asarray([], dtype=float)
        sharpes.append(sharpe_ratio(combined, periods_per_year=1))
    positive = int(sum(value > 0.0 for value in sharpes))
    diagnostic_passed = bool(len(sharpes) == 15 and positive >= 12)
    return {
        "status": "DATA_BLOCKED_UNRECONCILED_BLOCK_BOUNDARIES",
        "n_paths": len(sharpes),
        "positive_paths": positive,
        "required_positive_paths": 12,
        "oos_sharpe_paths": [round(float(value), 4) for value in sharpes],
        "independent_test_blocks": independent_blocks,
        "purge_sessions": HORIZON,
        "embargo_pct": spec.embargo_pct,
        "fixed_model_training_indices_used": False,
        "terminal_positions_reconciled": False,
        "diagnostic_12_of_15_pass": diagnostic_passed,
        "passed": False,
    }


def _gate(
    name: str, threshold: str, value: Any, passed: bool, *, status: str = "EVALUATED",
) -> dict[str, Any]:
    return {
        "gate": name, "threshold": threshold, "value": value,
        "status": status, "passed": bool(passed),
    }


def _render_report(payload: Mapping[str, Any]) -> str:
    lines = [
        "# C_FUNDED_V2 pre-registered research gate",
        "",
        f"**Verdict: {payload['verdict']}**",
        "",
        "This daily-OHLC result is research only. Missing executable contract, FX, "
        "bid/ask, stressed-loss, and guard-acknowledgement evidence fails closed.",
        "",
        "| Cell | Return | Annualized | Sharpe | Profit factor | Passed gates |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, cell in payload.get("cells", {}).items():
        metrics = cell.get("validation_metrics") or {}
        gates = cell.get("gates") or []
        passed = sum(bool(gate.get("passed")) for gate in gates)
        lines.append(
            f"| {name} | {v1._pct(metrics.get('total_return'))} | "
            f"{v1._pct(metrics.get('ann_return'))} | {v1._num(metrics.get('sharpe'))} | "
            f"{v1._num(metrics.get('profit_factor'))} | {passed}/{len(gates)} |"
        )
    lines += ["", "## Binding blockers", ""]
    for reason in payload.get("blocking_reasons", []):
        lines.append(f"- {reason}")
    lines += [
        "",
        "No paid challenge, broker order, website strategy, or paper-book state was changed.",
        "",
    ]
    return "\n".join(lines)


def _validation_only_blocked_cell(name: str) -> dict[str, Any]:
    return {
        "cell": name,
        "status": "DATA_BLOCKED_VALIDATION_ONLY_SMOKE",
        "validation_metrics": None,
        "gates": [
            _gate(
                "fresh_build_interim_validation_partitions", "all three",
                "validation-only engineering smoke", False,
                status="SKIPPED_NON_BINDING",
            )
        ],
        "passed_all_binding_gates": False,
    }


def qualification_decision(
    cells: Mapping[str, Mapping[str, Any]],
    exact_inputs: Mapping[str, Any],
    binding_invocation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the fail-closed boundary after all numerical diagnostics.

    Green returns, Sharpe, bootstrap, and replay numbers are insufficient.  The
    exact execution inputs must be integrated and every engine cell must itself
    report readiness with no unresolved blockers.
    """

    required = tuple(
        _cell(mode, max_mode)
        for mode in MODES for max_mode in MAX_LOSS_MODES
    )
    cells_present = all(name in cells for name in required)
    numerical_and_gate_pass = bool(
        cells_present
        and all(bool(cells[name].get("passed_all_binding_gates")) for name in required)
    )
    exact_ready = bool(
        exact_inputs.get("passed")
        and exact_inputs.get("integrated_into_executable_replay")
    )
    engine_ready = bool(
        cells_present
        and all(
            cells[name].get("engine_cash_risk_status") == "READY"
            and not cells[name].get("engine_cash_risk_blockers")
            for name in required
        )
    )
    invocation_ready = bool(
        binding_invocation is not None and binding_invocation.get("passed")
    )
    passed = (
        numerical_and_gate_pass
        and exact_ready
        and engine_ready
        and invocation_ready
    )
    return {
        "required_cells_present": cells_present,
        "all_cell_gates_pass": numerical_and_gate_pass,
        "exact_inputs_integrated": exact_ready,
        "engine_ready": engine_ready,
        "binding_invocation": invocation_ready,
        "passed": passed,
        "verdict": (
            "PROVISIONAL_SHADOW_ELIGIBLE" if passed else "NO_FUNDED_STRATEGY_V2"
        ),
    }


def _binding_invocation(args: argparse.Namespace) -> dict[str, Any]:
    observed = {
        "n_paths": int(args.n_paths),
        "order_permutations": int(args.order_permutations),
        "skip_bootstrap": bool(args.skip_bootstrap),
        "skip_cpcv": bool(args.skip_cpcv),
        "skip_deep_diagnostics": bool(args.skip_deep_diagnostics),
        "validation_only_smoke": bool(args.validation_only_smoke),
    }
    passed = bool(
        observed["n_paths"] == DEFAULT_N_PATHS
        and observed["order_permutations"] == DEFAULT_ORDER_PERMUTATIONS
        and not observed["skip_bootstrap"]
        and not observed["skip_cpcv"]
        and not observed["skip_deep_diagnostics"]
        and not observed["validation_only_smoke"]
    )
    return {
        "required": {
            "n_paths": DEFAULT_N_PATHS,
            "order_permutations": DEFAULT_ORDER_PERMUTATIONS,
            "all_skip_and_smoke_flags": False,
        },
        "observed": observed,
        "passed": passed,
        "status": "BINDING_INVOCATION" if passed else "NON_BINDING_INVOCATION",
    }


def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    if not PROTOCOL_PATH.is_file():
        raise FileNotFoundError(PROTOCOL_PATH)
    ledger_path = Path(args.ledger).expanduser()
    out_json = Path(args.out_json).expanduser()
    out_report = Path(args.out_report).expanduser()
    _require_distinct_output_paths(ledger_path, out_json, out_report)
    for path in (ledger_path, out_json, out_report):
        _assert_isolated_write(path)

    # Loading and hashing are read-only.  The immutable ledger is written before
    # the first call to PortfolioBacktester, as required by the preregistration.
    panel, data_manifest = v1._load_panel()
    exact_inputs = audit_exact_inputs({
        "contract_metadata": args.contract_metadata,
        "fx_conversion_data": args.fx_conversion_data,
        "bid_ask_data": args.bid_ask_data,
        "stressed_loss_data": args.stressed_loss_data,
    })
    source_hashes = _source_hashes()
    effective_config = _effective_config(args)
    prior_trial_reference = _prior_trial_reference()
    binding_invocation = _binding_invocation(args)
    ledger = initialize_v2_ledger(
        ledger_path,
        source_hashes=source_hashes,
        effective_config=effective_config,
        data_manifest=data_manifest,
        exact_input_manifest=exact_inputs,
        prior_trial_reference=prior_trial_reference,
    )

    latest = max(frame.index.max() for frame in panel.values())
    partitions: dict[str, tuple[str, str | pd.Timestamp]] = (
        {"validation": PARTITIONS["validation"]}
        if args.validation_only_smoke
        else {
            **PARTITIONS,
            "confirmation_previously_known": (CONFIRMATION_START, latest),
        }
    )

    results: dict[str, dict[str, PortfolioResult]] = defaultdict(dict)
    traces: dict[str, dict[str, pd.DataFrame]] = defaultdict(dict)
    records: dict[str, dict[str, tuple[DayRecord, ...]]] = defaultdict(dict)
    replays: dict[str, dict[str, Any]] = defaultdict(dict)
    seed_manifests: dict[str, dict[str, Any]] = defaultdict(dict)

    for mode in MODES:
        for max_mode in MAX_LOSS_MODES:
            name = _cell(mode, max_mode)
            for partition, (start, end) in partitions.items():
                result = _run_v2(
                    panel, mode=mode, max_loss_mode=max_mode, start=start, end=end,
                )
                trace = v1._require_balance_trace(result)
                day_records = v1.trace_to_day_records(trace)
                v1._validate_independent_records(day_records)
                results[name][partition] = result
                traces[name][partition] = trace
                records[name][partition] = day_records
                replays[name][partition] = _replay(
                    day_records, mode=mode, max_loss_mode=max_mode,
                )
                seed_manifests[name][partition] = v1._partition_seed_manifest(
                    trace, start=start, end=end,
                )

    validation_results = {name: value["validation"] for name, value in results.items()}
    validation_records = {name: value["validation"] for name, value in records.items()}

    doubled_results: dict[str, PortfolioResult] = {}
    doubled_payload: dict[str, Any] = {}
    for mode in MODES:
        for max_mode in MAX_LOSS_MODES:
            name = _cell(mode, max_mode)
            result = _run_v2(
                panel,
                mode=mode,
                max_loss_mode=max_mode,
                cost_multiplier=2.0,
                start=PARTITIONS["validation"][0],
                end=PARTITIONS["validation"][1],
            )
            trace = v1._require_balance_trace(result)
            day_records = v1.trace_to_day_records(trace)
            v1._validate_independent_records(day_records)
            doubled_results[name] = result
            doubled_payload[name] = {
                "metrics": result.metrics,
                "replay": _replay(day_records, mode=mode, max_loss_mode=max_mode),
                "independent_seed": v1._partition_seed_manifest(
                    trace,
                    start=PARTITIONS["validation"][0],
                    end=PARTITIONS["validation"][1],
                ),
            }

    if args.skip_bootstrap:
        evaluation_bootstrap = payout_12m = payout_24m = {
            "status": "SKIPPED_NON_BINDING", "candidates": {}, "n_paths": 0,
        }
    else:
        evaluation_cases = {
            name: rows for name, rows in validation_records.items()
            if name.startswith("evaluation::")
        }
        payout_cases = {
            name: rows for name, rows in validation_records.items()
            if name.startswith("payout::")
        }
        evaluation_bootstrap = _bootstrap(
            evaluation_cases,
            target=0.10,
            sample_length=252,
            n_paths=args.n_paths,
            seed=SEED + 100,
            chunk_size=args.bootstrap_chunk,
        )
        payout_12m = _bootstrap(
            payout_cases,
            target=None,
            sample_length=365,
            n_paths=args.n_paths,
            seed=SEED + 200,
            chunk_size=args.bootstrap_chunk,
        )
        payout_24m = _bootstrap(
            payout_cases,
            target=None,
            sample_length=730,
            n_paths=args.n_paths,
            seed=SEED + 300,
            chunk_size=args.bootstrap_chunk,
        )

    if args.skip_deep_diagnostics:
        poison = concentration = permutations = {
            name: {"status": "SKIPPED_NON_BINDING", "passed": False}
            for name in validation_results
        }
    else:
        poison = {}
        concentration = {}
        permutations = {}
        for mode in MODES:
            for max_mode in MAX_LOSS_MODES:
                name = _cell(mode, max_mode)
                poison[name] = _future_poison(
                    panel, mode=mode, max_loss_mode=max_mode,
                    reference=validation_results[name],
                )
                concentration[name] = _concentration(
                    validation_results[name], panel, mode=mode,
                    max_loss_mode=max_mode,
                )
                permutations[name] = _order_permutations(
                    panel, mode=mode, max_loss_mode=max_mode,
                    reference=validation_results[name],
                    count=args.order_permutations,
                )

    cpcv = {}
    for mode in MODES:
        for max_mode in MAX_LOSS_MODES:
            name = _cell(mode, max_mode)
            cpcv[name] = _cpcv_cell(
                panel, mode=mode, max_loss_mode=max_mode, skip=args.skip_cpcv,
            )

    validation_returns = {name: result.returns for name, result in validation_results.items()}
    dsr = _v2_dsr_reports(validation_returns, prior_trial_reference)

    cells: dict[str, Any] = {}
    ledger_results: dict[str, Any] = {}
    for mode in MODES:
        policy = POLICIES[mode]
        for max_mode in MAX_LOSS_MODES:
            name = _cell(mode, max_mode)
            result = validation_results[name]
            metrics = result.metrics
            required_partitions = tuple(PARTITIONS)
            partitions_present = all(partition in replays[name] for partition in required_partitions)
            historical_zero = bool(
                partitions_present
                and all(not replays[name][part]["official_breach"] for part in required_partitions)
            )
            worst_daily = (
                max(replays[name][part]["worst_intraday_daily_loss_pct_initial"] for part in required_partitions)
                if partitions_present else None
            )
            worst_dd = (
                max(replays[name][part]["worst_peak_to_intraday_cash_drawdown_pct_initial"] for part in required_partitions)
                if partitions_present else None
            )
            cap_diag = _trace_cap_diagnostics(
                pd.concat([traces[name][part] for part in traces[name]]), mode=mode,
            )
            engine_cash_status = result.metrics.get("funded_cash_risk_status")
            engine_cash_blockers = list(
                result.metrics.get("funded_cash_risk_blockers", [])
            )
            haircut = _winner_haircut_diagnostic(result)
            doubled_return = float(doubled_results[name].metrics.get("total_return", 0.0))
            monthly = _monthly_lower_95(result)

            common = [
                _gate(
                    "fresh_independent_build_interim_validation",
                    "all start flat at 100,000",
                    partitions_present,
                    partitions_present,
                    status="EVALUATED" if partitions_present else "SKIPPED_NON_BINDING",
                ),
                _gate("zero_official_breaches", "0 in build/interim/validation", historical_zero, historical_zero),
                _gate(
                    "combined_historical_stress_zero_breaches", "0",
                    "intraday gap/outage/partial-fill/missed-stop event stream absent",
                    False, status="DATA_BLOCKED_EXECUTION_PATH",
                ),
                _gate("validation_sharpe", ">= 0.75", metrics.get("sharpe"), float(metrics.get("sharpe", 0.0)) >= 0.75),
                _gate("validation_profit_factor", ">= 1.15", metrics.get("profit_factor"), float(metrics.get("profit_factor") or 0.0) >= 1.15),
                _gate("validation_doubled_cost_return", "> 0", doubled_return, doubled_return > 0.0),
                _gate(
                    "exact_winner_haircut_return", "> 0", haircut,
                    False, status=haircut["status"],
                ),
                _gate("purged_cpcv", ">= 12/15 positive", cpcv[name], bool(cpcv[name].get("passed")), status=cpcv[name]["status"]),
                _gate("deflated_sharpe_ratio", ">= 0.95", dsr[name].get("dsr"), bool(dsr[name].get("passed")), status=dsr[name]["status"]),
                _gate("top_profit_cluster_removal_and_concentration", "positive and <=35%", concentration[name], bool(concentration[name].get("passed")), status=concentration[name]["status"]),
                _gate("future_poison_causality", "identical", poison[name], bool(poison[name].get("passed")), status=poison[name]["status"]),
                _gate("input_order_permutations", "100/100 identical", permutations[name], bool(permutations[name].get("passed")), status=permutations[name]["status"]),
                _gate(
                    "observed_aggregate_stop_only_risk",
                    f"<= {float(policy['aggregate_risk_fraction']):.2%} of C",
                    {
                        "maximum": cap_diag[
                            "max_post_pending_stop_risk_pct_capital_base"
                        ],
                        "overrun": cap_diag[
                            "aggregate_stop_only_overrun_pct_capital_base"
                        ],
                        "currency_basis": "UNCONVERTED_RAW_QUOTE_CURRENCY",
                    },
                    bool(cap_diag["stop_only_cap_pass"]),
                    status="EVALUATED_RAW_QUOTE_STOP_ONLY",
                ),
                _gate(
                    "aggregate_carried_stop_risk_not_continuously_rebalanced",
                    "never exceed cap between decisions; include ordinary costs",
                    (
                        "carried-position mark-to-stop risk is not continuously "
                        "de-risked and planned risk excludes ordinary costs"
                    ),
                    False,
                    status="IMPLEMENTATION_BLOCKED",
                ),
                _gate(
                    "gross_correlation_notional_concurrency_and_cash_risk",
                    "all V2 limits observed including costs/stress",
                    cap_diag, False, status=cap_diag["status"],
                ),
                _gate(
                    "engine_cash_risk_readiness",
                    "READY with no unresolved implementation/data blockers",
                    {
                        "status": engine_cash_status,
                        "blockers": engine_cash_blockers,
                    },
                    False,
                    status=str(engine_cash_status or "DATA_BLOCKED"),
                ),
                _gate(
                    "exact_contract_fx_bidask_stressed_loss_inputs",
                    "all present, mapped, validated and replayed",
                    exact_inputs, False, status=exact_inputs["status"],
                ),
                _gate(
                    "protective_stop_acknowledged_with_entry",
                    "venue-native acknowledgement for every entry",
                    "daily backtest has no venue acknowledgement stream",
                    False, status="DATA_BLOCKED_ORDER_ACKNOWLEDGEMENT",
                ),
                _gate(
                    "pending_order_fill_time_revalidation",
                    "resize/cancel from opening equity before fill",
                    "current historical event ordering does not causally revalidate next-open pending risk",
                    False, status="IMPLEMENTATION_BLOCKED",
                ),
                _gate(
                    "persistent_guard_counterfactual",
                    "block/cancel/flatten/verify/latch",
                    "threshold replay is available but executable state transitions are not",
                    False, status="DATA_BLOCKED_INTRADAY_GUARD",
                ),
            ]

            if mode == "evaluation":
                pass_lower = _bootstrap_extreme(
                    evaluation_bootstrap, name, "pass_probability", "lower", "min",
                )
                breach_upper = _bootstrap_extreme(
                    evaluation_bootstrap, name, "breach_probability", "upper", "max",
                )
                specific = [
                    _gate("worst_intraday_daily_loss", "<= 1.50%", worst_daily, worst_daily is not None and worst_daily <= 0.015),
                    _gate("maximum_drawdown", "<= 5.00%", worst_dd, worst_dd is not None and worst_dd <= 0.05),
                    _gate(
                        "reach_10pct_252_lower_wilson", ">= 70%", pass_lower,
                        False, status="DATA_BLOCKED_BOOTSTRAP_POLICY_REPLAY",
                    ),
                    _gate(
                        "hard_breach_upper_wilson", "<= 5%", breach_upper,
                        False, status="DATA_BLOCKED_BOOTSTRAP_POLICY_REPLAY",
                    ),
                ]
                bootstrap_payload = {"evaluation_252": evaluation_bootstrap}
            else:
                survival_12 = _bootstrap_extreme(
                    payout_12m, name, "survival_probability", "lower", "min",
                )
                survival_24 = _bootstrap_extreme(
                    payout_24m, name, "survival_probability", "lower", "min",
                )
                annual = float(metrics.get("ann_return", 0.0))
                doubled_annual = float(doubled_results[name].metrics.get("ann_return", 0.0))
                specific = [
                    _gate("worst_intraday_daily_loss", "<= 1.20%", worst_daily, worst_daily is not None and worst_daily <= 0.012),
                    _gate("maximum_drawdown", "<= 4.00%", worst_dd, worst_dd is not None and worst_dd <= 0.04),
                    _gate(
                        "survival_12m_lower_wilson", ">= 99%", survival_12,
                        False, status="DATA_BLOCKED_BOOTSTRAP_POLICY_REPLAY",
                    ),
                    _gate(
                        "survival_24m_lower_wilson", ">= 97.5%", survival_24,
                        False, status="DATA_BLOCKED_BOOTSTRAP_POLICY_REPLAY",
                    ),
                    _gate(
                        "combined_stress_12m_survival_lower_wilson", ">= 95%",
                        "combined severe intraday execution path unavailable", False,
                        status="DATA_BLOCKED_EXECUTION_PATH",
                    ),
                    _gate("validation_annualized_return", ">= 4%", annual, annual >= 0.04),
                    _gate("doubled_cost_annualized_return", ">= 2%", doubled_annual, doubled_annual >= 0.02),
                    _gate("monthly_return_lower_95", "> 0", monthly.get("lower_95"), monthly.get("lower_95") is not None and monthly["lower_95"] > 0.0, status=monthly["status"]),
                ]
                bootstrap_payload = {
                    "survival_12m": payout_12m, "survival_24m": payout_24m,
                    "combined_severe": {
                        "status": "DATA_BLOCKED_EXECUTION_PATH", "passed": False,
                    },
                }
            gates = common + specific
            passed = all(gate["passed"] for gate in gates)
            stress_scenarios = {
                "base": {
                    "status": "EVALUATED_DAILY_OHLC_PROVISIONAL",
                    "historical_replay": replays[name],
                },
                "doubled_costs": {
                    "status": "EVALUATED_ENGINE_RERUN",
                    "validation": doubled_payload[name],
                },
                "volatility_1_5x_with_2x_gaps": {
                    "status": "DATA_BLOCKED",
                    "reason": "daily trace has no separable continuous-volatility and gap components",
                },
                "one_30_min_liquidation_outage_per_year": {
                    "status": "DATA_BLOCKED",
                    "reason": "no intraday liquidation clock or executable queued-fill path",
                },
                "fills_50pct_on_worst_liquidity_sessions": {
                    "status": "DATA_BLOCKED",
                    "reason": "no order book, fill acknowledgement, rejects, or depth history",
                },
                "one_missed_stop_per_year": {
                    "status": "DATA_BLOCKED",
                    "reason": "no venue stop-order event stream or next executable quote path",
                },
                "winners_reduced_50pct_losses_unchanged": haircut,
                "combined_severe": {
                    "status": "DATA_BLOCKED",
                    "reason": "unsupported execution events cannot be synthesized without inventing a path",
                },
            }
            cells[name] = {
                "cell": name,
                "mode": mode,
                "maximum_loss_mode": max_mode,
                "status": "PROVISIONAL_SHADOW_ELIGIBLE" if passed else "FAILED_BINDING_GATES",
                "risk_policy": dict(policy),
                "validation_metrics": metrics,
                "doubled_cost_validation": doubled_payload[name],
                "monthly_return_confidence": monthly,
                "historical_replay": replays[name],
                "independent_partition_manifest": seed_manifests[name],
                "cap_diagnostics": cap_diag,
                "engine_cash_risk_status": engine_cash_status,
                "engine_cash_risk_blockers": engine_cash_blockers,
                "winner_haircut": haircut,
                "stress_scenarios": stress_scenarios,
                "bootstrap": bootstrap_payload,
                "cpcv": cpcv[name],
                "dsr": dsr[name],
                "concentration": concentration[name],
                "future_poison": poison[name],
                "order_permutations": permutations[name],
                "gates": gates,
                "passed_all_binding_gates": passed,
            }
            ledger_results[name] = {
                "validation_per_period_sharpe": sharpe_ratio(
                    result.returns.to_numpy(dtype=float), periods_per_year=1,
                ),
                "passed_all_binding_gates": passed,
            }

    qualification = qualification_decision(
        cells, exact_inputs, binding_invocation,
    )
    all_cells_pass = bool(qualification["passed"])
    verdict = str(qualification["verdict"])
    aggregate_overruns = {
        name: cell["cap_diagnostics"][
            "aggregate_stop_only_overrun_pct_capital_base"
        ]
        for name, cell in cells.items()
        if not cell["cap_diagnostics"]["stop_only_cap_pass"]
    }
    blocking_reasons = [
        "Whole-day bootstrap output is diagnostic only: it does not replay V2 position state or persistent guard block/flatten/cycle-halt transitions.",
        "The historical trial ledger lacks per-observation Sharpe annualization metadata, so DSR is data-blocked.",
        "Concentration is data-blocked until terminal open positions and partial realizations are reconciled to final equity.",
        "Exact one-minute-or-better venue bid/ask, fills, spread, commission, swap, and liquidation event data are not integrated.",
        "Book C monetary exposures mix quote currencies without executable account-currency FX conversion.",
        "Planned stop-risk accounting currently excludes ordinary entry/exit costs and has no registered stressed-loss-per-unit feed.",
        "Queued next-open entries are not causally resized or cancelled from the opening account cushions before fill.",
        "The guard has no atomic broker-backed open-plus-pending risk reservation, so separate approvals cannot be treated as spendable order budgets.",
        "Daily OHLC cannot prove persistent block/cancel/flatten/fill-verification/latch guard transitions.",
        "The exact funded firm, product, platform, symbols, and contract specifications have not been frozen.",
    ]
    if aggregate_overruns:
        rendered = ", ".join(
            f"{name} +{overrun:.3%} of C" for name, overrun in sorted(aggregate_overruns.items())
        )
        blocking_reasons.insert(
            0,
            "Observed stop-only aggregate planned risk exceeded its registered cap "
            f"({rendered}); carried risk is not continuously de-risked between decisions.",
        )
    if not all_cells_pass:
        blocking_reasons.insert(0, "At least one required V2 cell failed a binding preregistered gate.")

    _update_ledger(ledger_path, ledger, ledger_results)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol": {
            "path": str(PROTOCOL_PATH.relative_to(REPO_DIR)),
            "sha256": source_hashes["protocol"],
            "registered_on": PROTOCOL_DATE,
            "historical_blind_claim": False,
            "pbo": "N/A_SINGLE_FIXED_CANDIDATE",
            "evidence_ceiling": "PROVISIONAL_PAPER_ONLY",
        },
        "run_config": {
            **effective_config,
            "binding_invocation_audit": binding_invocation,
            "smoke_or_non_binding": bool(
                args.validation_only_smoke
                or args.skip_bootstrap
                or args.skip_cpcv
                or args.skip_deep_diagnostics
                or args.n_paths != DEFAULT_N_PATHS
                or args.order_permutations != DEFAULT_ORDER_PERMUTATIONS
            ),
        },
        "isolation": {
            "dedicated_ledger": str(ledger_path),
            "shared_trial_ledger_written": False,
            "book_or_paper_state_written": False,
            "website_or_broker_state_written": False,
            "output_json": str(out_json),
            "output_report": str(out_report),
        },
        "source_hashes": source_hashes,
        "data_manifest": data_manifest,
        "exact_input_manifest": exact_inputs,
        "prior_trial_reference": prior_trial_reference,
        "cells": cells,
        "candidate_passed_all_modes_and_variants": all_cells_pass,
        "qualification_decision": qualification,
        "blocking_reasons": blocking_reasons,
        "winner": "C_FUNDED_V2" if all_cells_pass else None,
        "verdict": verdict,
    }
    _write_json(out_json, payload)
    v1._atomic_write(out_report, _render_report(payload))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Frozen 2026-09-03 C_FUNDED_V2 cash-risk research gate"
    )
    parser.add_argument("--out-json", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--out-report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument("--n-paths", type=int, default=DEFAULT_N_PATHS)
    parser.add_argument("--order-permutations", type=int, default=DEFAULT_ORDER_PERMUTATIONS)
    parser.add_argument("--bootstrap-chunk", type=int, default=DEFAULT_BOOTSTRAP_CHUNK)
    parser.add_argument("--skip-bootstrap", action="store_true")
    parser.add_argument("--skip-cpcv", action="store_true")
    parser.add_argument("--skip-deep-diagnostics", action="store_true")
    parser.add_argument("--validation-only-smoke", action="store_true")
    parser.add_argument("--contract-metadata")
    parser.add_argument("--fx-conversion-data")
    parser.add_argument("--bid-ask-data")
    parser.add_argument("--stressed-loss-data")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.n_paths <= 0:
        parser.error("--n-paths must be positive")
    if args.order_permutations < 0:
        parser.error("--order-permutations cannot be negative")
    if args.bootstrap_chunk <= 0:
        parser.error("--bootstrap-chunk must be positive")
    payload = run_gate(args)
    print(json.dumps({
        "verdict": payload["verdict"],
        "winner": payload["winner"],
        "evidence_ceiling": payload["protocol"]["evidence_ceiling"],
        "json": args.out_json,
        "report": args.out_report,
        "ledger": args.ledger,
    }, indent=2, sort_keys=True))
    return 0 if payload["verdict"] == "PROVISIONAL_SHADOW_ELIGIBLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
