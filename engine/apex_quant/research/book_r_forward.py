"""Forward-paper state machine for the frozen Book R-252 strategy.

The historical Book R simulator deliberately liquidates at the end of every
requested segment.  A forward book needs different plumbing: persistent cash
and positions, a decision frozen at the month-end close, and execution at the
next common-session open.  This module implements only that state transition;
data fetching, exchange calendars, and persistence stay in the runner.

This is paper accounting in USD.  It never talks to a broker.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from apex_quant.research.book_r_usd_etf import (
    BookRSpec,
    common_panel,
    select_book_r,
)


BOOK_LABEL = "book_r_252_usd_etf_forward_paper"
BOOK_SPEC = BookRSpec(
    name="R-252-forward",
    lookback=252,
    vol_window=63,
    max_positions=3,
    gross_target=0.95,
    cost_bps_per_side=5.0,
)
INITIAL_EQUITY_USD = 100_000.0
SCHEMA_VERSION = 1


def _date(value: pd.Timestamp | str) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.strftime("%Y-%m-%d")


def _position_value(position: dict, price: float) -> float:
    return float(position["units"]) * float(price)


def _state_extra(state: dict) -> dict:
    """Return the restore/display metadata attached to the latest daily row."""
    return {
        "book": state["book"],
        "status": state["status"],
        "account_currency": state["account_currency"],
        "params": copy.deepcopy(state["params"]),
        "initial_equity": state["initial_equity"],
        "peak": state["peak"],
        "cost_total": state["cost_total"],
        "pending": copy.deepcopy(state["pending"]),
        "trades": copy.deepcopy(state["trades"]),
        "fills": copy.deepcopy(state["fills"]),
        "selections": copy.deepcopy(state["selections"]),
        "last_processed_date": state["last_processed_date"],
    }


def validate_forward_state(state: dict) -> None:
    if int(state.get("schema_version", -1)) != SCHEMA_VERSION:
        raise ValueError("unsupported Book R forward state schema")
    if state.get("book") != BOOK_LABEL:
        raise ValueError("refusing state that is not the frozen Book R forward paper book")
    if state.get("account_currency") != "USD":
        raise ValueError("Book R forward state must be denominated in USD")
    if state.get("params") != BOOK_SPEC.to_dict():
        raise ValueError("Book R forward parameters changed; start a new experiment instead")
    if abs(float(state.get("initial_equity", 0.0)) - INITIAL_EQUITY_USD) > 1e-8:
        raise ValueError("Book R forward state is not seeded at $100,000")


def _new_state(seed_date: pd.Timestamp, close_prices: dict[str, float]) -> dict:
    state = {
        "schema_version": SCHEMA_VERSION,
        "book": BOOK_LABEL,
        "status": "active_forward_paper_no_broker_execution",
        "account_currency": "USD",
        "params": BOOK_SPEC.to_dict(),
        "initial_equity": INITIAL_EQUITY_USD,
        "cash": INITIAL_EQUITY_USD,
        "peak": INITIAL_EQUITY_USD,
        "cost_total": 0.0,
        "positions": {},
        "pending": {},
        "fills": [],
        "trades": [],
        "selections": [],
        "equity_curve": [],
        "last_processed_date": _date(seed_date),
        "seeded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _append_daily(
        state,
        seed_date,
        close_prices,
        previous_equity=INITIAL_EQUITY_USD,
        notes="Book R-252 forward paper seeded at $100,000; no broker execution",
    )
    return state


def _mark_equity(state: dict, prices: dict[str, float]) -> tuple[float, float]:
    gross = sum(
        abs(_position_value(position, prices[inst]))
        for inst, position in state["positions"].items()
    )
    equity = float(state["cash"]) + sum(
        _position_value(position, prices[inst])
        for inst, position in state["positions"].items()
    )
    return equity, gross


def _append_daily(
    state: dict,
    date: pd.Timestamp,
    close_prices: dict[str, float],
    *,
    previous_equity: float,
    notes: str,
) -> dict:
    equity, gross = _mark_equity(state, close_prices)
    state["peak"] = max(float(state["peak"]), equity)
    drawdown = max(0.0, 1.0 - equity / float(state["peak"])) if state["peak"] else 0.0
    row = {
        "date": _date(date),
        "equity": round(equity, 6),
        "cash": round(float(state["cash"]), 6),
        "n_open": len(state["positions"]),
        "gross_exposure_x": round(gross / equity, 8) if equity > 0 else 0.0,
        "day_pnl": round(equity - previous_equity, 6),
        "cum_pnl": round(equity - INITIAL_EQUITY_USD, 6),
        "drawdown_from_peak": round(drawdown, 10),
        "notes": notes,
        "metrics": {
            "account_currency": "USD",
            "total_return": equity / INITIAL_EQUITY_USD - 1.0,
            "max_drawdown": max(
                [drawdown]
                + [float(old.get("drawdown_from_peak", 0.0)) for old in state["equity_curve"]]
            ),
            "transaction_cost_usd": float(state["cost_total"]),
            "fill_count": len(state["fills"]),
            "closed_rebalance_legs": len(state["trades"]),
        },
    }
    state["equity_curve"].append(row)
    state["last_processed_date"] = row["date"]
    return row


def _queue_month_end_decision(
    state: dict,
    panel: dict[str, pd.DataFrame],
    i: int,
    decision_date: pd.Timestamp,
) -> list[str]:
    selected = select_book_r(panel, BOOK_SPEC, i)
    state["pending"] = {
        row["instrument"]: {
            "pos": {"direction": "long"},
            "decision_date": _date(decision_date),
            "score": float(row["score"]),
            "momentum": float(row["momentum"]),
            "cluster": row["cluster"],
        }
        for row in selected
    }
    # An empty selection is still an executable instruction: move the book to
    # cash at the next open.  Preserve it explicitly instead of confusing it
    # with "no decision pending".
    if not state["pending"]:
        state["pending"] = {
            "CASH": {
                "pos": {"direction": "flat"},
                "decision_date": _date(decision_date),
                "reason": "absolute_momentum_gate",
            }
        }
    state["selections"].append({
        "decision_date": _date(decision_date),
        "selected": copy.deepcopy(selected),
    })
    return [row["instrument"] for row in selected]


def _execute_pending(
    state: dict,
    date: pd.Timestamp,
    open_prices: dict[str, float],
) -> list[dict]:
    if not state["pending"]:
        return []

    selected = {inst for inst in state["pending"] if inst != "CASH"}
    equity, _ = _mark_equity(state, open_prices)
    target_value = equity * BOOK_SPEC.gross_target / len(selected) if selected else 0.0
    current_units = {
        inst: float(state["positions"].get(inst, {}).get("units", 0.0))
        for inst in open_prices
    }
    desired_units = {
        inst: target_value / open_prices[inst] if inst in selected else 0.0
        for inst in open_prices
    }
    deltas = {inst: desired_units[inst] - current_units[inst] for inst in open_prices}
    cost_rate = BOOK_SPEC.cost_bps_per_side / 10_000.0
    fills: list[dict] = []

    # Sells first makes the cash path explicit even though the final cash
    # result is algebraically identical to a single net rebalance.
    order = sorted(deltas, key=lambda inst: (deltas[inst] > 0.0, inst))
    for inst in order:
        delta = float(deltas[inst])
        if abs(delta) < 1e-12:
            continue
        price = float(open_prices[inst])
        notional = abs(delta) * price
        cost = notional * cost_rate
        side = "buy" if delta > 0 else "sell"
        fill = {
            "date": _date(date),
            "decision_date": next(iter(state["pending"].values()))["decision_date"],
            "instrument": inst,
            "side": side,
            "units": abs(delta),
            "price_usd": price,
            "notional_usd": notional,
            "cost_usd": cost,
            "reason": "monthly_rebalance",
        }

        if delta > 0.0:
            old = state["positions"].get(inst)
            old_units = float(old["units"]) if old else 0.0
            new_units = old_units + delta
            old_basis = old_units * float(old["entry_price"]) if old else 0.0
            position = old or {
                "entry_time": _date(date),
                "entry_cost_remaining": 0.0,
                "realized_pnl_total": 0.0,
                "bars_open": 0,
            }
            position["entry_price"] = (old_basis + delta * price) / new_units
            position["entry_cost_remaining"] = float(position["entry_cost_remaining"]) + cost
            position["units"] = new_units
            position["last_px"] = price
            state["positions"][inst] = position
            state["cash"] -= delta * price + cost
        else:
            position = state["positions"].get(inst)
            if position is None:
                raise RuntimeError(f"Book R attempted to sell an absent position: {inst}")
            sold = -delta
            old_units = float(position["units"])
            if sold > old_units + 1e-8:
                raise RuntimeError(f"Book R attempted to short {inst}")
            fraction = min(1.0, sold / old_units)
            allocated_entry_cost = float(position["entry_cost_remaining"]) * fraction
            pnl = sold * (price - float(position["entry_price"])) - cost - allocated_entry_cost
            position["realized_pnl_total"] = float(position["realized_pnl_total"]) + pnl
            position["entry_cost_remaining"] = max(
                0.0, float(position["entry_cost_remaining"]) - allocated_entry_cost
            )
            position["units"] = max(0.0, old_units - sold)
            position["last_px"] = price
            state["cash"] += sold * price - cost
            state["trades"].append({
                "instrument": inst,
                "direction": "long",
                "units": sold,
                "entry_price": float(position["entry_price"]),
                "entry_time": position["entry_time"],
                "exit_price": price,
                "exit_time": _date(date),
                "pnl": pnl,
                "return_pct": pnl / (sold * float(position["entry_price"])) if sold else 0.0,
                "exit_reason": "monthly_rebalance",
            })
            if position["units"] <= 1e-10:
                del state["positions"][inst]

        state["cost_total"] = float(state["cost_total"]) + cost
        state["fills"].append(fill)
        fills.append(fill)

    if state["cash"] < -1e-6:
        raise RuntimeError("Book R forward sizing attempted to borrow cash")
    state["cash"] = max(0.0, float(state["cash"]))
    state["pending"] = {}
    return fills


def advance_book_r_forward(
    panel: dict[str, pd.DataFrame],
    state: dict | None,
    *,
    month_end_sessions: set[str],
) -> tuple[dict, list[dict]]:
    """Seed or advance the persistent Book R paper state.

    ``panel`` must contain only closed common sessions.  ``month_end_sessions``
    comes from the exchange calendar in the runner, keeping holiday knowledge
    out of this deterministic accounting layer.
    """
    checked = common_panel(panel, panel.keys())
    index = next(iter(checked.values())).index
    instruments = tuple(checked)
    if len(index) < max(BOOK_SPEC.lookback, BOOK_SPEC.vol_window) + 1:
        raise ValueError("Book R forward panel has insufficient warmup")

    if state is None:
        seed_date = index[-1]
        close_prices = {inst: float(checked[inst].loc[seed_date, "close"]) for inst in instruments}
        state = _new_state(seed_date, close_prices)
        if _date(seed_date) in month_end_sessions:
            selected = _queue_month_end_decision(state, checked, len(index) - 1, seed_date)
            state["equity_curve"][-1]["notes"] += (
                f" | queued {len(selected)} ETF(s) for next-session open"
            )
        return state, [state["equity_curve"][-1]]

    state = copy.deepcopy(state)
    validate_forward_state(state)
    last = pd.Timestamp(state["last_processed_date"], tz="UTC")
    to_process = [date for date in index if date > last]
    rows: list[dict] = []

    for date in to_process:
        i = int(index.get_loc(date))
        open_prices = {inst: float(checked[inst].loc[date, "open"]) for inst in instruments}
        close_prices = {inst: float(checked[inst].loc[date, "close"]) for inst in instruments}
        previous_equity = float(state["equity_curve"][-1]["equity"])
        fills = _execute_pending(state, date, open_prices)
        for inst, position in state["positions"].items():
            position["bars_open"] = int(position.get("bars_open", 0)) + 1
            position["last_px"] = close_prices[inst]

        selected: list[str] = []
        if _date(date) in month_end_sessions:
            if state["pending"]:
                raise RuntimeError("Book R has an unfilled prior decision at a new month-end")
            selected = _queue_month_end_decision(state, checked, i, date)

        notes = f"fills {len(fills)}, open {len(state['positions'])}"
        if selected:
            notes += f", month-end decision queued {len(selected)} for next-session open"
        elif _date(date) in month_end_sessions:
            notes += ", month-end absolute-momentum gate selected cash"
        row = _append_daily(
            state,
            date,
            close_prices,
            previous_equity=previous_equity,
            notes=notes,
        )
        rows.append(row)

    return state, rows


def display_daily_rows(state: dict) -> list[dict]:
    validate_forward_state(state)
    rows = copy.deepcopy(state["equity_curve"])
    if rows:
        rows[-1]["state_extra"] = _state_extra(state)
    return rows


def display_position_rows(state: dict, *, updated_at: str | None = None) -> list[dict]:
    validate_forward_state(state)
    stamp = updated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for inst, position in sorted(state["positions"].items()):
        rows.append({
            "instrument": inst,
            "updated_at": stamp,
            "direction": "long",
            "units": float(position["units"]),
            "initial_units": float(position["units"]),
            "entry_price": float(position["entry_price"]),
            "entry_time": position["entry_time"],
            "entry_idx": 0,
            "stop": None,
            "initial_stop": None,
            "target": None,
            "risk_abs": None,
            "tf": "1d",
            "last_px": float(position["last_px"]),
            "bars_open": int(position.get("bars_open", 0)),
            "tms_p1": False,
            "tms_p2": False,
            "tms_be": False,
            "realized_pnl_total": float(position.get("realized_pnl_total", 0.0)),
            "tms_log": [],
        })
    return rows


def runtime_payload(state: dict) -> dict:
    """Compact JSONB payload used by the namespaced Supabase mirror."""
    validate_forward_state(state)
    return {
        "state": copy.deepcopy(state),
        "daily": display_daily_rows(state),
        "positions": display_position_rows(state),
    }
