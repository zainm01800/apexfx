"""Deterministic state machine and execution engine for SPY intraday forward books."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from .data import HistoricalWarmup, current_ny_time
from .signals import (
    SignalDecision,
    compute_v24_signal,
    compute_v30_signal,
    entry_units,
)
from .spec import BOOKS, BookSpec, Profile, PROFILES, SCHEMA_VERSION


def state_sha256(state: dict) -> str:
    raw = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def new_state(spec: BookSpec, now_utc: datetime | None = None) -> dict:
    """Create a pristine £100,000 GBP paper forward-trading state."""
    stamp = (now_utc or datetime.now(timezone.utc)).isoformat()
    profile = PROFILES["higher_5_12"]
    daily_floor = spec.initial_equity_gbp * (1.0 - spec.daily_loss_fraction)
    max_floor = spec.initial_equity_gbp * (1.0 - spec.maximum_loss_fraction)
    int_daily_floor = spec.initial_equity_gbp * (1.0 - spec.daily_loss_fraction * (1.0 - profile.buffer))
    int_max_floor = spec.initial_equity_gbp * (1.0 - spec.maximum_loss_fraction * (1.0 - profile.buffer))

    seed_daily = {
        "date": stamp[:10],
        "equity": spec.initial_equity_gbp,
        "cash": spec.initial_equity_gbp,
        "day_pnl": 0.0,
        "cum_pnl": 0.0,
        "open_pnl": 0.0,
        "drawdown_from_peak": 0.0,
        "external_daily_floor": daily_floor,
        "external_maximum_floor": max_floor,
        "internal_daily_floor": int_daily_floor,
        "internal_maximum_floor": int_max_floor,
        "is_seed": True,
        "trades": 0,
    }

    state = {
        "schema_version": SCHEMA_VERSION,
        "book_id": spec.book_id,
        "strategy_id": spec.strategy_id,
        "strategy_variant": spec.strategy_variant,
        "profile": spec.profile,
        "revision": 1,
        "parent_state_sha256": None,
        "initial_equity": spec.initial_equity_gbp,
        "cash": spec.initial_equity_gbp,
        "equity": spec.initial_equity_gbp,
        "peak": spec.initial_equity_gbp,
        "cost_total_gbp": 0.0,
        "halted": False,
        "status": "active_forward_paper",
        "activation_recorded_at_utc": stamp,
        "first_eligible_decision_session": stamp[:10],
        "last_processed_session": None,
        "last_data_as_of": stamp,
        "position": None,  # Flat active position
        "daily": [seed_daily],
        "trades": [],
        "events": [
            {
                "sequence": 1,
                "timestamp": stamp,
                "event": "account_activated",
                "initial_equity_gbp": spec.initial_equity_gbp,
                "book_id": spec.book_id,
            }
        ],
        "pending": [],
    }
    return state


def mark_equity(cash: float, pos: dict | None, price: float, fx: float, fee_rate: float, liquidation: bool = False) -> float:
    """Calculate marked equity in GBP given current price."""
    value = cash
    if pos:
        gross = pos["units"] * pos["direction"] * (price - pos["entry_price"]) / fx
        value += gross
        if liquidation:
            exit_fee = pos["units"] * price * fee_rate / fx
            value -= exit_fee
    return float(value)


def step_session(
    state: dict,
    spec: BookSpec,
    warmup: HistoricalWarmup,
    session_bars: pd.DataFrame,
    session_date: str,
) -> dict:
    """Deterministic causal execution across regular trading session bars."""
    st = copy.deepcopy(state)
    profile = PROFILES["higher_5_12"]
    fee_rate = spec.fee_bps_each_side / 10_000.0
    slip_rate = spec.stop_slippage_bps / 10_000.0

    fx = warmup.fx_rate
    initial = st["initial_equity"]
    cash = float(st["cash"])
    pos = copy.deepcopy(st.get("position"))
    peak = float(st["peak"])
    halted = bool(st.get("halted", False))

    day_start_equity = cash if pos is None else mark_equity(cash, pos, float(session_bars["open"].iloc[0]), fx, fee_rate)
    daily_floor = day_start_equity - initial * spec.daily_loss_fraction
    internal_daily_floor = day_start_equity - initial * spec.daily_loss_fraction * (1.0 - profile.buffer)
    external_max_floor = initial * (1.0 - spec.maximum_loss_fraction)
    internal_max_floor = initial * (1.0 - spec.maximum_loss_fraction * (1.0 - profile.buffer))
    bound_floor = max(internal_daily_floor, internal_max_floor)

    block = halted
    fee_total_day = 0.0
    stop_slippage_day = 0.0
    trades_closed_today = 0

    def add_event(kind: str, time_str: str, **extra: Any) -> None:
        st["events"].append({
            "sequence": len(st["events"]) + 1,
            "timestamp": time_str,
            "event": kind,
            **extra,
        })

    def close_position(time_str: str, benchmark_price: float, reason: str, stopped: bool = False) -> None:
        nonlocal cash, pos, fee_total_day, stop_slippage_day, trades_closed_today
        if pos is None:
            return
        actual_price = benchmark_price * (1.0 - pos["direction"] * slip_rate) if stopped else benchmark_price
        exit_fee = pos["units"] * actual_price * fee_rate / fx
        gross = pos["units"] * pos["direction"] * (actual_price - pos["entry_price"]) / fx
        slippage = pos["units"] * abs(actual_price - benchmark_price) / fx
        cash += gross - exit_fee
        fee_total_day += exit_fee
        stop_slippage_day += slippage
        net_pnl = gross - pos["entry_fee_gbp"] - exit_fee

        st["trades"].append({
            "instrument": spec.symbol,
            "direction": "LONG" if pos["direction"] == 1 else "SHORT",
            "units": pos["units"],
            "entry_price": pos["entry_price"],
            "exit_price": actual_price,
            "entry_time": pos["entry_time"],
            "exit_time": time_str,
            "exit_reason": reason,
            "net_pnl_gbp": net_pnl,
            "gross_pnl_gbp": gross,
            "entry_fee_gbp": pos["entry_fee_gbp"],
            "exit_fee_gbp": exit_fee,
            "stop_slippage_cost_gbp": slippage,
            "final_stop": pos["stop"],
            "return_pct": net_pnl / (pos["units"] * pos["entry_price"] / fx),
        })
        add_event(reason, time_str, exit_price=actual_price, net_pnl_gbp=net_pnl)
        trades_closed_today += 1
        pos = None

    quotes = session_bars[["open", "high", "low", "close"]].to_numpy(float)
    times = [t.isoformat() for t in session_bars.index]
    tp_series = (session_bars["high"] + session_bars["low"] + session_bars["close"]) / 3.0
    vol_cum = session_bars["volume"].cumsum()
    pv_cum = (tp_series * session_bars["volume"]).cumsum()
    vwap_series = (pv_cum / vol_cum).fillna(session_bars["close"]).to_numpy(float)

    session_open = float(quotes[0, 0])

    for i in range(len(quotes)):
        o, h, l, c = quotes[i]
        time_str = times[i]
        vw = vwap_series[i]

        # 1. Existing protective stop has precedence (gap at open or hit during bar)
        if pos is not None:
            # Check gap stop at open
            if pos["direction"] * (o - pos["stop"]) <= 0:
                close_position(time_str, o, "gap_stop", stopped=True)
            else:
                # Check intraday touch of stop
                stop_touched = l <= pos["stop"] if pos["direction"] == 1 else h >= pos["stop"]
                if stop_touched:
                    close_position(time_str, pos["stop"], "stop", stopped=True)

        # 2. Account risk floor check
        current_eq = mark_equity(cash, pos, c, fx, fee_rate, liquidation=True)
        peak = max(peak, current_eq)
        if current_eq <= bound_floor:
            block = True
            if current_eq <= internal_max_floor:
                halted = True
            if pos is not None:
                close_position(time_str, c, "account_guard_exit")

        # 3. Scheduled flatten at 15:59 NY (bar 389 or last available bar)
        if (i >= spec.mandatory_flat_offset or i == len(quotes) - 1) and pos is not None:
            close_position(time_str, o if i >= spec.mandatory_flat_offset else c, "scheduled_flat")

        # 4. Signal evaluation at boundary offsets
        # Bar i corresponds to offset i + 1 (e.g. minute 29 is offset 30)
        offset = i + 1
        decision: SignalDecision | None = None

        if spec.strategy_variant == "noise_band":
            if offset in range(spec.evaluation_start_offset, spec.evaluation_end_offset + 1, spec.evaluation_interval_minutes):
                sigma = warmup.noise_sigmas.get(offset, 0.0020)
                decision = compute_v24_signal(
                    offset_minute=offset,
                    bar_close=c,
                    session_open=session_open,
                    prior_close=warmup.prior_close,
                    sigma=sigma,
                    vwap=vw,
                    volatility=warmup.volatility_14,
                    bar_time_str=time_str,
                )
        elif spec.strategy_variant == "atr_open_stop":
            if offset in range(spec.evaluation_start_offset, spec.evaluation_end_offset + 1, spec.evaluation_interval_minutes):
                decision = compute_v30_signal(
                    offset_minute=offset,
                    bar_close=c,
                    session_open=session_open,
                    prior_close=warmup.prior_close,
                    atr_14=warmup.atr_14,
                    volatility=warmup.volatility_14,
                    bar_time_str=time_str,
                )

        # 5. Process signal decision
        if decision and not block and i < len(quotes) - 1:
            next_open = float(quotes[i + 1, 0])
            next_time_str = times[i + 1]

            if spec.strategy_variant == "noise_band":
                # V24 logic: can exit on neutral/opposite, tightens stops on same side
                if pos is not None:
                    if decision.direction != pos["direction"]:
                        close_position(next_time_str, next_open, "signal_exit")
                    elif decision.direction == pos["direction"] and decision.barrier is not None:
                        # Ratchet stop tighter
                        prior_stop = pos["stop"]
                        new_stop = max(prior_stop, decision.barrier) if pos["direction"] == 1 else min(prior_stop, decision.barrier)
                        if new_stop != prior_stop:
                            pos["stop"] = new_stop
                            add_event("stop_tightened", next_time_str, previous_stop=prior_stop, new_stop=new_stop)
                # Enter if flat
                if pos is None and decision.direction != 0 and decision.barrier is not None:
                    units, risk_unit = entry_units(
                        cash, next_open, decision.barrier, decision.direction, fx,
                        warmup.volatility_14, bound_floor, profile,
                        spec.fee_bps_each_side, spec.stop_slippage_bps
                    )
                    if units > 0:
                        entry_fee = units * next_open * fee_rate / fx
                        cash -= entry_fee
                        fee_total_day += entry_fee
                        pos = {
                            "instrument": spec.symbol,
                            "direction": decision.direction,
                            "units": units,
                            "entry_price": next_open,
                            "entry_time": next_time_str,
                            "entry_fee_gbp": entry_fee,
                            "stop": decision.barrier,
                            "initial_stop": decision.barrier,
                        }
                        add_event("entry", next_time_str, direction="LONG" if decision.direction == 1 else "SHORT", units=units, price=next_open)

            elif spec.strategy_variant == "atr_open_stop":
                # V30 logic: no signal exits or resizing while holding.
                if pos is None and decision.direction != 0 and decision.barrier is not None:
                    # Enter on next open if price is beyond session open barrier
                    if decision.direction * (next_open - decision.barrier) > 0:
                        units, risk_unit = entry_units(
                            cash, next_open, decision.barrier, decision.direction, fx,
                            warmup.volatility_14, bound_floor, profile,
                            spec.fee_bps_each_side, spec.stop_slippage_bps
                        )
                        if units > 0:
                            entry_fee = units * next_open * fee_rate / fx
                            cash -= entry_fee
                            fee_total_day += entry_fee
                            pos = {
                                "instrument": spec.symbol,
                                "direction": decision.direction,
                                "units": units,
                                "entry_price": next_open,
                                "entry_time": next_time_str,
                                "entry_fee_gbp": entry_fee,
                                "stop": decision.barrier,
                                "initial_stop": decision.barrier,
                            }
                            add_event("entry", next_time_str, direction="LONG" if decision.direction == 1 else "SHORT", units=units, price=next_open)

    # End of day settlement
    last_price = float(quotes[-1, 3])
    final_equity = mark_equity(cash, pos, last_price, fx, fee_rate)
    peak = max(peak, final_equity)
    day_pnl = final_equity - day_start_equity

    daily_record = {
        "date": session_date,
        "equity": final_equity,
        "cash": cash,
        "day_pnl": day_pnl,
        "cum_pnl": final_equity - initial,
        "open_pnl": final_equity - cash,
        "drawdown_from_peak": max(0.0, 1.0 - final_equity / peak),
        "external_daily_floor": daily_floor,
        "external_maximum_floor": external_max_floor,
        "internal_daily_floor": internal_daily_floor,
        "internal_maximum_floor": internal_max_floor,
        "trades": trades_closed_today,
        "is_seed": False,
    }

    st["cash"] = cash
    st["equity"] = final_equity
    st["peak"] = peak
    st["halted"] = halted
    st["position"] = pos
    st["cost_total_gbp"] += fee_total_day + stop_slippage_day
    st["last_processed_session"] = session_date
    st["last_data_as_of"] = times[-1] if times else session_date
    st["daily"].append(daily_record)
    st["revision"] += 1

    return st


def export_public_payload(state: dict, spec: BookSpec) -> dict:
    """Format authoritative payload for Supabase / API layer."""
    st = copy.deepcopy(state)
    stamp = datetime.now(timezone.utc).isoformat()
    daily = st["daily"]
    latest = daily[-1] if daily else {}

    # Format open position for public API
    public_positions = []
    if st.get("position"):
        p = st["position"]
        public_positions.append({
            "instrument": spec.symbol,
            "direction": "LONG" if p["direction"] == 1 else "SHORT",
            "units": p["units"],
            "entry_price": p["entry_price"],
            "last_px": p.get("last_px", p["entry_price"]),
            "stop_price": p["stop"],
            "initial_stop": p.get("initial_stop", p["stop"]),
            "unrealized_pnl_gbp": p.get("unrealized_pnl_gbp", 0.0),
            "entry_time": p["entry_time"],
        })

    metadata = {
        "book_id": spec.book_id,
        "label": spec.label,
        "strategy_id": spec.strategy_id,
        "strategy_variant": spec.strategy_variant,
        "profile": spec.profile,
        "account_currency": "GBP",
        "initial_equity": spec.initial_equity_gbp,
        "paper_only": True,
        "broker_enabled": False,
        "funded_qualified": False,
        "experimental": True,
        "status": "halted_internal_guard" if st.get("halted") else "active_forward_paper",
        "activation_recorded_at_utc": st.get("activation_recorded_at_utc"),
        "first_eligible_decision_session": st.get("first_eligible_decision_session"),
        "last_processed_session": st.get("last_processed_session"),
        "last_data_as_of": st.get("last_data_as_of"),
        "session_count": max(0, len(daily) - 1),
        "current_equity": float(latest.get("equity", spec.initial_equity_gbp)),
        "cash": float(latest.get("cash", spec.initial_equity_gbp)),
        "open_pnl": float(latest.get("open_pnl", 0.0)),
        "external_daily_floor": float(latest.get("external_daily_floor", 95000.0)),
        "external_maximum_floor": float(latest.get("external_maximum_floor", 88000.0)),
        "internal_daily_floor": float(latest.get("internal_daily_floor", 96250.0)),
        "internal_maximum_floor": float(latest.get("internal_maximum_floor", 91000.0)),
        "cost_total_gbp": float(st.get("cost_total_gbp", 0.0)),
        "warning": "Experimental forward paper forward test. No broker execution, real money, or funded-account qualification.",
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "book_id": spec.book_id,
        "generated_at_utc": stamp,
        "state": st,
        "daily": daily,
        "positions": public_positions,
        "trades": st.get("trades", []),
        "pending": st.get("pending", []),
        "metadata": metadata,
    }
