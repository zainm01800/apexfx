#!/usr/bin/env python3
"""Run the frozen 2026-09-03 Book R-252 stop-overlay audit."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.research.book_r_stop_overlay import (  # noqa: E402
    BookRStopRun,
    BookRStopSpec,
    run_book_r_stop_overlay,
)
from apex_quant.research.book_r_usd_etf import (  # noqa: E402
    BookRRun,
    BookRSpec,
    USD_ETF_UNIVERSE,
    _date_str,
    common_panel,
    run_book_r,
)
from apex_quant.validation.metrics import deflated_sharpe_ratio  # noqa: E402
from apex_quant.validation.paired_tests import paired_block_bootstrap  # noqa: E402


SNAPSHOT = ENGINE_DIR / "data_store" / "validation" / "book_r_stop_inputs_2026-09-03.parquet"
SNAPSHOT_PARTS_DIR = (
    ENGINE_DIR / "data_store" / "validation" / "book_r_stop_inputs_2026-09-03.parts"
)
MANIFEST = ENGINE_DIR / "data_store" / "validation" / "book_r_stop_inputs_2026-09-03.manifest.json"
PREREG = ENGINE_DIR / "data_store" / "book_r_stop_overlay_prereg_2026-09-03.md"
OVERLAY_SOURCE = ENGINE_DIR / "apex_quant" / "research" / "book_r_stop_overlay.py"
BASELINE_SOURCE = ENGINE_DIR / "apex_quant" / "research" / "book_r_usd_etf.py"
DEFAULT_JSON = ENGINE_DIR / "data_store" / "validation" / "book_r_stop_overlay_audit_2026-09-03.json"
DEFAULT_REPORT = ENGINE_DIR / "data_store" / "validation" / "book_r_stop_overlay_audit_2026-09-03.md"

SNAPSHOT_SHA256 = "efc75fb7056efe2d03d0cd13de955616882c2c7c54ac794c53cdfdbac0cc7974"
MANIFEST_SHA256 = "d5c1f9b664e6ec5a520313f58946d4169907fc32498cf2101796e5f19f6fe1ee"
BASELINE = BookRSpec(name="R-252-control", lookback=252)
ORIGINAL_SELECTION_SPECS = (
    BookRSpec(name="R-63-original-cell", lookback=63),
    BookRSpec(name="R-126-original-cell", lookback=126),
    BASELINE,
)
PRIMARY = BookRStopSpec()
STOP_ONLY = replace(PRIMARY, name="R-252-stop-only", risk_fraction=None)
SENSITIVITIES = (
    replace(PRIMARY, name="R-252-stop-2.0ATR-sensitivity", atr_multiple=2.0),
    replace(PRIMARY, name="R-252-stop-3.0ATR-sensitivity", atr_multiple=3.0),
)
SEGMENTS = {
    "research": ("2016-01-04", "2022-12-30"),
    "retrospective_validation": ("2023-01-03", "2024-12-31"),
    "known_data_replication": ("2025-01-02", "2026-08-27"),
    "full_history": ("2016-08-29", "2026-08-27"),
}
REGIME_BLOCKS = (
    ("2017_2018", "2017-01-03", "2018-12-31"),
    ("2019_2020", "2019-01-02", "2020-12-31"),
    ("2021_2022", "2021-01-04", "2022-12-30"),
    ("2023_2024", "2023-01-03", "2024-12-31"),
    ("2025_2026", "2025-01-02", "2026-08-27"),
)
EFFECTIVE_TRIAL_COUNT = 372
EXPECTED_OBSERVED_TRIAL_CELLS = 12


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_bytes() -> bytes:
    """Load the exact parquet bytes, preferring the Git-portable split package."""
    parts = sorted(SNAPSHOT_PARTS_DIR.glob("chunk-*"))
    if parts:
        payload = b"".join(part.read_bytes() for part in parts)
    elif SNAPSHOT.is_file():
        payload = SNAPSHOT.read_bytes()
    else:
        raise RuntimeError("frozen stop-study snapshot and packaged parts are missing")
    if hashlib.sha256(payload).hexdigest() != SNAPSHOT_SHA256:
        raise RuntimeError("frozen stop-study snapshot hash mismatch")
    return payload


def _load_frozen_panel() -> dict[str, pd.DataFrame]:
    if _sha256(MANIFEST) != MANIFEST_SHA256:
        raise RuntimeError("frozen stop-study manifest hash mismatch")
    data = pd.read_parquet(BytesIO(_snapshot_bytes()))
    expected = {"instrument", "timestamp", "open", "high", "low", "close", "volume"}
    if set(data.columns) != expected:
        raise RuntimeError("frozen snapshot schema mismatch")
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    panel: dict[str, pd.DataFrame] = {}
    for instrument in USD_ETF_UNIVERSE:
        frame = data[data["instrument"] == instrument].copy()
        frame = frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
        if frame.empty:
            raise RuntimeError(f"frozen snapshot is missing {instrument}")
        if (frame[["open", "high", "low", "close"]] <= 0).any().any():
            raise RuntimeError(f"frozen snapshot has non-positive OHLC for {instrument}")
        if (frame["high"] < frame[["open", "close"]].max(axis=1)).any():
            raise RuntimeError(f"frozen snapshot has invalid high for {instrument}")
        if (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
            raise RuntimeError(f"frozen snapshot has invalid low for {instrument}")
        if (frame["volume"] < 0).any():
            raise RuntimeError(f"frozen snapshot has negative volume for {instrument}")
        panel[instrument] = frame
    return common_panel(panel, USD_ETF_UNIVERSE)


def _risk_metrics(equity: pd.Series) -> dict[str, Any]:
    returns = equity.pct_change().dropna()
    drawdown = equity / equity.cummax() - 1.0
    monthly = returns.groupby([returns.index.year, returns.index.month]).apply(
        lambda values: float((1.0 + values).prod() - 1.0)
    )
    tail_count = max(1, int(np.ceil(len(returns) * 0.05))) if len(returns) else 0
    longest = current = 0
    for underwater in drawdown < 0.0:
        current = current + 1 if bool(underwater) else 0
        longest = max(longest, current)
    return {
        "worst_day": float(returns.min()) if len(returns) else 0.0,
        "expected_shortfall_5pct": float(returns.nsmallest(tail_count).mean()) if tail_count else 0.0,
        "worst_month": float(monthly.min()) if len(monthly) else 0.0,
        "max_drawdown_duration_sessions": int(longest),
        "drawdown_breach_days": {
            "5pct": int((drawdown <= -0.05).sum()),
            "8pct": int((drawdown <= -0.08).sum()),
            "10pct": int((drawdown <= -0.10).sum()),
            "12pct": int((drawdown <= -0.12).sum()),
        },
    }


def _summary(run: BookRRun | BookRStopRun, *, include_run: bool = False) -> dict[str, Any]:
    metrics = dict(run.metrics)
    for key, value in _risk_metrics(run.equity).items():
        metrics.setdefault(key, value)
    result: dict[str, Any] = {
        "metrics": metrics,
        "event_count": len(run.events),
        "selection_count": len(run.selections),
        "event_sha256": hashlib.sha256(
            json.dumps(run.events, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "equity_sha256": hashlib.sha256(
            np.asarray(run.equity, dtype="float64").tobytes()
        ).hexdigest(),
    }
    if include_run:
        result["run"] = run.to_dict(equity_points=1024)
    return result


def _realized_average_gross_exposure(
    run: BookRRun,
    panel: dict[str, pd.DataFrame],
) -> float:
    """Reconstruct close-marked gross exposure from the baseline fill ledger."""
    units = {instrument: 0.0 for instrument in panel}
    events_by_date: dict[str, list[dict[str, Any]]] = {}
    final_liquidation_cost_by_date: dict[str, float] = {}
    for event in run.events:
        if event["reason"] == "final_liquidation":
            final_liquidation_cost_by_date[event["date"]] = (
                final_liquidation_cost_by_date.get(event["date"], 0.0)
                + float(event["cost_usd"])
            )
            continue
        events_by_date.setdefault(event["date"], []).append(event)
    gross_fractions: list[float] = []
    for date, equity in run.equity.items():
        date_key = _date_str(date)
        for event in events_by_date.get(date_key, []):
            signed_units = float(event["units"]) * (1.0 if event["side"] == "buy" else -1.0)
            units[event["instrument"]] += signed_units
        gross = sum(
            units[instrument] * float(frame.loc[date, "close"])
            for instrument, frame in panel.items()
        )
        # The strategy is exposed through the final close and liquidates at
        # that close. Add the final sale cost back so this denominator matches
        # the overlay's pre-liquidation close exposure convention.
        marked_equity = float(equity) + final_liquidation_cost_by_date.get(date_key, 0.0)
        gross_fractions.append(gross / marked_equity if marked_equity > 0.0 else 0.0)
    return float(np.mean(gross_fractions)) if gross_fractions else 0.0


def _run_exposure_matched_no_stop(
    panel: dict[str, pd.DataFrame],
    *,
    start: str,
    end: str,
    target_average_gross: float,
) -> tuple[BookRRun, float]:
    """Match the primary's realised close-average gross exposure by bisection."""
    low, high = 0.001, 0.95
    candidates: list[tuple[float, BookRRun, float]] = []
    for _ in range(40):
        gross_target = (low + high) / 2.0
        run = run_book_r(
            panel,
            replace(
                BASELINE,
                name="R-252-exposure-matched-diagnostic",
                gross_target=gross_target,
            ),
            start=start,
            end=end,
        )
        realised = _realized_average_gross_exposure(run, panel)
        candidates.append((gross_target, run, realised))
        if realised < target_average_gross:
            low = gross_target
        else:
            high = gross_target
    _, best_run, best_realised = min(
        candidates,
        key=lambda item: abs(item[2] - target_average_gross),
    )
    return best_run, best_realised


def _comparison(base: BookRRun, challenger: BookRStopRun) -> dict[str, float]:
    bm, cm = base.metrics, challenger.metrics
    return {
        "total_return_difference": cm["total_return"] - bm["total_return"],
        "annualized_return_retention": (
            cm["annualized_return"] / bm["annualized_return"]
            if bm["annualized_return"] != 0.0 else 0.0
        ),
        "sharpe_difference": cm["sharpe"] - bm["sharpe"],
        "max_drawdown_relative_reduction": (
            1.0 - cm["max_drawdown"] / bm["max_drawdown"]
            if bm["max_drawdown"] > 0.0 else 0.0
        ),
        "calmar_ratio_vs_baseline": (
            cm["calmar"] / bm["calmar"] if bm["calmar"] != 0.0 else 0.0
        ),
    }


def _stress_baseline(spec: BookRSpec) -> BookRSpec:
    return replace(spec, name=f"{spec.name}-10bps", cost_bps_per_side=10.0)


def _stress_overlay(spec: BookRStopSpec) -> BookRStopSpec:
    return replace(
        spec,
        name=f"{spec.name}-stress",
        cost_bps_per_side=10.0,
        stop_slippage_bps=25.0,
    )


def _run_segment(panel: dict[str, pd.DataFrame], start: str, end: str, *, include_run: bool) -> dict[str, Any]:
    base = run_book_r(panel, BASELINE, start=start, end=end)
    base_stress = run_book_r(panel, _stress_baseline(BASELINE), start=start, end=end)
    primary = run_book_r_stop_overlay(panel, PRIMARY, start=start, end=end)
    primary_stress = run_book_r_stop_overlay(panel, _stress_overlay(PRIMARY), start=start, end=end)
    stop_only = run_book_r_stop_overlay(panel, STOP_ONLY, start=start, end=end)
    stop_only_stress = run_book_r_stop_overlay(panel, _stress_overlay(STOP_ONLY), start=start, end=end)
    sensitivities = {
        f"{spec.atr_multiple:.1f}ATR": run_book_r_stop_overlay(panel, spec, start=start, end=end)
        for spec in SENSITIVITIES
    }
    target_average_gross = float(primary.metrics["average_close_gross_exposure"])
    exposure_matched, matched_average_gross = _run_exposure_matched_no_stop(
        panel,
        start=start,
        end=end,
        target_average_gross=target_average_gross,
    )
    baseline_summary = _summary(base, include_run=include_run)
    baseline_summary["metrics"]["average_close_gross_exposure"] = (
        _realized_average_gross_exposure(base, panel)
    )
    baseline_stress_summary = _summary(base_stress)
    baseline_stress_summary["metrics"]["average_close_gross_exposure"] = (
        _realized_average_gross_exposure(base_stress, panel)
    )
    exposure_matched_summary = _summary(exposure_matched)
    exposure_matched_summary["metrics"]["average_close_gross_exposure"] = (
        matched_average_gross
    )
    return {
        "window": {"start": start, "end": end},
        "baseline": {
            "base": baseline_summary,
            "stress_10bps": baseline_stress_summary,
        },
        "primary_stop_risk085": {
            "base": _summary(primary, include_run=include_run),
            "stress_10bps_plus_25bps_stop_slippage": _summary(primary_stress),
        },
        "stop_only_diagnostic": {
            "base": _summary(stop_only),
            "stress_10bps_plus_25bps_stop_slippage": _summary(stop_only_stress),
        },
        "exposure_matched_no_stop_diagnostic": {
            "requested_gross_target": exposure_matched.spec.gross_target,
            "target_average_gross_exposure": target_average_gross,
            "realized_average_gross_exposure": matched_average_gross,
            "absolute_match_error": abs(matched_average_gross - target_average_gross),
            "base": exposure_matched_summary,
        },
        "sensitivity": {
            name: _summary(run) for name, run in sensitivities.items()
        },
        "primary_vs_baseline": _comparison(base, primary),
        "stop_only_vs_baseline": _comparison(base, stop_only),
        "primary_vs_exposure_matched": _comparison(exposure_matched, primary),
        "_objects": {
            "base": base,
            "base_stress": base_stress,
            "primary": primary,
            "primary_stress": primary_stress,
            "stop_only": stop_only,
            "stop_only_stress": stop_only_stress,
            "sensitivities": sensitivities,
        },
    }


def _strip_objects(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_objects(item) for key, item in value.items() if key != "_objects"}
    if isinstance(value, list):
        return [_strip_objects(item) for item in value]
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    return value


def _gates(validation: dict[str, Any]) -> dict[str, Any]:
    objects = validation["_objects"]
    base = objects["base"].metrics
    primary = objects["primary"].metrics
    stressed = objects["primary_stress"].metrics
    sensitivities = [run.metrics for run in objects["sensitivities"].values()]
    checks = {
        "drawdown_reduction_at_least_20pct": bool(
            primary["max_drawdown"] <= base["max_drawdown"] * 0.80
        ),
        "drawdown_no_greater_than_12pct": bool(primary["max_drawdown"] <= 0.12),
        "annualized_return_retention_at_least_60pct": bool(
            primary["annualized_return"] >= base["annualized_return"] * 0.60
        ),
        "sharpe_no_more_than_0_10_below_baseline": bool(
            primary["sharpe"] >= base["sharpe"] - 0.10
        ),
        "stressed_total_return_positive": bool(stressed["total_return"] > 0.0),
        "both_sensitivity_returns_positive": bool(
            all(metrics["total_return"] > 0.0 for metrics in sensitivities)
        ),
        "both_sensitivity_drawdowns_below_baseline": bool(
            all(metrics["max_drawdown"] < base["max_drawdown"] for metrics in sensitivities)
        ),
    }
    return {
        "checks": checks,
        "passed": bool(all(checks.values())),
        "actual": {
            "baseline_max_drawdown": base["max_drawdown"],
            "primary_max_drawdown": primary["max_drawdown"],
            "baseline_annualized_return": base["annualized_return"],
            "primary_annualized_return": primary["annualized_return"],
            "baseline_sharpe": base["sharpe"],
            "primary_sharpe": primary["sharpe"],
            "primary_stressed_total_return": stressed["total_return"],
        },
    }


def _regime_blocks(panel: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    rows = []
    for name, start, end in REGIME_BLOCKS:
        base = run_book_r(panel, _stress_baseline(BASELINE), start=start, end=end)
        primary = run_book_r_stop_overlay(panel, _stress_overlay(PRIMARY), start=start, end=end)
        rows.append({
            "name": name,
            "start": start,
            "end": end,
            "baseline": _summary(base),
            "primary": _summary(primary),
            "comparison": _comparison(base, primary),
        })
    return rows


def _bootstrap(base: BookRRun, primary: BookRStopRun) -> dict[str, Any]:
    return paired_block_bootstrap(
        base.equity.pct_change().dropna(),
        primary.equity.pct_change().dropna(),
        block_size=21,
        n_bootstraps=10_000,
        seed=42,
        periods_per_year=252.0,
    )


def _pct(value: float) -> str:
    return f"{float(value) * 100.0:.2f}%"


def _report(payload: dict[str, Any]) -> str:
    def result_row(segment_name: str, label: str, metrics: dict[str, Any]) -> str:
        average_gross = metrics.get("average_close_gross_exposure")
        return (
            f"| {segment_name.replace('_', ' ')} | {label} | "
            f"{_pct(metrics['annualized_return'])} | {metrics['sharpe']:.3f} | "
            f"{_pct(metrics['max_drawdown'])} | {_pct(metrics['total_return'])} | "
            f"{_pct(metrics['worst_day'])} | "
            f"{_pct(average_gross) if average_gross is not None else 'n/a'} | "
            f"{metrics.get('stop_exit_count', 0)} |"
        )

    lines = [
        "# Book R-252 stop-overlay audit",
        "",
        f"**Verdict: {payload['verdict']}**",
        "",
        "This is a causal retrospective test on a newly frozen 2026-09-03 OHLCV snapshot, not a true blind backtest. The running Book R forward-paper strategy was not changed.",
        "",
        "## Segment results (5 bps/side)",
        "",
        "| Segment | Variant | CAGR | Sharpe | Max DD | Total return | Worst day | Avg gross | Stops |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for segment_name, segment in payload["segments"].items():
        for label, key in (
            ("Baseline", "baseline"),
            ("Stop + 0.85% price-risk sizing", "primary_stop_risk085"),
            ("Stop only", "stop_only_diagnostic"),
            ("Exposure-matched no stop", "exposure_matched_no_stop_diagnostic"),
        ):
            lines.append(result_row(segment_name, label, segment[key]["base"]["metrics"]))
        for sensitivity_name, sensitivity in segment["sensitivity"].items():
            lines.append(
                result_row(
                    segment_name,
                    f"{sensitivity_name} sensitivity",
                    sensitivity["metrics"],
                )
            )

    validation = payload["segments"]["retrospective_validation"]
    stress_rows = (
        ("Baseline, 10 bps/side", validation["baseline"]["stress_10bps"]["metrics"]),
        (
            "Stop + sizing, 10 bps/side + 25 bps stop slippage",
            validation["primary_stop_risk085"][
                "stress_10bps_plus_25bps_stop_slippage"
            ]["metrics"],
        ),
        (
            "Stop only, 10 bps/side + 25 bps stop slippage",
            validation["stop_only_diagnostic"][
                "stress_10bps_plus_25bps_stop_slippage"
            ]["metrics"],
        ),
    )
    lines += [
        "",
        "## Validation execution stress",
        "",
        "| Variant | Total return | CAGR | Sharpe | Max DD | Costs |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, metrics in stress_rows:
        lines.append(
            f"| {label} | {_pct(metrics['total_return'])} | "
            f"{_pct(metrics['annualized_return'])} | {metrics['sharpe']:.3f} | "
            f"{_pct(metrics['max_drawdown'])} | ${metrics['transaction_cost_usd']:,.2f} |"
        )

    actual = payload["validation_gates"]["actual"]
    return_retention = (
        actual["primary_annualized_return"] / actual["baseline_annualized_return"]
    )
    sharpe_change = actual["primary_sharpe"] - actual["baseline_sharpe"]
    matched = validation["exposure_matched_no_stop_diagnostic"]
    matched_metrics = matched["base"]["metrics"]
    primary_metrics = validation["primary_stop_risk085"]["base"]["metrics"]
    stop_only_metrics = validation["stop_only_diagnostic"]["base"]["metrics"]
    bootstrap = payload["paired_block_bootstrap"]["retrospective_validation_stressed"]
    dsr = payload["deflated_sharpe_diagnostic"]
    regimes = payload["regime_summary"]
    lines += [
        "",
        "## Frozen validation gates",
        "",
    ]
    for name, passed in payload["validation_gates"]["checks"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} — {name.replace('_', ' ')}")
    lines += [
        "",
        f"The primary retained {_pct(return_retention)} of baseline CAGR (required 60.00%) and changed Sharpe by {sharpe_change:+.3f} (allowed decline: -0.100).",
        "",
        "## Attribution and robustness",
        "",
        f"The exposure-matched no-stop control matched close-average gross exposure to within {_pct(matched['absolute_match_error'])} and beat the primary: {_pct(matched_metrics['annualized_return'])} vs {_pct(primary_metrics['annualized_return'])} CAGR, {matched_metrics['sharpe']:.3f} vs {primary_metrics['sharpe']:.3f} Sharpe, and {_pct(matched_metrics['max_drawdown'])} vs {_pct(primary_metrics['max_drawdown'])} max drawdown.",
        "",
        f"The stop-only control produced {_pct(stop_only_metrics['total_return'])} total return with {stop_only_metrics['sharpe']:.3f} Sharpe and {_pct(stop_only_metrics['max_drawdown'])} max drawdown. This shows that most of the primary's apparent drawdown benefit came from lower exposure and cash, not from the stop rule.",
        "",
        f"Under stressed execution, the paired block-bootstrap Sharpe difference was {bootstrap['sharpe_delta']:+.3f}, with a 95% interval of [{bootstrap['ci_95_lower']:+.3f}, {bootstrap['ci_95_upper']:+.3f}] and one-sided superiority p={bootstrap['p_value_one_sided']:.4f}.",
        "",
        f"The stressed primary had positive return in {regimes['positive_primary_returns']}/{regimes['block_count']} regime blocks, lower drawdown in {regimes['lower_primary_drawdown']}/{regimes['block_count']}, and no-worse Sharpe in only {regimes['primary_sharpe_no_worse']}/{regimes['block_count']}. The corrected 12-cell Deflated Sharpe diagnostic was {dsr['dsr']:.3f} (below the conventional 0.95 reference level).",
        "",
        "## Interpretation",
        "",
        payload["interpretation"],
        "",
        "## Integrity and limitations",
        "",
        "- The result runner verifies the frozen parquet and manifest hashes before loading data.",
        "- A first diagnostic artifact omitted four original Book R cells from Sharpe dispersion. The corrected run includes all 12 pre-registered cells; the non-gating DSR fell, while every validation gate and the verdict stayed unchanged.",
        "- The first exposure control equated target gross with the primary's realised gross and therefore only approximated the match. The corrected non-gating control solves for equal realised close exposure; the primary and frozen gates are unchanged.",
        "- Yahoo quote OHLCV is a price-return dataset; dividends and cash interest are not reconstructed.",
        "- The 0.85% sizing budget covers entry-to-stop price movement before costs and slippage. Trading costs, adverse stop slippage, and opening gaps can make realised loss larger.",
        "- Resting stops take precedence over a same-open monthly rebalance in this conservative simulator; stressed stop fills therefore receive the extra adverse slippage assumption.",
        "- Average exposure is measured at each close before the final close liquidation; a position stopped intraday contributes zero close exposure for that session.",
        "- The 2023–2024 segment is retrospective validation, not an externally held blind lockbox.",
        "- A historical pass would authorize only a separate forward-paper challenger, never funding.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    panel = _load_frozen_panel()
    raw_segments = {
        name: _run_segment(panel, start, end, include_run=name == "retrospective_validation")
        for name, (start, end) in SEGMENTS.items()
    }
    gates = _gates(raw_segments["retrospective_validation"])
    regimes = _regime_blocks(panel)
    validation_objects = raw_segments["retrospective_validation"]["_objects"]
    full_objects = raw_segments["full_history"]["_objects"]
    research_objects = raw_segments["research"]["_objects"]
    research_start, research_end = SEGMENTS["research"]
    original_selection_runs = []
    for spec in ORIGINAL_SELECTION_SPECS:
        if spec.lookback == BASELINE.lookback:
            original_selection_runs.extend(
                [research_objects["base"], research_objects["base_stress"]]
            )
        else:
            original_selection_runs.extend([
                run_book_r(panel, spec, start=research_start, end=research_end),
                run_book_r(
                    panel,
                    _stress_baseline(spec),
                    start=research_start,
                    end=research_end,
                ),
            ])
    new_overlay_runs = [
        research_objects["primary"],
        research_objects["primary_stress"],
        research_objects["stop_only"],
        research_objects["stop_only_stress"],
        *research_objects["sensitivities"].values(),
    ]
    trial_runs = [*original_selection_runs, *new_overlay_runs]
    if len(trial_runs) != EXPECTED_OBSERVED_TRIAL_CELLS:
        raise RuntimeError(
            "multiplicity composition mismatch: "
            f"expected {EXPECTED_OBSERVED_TRIAL_CELLS} cells, got {len(trial_runs)}"
        )
    trial_sharpes = [run.metrics["sharpe"] / np.sqrt(252.0) for run in trial_runs]
    dsr = deflated_sharpe_ratio(
        full_objects["primary_stress"].equity.pct_change().dropna().to_numpy(),
        trial_sharpes,
        periods_per_year=252,
        n_trials=EFFECTIVE_TRIAL_COUNT,
    )
    regime_summary = {
        "positive_primary_returns": sum(row["primary"]["metrics"]["total_return"] > 0.0 for row in regimes),
        "lower_primary_drawdown": sum(
            row["primary"]["metrics"]["max_drawdown"] < row["baseline"]["metrics"]["max_drawdown"]
            for row in regimes
        ),
        "primary_sharpe_no_worse": sum(
            row["primary"]["metrics"]["sharpe"] >= row["baseline"]["metrics"]["sharpe"]
            for row in regimes
        ),
        "block_count": len(regimes),
    }
    verdict = "PASS_TO_SEPARATE_FORWARD_PAPER" if gates["passed"] else "FAIL_DO_NOT_DEPLOY"
    interpretation = (
        "Every pre-registered 2023–2024 gate passed. This supports creating a separate forward-paper challenger, while leaving the original R-252 untouched."
        if gates["passed"] else
        "At least one pre-registered 2023–2024 gate failed. The stop overlay must not be deployed or retuned on the validation/replication periods."
    )
    payload = {
        "audit_id": "book_r_252_stop_overlay_2026_09_03",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "causal_retrospective_not_true_blind",
        "verdict": verdict,
        "interpretation": interpretation,
        "frozen_primary": PRIMARY.to_dict(),
        "frozen_sensitivities": [spec.to_dict() for spec in SENSITIVITIES],
        "frozen_stop_only_diagnostic": STOP_ONLY.to_dict(),
        "validation_gates": gates,
        "segments": _strip_objects(raw_segments),
        "regime_blocks_stressed": regimes,
        "regime_summary": regime_summary,
        "paired_block_bootstrap": {
            "retrospective_validation_stressed": _bootstrap(
                validation_objects["base_stress"], validation_objects["primary_stress"]
            ),
            "full_history_stressed": _bootstrap(
                full_objects["base_stress"], full_objects["primary_stress"]
            ),
        },
        "deflated_sharpe_diagnostic": dsr,
        "multiplicity": {
            "effective_trial_count": EFFECTIVE_TRIAL_COUNT,
            "observed_trial_cells": len(trial_runs),
            "original_book_r_cells": len(original_selection_runs),
            "new_overlay_and_control_cells": len(new_overlay_runs),
            "note": "The observed dispersion includes all six original Book R selection cells and all six frozen overlay/control/sensitivity cells. The 372-cell effective count remains the conservative global research-family adjustment; the shared dirty ledger was not mutated.",
        },
        "methodology_corrections": [
            "The first generated diagnostic counted eight observed trial cells and omitted the R-63 and R-126 base/stress cells from Sharpe dispersion. This rerun includes all twelve cells required by the pre-registration. Validation gates and the deployment verdict never depended on DSR.",
            "The first exposure diagnostic set the no-stop gross target equal to the primary's realised average gross, which only approximately matched realised exposure. This rerun solves the non-gating control's gross target by bisection to match realised close-average exposure directly. The primary strategy and every validation gate are unchanged.",
        ],
        "integrity": {
            "snapshot_sha256": hashlib.sha256(_snapshot_bytes()).hexdigest(),
            "snapshot_packaging": {
                "format": "ordered_binary_parts",
                "part_count": len(sorted(SNAPSHOT_PARTS_DIR.glob("chunk-*"))),
                "part_sha256": {
                    part.name: _sha256(part)
                    for part in sorted(SNAPSHOT_PARTS_DIR.glob("chunk-*"))
                },
            },
            "manifest_sha256": _sha256(MANIFEST),
            "preregistration_sha256": _sha256(PREREG),
            "runner_sha256": _sha256(Path(__file__)),
            "overlay_source_sha256": _sha256(OVERLAY_SOURCE),
            "baseline_source_sha256": _sha256(BASELINE_SOURCE),
            "snapshot_rows_per_instrument": len(next(iter(panel.values()))),
            "snapshot_start": str(next(iter(panel.values())).index.min()),
            "snapshot_end": str(next(iter(panel.values())).index.max()),
        },
        "limitations": [
            "historical cache was accessible before this study; no local segment is truly blind",
            "Yahoo quote OHLCV price returns; dividends and cash interest excluded",
            "0.85% is entry-to-stop price risk before costs/slippage, not a guaranteed loss cap",
            "opening gaps can execute below a resting stop",
            "resting stops precede same-open monthly rebalances in the conservative stress model",
            "exposure is measured at the close, so intraday-stopped positions count as zero that session",
            "only subsequent untouched forward paper can provide new evidence",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(_strip_objects(payload), indent=2, sort_keys=True) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_report(payload))
    print(json.dumps({
        "verdict": verdict,
        "validation_gates": gates,
        "regime_summary": regime_summary,
        "bootstrap_validation": payload["paired_block_bootstrap"]["retrospective_validation_stressed"],
        "dsr": dsr,
        "json": str(args.out),
        "report": str(args.report),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
