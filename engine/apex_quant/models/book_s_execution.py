"""Restart-invariant execution for the repaired Book S paper ledger.

Signals use closed hourly bars; pending signals execute at the next hourly
open. Stops win ambiguous OHLC bars and adverse gaps are charged in full.
"""
from __future__ import annotations

import pandas as pd
from .paper_accounting import positive


def advance_hours(state, frames, times, *, universe, risk, rr, max_positions,
                  daily_limit, max_hours, pip_sizes, spreads, stamp):
    cash, nav, peak = float(state["cash"]), float(state["equity"]), float(state["peak"])
    positions, trades = state["positions"], state["trades"]
    pending = state.setdefault("pending", {})
    traded = state.setdefault("last_traded_date", {})
    guard = state.setdefault("daily_guard", {})
    changed_days = {}

    def rate(sym, px):
        base, quote = sym.split("/")
        if quote == "USD":
            return 1.0
        if base == "USD":
            return 1.0 / positive(px, "FX conversion price")
        raise ValueError(f"{sym}: unsupported FX cross; explicit conversion required")

    def spread(sym):
        return pip_sizes[sym] * spreads[sym]

    def upnl(sym, pos, price):
        side = 1 if pos["direction"] == "long" else -1
        return (price - pos["entry_price"]) * pos["units"] * side * rate(sym, price)

    for t in times:
        previous = pd.Timestamp(state["last_processed_time"])
        if previous.tzinfo is None and t.tzinfo is not None:
            previous = previous.tz_localize("UTC")
        if positions and t-previous > pd.Timedelta(hours=1):
            raise ValueError("Missing hourly coverage while positions are open; cannot certify exit path")
        day = t.strftime("%Y-%m-%d")
        if guard.get("date") != day:
            guard.clear()
            guard.update(date=day, start_equity=nav, minimum_equity=nav, locked=False)

        # Entry at next bar open, with a full-spread round-trip friction model
        # (half spread each side). Do not fill yesterday's expired intraday order.
        for sym, order in list(pending.items()):
            frame = frames[sym]
            if t not in frame.index:
                continue
            del pending[sym]
            decision = pd.Timestamp(order["decision_time"])
            if decision.tzinfo is None and t.tzinfo is not None:
                decision = decision.tz_localize("UTC")
            if t - decision != pd.Timedelta(hours=1) or guard["locked"] or state.get("halted"):
                continue
            bar = frame.loc[t]
            side = 1 if order["direction"] == "long" else -1
            entry = positive(bar["open"]) + side * spread(sym) / 2
            stop_dist = order["stop_dist"]
            stop_fill = entry - side * (stop_dist + spread(sym) / 2)
            units = risk / ((stop_dist + spread(sym) / 2) * rate(sym, stop_fill))
            # Units are fixed for the life of the position, never recomputed at exit.
            positions[sym] = {**order, "entry_price": entry, "entry_time": stamp(t),
                              "units": units, "risk_usd": risk,
                              "stop_loss": entry - side * stop_dist,
                              "take_profit": entry + side * rr * stop_dist,
                              "unrealized_pnl": 0.0, "current_price": float(bar["open"])}

        for sym, pos in list(positions.items()):
            frame = frames.get(sym)
            if frame is None:
                raise ValueError(f"{sym}: open position has no input data")
            if t not in frame.index:
                raise ValueError(f"{sym}: missing hourly bar for an open position")
            bar = frame.loc[t]
            o, h, l, c = (positive(bar[k], k) for k in ("open", "high", "low", "close"))
            side = 1 if pos["direction"] == "long" else -1
            stop, target = pos["stop_loss"], pos["take_profit"]
            entered = pd.Timestamp(pos["entry_time"])
            if entered.tzinfo is None and t.tzinfo is not None:
                entered = entered.tz_localize("UTC")
            age = (t - entered).total_seconds() / 3600
            stop_hit = l <= stop if side == 1 else h >= stop
            target_hit = h >= target if side == 1 else l <= target
            price, reason = None, None
            if age >= max_hours:
                price, reason = o, "time_limit"  # known deadline, at first available open
            elif stop_hit:
                price, reason = (min(o, stop) if side == 1 else max(o, stop)), "stop_loss"
            elif target_hit:
                price, reason = target, "take_profit"  # no favourable-gap windfall
            elif pd.to_datetime(t,utc=True).tz_convert("America/New_York").weekday()==4 and pd.to_datetime(t,utc=True).tz_convert("America/New_York").hour>=16:
                price, reason = c, "weekend_close"
            if price is not None:
                filled = positive(price - side * spread(sym) / 2)
                pnl = upnl(sym, pos, filled)
                cash += pnl
                trades.append({"instrument": sym, "symbol": sym, "direction": pos["direction"],
                               "units": pos["units"], "entry_price": pos["entry_price"],
                               "exit_price": filled, "stop_loss": stop, "take_profit": target,
                               "pnl": pnl, "win": pnl > 0, "return_pct": pnl / state["initial_equity"],
                               "entry_time": pos["entry_time"], "exit_time": stamp(t),
                               "holding_hours": age, "exit_reason": reason})
                del positions[sym]
            else:
                pos["current_price"] = c
                pos["unrealized_pnl"] = upnl(sym, pos, c - side * spread(sym) / 2)

        nav = cash + sum(p["unrealized_pnl"] for p in positions.values())
        guard["minimum_equity"] = min(guard["minimum_equity"], nav)
        # Persist the latch across invocations and recoveries within the day.
        guard["locked"] = guard["locked"] or guard["start_equity"] - guard["minimum_equity"] >= daily_limit
        peak = max(peak, nav)
        if nav <= 0:
            state["halted"] = True
        if 7 <= t.hour < 12 and not guard["locked"] and not state.get("halted"):
            for sym in universe:
                if len(positions) + len(pending) >= max_positions:
                    break
                if sym in positions or sym in pending or traded.get(sym) == day:
                    continue
                frame = frames.get(sym)
                if frame is None or t not in frame.index:
                    continue
                bar = frame.loc[t]
                c, ah, al, atr = (bar.get(k) for k in ("close", "asian_high", "asian_low", "atr"))
                bull = bar.get("htf_bull")
                if any(pd.isna(v) for v in (c, ah, al, atr, bull)) or atr <= 0:
                    continue  # missing daily trend is NOT an automatic long approval
                direction, dist = None, None
                if bool(bull) and c > ah + 0.1 * atr:
                    direction, dist = "long", max(c - al, 1.2 * atr)
                elif not bool(bull) and c < al - 0.1 * atr:
                    direction, dist = "short", max(ah - c, 1.2 * atr)
                if direction:
                    pending[sym] = {"symbol": sym, "direction": direction,
                                    "decision_time": stamp(t), "stop_dist": float(dist)}
                    traded[sym] = day

        row = {"date": day, "timestamp": stamp(t), "equity": nav, "cash": cash,
               "day_pnl": nav - guard["start_equity"], "cum_pnl": nav - state["initial_equity"],
               "drawdown": (peak - nav) / peak, "open_count": len(positions),
               "notes": "Repaired paper accounting; hourly OHLC is not intrabar funded-compliance proof"}
        changed_days[day] = row
        state["last_processed_time"] = stamp(t)
    # One canonical final snapshot per day, whether one call or 24 restarts.
    by_day = {r["date"]: r for r in state["equity_curve"]}
    by_day.update(changed_days)
    state["equity_curve"] = [by_day[d] for d in sorted(by_day)]
    state.update(cash=cash, equity=nav, peak=peak, last_processed_time=stamp(times[-1]))
    return state, list(changed_days.values())
