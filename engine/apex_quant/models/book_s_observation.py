"""Non-trading observations for Book S's completed-hour paper ledger.

Never reconstruct or overwrite cash, fills, positions or old daily rows here.
An old ledger without a durable hourly maximum supplies only a lower bound;
it must not be relabelled as complete merely because later hours are observed.
This measures hourly CLOSE equity, never tick-level or intrabar drawdown.
"""
from __future__ import annotations

import copy
import math

import pandas as pd

TRACKER_VERSION = "hourly_close_drawdown_v1"


def _utc(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _finite(value, name):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Book S {name} must be finite")
    return number


def new_drawdown_tracker(initial_equity, activated_at):
    initial = _finite(initial_equity, "initial equity")
    if initial <= 0:
        raise ValueError("Book S initial equity must be positive")
    return {
        "version": TRACKER_VERSION,
        "basis": "completed_hour_close_equity",
        "complete_since_activation": True,
        "coverage_started_at_utc": _utc(activated_at).isoformat(),
        "observed_through_utc": None,
        "high_water_equity": initial,
        "max_drawdown_fraction": 0.0,
        "max_drawdown_amount_usd": 0.0,
        "max_drawdown_at_utc": None,
        "observation_count": 0,
        "history_note": "All post-activation completed-hour equity observations retained in the running maximum; not intrabar risk.",
    }


def ensure_drawdown_tracker(state):
    """Attach an explicitly incomplete lower bound to a pre-tracker ledger.

    The original monetary ledger is untouched. A saved peak is usable as a
    high-water mark, but the final snapshot of each day cannot reveal the
    maximum intervening loss. Never silently infer completeness from it.
    """
    tracker = state.get("drawdown_tracker")
    if tracker is not None:
        if tracker.get("version") != TRACKER_VERSION:
            raise ValueError("Unsupported Book S drawdown tracker")
        for key in ("high_water_equity", "max_drawdown_fraction", "max_drawdown_amount_usd"):
            value = _finite(tracker[key], key)
            if value < 0 or (key == "high_water_equity" and value == 0):
                raise ValueError(f"Invalid Book S drawdown tracker {key}")
        if not isinstance(tracker.get("complete_since_activation"), bool):
            raise ValueError("Book S drawdown coverage is missing")
        return tracker

    tracker = new_drawdown_tracker(state["initial_equity"], state["last_processed_time"])
    equity = _finite(state["equity"], "equity")
    peak = max(tracker["high_water_equity"], _finite(state["peak"], "peak"), equity)
    tracker.update(
        complete_since_activation=False,
        high_water_equity=peak,
        history_note="Pre-tracker hourly drawdown history is incomplete. Reported maximum is an observed lower bound, not the complete since-activation maximum.",
    )
    # Preserve whatever lower bound the retained snapshots can actually prove.
    for row in state.get("equity_curve", []):
        value = row.get("drawdown")
        if value is not None:
            fraction = max(0.0, _finite(value, "saved drawdown"))
            if fraction > tracker["max_drawdown_fraction"]:
                tracker["max_drawdown_fraction"] = fraction
                tracker["max_drawdown_at_utc"] = None  # saved labels may be date-only
    current_fraction = max(0.0, (peak - equity) / peak)
    tracker["max_drawdown_fraction"] = max(tracker["max_drawdown_fraction"], current_fraction)
    tracker["max_drawdown_amount_usd"] = max(0.0, peak - equity)
    state["drawdown_tracker"] = tracker
    return tracker


def observe_hour_close(state, bar_start, equity, peak):
    """Update a restart-safe maximum before daily snapshots are compressed."""
    tracker = ensure_drawdown_tracker(state)
    closed_at = _utc(bar_start) + pd.Timedelta(hours=1)
    previous = tracker["observed_through_utc"]
    if previous is not None and closed_at <= _utc(previous):
        raise ValueError("Book S drawdown observations must advance strictly once per hour")
    equity = _finite(equity, "hourly equity")
    high = max(tracker["high_water_equity"], _finite(peak, "peak"), equity)
    amount = max(0.0, high - equity)
    fraction = amount / high
    tracker["high_water_equity"] = high
    if fraction > tracker["max_drawdown_fraction"]:
        tracker["max_drawdown_fraction"] = fraction
        tracker["max_drawdown_at_utc"] = closed_at.isoformat()
    tracker["max_drawdown_amount_usd"] = max(tracker["max_drawdown_amount_usd"], amount)
    tracker["observed_through_utc"] = closed_at.isoformat()
    tracker["observation_count"] += 1
    return tracker


def observation_metadata(state):
    """Read-only public projection; missing old tracking remains incomplete."""
    view = copy.deepcopy(state)
    tracker = ensure_drawdown_tracker(view)
    processed = _utc(state["last_processed_time"])
    last = (state.get("equity_curve") or [{}])[-1]
    # A seed timestamp is not a market-data timestamp. Existing processed rows
    # are distinguishable by their execution note; new trackers count hours.
    has_bar = tracker["observation_count"] > 0 or "hourly OHLC" in str(last.get("notes", ""))
    return {
        "drawdown": copy.deepcopy(tracker),
        "last_processed_bar_start_utc": processed.isoformat() if has_bar else None,
        "market_data_through_utc": (processed + pd.Timedelta(hours=1)).isoformat() if has_bar else None,
        "timestamp_convention": "Hourly bar labels are period starts; market_data_through_utc is their close.",
        "execution_evidence": "Retrospective bar-based paper simulation; saved fill timestamps are not broker orders or proof of contemporaneous submission.",
    }
