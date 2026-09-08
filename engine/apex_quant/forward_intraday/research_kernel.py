"""Vendored V30/V24 compatible research execution; only preserved cash/peak/lot identity adapters added.
Source: tmp/research/v30_atr_intraday_20260905/replay.py. No research I/O imports are used.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import gzip
import hashlib
import json
from pathlib import Path
import sys

import exchange_calendars as xcals
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
V22 = HERE.parent / "v22_strategy_broadening_20260905"
V24 = HERE.parent / "v24_public_minute_probe_20260905"
VARIANTS = ("atr_open_stop", "atr_pullback_entry")
START, END = "2021-06-01", "2024-12-30"
PERIODS = {"full": (START, END), "partial_2021": (START, "2021-12-31"),
           "2022": ("2022-01-01", "2022-12-31"),
           "2023": ("2023-01-01", "2023-12-31"),
           "2024": ("2024-01-01", END), "2022_2024": ("2022-01-01", END)}
COSTS = {"base": (1., 1.), "stress": (2.5, 2.5), "diagnostic_5bps": (5., 5.)}
INITIAL = 100000.
V22_INPUT_NAMES = ("SPY_events.json", "fx_by_date.json", "manifest.json",
                   "boe_xudluss_2007-01-01_2026-09-03.csv",
                   "boe_xudluss_2007-01-01_2026-09-03_available.csv",
                   "boe_xudluss_2007-01-01_2026-09-03_vintage.xml")
digest = lambda raw: hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Profile:
    daily: float
    maximum: float
    original_maximum: float
    risk: float
    vol_target: float
    gross: float
    utilization: float = .90
    buffer: float = .25


PROFILES = {"lower_3_7": Profile(.03, .07, .06, .0075, .015, 3.),
            "higher_5_12": Profile(.05, .12, .10, .01, .02, 4.)}


def clean(x):
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (tuple, list)):
        return [clean(v) for v in x]
    if isinstance(x, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(x).isoformat()
    if isinstance(x, (np.integer, np.bool_)):
        return x.item()
    if isinstance(x, (float, np.floating)):
        return float(x) if np.isfinite(x) else None
    return x


def json_bytes(obj):
    return (json.dumps(clean(obj), sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode()


def save_new(path, raw):
    with Path(path).open("xb") as out:
        out.write(raw)


def valid_prices(values):
    a = np.asarray(values, dtype=float)
    tol = np.max(np.abs(a), axis=1)*1e-12
    return bool(np.isfinite(a).all() and (a > 0).all()
                and (a[:, 1]+tol >= a.max(axis=1)).all()
                and (a[:, 2]-tol <= a.min(axis=1)).all())


def check_fx(date, info, opening):
    rate = float(info["rate"])
    observation = pd.Timestamp(info["source_date"]).date()
    age = (pd.Timestamp(date).date()-observation).days
    availability = pd.Timestamp(info["available_at_utc"])
    if (not np.isfinite(rate) or rate <= 0 or not 1 <= age <= 6
            or availability.tzinfo is None or availability > opening):
        raise ValueError(f"Noncausal/stale/invalid GBP fixing on {date}")
    return rate


def entry_units(equity, price, barrier, direction, fx, volatility, floor, profile, fee, slip):
    """Inclusive risk is measured before fees; its ceiling uses after-fee equity."""
    if equity <= 0 or volatility <= 0 or direction not in (-1, 1) or direction*(price-barrier) <= 0:
        return 0., 0.
    stopped = barrier*(1-direction*slip)
    if stopped <= 0:
        raise ValueError("Nonpositive slipped stop")
    price_gbp, entry_fee = price/fx, price*fee/fx
    risk_unit = (direction*(price-stopped)+price*fee+stopped*fee)/fx
    if risk_unit <= 0:
        return 0., 0.
    desired = min(profile.gross, profile.vol_target/volatility)
    caps = (desired*equity/(price_gbp+desired*entry_fee),
            profile.gross*equity/(price_gbp+profile.gross*entry_fee),
            profile.risk*equity/(risk_unit+profile.risk*entry_fee),
            max(0., equity-floor)/risk_unit)
    units = profile.utilization*max(0., min(caps))
    if units*price_gbp < 1000.:
        return 0., risk_unit
    return units, risk_unit


def replay(sessions, *, start=START, end=END, profile="lower_3_7", cost="base", initial=INITIAL, variant="baseline", starting_cash=None, prior_peak=None, already_halted=False, lot_id_offset=0):
    """Single-position minute replay. All account-floor exits wait until next open."""
    if variant not in ("baseline", *VARIANTS):
        raise ValueError("Unknown fixed V30 variant")
    p = PROFILES[profile] if isinstance(profile, str) else profile
    if not (0 < p.utilization <= 1 and 0 <= p.buffer < 1 and 0 < initial):
        raise ValueError("Invalid risk specification")
    fee_bps, slip_bps = COSTS[cost] if isinstance(cost, str) else cost
    if min(fee_bps, slip_bps) < 0 or not np.isfinite([fee_bps, slip_bps]).all():
        raise ValueError("Invalid costs")
    fee, slip = fee_bps/1e4, slip_bps/1e4
    active = [s for s in sessions if start <= s["date"] <= end]
    if not active:
        raise ValueError("Empty replay period")
    opening_cash = float(initial if starting_cash is None else starting_cash)
    cash, pos, halted, pending_guard = opening_cash, None, bool(already_halted), None
    peak = float(initial if prior_peak is None else prior_peak)
    if not np.isfinite([opening_cash, peak]).all() or peak < initial or peak < opening_cash:
        raise ValueError("Invalid preserved cash/peak")
    daily, trades, tape, events = [], [], [], []
    external_max, original_max = initial*(1-p.maximum), initial*(1-p.original_maximum)
    internal_max = initial*(1-p.maximum*(1-p.buffer))

    def event(kind, time, **kw):
        events.append({"kind": kind, "time": time, **kw})

    for session in active:
        if pos is not None:
            raise AssertionError("Unexpected overnight position")
        day, fx = session["date"], float(session["fx"])
        if fx != check_fx(day, session["fx_info"], session["times"][0]):
            raise ValueError("Session GBP fixing differs from its provenance")
        day_start, day_peak = cash, peak
        daily_floor = day_start-initial*p.daily
        internal_daily = day_start-initial*p.daily*(1-p.buffer)
        bound_floor = max(internal_daily, internal_max)
        block = halted or not session["normal"]
        day_min, day_max = cash, cash
        fee_total = stop_total = 0.
        touches = {"daily": False, "maximum": False, "original_maximum": False,
                   "internal_daily": False, "internal_maximum": False}
        max_gross = max_risk = 0.
        session_trades_start = len(trades)
        pending_entry = None

        def mark(price, liquidation=False):
            value = cash
            if pos:
                value += pos["units"]*pos["direction"]*(price-pos["entry_price"])/fx
                if liquidation:
                    value -= pos["units"]*price*fee/fx
            return float(value)

        def close_position(time, benchmark, reason, stopped=False):
            nonlocal cash, pos, fee_total, stop_total
            if pos is None:
                return
            actual = benchmark*(1-pos["direction"]*slip) if stopped else benchmark
            exit_fee = pos["units"]*actual*fee/fx
            gross = pos["units"]*pos["direction"]*(actual-pos["entry_price"])/fx
            slippage = pos["units"]*abs(actual-benchmark)/fx
            cash += gross-exit_fee
            fee_total += exit_fee
            stop_total += slippage
            net = gross-pos["entry_fee_gbp"]-exit_fee
            trades.append({**pos, "exit_time": time, "exit_price": actual,
                           "unslipped_exit_price": benchmark, "exit_reason": reason,
                           "exit_fee_gbp": exit_fee, "stop_slippage_cost_gbp": slippage,
                           "gross_pnl_gbp": gross, "net_pnl_gbp": net,
                           "exit_fx": fx, "cash_after_exit": cash,
                           "return_pct": net/(pos["units"]*pos["entry_price"]/pos["entry_fx"]),
                           "final_stop": pos["stop"]})
            event(reason, time, lot_id=pos["lot_id"], exit_price=actual, net_pnl_gbp=net)
            pos = None

        def observe(value, time, stage):
            nonlocal day_min, day_max, block, halted, pending_guard
            day_min = min(day_min, value)
            day_max = max(day_max, value)
            for name, floor in (("daily", daily_floor), ("maximum", external_max),
                                ("original_maximum", original_max),
                                ("internal_daily", internal_daily), ("internal_maximum", internal_max)):
                if value <= floor:
                    touches[name] = True
            if value <= internal_daily or value <= internal_max:
                kind = "internal_maximum" if value <= internal_max else "internal_daily"
                if not block or (kind == "internal_maximum" and not halted):
                    event("account_guard_observed", time, guard=kind, stage=stage, equity=value)
                block = True
                halted = halted or value <= internal_max
                if pos and pending_guard is None:
                    pending_guard = kind

        quotes = session["bars"][["open", "high", "low", "close"]].to_numpy(float)
        times = session["times"]
        for i, (time, bar) in enumerate(zip(times, quotes)):
            if not np.isfinite(bar).all():
                if pos:
                    raise ValueError(f"Missing active minute while holding: {time}; no invented execution")
                if not block:
                    event("missing_minute_block", time)
                block = True
                if variant != "baseline" and pending_entry:
                    event("pending_cancelled", time, reason="missing_minute", pending_side=pending_entry["direction"],
                          pending_created_at=pending_entry["created_at"])
                    pending_entry = None
                tape.append({"time": time, "date": day, "missing": True, "cash": cash,
                             "open_equity": cash, "adverse_equity": cash, "close_equity": cash,
                             "units": 0., "direction": 0, "gross_x": 0., "peak": peak,
                             "drawdown": max(0., 1-cash/peak), "halted": halted})
                continue
            o, h, l, c = map(float, bar)
            stopped_this_minute = False
            pre_open_equity = mark(o, True)
            peak = max(peak, pre_open_equity)
            # Existing protective stop has precedence over pending orders/guards.
            if pos and pos["direction"]*(o-pos["stop"]) <= 0:
                close_position(time, o, "gap_stop", True)
                stopped_this_minute = True
            if pending_guard:
                close_position(time, o, "guard_next_open")
                pending_guard = None
            observe(min(pre_open_equity, mark(o, True)), time, "opening")
            # A just-detected opening guard must also wait a minute, not fill here.
            if i == len(times)-1 and pos:
                close_position(time, o, "scheduled_flat")
                pending_guard = None
            decision = session["decisions"].get(i)
            allowed_boundaries = range(30, 361, 30) if variant == "baseline" else range(30, 376, 15)
            if decision and (i not in allowed_boundaries
                             or pd.Timestamp(decision["decision_bar_start"]) != time-pd.Timedelta(minutes=1)
                             or pd.Timestamp(decision["decision_at"]) != time):
                raise ValueError("Decision is not the completed prior-minute/next-open boundary")
            if decision and variant != "baseline" and float(decision["barrier"]) != float(quotes[0, 0]):
                raise ValueError("ATR protective barrier must equal the actual session open")
            if variant != "baseline":
                breakout = decision
                decision = None
                # A pending entry is never a protective exit. Existing resting
                # stops and account guards above already have first priority.
                if pending_entry and (block or i == len(times)-1):
                    event("pending_cancelled", time, reason="account_or_session_block" if block else "scheduled_flat",
                          pending_side=pending_entry["direction"], pending_created_at=pending_entry["created_at"])
                    pending_entry = None
                if pending_entry and i > 0:
                    prior_close = float(quotes[i-1, 3])
                    opposite = breakout and breakout["direction"] == -pending_entry["direction"]
                    if opposite or pending_entry["direction"]*(prior_close-pending_entry["barrier"]) <= 0:
                        event("pending_cancelled", time, reason="opposite_breakout" if opposite else "completed_close_at_stop",
                              pending_side=pending_entry["direction"], pending_created_at=pending_entry["created_at"])
                        pending_entry = None
                if pos is not None:
                    pending_entry = None  # No held-position signal exits or resizing.
                elif not block and not stopped_this_minute and i < len(times)-1:
                    if variant == "atr_open_stop":
                        if breakout and breakout["direction"]:
                            decision = dict(breakout)
                    else:
                        if breakout and breakout["direction"]:
                            if pending_entry is None:
                                pending_entry = {**breakout, "created_index": i, "created_at": time}
                                event("pending_created", time, direction=breakout["direction"], stop=breakout["barrier"])
                            else:
                                event("pending_same_side_retained", time, direction=pending_entry["direction"],
                                      pending_created_at=pending_entry["created_at"], age_minutes=i-pending_entry["created_index"])
                        # The first confirmation must be strictly later than
                        # creation; five-minute returns never cross a session.
                        if pending_entry and i > pending_entry["created_index"] and i % 5 == 0:
                            confirmation = session["five_minute_returns"].get(i)
                            if confirmation is not None and (pd.Timestamp(confirmation["decision_at"]) != time
                                    or pd.Timestamp(confirmation["end_bar_start"]) != time-pd.Timedelta(minutes=1)
                                    or pd.Timestamp(confirmation["anchor_bar_start"]) != time-pd.Timedelta(minutes=6)):
                                raise ValueError("Five-minute confirmation must use only completed in-session bars")
                            ret = None if confirmation is None else confirmation["return"]
                            if ret is not None and np.isfinite(ret) and pending_entry["direction"]*float(ret) < 0:
                                decision = {**pending_entry, "breakout_decision_at": pending_entry["decision_at"],
                                    "breakout_decision_bar_start": pending_entry["decision_bar_start"],
                                    "decision_at": time, "decision_bar_start": time-pd.Timedelta(minutes=1),
                                    "pullback_return": float(ret), "pending_age_minutes": i-pending_entry["created_index"]}
                                event("pending_confirmed", time, direction=pending_entry["direction"],
                                      pending_created_at=pending_entry["created_at"], age_minutes=i-pending_entry["created_index"], five_minute_return=float(ret))
            if decision and not block and not stopped_this_minute and i < len(times)-1:
                side = int(decision["direction"])
                barrier = decision["barrier"]
                if pos and side != pos["direction"]:
                    close_position(time, o, "signal_exit")
                if pos and side == pos["direction"]:
                    prior = pos["stop"]
                    pos["stop"] = max(prior, barrier) if side == 1 else min(prior, barrier)
                    if pos["stop"] != prior:
                        event("stop_tightened", time, lot_id=pos["lot_id"], previous_stop=prior, stop=pos["stop"],
                              decision_bar_start=decision["decision_bar_start"])
                    if side*(o-pos["stop"]) <= 0:
                        close_position(time, o, "new_stop_gap", True)
                        stopped_this_minute = True
                if pos is None and side and not stopped_this_minute:
                    units, risk_unit = entry_units(cash, o, barrier, side, fx, decision["daily_volatility"],
                                                   bound_floor, p, fee, slip)
                    if units:
                        entry_fee = units*o*fee/fx
                        before_fee = cash
                        cash -= entry_fee
                        fee_total += entry_fee
                        pos = {"lot_id": lot_id_offset+len(trades)+1, "instrument": "SPY", "direction": side,
                               "units": units, "entry_time": time, "entry_price": o, "entry_fx": fx,
                               "entry_fee_gbp": entry_fee, "initial_stop": barrier, "stop": barrier,
                               "initial_total_risk_gbp": units*risk_unit, "entry_equity_before_fee": before_fee,
                               "entry_equity_after_fee": cash, "entry_risk_ceiling_gbp": p.risk*cash,
                               "entry_gross_x": units*o/fx/cash, "entry_internal_floor": bound_floor,
                               "fx_source_date": session["fx_info"]["source_date"],
                               "fx_available_at_utc": session["fx_info"]["available_at_utc"], **decision}
                        assert units*risk_unit <= p.risk*cash+1e-7
                        assert units*risk_unit <= before_fee-bound_floor+1e-7
                        assert units*o/fx <= p.gross*cash+1e-7
                        event("entry", time, lot_id=pos["lot_id"], direction=side, units=units)
                        if variant != "baseline":
                            pending_entry = None
                    else:
                        event("entry_rejected", time, direction=side,
                              reason="open_not_beyond_barrier" if side*(o-barrier) <= 0 else "risk_or_minimum_notional")
            open_equity = mark(o, True)
            peak = max(peak, open_equity)
            observe(open_equity, time, "post_order_opening")
            adverse_equity = mark(c, True)
            gross_x = risk_ratio = 0.
            held_lot, held_units, held_side, held_stop = None, 0., 0, None
            if pos:
                held_lot, held_units, held_side, held_stop = pos["lot_id"], pos["units"], pos["direction"], pos["stop"]
                stop_touched = l <= held_stop if held_side == 1 else h >= held_stop
                adverse_price = held_stop*(1-held_side*slip) if stop_touched else l if held_side == 1 else h
                adverse_equity = mark(adverse_price, True)
                e = mark(o)
                gross_x = held_units*o/fx/e if e > 0 else float("inf")
                prospective_stop = held_stop*(1-held_side*slip)
                risk = max(0., held_units*(held_side*(o-prospective_stop)+prospective_stop*fee)/fx)
                risk_ratio = risk/e if e > 0 else float("inf")
                max_gross, max_risk = max(max_gross, gross_x), max(max_risk, risk_ratio)
                observe(adverse_equity, time, "adverse_stopped_liquidation")
                if stop_touched:
                    close_position(time, held_stop, "stop", True)
                    # A flat account has no deferred order, but its day remains blocked.
                    pending_guard = None
            close_equity = mark(c, True)
            observe(close_equity, time, "closing")
            drawdown = max(0., 1-min(pre_open_equity, open_equity, adverse_equity, close_equity)/peak)
            peak = max(peak, close_equity)
            tape.append({"time": time, "date": day, "missing": False, "cash": cash,
                         "pre_open_equity": pre_open_equity, "open_equity": open_equity,
                         "adverse_equity": adverse_equity, "close_equity": close_equity,
                         "open": o, "high": h, "low": l, "close": c, "fx": fx,
                         "lot_id": held_lot, "units": held_units, "direction": held_side, "stop": held_stop,
                         "gross_x": gross_x, "risk_ratio": risk_ratio, "peak": peak, "drawdown": drawdown,
                         "daily_floor": daily_floor, "maximum_floor": external_max,
                         "original_maximum_floor": original_max, "internal_daily_floor": internal_daily,
                         "internal_maximum_floor": internal_max, "halted": halted, "blocked": block})
        if pos is not None:
            raise AssertionError("Session did not finish flat")
        pending_guard = None
        daily.append({"date": day, "equity": cash, "cash": cash, "day_start_equity": day_start,
                      "day_pnl": cash-day_start, "day_return": (cash/day_start-1) if day_start > 0 else 0.,
                      "minimum_equity": day_min, "maximum_equity": day_max,
                      "conservative_day_return": (day_min/day_start-1) if day_start > 0 else 0.,
                      "fees_gbp": fee_total, "stop_slippage_cost_gbp": stop_total,
                      "trades": len(trades)-session_trades_start, "max_gross_x": max_gross,
                      "max_mark_to_stop_risk_ratio": max_risk, "normal_session": session["normal"],
                      "data_gap": bool(session["missing"]), "halted": halted,
                      "peak": peak, "drawdown_close": max(0., 1-cash/peak),
                      "daily_floor": daily_floor, "maximum_floor": external_max,
                      "original_maximum_floor": original_max, "internal_daily_floor": internal_daily,
                      "internal_maximum_floor": internal_max,
                      **{k+"_possible_touch": v for k, v in touches.items()}})
    d, t, m, e = pd.DataFrame(daily), pd.DataFrame(trades), pd.DataFrame(tape), pd.DataFrame(events)
    m["minimum_liquidation_equity"] = m[["pre_open_equity", "open_equity", "adverse_equity", "close_equity"]].min(axis=1)
    previous_close = m.close_equity.shift(1, fill_value=opening_cash)
    m["conservative_minute_return"] = (m.minimum_liquidation_equity/previous_close.where(previous_close > 0)-1).fillna(0.)
    net = cash-opening_cash
    if abs(net-(float(t.net_pnl_gbp.sum()) if len(t) else 0.)) > 1e-6:
        raise AssertionError("Closed-lot GBP accounting reconciliation failed")
    returns = d.day_return.to_numpy(float)
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.
    months = max(1/30.4375, ((pd.Timestamp(d.date.iloc[-1])-pd.Timestamp(d.date.iloc[0])).days+1)/30.4375)
    monthly = d.groupby(d.date.str.slice(0, 7)).day_pnl.sum()
    metrics = {"net_profit_gbp": net, "average_monthly_profit_gbp": net/months,
               "sharpe": float(np.mean(returns)/sd*np.sqrt(252)) if sd > 0 else 0.,
               "peak_drawdown": float(m.drawdown.max()), "close_drawdown": float(d.drawdown_close.max()),
               "minimum_equity_gbp": float(d.minimum_equity.min()), "trades": len(t),
               "fees_gbp": float(d.fees_gbp.sum()), "stop_slippage_cost_gbp": float(d.stop_slippage_cost_gbp.sum()),
               "stop_exits": int(t.exit_reason.isin(["stop", "gap_stop", "new_stop_gap"]).sum()) if len(t) else 0,
               "worst_day_return": float(d.day_return.min()),
               "worst_conservative_day_return": float(d.conservative_day_return.min()),
               "worst_conservative_minute_return": float(m.conservative_minute_return.min()),
               "maximum_gross_x": float(d.max_gross_x.max()), "halted": halted, "terminal_flat": True,
               "losing_month_frequency": float((monthly < 0).mean()), "monthly_pnl_gbp": monthly.to_dict(),
               "active_entry_days": int(t.entry_time.map(lambda v: str(v)[:10]).nunique()) if len(t) else 0,
               "last_entry_time": str(t.entry_time.max()) if len(t) else None,
               "normal_sessions": int(d.normal_session.sum()), "calendar_sessions": len(d),
               "missing_blocked_dates": d.loc[d.data_gap, "date"].tolist(),
               **{k+"_possible_touch_days": int(d[k+"_possible_touch"].sum()) for k in touches}}
    return {"daily": d, "trades": t, "minutes": m, "events": e, "metrics": metrics}
