#!/usr/bin/env python3
"""Create a reproducible exact-ten-year causal audit for the frozen Book R.

This script deliberately *imports* the frozen ``book_r_usd_etf`` implementation
rather than reimplementing or tuning it.  It is therefore an audit of the
already-declared R-252 control, not another strategy-selection exercise.

The output is a causal retrospective replay: each signal is formed on a
month-end close and is filled at the following common-session open.  It is not
a true blind backtest, because the local price cache was already accessible to
this research project.  A true blind result would need an independently held
vendor lockbox or forward data collected after the specification freeze.

Run from ``engine``:

    .venv-mac/bin/python scripts/run_book_r_10y_causal_audit.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.research.book_r_usd_etf import (  # noqa: E402
    BookRRun,
    BookRSpec,
    USD_ETF_UNIVERSE,
    load_usd_etf_panel,
    panel_manifest,
    run_book_r,
)


AUDIT_START = "2016-08-19"
AUDIT_END = "2026-08-19"
INITIAL_EQUITY_USD = 100_000.0
FROZEN_SPEC = BookRSpec(name="R-252", lookback=252)
PREREG_PATH = ENGINE_DIR / "data_store" / "book_r_usd_etf_prereg_2026-08-28.md"
SOURCE_PATH = ENGINE_DIR / "apex_quant" / "research" / "book_r_usd_etf.py"
DEFAULT_JSON_OUT = (
    ENGINE_DIR / "data_store" / "validation" / "book_r_10y_causal_audit_2026-08-28.json"
)
DEFAULT_REPORT_OUT = (
    ENGINE_DIR / "data_store" / "validation" / "book_r_10y_causal_audit_2026-08-28.md"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date(value: pd.Timestamp | str) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.strftime("%Y-%m-%d")


def _stress_spec(spec: BookRSpec) -> BookRSpec:
    """Return the frozen 2x-cost stress without changing any other rule."""
    return BookRSpec(
        name=f"{spec.name}-2x-cost",
        lookback=spec.lookback,
        vol_window=spec.vol_window,
        max_positions=spec.max_positions,
        gross_target=spec.gross_target,
        cost_bps_per_side=spec.cost_bps_per_side * 2.0,
    )


def _episodic_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Distinguish order fills from continuous holding episodes.

    A monthly rebalance can generate a sell and a new buy while remaining in
    the same economic holding.  Counting every order as a "trade" would make
    the strategy look much more active than it is.  We instead replay units by
    instrument and define an episode as zero units -> positive units through
    the later return to zero.  Book R always applies a terminal liquidation,
    so active episodes should be closed by the end of a run.
    """
    order_events = [event for event in events if event["reason"] == "monthly_rebalance"]
    final_events = [event for event in events if event["reason"] == "final_liquidation"]
    by_instrument: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_instrument.setdefault(str(event["instrument"]), []).append(event)

    episodes: list[dict[str, Any]] = []
    tolerance = 1e-7
    for instrument, rows in sorted(by_instrument.items()):
        units = 0.0
        opened: dict[str, Any] | None = None
        for row in rows:
            delta = float(row["units"]) if row["side"] == "buy" else -float(row["units"])
            before, after = units, units + delta
            if abs(after) < tolerance:
                after = 0.0
            if before <= tolerance and after > tolerance:
                opened = {
                    "instrument": instrument,
                    "open_date": str(row["date"]),
                    "open_reason": str(row["reason"]),
                }
            elif before > tolerance and after <= tolerance and opened is not None:
                episodes.append({
                    **opened,
                    "close_date": str(row["date"]),
                    "close_reason": str(row["reason"]),
                })
                opened = None
            units = after
        if opened is not None:
            # This should not occur because ``run_book_r`` charges a final
            # liquidation.  Preserve it as an audit failure rather than
            # silently treating a marked position as a completed trade.
            episodes.append({**opened, "close_date": None, "close_reason": None})

    complete = [episode for episode in episodes if episode["close_date"] is not None]
    durations: list[int] = []
    for episode in complete:
        begin = pd.Timestamp(episode["open_date"])
        finish = pd.Timestamp(episode["close_date"])
        durations.append(max(0, int((finish - begin).days)))
    reason_breakdown: dict[str, int] = {}
    for event in events:
        reason = str(event["reason"])
        reason_breakdown[reason] = reason_breakdown.get(reason, 0) + 1
    return {
        "order_fills_total": len(events),
        "monthly_rebalance_order_fills": len(order_events),
        "final_liquidation_order_fills": len(final_events),
        "buy_order_fills": sum(1 for event in events if event["side"] == "buy"),
        "sell_order_fills": sum(1 for event in events if event["side"] == "sell"),
        "holding_episodes": len(episodes),
        "completed_holding_episodes": len(complete),
        "open_episodes_at_end": len(episodes) - len(complete),
        "median_calendar_days_per_completed_episode": (
            float(pd.Series(durations).median()) if durations else 0.0
        ),
        "mean_calendar_days_per_completed_episode": (
            float(pd.Series(durations).mean()) if durations else 0.0
        ),
        "episode_definition": (
            "Per instrument: a zero-to-positive unit transition through the later "
            "return to zero. Rebalances inside that continuous holding are not new episodes."
        ),
        "event_reason_breakdown": reason_breakdown,
    }


def _causality_checks(run: BookRRun) -> dict[str, Any]:
    rebalance_events = [event for event in run.events if event["reason"] == "monthly_rebalance"]
    next_open_fills = all(pd.Timestamp(event["date"]) > pd.Timestamp(event["decision_date"])
                          for event in rebalance_events)
    selection_fills = all(pd.Timestamp(row["fill_date"]) > pd.Timestamp(row["decision_date"])
                          for row in run.selections)
    return {
        "monthly_rebalance_fill_strictly_after_decision_close": bool(next_open_fills),
        "all_scheduled_fill_dates_strictly_after_decision_date": bool(selection_fills),
        "no_same_bar_close_fill_in_rebalance_events": bool(next_open_fills),
        "final_liquidation_cost_recorded": any(
            event["reason"] == "final_liquidation" for event in run.events
        ),
        "monthly_rebalance_event_count": len(rebalance_events),
        "scheduled_selection_count": len(run.selections),
    }


def _folds(panel: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    """Run flat-start calendar folds without carrying positions across years."""
    common_index = next(iter(panel.values())).index
    start_ts = pd.Timestamp(AUDIT_START, tz="UTC")
    end_ts = pd.Timestamp(AUDIT_END, tz="UTC")
    first_signal_index = max(FROZEN_SPEC.lookback, FROZEN_SPEC.vol_window)
    first_possible_signal = common_index[first_signal_index]
    folds: list[dict[str, Any]] = []

    for year in range(start_ts.year, end_ts.year + 1):
        candidate_start = max(start_ts, pd.Timestamp(f"{year}-01-01", tz="UTC"))
        candidate_end = min(end_ts, pd.Timestamp(f"{year}-12-31", tz="UTC"))
        dates = common_index[(common_index >= candidate_start) & (common_index <= candidate_end)]
        if len(dates) < 2:
            continue
        actual_start, actual_end = dates[0], dates[-1]
        signal_ready = actual_end >= first_possible_signal
        base = run_book_r(panel, FROZEN_SPEC, start=actual_start, end=actual_end)
        stress = run_book_r(panel, _stress_spec(FROZEN_SPEC), start=actual_start, end=actual_end)
        folds.append({
            "calendar_year": year,
            "start": _date(actual_start),
            "end": _date(actual_end),
            "flat_start_initial_equity_usd": INITIAL_EQUITY_USD,
            "signal_ready_with_252_session_lookback": bool(signal_ready),
            "comparability_note": (
                "The 2016 partial fold is pre-warmup and contains no eligible R-252 "
                "signal. Other folds use prior cached closes solely for the frozen "
                "lookback calculation, begin flat, and do not carry a prior-year position."
                if year == 2016 else
                "Flat-start fold; prior cached closes are used only as the frozen 252-session lookback. "
                "No position is carried in from the preceding fold."
            ),
            "base": {
                "metrics": base.metrics,
                "activity": _episodic_summary(base.events),
                "causality_checks": _causality_checks(base),
            },
            "two_x_cost": {
                "metrics": stress.metrics,
                "activity": _episodic_summary(stress.events),
                "causality_checks": _causality_checks(stress),
            },
        })
    return folds


def _pct(value: float) -> str:
    return f"{float(value) * 100.0:+.2f}%"


def _pct_abs(value: float) -> str:
    """Format a magnitude such as drawdown without an artificial plus sign."""
    return f"{abs(float(value)) * 100.0:.2f}%"


def _number(value: float) -> str:
    return f"{float(value):,.2f}"


def _report(payload: dict[str, Any]) -> str:
    base = payload["full_history"]["base"]
    stress = payload["full_history"]["two_x_cost"]
    base_m, stress_m = base["metrics"], stress["metrics"]
    base_a, stress_a = base["activity"], stress["activity"]
    lines = [
        "# Book R-252 — exact 10-year causal retrospective audit",
        "",
        "**Status:** research-only, causal retrospective replay; **not a true blind backtest**.",
        "",
        f"**Window:** {payload['window']['requested_start']} to {payload['window']['requested_end']} "
        f"({payload['window']['common_sessions']} common daily sessions).  All values are USD.",
        "",
        "## Locked method audited",
        "",
        "R-252 is not retuned here: monthly decision at the final common-session close, next common-session open fill, positive 252-session momentum divided by 63-session log-return volatility, maximum three equal-weight long ETFs, 95% gross, one ETF per predeclared economic cluster, 5 bps/side (plus 10 bps/side stress), and a paid final liquidation. Cached bars are price-return OHLCV; dividends and cash interest are not reconstructed.",
        "",
        "## Full-window results",
        "",
        "| Cost assumption | Total return | Annualized return | Sharpe | Max drawdown | Final NAV | Transaction cost |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| 5 bps/side | {_pct(base_m['total_return'])} | {_pct(base_m['annualized_return'])} | {base_m['sharpe']:.3f} | {_pct_abs(base_m['max_drawdown'])} | ${_number(base_m['final_equity_usd'])} | ${_number(base_m['transaction_cost_usd'])} |",
        f"| 10 bps/side (2x stress) | {_pct(stress_m['total_return'])} | {_pct(stress_m['annualized_return'])} | {stress_m['sharpe']:.3f} | {_pct_abs(stress_m['max_drawdown'])} | ${_number(stress_m['final_equity_usd'])} | ${_number(stress_m['transaction_cost_usd'])} |",
        "",
        "## Activity: fills are not independent trades",
        "",
        f"The base run produced **{base_a['order_fills_total']} order fills** ({base_a['monthly_rebalance_order_fills']} at monthly rebalances plus {base_a['final_liquidation_order_fills']} final liquidation fills) across **{base_m['selection_count']} scheduled monthly selections**. These correspond to **{base_a['holding_episodes']} continuous per-ETF holding episodes**, not {base_a['order_fills_total']} independent trade ideas. Median completed episode length was {base_a['median_calendar_days_per_completed_episode']:.0f} calendar days.",
        "",
        f"At 2x costs, fill count and episodes are unchanged by construction: **{stress_a['order_fills_total']} fills**, **{stress_a['holding_episodes']} holding episodes**. Only the cost assumption changes.",
        "",
        "## Flat-start calendar folds (not additive)",
        "",
        "Each fold starts at $100,000 flat. It uses prior closes only to calculate the already-frozen 252-session lookback; no position is carried between folds. Returns below therefore should not be compounded into the full-window result.",
        "",
        "| Fold | Base return | 2x-cost return | Base max DD | Scheduled selections | Fills | Holding episodes | Note |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for fold in payload["flat_start_calendar_folds"]:
        bm = fold["base"]["metrics"]
        activity = fold["base"]["activity"]
        note = "pre-warmup / no R-252 signal" if not fold["signal_ready_with_252_session_lookback"] else "flat start"
        lines.append(
            f"| {fold['calendar_year']} ({fold['start']}–{fold['end']}) | {_pct(bm['total_return'])} | "
            f"{_pct(fold['two_x_cost']['metrics']['total_return'])} | {_pct_abs(bm['max_drawdown'])} | "
            f"{bm['selection_count']} | {activity['order_fills_total']} | {activity['holding_episodes']} | {note} |"
        )
    lines += [
        "",
        "## Causality and reproducibility checks",
        "",
        "- Every monthly-rebalance fill is recorded strictly after its decision date; none is a same-bar close fill.",
        "- Every requested input parquet is SHA-256 hashed in the JSON artifact, alongside this runner, the frozen R source, and the preregistration document.",
        "- The test uses a strict common-session ETF panel. It does not substitute stale prices for missing sessions.",
        "- The annual folds are independent flat starts, so they make calendar variation visible without hiding a prior-year open position.",
        "",
        "## Important limitation",
        "",
        "This is **not a blind 10-year backtest**: this repository's historical cache was already accessible before this audit. Causal timing avoids look-ahead inside the simulation, but it cannot undo prior human exposure to the data. Do not fund or deploy Book R from this result. The next valid evidence is an externally held vendor lockbox or forward-paper period after the 2026-08-28 specification freeze.",
        "",
        "## Artifacts",
        "",
        f"- JSON audit: `{payload['artifact_paths']['json_relative']}`",
        f"- Frozen specification: `{payload['artifact_paths']['prereg_relative']}`",
        f"- Frozen source audited: `{payload['artifact_paths']['source_relative']}`",
        "",
    ]
    return "\n".join(lines)


def _relative_to_repo(path: Path) -> str:
    return str(path.relative_to(ENGINE_DIR.parent))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_JSON_OUT, help="JSON audit path")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_OUT, help="Markdown report path")
    args = parser.parse_args()

    if not PREREG_PATH.exists() or not SOURCE_PATH.exists():
        raise FileNotFoundError("Book R preregistration or frozen source is missing")
    store_root = ENGINE_DIR / "data_store"
    manifest = panel_manifest(store_root, USD_ETF_UNIVERSE)
    panel = load_usd_etf_panel(store_root, USD_ETF_UNIVERSE)
    common_index = next(iter(panel.values())).index
    requested_start = pd.Timestamp(AUDIT_START, tz="UTC")
    requested_end = pd.Timestamp(AUDIT_END, tz="UTC")
    if common_index.min() > requested_start or common_index.max() < requested_end:
        raise RuntimeError(
            "The pinned USD ETF panel does not cover the exact audit window: "
            f"{_date(common_index.min())} to {_date(common_index.max())}"
        )

    base = run_book_r(panel, FROZEN_SPEC, start=requested_start, end=requested_end)
    stress = run_book_r(panel, _stress_spec(FROZEN_SPEC), start=requested_start, end=requested_end)
    active_dates = common_index[(common_index >= requested_start) & (common_index <= requested_end)]
    first_signal_index = max(FROZEN_SPEC.lookback, FROZEN_SPEC.vol_window)

    payload: dict[str, Any] = {
        "book_id": "book_r_usd_etf_momentum_control",
        "audit_id": "book_r_252_exact_10y_causal_retrospective_2026-08-28",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "research_only_causal_retrospective_not_true_blind",
        "truthfulness_note": (
            "Causal signal/fill timing is enforced, but the cache was already accessible "
            "to this project. This is not an independently held blind lockbox and does not "
            "qualify Book R for funding or deployment."
        ),
        "window": {
            "requested_start": AUDIT_START,
            "requested_end": AUDIT_END,
            "actual_first_common_session": _date(active_dates[0]),
            "actual_last_common_session": _date(active_dates[-1]),
            "common_sessions": int(len(active_dates)),
            "first_panel_date": _date(common_index.min()),
            "last_panel_date": _date(common_index.max()),
            "first_possible_252_session_signal_date": _date(common_index[first_signal_index]),
        },
        "specification": {
            "spec": FROZEN_SPEC.to_dict(),
            "account_currency": "USD",
            "initial_equity_usd": INITIAL_EQUITY_USD,
            "universe": list(USD_ETF_UNIVERSE),
            "execution": {
                "signal_time": "final common session of calendar month, at close",
                "fill_time": "next common trading-session open",
                "intrabar_stops_or_targets": "none",
                "final_liquidation_cost": True,
            },
            "cost_model": {
                "base_bps_per_side": FROZEN_SPEC.cost_bps_per_side,
                "stress_bps_per_side": FROZEN_SPEC.cost_bps_per_side * 2.0,
                "cash_interest": 0.0,
                "dividends": "not reconstructed; price-return bars only",
            },
        },
        "hashes": {
            "runner_sha256": _sha256(Path(__file__)),
            "frozen_book_r_source_sha256": _sha256(SOURCE_PATH),
            "preregistration_sha256": _sha256(PREREG_PATH),
        },
        "data_manifest": manifest,
        "full_history": {
            "base": {
                "metrics": base.metrics,
                "activity": _episodic_summary(base.events),
                "causality_checks": _causality_checks(base),
                "run": base.to_dict(equity_points=2048),
            },
            "two_x_cost": {
                "metrics": stress.metrics,
                "activity": _episodic_summary(stress.events),
                "causality_checks": _causality_checks(stress),
                "run": stress.to_dict(equity_points=2048),
            },
        },
        "flat_start_calendar_folds": _folds(panel),
        "interpretation": {
            "order_fills_are_not_trade_ideas": (
                "Order fills count every rebalance buy/sell. Holding episodes are the appropriate "
                "coarser count of continuous instrument exposures."
            ),
            "flat_start_fold_rule": (
                "Each calendar fold begins with cash; use only the frozen lookback prices preceding "
                "the fold and do not carry positions from an earlier fold."
            ),
            "not_true_blind": True,
            "next_valid_evidence": [
                "externally held, unseen vendor lockbox",
                "or forward paper data collected after the 2026-08-28 frozen specification",
                "verified total-return/dividend series before benchmark-quality ETF comparisons",
            ],
        },
        "artifact_paths": {
            "json_relative": _relative_to_repo(args.out),
            "report_relative": _relative_to_repo(args.report),
            "prereg_relative": _relative_to_repo(PREREG_PATH),
            "source_relative": _relative_to_repo(SOURCE_PATH),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_report(payload))

    print(f"wrote {args.out}")
    print(f"wrote {args.report}")
    print("base metrics:", json.dumps(base.metrics, sort_keys=True))
    print("2x cost metrics:", json.dumps(stress.metrics, sort_keys=True))
    print("base activity:", json.dumps(_episodic_summary(base.events), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
