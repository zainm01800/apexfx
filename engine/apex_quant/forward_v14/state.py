"""Deterministic persistent state machine for V14 GBP forward paper.

Signals are recorded after a settled XNYS close and may fill only at the
immediate next XNYS open when that instruction was durably recordable before
the open.  The accounting is a CFD-style GBP paper ledger: USD price-change
P&L and declared costs are converted; principal is never translated as profit.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .data import (
    DataUnavailable,
    MarketData,
    PRICE_COLUMNS,
    iso_date,
    next_session,
    panel_observation,
    select_fx,
    session_label,
    session_open_utc,
    utc_timestamp,
)
from .guard import cash_budget, floors, latch
from .signals import build_decision
from .spec import (
    SCHEMA_VERSION,
    SOURCE_AMENDMENT_SHA256,
    SOURCE_MANIFEST_SHA256,
    STRATEGY_ID,
    SYMBOLS,
    BookSpec,
)


PERSISTENCE_SAFETY_LEAD = pd.Timedelta(minutes=30)


class ForwardInvariantError(RuntimeError):
    """A persisted state or temporal invariant would be violated."""


class DataRevisionError(DataUnavailable):
    """A previously frozen adjusted bar changed non-uniformly."""


def _now_iso(value: Any | None = None) -> str:
    return utc_timestamp(value or datetime.now(timezone.utc)).isoformat()


def _event(state: dict, date: Any, phase: str, kind: str, **extra: Any) -> None:
    state["events"].append(
        {
            "sequence": len(state["events"]) + 1,
            "date": iso_date(date),
            "phase": phase,
            "event": kind,
            **copy.deepcopy(extra),
        }
    )


def validate_state(state: Mapping[str, Any], spec: BookSpec) -> None:
    if not isinstance(state, Mapping):
        raise ValueError("V14 forward state must be an object")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported V14 forward state schema")
    if state.get("book_id") != spec.book_id:
        raise ValueError("state belongs to a different V14 book")
    if state.get("strategy_id") != STRATEGY_ID or state.get("profile") != spec.profile:
        raise ValueError("state strategy/profile mismatch")
    if state.get("account_currency") != "GBP":
        raise ValueError("V14 forward state must be GBP")
    if state.get("paper_only") is not True or state.get("broker_enabled") is not False:
        raise ValueError("V14 forward state must remain paper-only")
    if state.get("funded_qualified") is not False:
        raise ValueError("experimental V14 state cannot claim funded qualification")
    if state.get("spec") != spec.to_dict() or state.get("spec_sha256") != spec.spec_sha256:
        raise ValueError("V14 parameters changed; start a separate experiment")
    if state.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA256:
        raise ValueError("V14 source manifest mismatch")
    if state.get("source_amendment_sha256") != SOURCE_AMENDMENT_SHA256:
        raise ValueError("V14 source amendment mismatch")
    if abs(float(state.get("initial_equity", 0.0)) - spec.initial_equity_gbp) > 1e-8:
        raise ValueError("V14 forward state is not seeded at GBP100,000")
    if not isinstance(state.get("revision"), int) or int(state["revision"]) < 1:
        raise ValueError("invalid V14 state revision")
    parent_hash = state.get("parent_state_sha256")
    if parent_hash is not None and (
        not isinstance(parent_hash, str) or len(parent_hash) != 64
    ):
        raise ValueError("invalid V14 parent state hash")
    for key in ("positions", "daily", "trades", "events", "decisions"):
        expected = dict if key == "positions" else list
        if not isinstance(state.get(key), expected):
            raise ValueError(f"invalid V14 state collection: {key}")
    if not state["daily"] or state["daily"][-1].get("date") != state.get("last_processed_session"):
        raise ValueError("last processed session does not match the daily ledger")
    pending = state.get("pending_batch")
    if pending is not None:
        if not isinstance(pending, dict) or not isinstance(pending.get("legs"), list):
            raise ValueError("invalid pending V14 batch")
        if iso_date(next_session(pending["decision_date"])) != pending.get("eligible_fill_session"):
            raise ValueError("pending V14 batch is not assigned to the immediate next XNYS open")
    for key in ("cash", "peak", "cost_total_gbp"):
        if not isfinite(float(state.get(key, np.nan))):
            raise ValueError(f"non-finite V14 state value: {key}")
    _reconcile(state, spec)


def _reconcile(state: Mapping[str, Any], spec: BookSpec) -> None:
    """Independently reconcile realized cash and the latest marked equity."""

    closed_net = sum(float(trade["net_pnl_gbp"]) for trade in state.get("trades", []))
    open_costs = sum(
        float(lot["entry_fee_gbp"])
        + float(lot["holding_cost_gbp"])
        + float(lot["borrow_cost_gbp"])
        for lot in state.get("positions", {}).values()
    )
    expected_cash = spec.initial_equity_gbp + closed_net - open_costs
    if abs(float(state["cash"]) - expected_cash) > 1e-6:
        raise ForwardInvariantError(
            f"cash attribution failed by {float(state['cash']) - expected_cash:.9f} GBP"
        )
    latest = state.get("daily", [])[-1] if state.get("daily") else None
    if latest is None:
        return
    if state.get("positions"):
        fx = state.get("last_fx") or {}
        rate = float(fx.get("rate", np.nan))
        if not isfinite(rate) or rate <= 0:
            raise ForwardInvariantError("open positions lack a valid latest FX mark")
        prices = {
            symbol: float(lot["last_px"])
            for symbol, lot in state["positions"].items()
        }
        marked = _equity(state, prices, rate)
    else:
        marked = float(state["cash"])
    if abs(float(latest["equity"]) - marked) > 1e-6:
        raise ForwardInvariantError(
            f"latest equity attribution failed by {float(latest['equity']) - marked:.9f} GBP"
        )


def _equity(state: Mapping[str, Any], prices: Mapping[str, float], rate: float) -> float:
    return float(state["cash"]) + sum(
        float(lot["direction_sign"])
        * float(lot["units"])
        * (float(prices[symbol]) - float(lot["entry_price"]))
        / rate
        for symbol, lot in state["positions"].items()
    )


def _open_pnl(state: Mapping[str, Any], prices: Mapping[str, float], rate: float) -> float:
    return _equity(state, prices, rate) - float(state["cash"])


def _risk(
    state: Mapping[str, Any], prices: Mapping[str, float], rate: float, spec: BookSpec
) -> tuple[float, float, float, float]:
    fee = spec.fee_bps_each_side / 10_000.0
    slip = spec.stop_slippage_bps / 10_000.0
    risks, notionals = [], []
    for symbol, lot in state["positions"].items():
        direction = int(lot["direction_sign"])
        stop_fill = float(lot["stop_price"]) * (1.0 - direction * slip)
        risk_gbp = (
            max(0.0, direction * (float(prices[symbol]) - stop_fill))
            * float(lot["units"])
            / rate
            + abs(stop_fill) * float(lot["units"]) / rate * fee
        )
        risks.append(risk_gbp)
        notionals.append(abs(float(prices[symbol]) * float(lot["units"]) / rate))
    return sum(risks), max(risks, default=0.0), sum(notionals), max(notionals, default=0.0)


def _close_lot(
    state: dict,
    symbol: str,
    price: float,
    date: Any,
    phase: str,
    reason: str,
    rate: float,
    totals: dict,
    spec: BookSpec,
    *,
    unslipped_price: float | None = None,
) -> None:
    lot = state["positions"].pop(symbol)
    direction = int(lot["direction_sign"])
    units = float(lot["units"])
    fee_rate = spec.fee_bps_each_side / 10_000.0
    gross = direction * units * (price - float(lot["entry_price"])) / rate
    exit_fee = units * price / rate * fee_rate
    stop_slippage = (
        direction * ((unslipped_price if unslipped_price is not None else price) - price)
        * units
        / rate
    )
    state["cash"] = float(state["cash"]) + gross - exit_fee
    state["exit_fees_gbp"] = float(state["exit_fees_gbp"]) + exit_fee
    state["stop_slippage_cost_gbp"] = (
        float(state["stop_slippage_cost_gbp"]) + stop_slippage
    )
    state["cost_total_gbp"] = (
        float(state["entry_fees_gbp"])
        + float(state["exit_fees_gbp"])
        + float(state["holding_cost_gbp"])
        + float(state["borrow_cost_gbp"])
    )
    totals["exit_fees"] += exit_fee
    totals["stop_slippage_cost"] += stop_slippage
    net_pnl = (
        gross
        - exit_fee
        - float(lot["entry_fee_gbp"])
        - float(lot["holding_cost_gbp"])
        - float(lot["borrow_cost_gbp"])
    )
    trade = {
        "instrument": symbol,
        "symbol": symbol,
        "direction": "long" if direction > 0 else "short",
        "units": units,
        "entry_price": float(lot["entry_price"]),
        "entry_time": lot["entry_date"],
        "exit_price": float(price),
        "exit_time": iso_date(date),
        "exit_phase": phase,
        "pnl": net_pnl,
        "net_pnl_gbp": net_pnl,
        "gross_pnl_gbp": gross,
        "return_pct": net_pnl
        / max(abs(units * float(lot["entry_price"]) / float(lot["entry_fx_rate"])), 1e-12),
        "exit_reason": reason,
        "entry_fee_gbp": float(lot["entry_fee_gbp"]),
        "exit_fee_gbp": exit_fee,
        "holding_cost_gbp": float(lot["holding_cost_gbp"]),
        "borrow_cost_gbp": float(lot["borrow_cost_gbp"]),
        "stop_slippage_cost_gbp": stop_slippage,
        "unslipped_exit_price": float(unslipped_price if unslipped_price is not None else price),
        "entry_fx_rate": float(lot["entry_fx_rate"]),
        "exit_fx_rate": rate,
        "decision_date": lot["decision_date"],
        "decision_recorded_at_utc": lot["decision_recorded_at_utc"],
        "regime": lot["regime"],
        "lagged_vix": float(lot["lagged_vix"]),
        "lagged_vix_source_date": lot["lagged_vix_source_date"],
        "scheduled_exit_session": lot["scheduled_exit_session"],
        "lot_id": lot["lot_id"],
        "initial_stop": float(lot["initial_stop"]),
        "stop": float(lot["stop_price"]),
        "decision_atr": float(lot["decision_atr"]),
        "decision_atr_original": float(lot["decision_atr_original"]),
        "initial_total_risk_gbp": float(lot["initial_total_risk_gbp"]),
        "initial_total_risk": float(lot["initial_total_risk_gbp"]),
        "signal_rationale": lot["signal_rationale"],
        "decision_input_sha256": lot["decision_input_sha256"],
    }
    state["trades"].append(trade)
    _event(
        state,
        date,
        phase,
        "exit",
        instrument=symbol,
        lot_id=lot["lot_id"],
        reason=reason,
        price=price,
        fx_rate=rate,
        net_pnl_gbp=net_pnl,
    )


def _flatten(
    state: dict,
    prices: Mapping[str, float],
    date: Any,
    phase: str,
    reason: str,
    rate: float,
    totals: dict,
    spec: BookSpec,
) -> None:
    for symbol in list(state["positions"]):
        _close_lot(state, symbol, prices[symbol], date, phase, reason, rate, totals, spec)


def _queue_decision(
    state: dict,
    market: MarketData,
    decision_date: Any,
    recorded_at: Any,
    marked_equity: float,
    spec: BookSpec,
    *,
    limits_override=None,
) -> dict | None:
    decision_day = session_label(decision_date)
    next_day = next_session(decision_day)
    next_open = session_open_utc(next_day)
    recorded = utc_timestamp(recorded_at)
    decision = build_decision(market.panel, market.vix, decision_day, spec)
    decision.update(
        {
            "decision_recorded_at_utc": recorded.isoformat(),
            "eligible_fill_session": iso_date(next_day),
            "eligible_fill_open_utc": next_open.isoformat(),
            "status": "NO_SIGNAL" if not decision["legs"] else "OBSERVED",
        }
    )
    state["decisions"].append(copy.deepcopy(decision))
    if not decision["legs"]:
        _event(
            state,
            decision_day,
            "close",
            "no_signal",
            regime=decision["regime"],
            lagged_vix=decision["lagged_vix"],
        )
        return None
    safety_deadline = next_open - PERSISTENCE_SAFETY_LEAD
    if recorded >= safety_deadline:
        decision["status"] = "SKIPPED_NOT_RECORDED_BEFORE_OPEN"
        state["decisions"][-1]["status"] = decision["status"]
        state["evidence_gap_count"] = int(state["evidence_gap_count"]) + 1
        _event(
            state,
            decision_day,
            "close",
            "decision_evidence_gap",
            reason="instruction missed the pre-open persistence safety deadline",
            eligible_fill_session=iso_date(next_day),
        )
        return None
    limits = limits_override or floors(spec, float(state["cash"]), marked_equity)
    reserve = min(
        cash_budget(marked_equity, limits, halted=bool(state["halted"])),
        spec.aggregate_risk_fraction * spec.entry_utilization * marked_equity,
    )
    if reserve <= 0:
        decision["status"] = "REJECTED_NO_RISK_BUDGET"
        state["decisions"][-1]["status"] = decision["status"]
        _event(state, decision_day, "close", "batch_rejected", reason="no risk budget")
        return None
    pending = {
        **decision,
        "status": "PENDING_NEXT_OPEN",
        "reserved_budget_gbp": reserve,
        "scheduled_exit_session": iso_date(next_session(next_day, spec.holding_sessions)),
        "persistence_safety_deadline_utc": safety_deadline.isoformat(),
    }
    _event(
        state,
        decision_day,
        "close",
        "batch_planned",
        instruments=[leg["instrument"] for leg in pending["legs"]],
        reserved_budget_gbp=reserve,
        eligible_fill_session=pending["eligible_fill_session"],
    )
    return pending


def _seed_daily(state: dict, spec: BookSpec, decision: dict | None) -> None:
    limits = floors(spec, spec.initial_equity_gbp, spec.initial_equity_gbp)
    notes = "GBP100,000 experimental forward-paper account activated; no historical P&L"
    if decision:
        notes += f"; {len(decision['legs'])} leg(s) persisted for next XNYS open"
    else:
        notes += "; no qualifying close signal"
    state["daily"].append(
        {
            "date": state["seed_session"],
            "is_seed": True,
            "equity": spec.initial_equity_gbp,
            "cash": spec.initial_equity_gbp,
            "open_pnl": 0.0,
            "n_open": 0,
            "gross_exposure_x": 0.0,
            "day_pnl": 0.0,
            "cum_pnl": 0.0,
            "drawdown_from_peak": 0.0,
            "external_daily_floor": limits.external_daily,
            "external_maximum_floor": limits.external_maximum,
            "internal_daily_floor": limits.internal_daily,
            "internal_maximum_floor": limits.internal_maximum,
            "possible_external_daily_touch": False,
            "possible_external_maximum_touch": False,
            "possible_internal_daily_touch": False,
            "possible_internal_maximum_touch": False,
            "halted": False,
            "day_blocked": False,
            "entry_fees": 0.0,
            "exit_fees": 0.0,
            "holding_cost": 0.0,
            "borrow_cost": 0.0,
            "stop_slippage_cost": 0.0,
            "fx_rate": None,
            "fx_source_date": None,
            "notes": notes,
            "metrics": {
                "account_currency": "GBP",
                "total_return": 0.0,
                "max_drawdown": 0.0,
                "cost_total_gbp": 0.0,
                "closed_lots": 0,
                "session_count": 0,
            },
        }
    )


def new_state(spec: BookSpec, market: MarketData, now: Any | None = None) -> dict:
    """Activate a fresh GBP100k account at the latest settled close.

    Activation refuses stale data whose immediate next XNYS open has already
    occurred.  That makes the first pending instruction genuine forward evidence.
    """

    recorded = utc_timestamp(now or market.retrieved_at_utc)
    seed = session_label(market.latest_completed_session)
    next_day = next_session(seed)
    if recorded >= session_open_utc(next_day):
        raise DataUnavailable(
            "latest common adjusted bar is stale; its immediate next XNYS open already occurred"
        )
    state = {
        "schema_version": SCHEMA_VERSION,
        "book_id": spec.book_id,
        "book": spec.label,
        "strategy_id": STRATEGY_ID,
        "profile": spec.profile,
        "spec": spec.to_dict(),
        "spec_sha256": spec.spec_sha256,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "source_amendment_sha256": SOURCE_AMENDMENT_SHA256,
        "status": "ACTIVE_FORWARD_PAPER",
        "paper_only": True,
        "broker_enabled": False,
        "funded_qualified": False,
        "experimental": True,
        "account_currency": "GBP",
        "initial_equity": spec.initial_equity_gbp,
        "activation_recorded_at_utc": recorded.isoformat(),
        "seed_session": iso_date(seed),
        "first_eligible_decision_session": iso_date(next_day),
        "last_processed_session": iso_date(seed),
        "last_data_as_of": iso_date(seed),
        "cash": spec.initial_equity_gbp,
        "peak": spec.initial_equity_gbp,
        "halted": False,
        "entry_fees_gbp": 0.0,
        "exit_fees_gbp": 0.0,
        "holding_cost_gbp": 0.0,
        "borrow_cost_gbp": 0.0,
        "stop_slippage_cost_gbp": 0.0,
        "cost_total_gbp": 0.0,
        "positions": {},
        "pending_batch": None,
        "daily": [],
        "trades": [],
        "events": [],
        "decisions": [],
        "next_lot_id": 1,
        "evidence_gap_count": 0,
        "last_observed_bars": panel_observation(market.panel, seed),
        "last_fx": None,
        "data_provenance": copy.deepcopy(market.provenance),
        "revision": 1,
        "parent_state_sha256": None,
    }
    pending = _queue_decision(
        state, market, seed, recorded, spec.initial_equity_gbp, spec
    )
    state["pending_batch"] = pending
    _seed_daily(state, spec, pending)
    validate_state(state, spec)
    return state


def enforce_persistence_deadline(
    state: Mapping[str, Any], spec: BookSpec, now: Any | None = None
) -> dict:
    """Drop a newly planned batch if durable writing no longer has safe lead."""

    output = copy.deepcopy(dict(state))
    validate_state(output, spec)
    pending = output.get("pending_batch")
    if not pending:
        return output
    stamp = utc_timestamp(now or datetime.now(timezone.utc))
    if stamp < utc_timestamp(pending["persistence_safety_deadline_utc"]):
        return output
    output["pending_batch"] = None
    output["evidence_gap_count"] = int(output["evidence_gap_count"]) + 1
    for decision in reversed(output["decisions"]):
        if decision.get("decision_date") == pending["decision_date"]:
            decision["status"] = "SKIPPED_PERSISTENCE_DEADLINE"
            break
    _event(
        output,
        pending["decision_date"],
        "persistence",
        "decision_evidence_gap",
        reason="remote verification could not start before safety deadline",
        eligible_fill_session=pending["eligible_fill_session"],
    )
    output["daily"][-1]["notes"] += "; pending dropped before persistence deadline"
    output["daily"][-1]["metrics"]["evidence_gap_count"] = int(
        output["evidence_gap_count"]
    )
    validate_state(output, spec)
    return output


def _apply_adjustment_rebase(
    state: dict, panel: Mapping[str, pd.DataFrame], processing_date: Any
) -> bool:
    """Rebase open synthetic units across a uniform adjusted-history revision.

    This preserves already accrued GBP P&L while allowing a legitimate uniform
    split/dividend adjustment.  A non-uniform correction to the frozen anchor is
    ambiguous and therefore fails closed instead of rewriting evidence.
    """

    anchor = session_label(state["last_processed_session"])
    frozen = state.get("last_observed_bars") or {}
    if set(frozen) != set(SYMBOLS):
        raise ForwardInvariantError("missing last adjusted-bar freeze")
    changed = False
    for symbol in SYMBOLS:
        fresh = {column: float(panel[symbol].at[anchor, column]) for column in PRICE_COLUMNS}
        old = {column: float(frozen[symbol][column]) for column in PRICE_COLUMNS}
        ratios = [fresh[column] / old[column] for column in PRICE_COLUMNS]
        if max(abs(ratio - 1.0) for ratio in ratios) <= 1e-11:
            continue
        if max(ratios) - min(ratios) > 1e-8 * max(1.0, abs(sum(ratios) / len(ratios))):
            raise DataRevisionError(
                f"{symbol}: prior adjusted anchor changed non-uniformly; state left untouched"
            )
        ratio = sum(ratios) / len(ratios)
        changed = True
        lot = state["positions"].get(symbol)
        if lot is not None:
            for key in ("entry_price", "stop_price", "initial_stop", "last_px", "decision_atr"):
                lot[key] = float(lot[key]) * ratio
            lot["units"] = float(lot["units"]) / ratio
            lot["initial_units"] = float(lot["initial_units"]) / ratio
            lot["entry_adjustment_factor"] = float(lot.get("entry_adjustment_factor", 1.0)) * ratio
        pending = state.get("pending_batch")
        if pending:
            for leg in pending["legs"]:
                if leg["instrument"] == symbol:
                    leg.setdefault("decision_atr_original", float(leg["decision_atr"]))
                    leg["decision_atr"] = float(leg["decision_atr"]) * ratio
                    leg["decision_atr_adjustment_factor"] = (
                        float(leg.get("decision_atr_adjustment_factor", 1.0)) * ratio
                    )
        _event(
            state,
            processing_date,
            "preopen",
            "adjusted_history_rebase",
            instrument=symbol,
            anchor_session=iso_date(anchor),
            factor=ratio,
            open_position=lot is not None,
        )
    state["last_observed_bars"] = panel_observation(panel, anchor)
    return changed


def _execute_pending(
    state: dict,
    pending: dict,
    date: pd.Timestamp,
    opens: Mapping[str, float],
    rate: float,
    limits,
    spec: BookSpec,
    totals: dict,
) -> bool:
    if pending["eligible_fill_session"] != iso_date(date):
        raise ForwardInvariantError("pending instruction missed its immediate eligible session")
    if utc_timestamp(pending["decision_recorded_at_utc"]) >= session_open_utc(date):
        raise ForwardInvariantError("pending instruction was not recorded before its fill open")
    components = []
    fee = spec.fee_bps_each_side / 10_000.0
    slip = spec.stop_slippage_bps / 10_000.0
    reserve_days = (next_session(date, spec.holding_sessions) - date).days
    for leg in pending["legs"]:
        symbol = leg["instrument"]
        direction = int(leg["direction_sign"])
        entry = float(opens[symbol])
        atr = float(leg["decision_atr"])
        stop = entry - direction * spec.stop_atr_multiple * atr
        if not isfinite(stop) or stop <= 0:
            _event(state, date, "open", "batch_rejected", reason="invalid nonpositive stop")
            return False
        stop_fill = stop * (1.0 - direction * slip)
        stop_r = (
            max(0.0, direction * (entry - stop_fill)) / entry
            + stop_fill / entry * fee
        )
        holding_r = reserve_days / 365.0 * (
            spec.annual_holding_rate
            + (spec.annual_short_borrow_rate if direction < 0 else 0.0)
        )
        components.append((leg, entry, stop, stop_r, holding_r))
    marked = _equity(state, opens, rate)
    m = len(components)
    risks = [row[3] + row[4] + fee for row in components]
    total_risk_fraction = sum(risks)
    per_notional = min(
        spec.gross_fraction
        * spec.entry_utilization
        * marked
        / (m * (1.0 + spec.gross_fraction * spec.entry_utilization * fee)),
        spec.single_name_fraction
        * spec.entry_utilization
        * marked
        / (1.0 + spec.single_name_fraction * spec.entry_utilization * m * fee),
        spec.aggregate_risk_fraction
        * spec.entry_utilization
        * marked
        / (
            total_risk_fraction
            + spec.aggregate_risk_fraction * spec.entry_utilization * m * fee
        ),
        spec.per_trade_risk_fraction
        * marked
        / (max(risks) + spec.per_trade_risk_fraction * m * fee),
        cash_budget(marked, limits, halted=bool(state["halted"])) / total_risk_fraction,
        float(pending["reserved_budget_gbp"]) / total_risk_fraction,
    ) * (1.0 - 1e-12)
    if not isfinite(per_notional) or per_notional <= 1e-8:
        _event(state, date, "open", "batch_rejected", reason="insufficient shared entry budget")
        return False
    scheduled_exit = iso_date(next_session(date, spec.holding_sessions))
    for leg, entry, stop, stop_r, holding_r in components:
        symbol = leg["instrument"]
        direction = int(leg["direction_sign"])
        units = per_notional * rate / entry
        entry_fee = per_notional * fee
        state["cash"] = float(state["cash"]) - entry_fee
        state["entry_fees_gbp"] = float(state["entry_fees_gbp"]) + entry_fee
        totals["entry_fees"] += entry_fee
        lot_id = int(state["next_lot_id"])
        state["next_lot_id"] = lot_id + 1
        state["positions"][symbol] = {
            "lot_id": lot_id,
            "direction": "long" if direction > 0 else "short",
            "direction_sign": direction,
            "units": units,
            "initial_units": units,
            "decision_date": pending["decision_date"],
            "decision_recorded_at_utc": pending["decision_recorded_at_utc"],
            "decision_input_sha256": pending["decision_input_sha256"],
            "entry_date": iso_date(date),
            "entry_price": entry,
            "entry_fx_rate": rate,
            "entry_fx_source_date": state["last_fx"]["source_date"],
            "decision_atr": float(leg["decision_atr"]),
            "decision_atr_original": float(leg.get("decision_atr_original", leg["decision_atr"])),
            "stop_price": stop,
            "initial_stop": stop,
            "last_px": entry,
            "bars_open": 0,
            "entry_fee_gbp": entry_fee,
            "holding_cost_gbp": 0.0,
            "borrow_cost_gbp": 0.0,
            "reserved_holding_calendar_days": reserve_days,
            "reserved_holding_gbp": per_notional * holding_r,
            "initial_total_risk_gbp": per_notional * (stop_r + holding_r + fee),
            "regime": pending["regime"],
            "lagged_vix": pending["lagged_vix"],
            "lagged_vix_source_date": pending["lagged_vix_source_date"],
            "score": leg["score"],
            "signal_rationale": leg["signal_rationale"],
            "scheduled_exit_session": scheduled_exit,
            "entry_adjustment_factor": 1.0,
        }
        _event(
            state,
            date,
            "open",
            "entry",
            instrument=symbol,
            lot_id=lot_id,
            direction=state["positions"][symbol]["direction"],
            units=units,
            price=entry,
            stop_price=stop,
            fx_rate=rate,
            entry_fee_gbp=entry_fee,
        )
    state["cost_total_gbp"] = (
        float(state["entry_fees_gbp"])
        + float(state["exit_fees_gbp"])
        + float(state["holding_cost_gbp"])
        + float(state["borrow_cost_gbp"])
    )
    return True


def _append_daily(
    state: dict,
    date: pd.Timestamp,
    prices: Mapping[str, float],
    rate: float,
    previous_equity: float,
    limits,
    conservative_min: float,
    blocked: bool,
    totals: Mapping[str, float],
    spec: BookSpec,
    notes: str,
) -> dict:
    equity = _equity(state, prices, rate)
    open_pnl = _open_pnl(state, prices, rate)
    _, _, gross, _ = _risk(state, prices, rate, spec)
    state["peak"] = max(float(state["peak"]), equity)
    drawdown = max(0.0, 1.0 - equity / float(state["peak"])) if state["peak"] > 0 else 1.0
    prior_max = max(
        [drawdown] + [float(row.get("drawdown_from_peak", 0.0)) for row in state["daily"]]
    )
    row = {
        "date": iso_date(date),
        "is_seed": False,
        "equity": equity,
        "cash": float(state["cash"]),
        "open_pnl": open_pnl,
        "n_open": len(state["positions"]),
        "gross_exposure_x": gross / equity if equity > 0 else 0.0,
        "day_pnl": equity - previous_equity,
        "cum_pnl": equity - spec.initial_equity_gbp,
        "drawdown_from_peak": drawdown,
        "external_daily_floor": limits.external_daily,
        "external_maximum_floor": limits.external_maximum,
        "internal_daily_floor": limits.internal_daily,
        "internal_maximum_floor": limits.internal_maximum,
        "possible_external_daily_touch": conservative_min <= limits.external_daily,
        "possible_external_maximum_touch": conservative_min <= limits.external_maximum,
        "possible_internal_daily_touch": conservative_min <= limits.internal_daily,
        "possible_internal_maximum_touch": conservative_min <= limits.internal_maximum,
        "halted": bool(state["halted"]),
        "day_blocked": bool(blocked),
        "entry_fees": float(totals["entry_fees"]),
        "exit_fees": float(totals["exit_fees"]),
        "holding_cost": float(totals["holding_cost"]),
        "borrow_cost": float(totals["borrow_cost"]),
        "stop_slippage_cost": float(totals["stop_slippage_cost"]),
        "fx_rate": rate,
        "fx_source_date": state["last_fx"]["source_date"],
        "fx_available_at_utc": state["last_fx"]["available_at_utc"],
        "notes": notes,
        "metrics": {
            "account_currency": "GBP",
            "total_return": equity / spec.initial_equity_gbp - 1.0,
            "max_drawdown": prior_max,
            "cost_total_gbp": float(state["cost_total_gbp"]),
            "entry_fees_gbp": float(state["entry_fees_gbp"]),
            "exit_fees_gbp": float(state["exit_fees_gbp"]),
            "holding_cost_gbp": float(state["holding_cost_gbp"]),
            "borrow_cost_gbp": float(state["borrow_cost_gbp"]),
            "stop_slippage_cost_gbp": float(state["stop_slippage_cost_gbp"]),
            "closed_lots": len(state["trades"]),
            "session_count": len(state["daily"]),
            "evidence_gap_count": int(state["evidence_gap_count"]),
        },
    }
    state["daily"].append(row)
    state["last_processed_session"] = row["date"]
    return row


def _process_session(
    state: dict,
    market: MarketData,
    date: pd.Timestamp,
    now: pd.Timestamp,
    spec: BookSpec,
    pending_was_durable: bool,
) -> dict:
    prior = session_label(state["last_processed_session"])
    if date != next_session(prior):
        raise ForwardInvariantError("forward step skipped an official XNYS session")
    fx = select_fx(market.fx, date, spec)
    state["last_fx"] = fx
    rate = float(fx["rate"])
    opens = {symbol: float(market.panel[symbol].at[date, "open"]) for symbol in SYMBOLS}
    highs = {symbol: float(market.panel[symbol].at[date, "high"]) for symbol in SYMBOLS}
    lows = {symbol: float(market.panel[symbol].at[date, "low"]) for symbol in SYMBOLS}
    closes = {symbol: float(market.panel[symbol].at[date, "close"]) for symbol in SYMBOLS}
    previous_equity = float(state["daily"][-1]["equity"])
    day_balance, day_equity = float(state["cash"]), previous_equity
    limits = floors(spec, day_balance, day_equity)
    totals = {
        "entry_fees": 0.0,
        "exit_fees": 0.0,
        "holding_cost": 0.0,
        "borrow_cost": 0.0,
        "stop_slippage_cost": 0.0,
    }
    elapsed_days = int((date - prior).days)
    for symbol, lot in state["positions"].items():
        prior_price = float(market.panel[symbol].at[prior, "close"])
        notional = abs(float(lot["units"]) * prior_price / rate)
        holding = notional * spec.annual_holding_rate * elapsed_days / 365.0
        borrow = (
            notional * spec.annual_short_borrow_rate * elapsed_days / 365.0
            if int(lot["direction_sign"]) < 0
            else 0.0
        )
        lot["holding_cost_gbp"] = float(lot["holding_cost_gbp"]) + holding
        lot["borrow_cost_gbp"] = float(lot["borrow_cost_gbp"]) + borrow
        state["cash"] = float(state["cash"]) - holding - borrow
        state["holding_cost_gbp"] = float(state["holding_cost_gbp"]) + holding
        state["borrow_cost_gbp"] = float(state["borrow_cost_gbp"]) + borrow
        totals["holding_cost"] += holding
        totals["borrow_cost"] += borrow
    state["cost_total_gbp"] = (
        float(state["entry_fees_gbp"])
        + float(state["exit_fees_gbp"])
        + float(state["holding_cost_gbp"])
        + float(state["borrow_cost_gbp"])
    )

    raw_open = _equity(state, opens, rate)
    conservative_min = min(day_equity, raw_open)
    pre_risk, _, pre_gross, pre_name = _risk(state, opens, rate, spec)
    overages = []
    if state["positions"]:
        if pre_risk > spec.aggregate_risk_fraction * raw_open + 1e-7:
            overages.append("aggregate_risk")
        if pre_gross > spec.gross_fraction * raw_open + 1e-7:
            overages.append("gross")
        if pre_name > spec.single_name_fraction * raw_open + 1e-7:
            overages.append("single_name")
        if pre_risk > max(0.0, raw_open - limits.internal) + 1e-7:
            overages.append("cash_headroom")
    if overages:
        _event(state, date, "open", "preopen_overage", reasons=overages)

    exited = False
    slip = spec.stop_slippage_bps / 10_000.0
    for symbol in list(state["positions"]):
        lot = state["positions"][symbol]
        direction = int(lot["direction_sign"])
        if direction * (opens[symbol] - float(lot["stop_price"])) <= 0:
            _close_lot(
                state,
                symbol,
                opens[symbol] * (1.0 - direction * slip),
                date,
                "open",
                "gap_stop",
                rate,
                totals,
                spec,
                unslipped_price=opens[symbol],
            )
            exited = True

    observed_open = min(raw_open, _equity(state, opens, rate))
    state["halted"] = latch(bool(state["halted"]), observed_open, limits)
    blocked = bool(state["halted"]) or observed_open <= limits.internal_daily
    if state["positions"] and (blocked or overages):
        _flatten(
            state,
            opens,
            date,
            "open",
            "maximum_guard" if state["halted"] else "daily_guard" if blocked else "maintenance_guard",
            rate,
            totals,
            spec,
        )
        exited = True
        blocked = True

    for symbol in list(state["positions"]):
        if iso_date(date) >= state["positions"][symbol]["scheduled_exit_session"]:
            _close_lot(state, symbol, opens[symbol], date, "open", "time_exit", rate, totals, spec)
            exited = True

    after_exits = _equity(state, opens, rate)
    state["halted"] = latch(bool(state["halted"]), after_exits, limits)
    blocked = blocked or bool(state["halted"]) or after_exits <= limits.internal_daily
    if blocked and state["positions"]:
        _flatten(state, opens, date, "open", "cost_guard", rate, totals, spec)
        exited = True

    pending = state.get("pending_batch")
    if pending is not None:
        if pending["eligible_fill_session"] < iso_date(date):
            raise ForwardInvariantError("persisted pending instruction passed without processing")
        if pending["eligible_fill_session"] == iso_date(date):
            if not pending_was_durable:
                state["evidence_gap_count"] = int(state["evidence_gap_count"]) + 1
                _event(
                    state,
                    date,
                    "open",
                    "pending_cancelled",
                    reason="instruction was not restored from durable remote state",
                )
            elif not state["positions"] and not blocked and not exited:
                _execute_pending(state, pending, date, opens, rate, limits, spec, totals)
            else:
                _event(state, date, "open", "pending_cancelled", reason="guard or same-open exit")
            state["pending_batch"] = None

    post_equity = _equity(state, opens, rate)
    conservative_min = min(conservative_min, post_equity)
    adverse = float(state["cash"])
    fee = spec.fee_bps_each_side / 10_000.0
    for symbol, lot in state["positions"].items():
        direction = int(lot["direction_sign"])
        worst = lows[symbol] if direction > 0 else highs[symbol]
        touched = direction * (worst - float(lot["stop_price"])) <= 0
        if touched:
            worst = float(lot["stop_price"]) * (1.0 - direction * slip)
        adverse += direction * float(lot["units"]) * (worst - float(lot["entry_price"])) / rate
        if touched:
            adverse -= float(lot["units"]) * worst / rate * fee
    conservative_min = min(conservative_min, adverse)
    for symbol in list(state["positions"]):
        lot = state["positions"][symbol]
        direction = int(lot["direction_sign"])
        worst = lows[symbol] if direction > 0 else highs[symbol]
        if direction * (worst - float(lot["stop_price"])) <= 0:
            _close_lot(
                state,
                symbol,
                float(lot["stop_price"]) * (1.0 - direction * slip),
                date,
                "intraday",
                "stop",
                rate,
                totals,
                spec,
                unslipped_price=float(lot["stop_price"]),
            )
            exited = True

    before_close = _equity(state, closes, rate)
    conservative_min = min(conservative_min, before_close)
    state["halted"] = latch(bool(state["halted"]), before_close, limits)
    blocked = blocked or bool(state["halted"]) or before_close <= limits.internal_daily
    if (state["halted"] or blocked) and state["positions"]:
        _flatten(
            state,
            closes,
            date,
            "close",
            "maximum_guard" if state["halted"] else "daily_guard",
            rate,
            totals,
            spec,
        )
    end_equity = _equity(state, closes, rate)
    state["halted"] = latch(bool(state["halted"]), end_equity, limits)
    blocked = blocked or bool(state["halted"]) or end_equity <= limits.internal_daily
    conservative_min = min(conservative_min, end_equity)
    for symbol, lot in state["positions"].items():
        lot["last_px"] = closes[symbol]
        lot["bars_open"] = int(lot.get("bars_open", 0)) + 1

    next_pending = None
    if not state["halted"] and not state["positions"]:
        next_pending = _queue_decision(
            state,
            market,
            date,
            now,
            end_equity,
            spec,
            limits_override=limits,
        )
    state["pending_batch"] = next_pending
    notes = f"open {len(state['positions'])}; closed lots {len(state['trades'])}"
    if next_pending:
        notes += f"; {len(next_pending['legs'])} leg(s) persisted for {next_pending['eligible_fill_session']} open"
    elif state["halted"]:
        notes += "; permanent internal maximum-loss halt"
    elif state["decisions"] and state["decisions"][-1].get("decision_date") == iso_date(date):
        notes += f"; decision {state['decisions'][-1]['status'].lower()}"
    row = _append_daily(
        state,
        date,
        closes,
        rate,
        previous_equity,
        limits,
        conservative_min,
        blocked,
        totals,
        spec,
        notes,
    )
    state["last_observed_bars"] = panel_observation(market.panel, date)
    state["last_data_as_of"] = iso_date(date)
    state["status"] = "HALTED" if state["halted"] else "ACTIVE_FORWARD_PAPER"
    return row


def advance(
    state: Mapping[str, Any],
    spec: BookSpec,
    market: MarketData,
    now: Any | None = None,
    *,
    pending_was_durable: bool = False,
) -> tuple[dict, list[dict]]:
    """Advance only settled unseen sessions; never manufacture missed fills."""

    validate_state(state, spec)
    output = copy.deepcopy(dict(state))
    recorded = utc_timestamp(now or market.retrieved_at_utc)
    last = session_label(output["last_processed_session"])
    if any(last not in market.panel[symbol].index for symbol in SYMBOLS):
        raise DataUnavailable("fresh adjusted panel no longer covers the persisted anchor")
    rebased = _apply_adjustment_rebase(output, market.panel, last)
    latest = session_label(market.latest_completed_session)
    if latest < last:
        raise DataUnavailable("fresh adjusted panel is older than the persisted state")
    common = next(iter(market.panel.values())).index
    to_process = [day for day in common if last < day <= latest]
    rows = []
    expected = last
    for date in to_process:
        expected = next_session(expected)
        if date != expected:
            raise DataUnavailable("fresh adjusted panel skipped an unseen XNYS session")
        rows.append(
            _process_session(
                output, market, date, recorded, spec, pending_was_durable
            )
        )
    if rows or rebased:
        output["data_provenance"] = copy.deepcopy(market.provenance)
    validate_state(output, spec)
    return output, rows


def _public_pending(state: Mapping[str, Any]) -> list[dict]:
    pending = state.get("pending_batch")
    if not pending:
        return []
    return [
        {
            **copy.deepcopy(leg),
            "decision_date": pending["decision_date"],
            "decision_recorded_at_utc": pending["decision_recorded_at_utc"],
            "eligible_fill_session": pending["eligible_fill_session"],
            "scheduled_exit_session": pending["scheduled_exit_session"],
            "regime": pending["regime"],
            "lagged_vix": pending["lagged_vix"],
            "lagged_vix_source_date": pending["lagged_vix_source_date"],
            "status": "PENDING_NEXT_OPEN",
        }
        for leg in pending["legs"]
    ]


def _public_positions(state: Mapping[str, Any], spec: BookSpec, stamp: str) -> list[dict]:
    if not state["positions"]:
        return []
    latest = state["daily"][-1]
    rate = float(latest["fx_rate"])
    equity = float(latest["equity"])
    rows = []
    for symbol, lot in sorted(state["positions"].items()):
        direction = int(lot["direction_sign"])
        units = float(lot["units"])
        last_px = float(lot["last_px"])
        open_pnl = direction * units * (last_px - float(lot["entry_price"])) / rate
        risk_abs = _risk(
            {**state, "positions": {symbol: lot}}, {symbol: last_px}, rate, spec
        )[0]
        rows.append(
            {
                "instrument": symbol,
                "updated_at": stamp,
                "direction": lot["direction"],
                "units": units,
                "initial_units": float(lot["initial_units"]),
                "entry_price": float(lot["entry_price"]),
                "entry_time": lot["entry_date"],
                "stop": float(lot["stop_price"]),
                "initial_stop": float(lot["initial_stop"]),
                "target": None,
                "last_px": last_px,
                "bars_open": int(lot["bars_open"]),
                "risk_abs": risk_abs,
                "current_risk_gbp": risk_abs,
                "stop_risk_gbp": risk_abs,
                "open_pnl": open_pnl,
                "account_currency": "GBP",
                "strategy": "V14 regime-switch 5-session",
                "decision_date": lot["decision_date"],
                "decision_recorded_at_utc": lot["decision_recorded_at_utc"],
                "entry_fx_rate": float(lot["entry_fx_rate"]),
                "regime": lot["regime"],
                "lagged_vix": float(lot["lagged_vix"]),
                "lagged_vix_source_date": lot["lagged_vix_source_date"],
                "decision_atr": float(lot["decision_atr"]),
                "decision_atr_original": float(lot["decision_atr_original"]),
                "initial_total_risk": float(lot["initial_total_risk_gbp"]),
                "entry_fee": float(lot["entry_fee_gbp"]),
                "borrow_cost": float(lot["borrow_cost_gbp"]),
                "current_notional": abs(units * last_px / rate),
                "current_weight": abs(units * last_px / rate) / equity if equity > 0 else None,
                "scheduled_exit_session": lot["scheduled_exit_session"],
                "exit_rule": "hard 1.5 ATR20 stop or fifth completed session at next XNYS open",
                "signal_rationale": lot["signal_rationale"],
                "tms_p1": False,
                "tms_p2": False,
                "tms_be": False,
                "partial_taken": False,
                "realized_pnl_total": 0.0,
            }
        )
    return rows


def public_payload(
    state: Mapping[str, Any], spec: BookSpec, generated_at: Any | None = None
) -> dict:
    validate_state(state, spec)
    stamp = _now_iso(generated_at)
    daily = copy.deepcopy(state["daily"])
    if daily:
        daily[-1]["state_extra"] = {
            "book": spec.label,
            "status": state["status"],
            "account_currency": "GBP",
            "initial_equity": spec.initial_equity_gbp,
            "peak": float(state["peak"]),
            "halted": bool(state["halted"]),
            "pending": _public_pending(state),
            "last_processed_session": state["last_processed_session"],
        }
    latest = daily[-1]
    metadata = {
        "book_id": spec.book_id,
        "label": spec.label,
        "strategy_id": STRATEGY_ID,
        "profile": spec.profile,
        "account_currency": "GBP",
        "initial_equity": spec.initial_equity_gbp,
        "paper_only": True,
        "broker_enabled": False,
        "funded_qualified": False,
        "experimental": True,
        "status": state["status"],
        "activation_recorded_at_utc": state["activation_recorded_at_utc"],
        "first_eligible_decision_session": state["first_eligible_decision_session"],
        "last_processed_session": state["last_processed_session"],
        "last_data_as_of": state["last_data_as_of"],
        "session_count": max(0, len(state["daily"]) - 1),
        "current_equity": float(latest["equity"]),
        "cash": float(latest["cash"]),
        "open_pnl": float(latest["open_pnl"]),
        "external_daily_floor": float(latest["external_daily_floor"]),
        "external_maximum_floor": float(latest["external_maximum_floor"]),
        "internal_daily_floor": float(latest["internal_daily_floor"]),
        "internal_maximum_floor": float(latest["internal_maximum_floor"]),
        "cost_total_gbp": float(state["cost_total_gbp"]),
        "risk": {
            "daily_loss_fraction": spec.daily_loss_fraction,
            "maximum_loss_fraction": spec.maximum_loss_fraction,
            "per_trade_risk_fraction": spec.per_trade_risk_fraction,
            "aggregate_risk_fraction": spec.aggregate_risk_fraction,
            "gross_fraction": spec.gross_fraction,
            "single_name_fraction": spec.single_name_fraction,
            "internal_buffer_fraction": spec.internal_buffer_fraction,
            "maximum_loss_mode": spec.maximum_loss_mode,
        },
        "execution": {
            "decision_timing": "settled XNYS close",
            "fill_timing": "immediate next XNYS open only when persisted before that open",
            "holding_sessions": spec.holding_sessions,
            "stop_atr_multiple": spec.stop_atr_multiple,
            "fee_bps_each_side": spec.fee_bps_each_side,
            "stop_slippage_bps": spec.stop_slippage_bps,
            "annual_short_borrow_rate": spec.annual_short_borrow_rate,
        },
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "source_amendment_sha256": SOURCE_AMENDMENT_SHA256,
        "spec_sha256": spec.spec_sha256,
        "warning": (
            "Experimental forward paper only. V14 did not pass funded-qualification gates; "
            "ETF/FX proxies are not provider CFD execution evidence."
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "book_id": spec.book_id,
        "generated_at_utc": stamp,
        "state": copy.deepcopy(dict(state)),
        "daily": daily,
        "positions": _public_positions(state, spec, stamp),
        "trades": copy.deepcopy(state["trades"]),
        "pending": _public_pending(state),
        "metadata": metadata,
    }
