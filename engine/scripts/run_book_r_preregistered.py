#!/usr/bin/env python3
"""Run the frozen Book R USD-ETF research protocol.

This is deliberately not named a blind backtest.  The local cache was already
available to prior project research, so the script produces a reproducible,
pre-registered *retrospective validation* and known-data replication.  A true
blind test requires a separately held lockbox or forward paper data after this
specification's freeze date.

Run from ``engine``:

    .venv-mac/bin/python scripts/run_book_r_preregistered.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.research.book_r_usd_etf import (  # noqa: E402
    BookRSpec,
    USD_ETF_UNIVERSE,
    load_usd_etf_panel,
    panel_manifest,
    run_book_r,
)


PREREG_PATH = ENGINE_DIR / "data_store" / "book_r_usd_etf_prereg_2026-08-28.md"
DEFAULT_OUT = ENGINE_DIR / "data_store" / "validation" / "book_r_usd_etf_preregistered_2026-08-28.json"

TRAIN_START = "2016-01-04"
TRAIN_END = "2022-12-30"
VALIDATION_START = "2023-01-03"
VALIDATION_END = "2024-12-31"
REPLICATION_START = "2025-01-02"

CANDIDATES: tuple[BookRSpec, ...] = (
    BookRSpec(name="R-63", lookback=63),
    BookRSpec(name="R-126", lookback=126),
    BookRSpec(name="R-252", lookback=252),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positive_calendar_years(metrics: dict) -> int:
    return sum(1 for ret in metrics["annual_returns"].values() if float(ret) > 0.0)


def _selection_row(base, stress) -> dict:
    base_m, stress_m = base.metrics, stress.metrics
    eligible = (
        base_m["total_return"] > 0.0
        and stress_m["total_return"] > 0.0
        and _positive_calendar_years(base_m) >= 4
        and base_m["max_drawdown"] <= 0.25
        and base_m["selection_count"] >= 48
    )
    return {
        "candidate": base.spec.name,
        "lookback": base.spec.lookback,
        "eligible": bool(eligible),
        "positive_calendar_years": _positive_calendar_years(base_m),
        "base_total_return": base_m["total_return"],
        "stress_total_return": stress_m["total_return"],
        "base_max_drawdown": base_m["max_drawdown"],
        "stress_calmar": stress_m["calmar"],
        "selection_count": base_m["selection_count"],
    }


def select_candidate(rows: list[dict]) -> dict | None:
    """Apply the frozen lexicographic selection rule without using holdouts."""
    eligible = [row for row in rows if row["eligible"]]
    if not eligible:
        return None
    return sorted(
        eligible,
        key=lambda row: (
            -float(row["stress_calmar"]),
            float(row["base_max_drawdown"]),
            -int(row["lookback"]),
            str(row["candidate"]),
        ),
    )[0]


def _stress_spec(spec: BookRSpec) -> BookRSpec:
    return BookRSpec(
        name=f"{spec.name}-2x-cost",
        lookback=spec.lookback,
        vol_window=spec.vol_window,
        max_positions=spec.max_positions,
        gross_target=spec.gross_target,
        cost_bps_per_side=spec.cost_bps_per_side * 2.0,
    )


def _promotion_gates(validation_base, validation_stress, selection: dict | None) -> dict:
    if selection is None:
        return {
            "selection_eligible": False,
            "validation_after_2x_cost_positive": False,
            "validation_drawdown_at_or_below_25pct": False,
            "true_blind_lockbox": False,
            "overall_promoted": False,
            "reason": "No frozen candidate satisfied the research-selection eligibility rule.",
        }
    stress_m = validation_stress.metrics
    base_m = validation_base.metrics
    return {
        "selection_eligible": True,
        "validation_after_2x_cost_positive": bool(stress_m["total_return"] > 0.0),
        "validation_drawdown_at_or_below_25pct": bool(base_m["max_drawdown"] <= 0.25),
        "true_blind_lockbox": False,
        "overall_promoted": False,
        "reason": (
            "Historical data inside this repository was already accessible, so no true "
            "blind lockbox or forward-paper evidence exists. Book R is research-only."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not PREREG_PATH.exists():
        raise FileNotFoundError(f"missing frozen preregistration: {PREREG_PATH}")
    store_root = ENGINE_DIR / "data_store"
    manifest = panel_manifest(store_root, USD_ETF_UNIVERSE)
    panel = load_usd_etf_panel(store_root, USD_ETF_UNIVERSE)
    latest = next(iter(panel.values())).index.max().strftime("%Y-%m-%d")
    if latest < REPLICATION_START:
        raise RuntimeError(f"cached panel ends before replication begins: {latest}")

    selection_rows: list[dict] = []
    research_runs: dict[str, dict] = {}
    by_name = {spec.name: spec for spec in CANDIDATES}
    for spec in CANDIDATES:
        base = run_book_r(panel, spec, start=TRAIN_START, end=TRAIN_END)
        stress = run_book_r(panel, _stress_spec(spec), start=TRAIN_START, end=TRAIN_END)
        selection_rows.append(_selection_row(base, stress))
        research_runs[spec.name] = {"base": base.to_dict(), "two_x_cost": stress.to_dict()}

    selected = select_candidate(selection_rows)
    selected_runs: dict[str, dict] = {}
    validation_base = validation_stress = None
    if selected is not None:
        spec = by_name[selected["candidate"]]
        validation_base = run_book_r(panel, spec, start=VALIDATION_START, end=VALIDATION_END)
        validation_stress = run_book_r(panel, _stress_spec(spec), start=VALIDATION_START, end=VALIDATION_END)
        replication_base = run_book_r(panel, spec, start=REPLICATION_START, end=latest)
        replication_stress = run_book_r(panel, _stress_spec(spec), start=REPLICATION_START, end=latest)
        selected_runs = {
            "retrospective_validation": {
                "base": validation_base.to_dict(),
                "two_x_cost": validation_stress.to_dict(),
            },
            "known_data_replication": {
                "base": replication_base.to_dict(),
                "two_x_cost": replication_stress.to_dict(),
            },
        }

    output = {
        "book_id": "book_r_usd_etf_momentum_control",
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "research_only_not_a_true_blind_backtest",
        "truthfulness_note": (
            "The 2023-2024 segment is a pre-registered retrospective validation, "
            "not a true blind lockbox. The 2025+ segment was previously accessible "
            "to repository research and is labelled known-data replication."
        ),
        "account_currency": "USD",
        "universe": list(USD_ETF_UNIVERSE),
        "cluster_cap": "at most one ETF per fixed economic cluster",
        "execution": {
            "signal_time": "month-end close",
            "fill_time": "next common trading-session open",
            "intrabar_stops_or_targets": "none; no ambiguous OHLC path inference",
            "final_liquidation_cost": True,
        },
        "cost_model": {
            "base_bps_per_side": 5.0,
            "stress_bps_per_side": 10.0,
            "cash_interest": 0.0,
            "dividends": "not reconstructed; cached bars are price returns",
        },
        "segments": {
            "research_selection": {"start": TRAIN_START, "end": TRAIN_END},
            "retrospective_validation": {"start": VALIDATION_START, "end": VALIDATION_END},
            "known_data_replication": {"start": REPLICATION_START, "end": latest},
        },
        "frozen_preregistration": {
            "path": str(PREREG_PATH.relative_to(ENGINE_DIR.parent)),
            "sha256": _sha256(PREREG_PATH),
        },
        "script_sha256": _sha256(Path(__file__)),
        "data_manifest": manifest,
        "selection_rule": {
            "eligibility": [
                "positive base-cost research return",
                "positive 2x-cost research return",
                "at least four positive calendar-year blocks",
                "base-cost max drawdown <= 25%",
                "at least 48 scheduled selections",
            ],
            "ranking": "highest 2x-cost Calmar, then lower base-cost drawdown, then longer lookback, then name",
        },
        "research_selection": {
            "candidates": selection_rows,
            "selected": selected,
            "runs": research_runs,
        },
        "selected_candidate_runs": selected_runs,
        "promotion_gates": _promotion_gates(validation_base, validation_stress, selected),
        "next_required_evidence": [
            "true externally held lockbox or forward paper data after the freeze date",
            "reconciled broker/account-currency valuation before any GBP comparison",
            "dividend/total-return data if benchmark-quality ETF comparisons are needed",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.out}")
    print("selected:", selected["candidate"] if selected else "NONE")
    if selected is not None:
        print("validation base:", validation_base.metrics)
        print("validation 2x cost:", validation_stress.metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
