"""Repaired Book F USD forward-paper state machine.

Multi-horizon momentum with breadth/correlation gates, next-open entries,
partial realization and separately priced pyramid lots. A 5 bps/side cost
proxy and adverse/ambiguous-bar stops apply. Not a blind-validation result
or a guarantee of profitability or funded-account compliance. Version 1
ledgers cannot be resumed as version 2; their evidence remains archived.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from .paper_accounting import VERSION, conversion_rate, lot_pnl, close_fraction, positive

BOOK_LABEL = "book_f_prop_shield_elite_100k"
INITIAL_EQUITY_USD = 100_000.0
BASE_MRPT = 0.0034            # 0.34% ($340 per initial trade on $100k)
MAX_POSITIONS = 8             # Max 8 concurrent positions
MAX_PER_CLUSTER = 2           # Max 2 bets per correlation cluster
CORR_THRESHOLD = 0.55         # Correlation clustering threshold
PYR_TRIGGER_R = 1.5           # Trigger pyramiding at +1.5R
PYR_SCALE = 0.50              # Add 0.50x of original units
STOP_ATR_MULT = 1.8           # Initial stop: 1.8x ATR
TRAIL_ATR_MULT = 2.2          # Trailing stop: 2.2x ATR (1.4x past +2R)
SCHEMA_VERSION = 2
FILL_COST_RATE = 0.0005  # explicit 5 bps/side proxy, not broker-certified costs

CORE_UNIVERSE = [
    "NVDA", "TSM", "MSFT", "NFLX", "TSLA", "AAPL", "AMD", "PLTR", "META", "GOOGL", "AMZN",
    "SMH", "XLK", "SOXX", "SGLD.L",
    "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "ADA/USD", "LINK/USD",
    "USD/JPY"
]

def _date_str(val: pd.Timestamp | str) -> str:
    ts = pd.Timestamp(val)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.strftime("%Y-%m-%d")

def new_book_f_state(seed_date: pd.Timestamp) -> dict:
    """Initialize a clean Book F state seeded at $100,000 USD."""
    seed_s = _date_str(seed_date)
    return {
        "schema_version": SCHEMA_VERSION,
        "accounting_version": VERSION,
        "book": BOOK_LABEL,
        "status": "active_forward_paper_prop_shield",
        "account_currency": "USD",
        "initial_equity": INITIAL_EQUITY_USD,
        "cash": INITIAL_EQUITY_USD,
        "equity": INITIAL_EQUITY_USD,
        "peak": INITIAL_EQUITY_USD,
        "base_mrpt": BASE_MRPT,
        "max_positions": MAX_POSITIONS,
        "pyr_scale": PYR_SCALE,
        "last_processed_date": seed_s,
        "positions": {},
        "pending": {},
        "trades": [],
        "equity_curve": [{
            "date": seed_s,
            "equity": INITIAL_EQUITY_USD,
            "cash": INITIAL_EQUITY_USD,
            "day_pnl": 0.0,
            "cum_pnl": 0.0,
            "drawdown": 0.0,
            "gross_notional": 0.0,
            "open_count": 0,
            "notes": "Book F Prop Shield Elite seeded at $100,000 USD"
        }]
    }

def validate_book_f_state(state: dict) -> None:
    if int(state.get("schema_version", -1)) != SCHEMA_VERSION:
        raise ValueError("Unsupported Book F schema version")
    if state.get("book") != BOOK_LABEL:
        raise ValueError("Invalid Book F label")
    if state.get("account_currency") != "USD":
        raise ValueError("Book F must be denominated in USD")
    if state.get("accounting_version") != VERSION:
        raise ValueError("Book F requires a separate repaired ledger; legacy history cannot be relabelled")
    for pos in state.get("positions", {}).values():
        if not pos.get("lots"):
            raise ValueError("Book F position is missing actual entry lots")

def advance_book_f_forward(
    state: dict,
    panel: dict[str, pd.DataFrame],
    cutoff_date: pd.Timestamp
) -> tuple[dict, list[dict]]:
    """Advance Book F forward across all unadvanced closed daily bars up to cutoff_date."""
    validate_book_f_state(state)
    state = copy.deepcopy(state)  # failures must not partially mutate caller state
    
    # Collect all calendar dates across panel
    all_dates = set()
    for sym, df in panel.items():
        if not df.empty:
            all_dates.update(df.index)
            
    calendar = sorted(list(all_dates))
    if not calendar:
        return state, []
        
    last_dt = pd.Timestamp(state["last_processed_date"], tz="UTC") if calendar[0].tzinfo else pd.Timestamp(state["last_processed_date"])
    to_process = [d for d in calendar if d > last_dt and d <= cutoff_date]
    if not to_process:
        return state, []
        
    # Precompute indicators across full history
    cached_atr = {}
    cached_returns = {}
    cached_sma200 = {}
    cached_trend_score = {}
    
    for sym, df in panel.items():
        if df.empty:
            continue
        c, h, l = df["close"], df["high"], df["low"]
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        cached_atr[sym] = tr.rolling(20).mean()
        cached_returns[sym] = c.pct_change(1)
        cached_sma200[sym] = c.rolling(200).mean()
        
        vol63 = c.pct_change(1).rolling(63).std() * np.sqrt(252) + 1e-6
        s63 = c.pct_change(63) / vol63
        s126 = c.pct_change(126) / vol63
        s252 = c.pct_change(252) / vol63
        cached_trend_score[sym] = 0.50 * s63 + 0.30 * s126 + 0.20 * s252

    # Cross-sectional market breadth
    breadth_series = {}
    for dt in calendar:
        above_count = 0
        total_count = 0
        for sym, sma_s in cached_sma200.items():
            if dt in sma_s.index and dt in panel[sym].index:
                sma_v = sma_s.loc[dt]
                close_v = panel[sym].loc[dt, "close"]
                if not pd.isna(sma_v) and not pd.isna(close_v):
                    total_count += 1
                    if close_v > sma_v:
                        above_count += 1
        breadth_series[dt] = (above_count / total_count) if total_count > 0 else 0.5

    tok_list = sorted(list(panel.keys()))
    tok_to_idx = {t: idx for idx, t in enumerate(tok_list)}
    ret_df_all = pd.DataFrame({t: cached_returns[t] for t in tok_list}, index=calendar)
    rolling_corr = ret_df_all.rolling(60, min_periods=30).corr()
    corr_3d = rolling_corr.values.reshape(len(calendar), len(tok_list), len(tok_list))
    date_to_idx = {d: i for i, d in enumerate(calendar)}
    
    positions = state["positions"]
    closed_trades = state["trades"]
    equity = float(state["cash"])
    previous_nav = float(state["equity"])
    pending = state.setdefault("pending", {})
    peak_equity = float(state["peak"])
    new_daily_rows = []
    
    for current_dt in to_process:
        i = date_to_idx[current_dt]
        prev_close_equity = previous_nav
        closed_this_bar = []
        open_upnl = 0.0

        def fx(sym, price=None, field="close"):
            if sym.startswith("USD/") and price is not None:
                return 1.0 / positive(price)
            return conversion_rate(sym, "USD", panel, current_dt, field)

        # Prior-close signals fill at the next instrument bar's open, not at
        # the close that revealed the signal. Entry bars are managed below.
        for sym, order in list(pending.items()):
            if sym not in panel or current_dt not in panel[sym].index:
                continue
            if sym in positions:
                raise ValueError("position and pending entry overlap")
            raw = positive(panel[sym].loc[current_dt, "open"], "entry open")
            is_long = order["direction"] == "long"
            entry = raw * (1 + FILL_COST_RATE * (1 if is_long else -1))
            stop_dist = order["stop_dist"]
            units = order["risk_amount"] / (stop_dist * fx(sym, entry, "open"))
            positions[sym] = {
                "direction": order["direction"], "entry_price": entry,
                "entry_time": _date_str(current_dt), "decision_date": order["decision_date"],
                "units": units, "initial_units": units,
                "lots": [{"entry_price": entry, "units": units}],
                "stop_loss": entry - stop_dist if is_long else entry + stop_dist,
                "initial_risk": stop_dist, "partial_taken": False, "pyramided": False,
                "realized_pnl": 0.0, "last_close": raw, "bars_open": 0,
            }
            del pending[sym]
        
        # 1. Update open positions
        for sym, pos in list(positions.items()):
            df = panel.get(sym)
            if df is None:
                raise ValueError(f"{sym}: open position has no input data")
            if current_dt not in df.index:
                # Market closed today (weekend/holiday) -> carry forward last known price!
                last_px = float(pos.get("last_close", pos["entry_price"]))
                pos["quote_to_account_rate"] = fx(sym, last_px)
                pos["unrealized_pnl"] = lot_pnl(pos, last_px) * fx(sym, last_px)
                open_upnl += lot_pnl(pos, last_px) * fx(sym, last_px)
                continue
                
            bar = df.loc[current_dt]
            high_px, low_px, close_px = float(bar["high"]), float(bar["low"]), float(bar["close"])
            pos["last_close"] = close_px
            pos["bars_open"] = int(pos.get("bars_open", 0)) + 1
            is_long = pos["direction"] == "long"
            entry_px = float(pos["entry_price"])
            stop_px = float(pos["stop_loss"])
            units = float(pos["units"])
            initial_risk = float(pos["initial_risk"])
            
            stop_hit = (low_px <= stop_px) if is_long else (high_px >= stop_px)

            def realize(price, fraction=1.0):
                nonlocal equity
                filled = price * (1 - FILL_COST_RATE * (1 if is_long else -1))
                pnl = close_fraction(pos, fraction, filled) * fx(sym, filled, "open")
                equity += pnl
                pos["realized_pnl"] += pnl
                return filled

            # Stops have priority over favourable triggers on an ambiguous bar.
            # Gap-through stops fill at the adverse open, never at a stale stop.
            if stop_hit:
                opening = float(bar["open"])
                raw_exit = min(opening, stop_px) if is_long else max(opening, stop_px)
                exit_px = realize(raw_exit)
                closed_trades.append({
                    "instrument": sym, "direction": pos["direction"],
                    "entry_price": entry_px, "exit_price": exit_px,
                    "stop_loss": stop_px, "initial_stop": entry_px - initial_risk if is_long else entry_px + initial_risk,
                    "pnl": pos["realized_pnl"], "win": pos["realized_pnl"] > 0,
                    "entry_time": pos["entry_time"], "exit_time": _date_str(current_dt),
                    "exit_reason": "stop_loss", "holding_days": pos["bars_open"],
                    "pyramided": pos.get("pyramided", False),
                })
                closed_this_bar.append(sym)
                continue
            
            # Partial profit at +1.0R
            if not pos.get("partial_taken", False):
                p_target = (entry_px + initial_risk) if is_long else (entry_px - initial_risk)
                if (high_px >= p_target) if is_long else (low_px <= p_target):
                    realize(p_target, 0.5)
                    pos["partial_taken"] = True
                    pos["stop_loss"] = entry_px  # Breakeven lock
                    units = pos["units"]
                    
            # Convexity Pyramiding at +1.5R
            if PYR_SCALE > 0 and pos.get("partial_taken", False) and not pos.get("pyramided", False):
                pyr_target = (entry_px + PYR_TRIGGER_R * initial_risk) if is_long else (entry_px - PYR_TRIGGER_R * initial_risk)
                pyr_hit = (high_px >= pyr_target) if is_long else (low_px <= pyr_target)
                if pyr_hit:
                    pyr_units = float(pos["initial_units"]) * PYR_SCALE
                    pyr_entry = max(float(bar["open"]), pyr_target) if is_long else min(float(bar["open"]), pyr_target)
                    pyr_entry *= 1 + FILL_COST_RATE * (1 if is_long else -1)
                    pos["lots"].append({"entry_price": pyr_entry, "units": pyr_units})
                    pos["units"] = units + pyr_units
                    pos["pyramided"] = True
                    # Raised stop is not a guarantee: added lots, costs and gaps count.
                    pos["stop_loss"] = (entry_px + 0.75 * initial_risk) if is_long else (entry_px - 0.75 * initial_risk)
                    units = pos["units"]
                    
            raised_stop = float(pos["stop_loss"])
            if (low_px <= raised_stop) if is_long else (high_px >= raised_stop):
                exit_px = realize(raised_stop)
                total_pnl = float(pos["realized_pnl"])
                closed_trades.append({
                    "token": sym,
                    "instrument": sym,
                    "direction": pos["direction"],
                    "entry_price": entry_px,
                    "exit_price": exit_px,
                    "stop_loss": raised_stop,
                    "exit_reason": "management_stop_ambiguous_bar",
                    "pnl": total_pnl,
                    "win": total_pnl > 0,
                    "entry_time": pos["entry_time"],
                    "exit_time": _date_str(current_dt),
                    "holding_days": pos["bars_open"],
                    "pyramided": pos.get("pyramided", False)
                })
                closed_this_bar.append(sym)
            else:
                atr = cached_atr[sym].get(current_dt, 0)
                if atr and atr > 0:
                    cur_gain = (high_px - entry_px) if is_long else (entry_px - low_px)
                    use_trail = 1.4 if cur_gain >= 2.0 * initial_risk else TRAIL_ATR_MULT
                    if is_long:
                        new_stop = high_px - use_trail * atr
                        if new_stop > float(pos["stop_loss"]):
                            pos["stop_loss"] = new_stop
                    else:
                        new_stop = low_px + use_trail * atr
                        if new_stop < float(pos["stop_loss"]):
                            pos["stop_loss"] = new_stop
                            
                floating_pnl = lot_pnl(pos, close_px) * fx(sym, close_px)
                pos["unrealized_pnl"] = floating_pnl
                pos["quote_to_account_rate"] = fx(sym, close_px)
                open_upnl += floating_pnl
                
        for sym in closed_this_bar:
            del positions[sym]
            
        cur_total_equity = equity + open_upnl
        day_pnl = cur_total_equity - prev_close_equity
        daily_guard_active = (day_pnl <= -(INITIAL_EQUITY_USD * 0.025))
        
        peak_equity = max(peak_equity, cur_total_equity)
        current_dd = (peak_equity - cur_total_equity) / peak_equity
        
        # Asymptotic Drawdown Throttling
        if current_dd < 0.035:
            risk_mult = 1.0
        elif current_dd < 0.055:
            risk_mult = 0.65
        elif current_dd < 0.075:
            risk_mult = 0.40
        else:
            risk_mult = 0.15
            
        active_mrpt = BASE_MRPT * risk_mult
        breadth = breadth_series.get(current_dt, 0.5)
        
        # 2. Check Candidate Entries
        if len(positions) < MAX_POSITIONS and not daily_guard_active:
            candidates = []
            for sym, score_series in cached_trend_score.items():
                if sym in positions or sym in pending or current_dt not in score_series.index:
                    continue
                score = score_series.loc[current_dt]
                if pd.isna(score):
                    continue
                if score > 0.80 and breadth >= 0.40:
                    candidates.append((sym, "long", score))
                elif score < -0.80 and breadth < 0.35:
                    candidates.append((sym, "short", abs(score)))
            candidates.sort(key=lambda x: x[2], reverse=True)
            
            for sym, direction, score in candidates:
                if len(positions) + len(pending) >= MAX_POSITIONS:
                    break
                correlated_cluster_count = 0
                if (positions or pending) and i >= 60:
                    cand_idx = tok_to_idx[sym]
                    for open_tok in list(positions) + list(pending):
                        open_idx = tok_to_idx[open_tok]
                        val = corr_3d[i, cand_idx, open_idx]
                        if not np.isnan(val) and val >= CORR_THRESHOLD:
                            correlated_cluster_count += 1
                if correlated_cluster_count >= MAX_PER_CLUSTER:
                    continue
                    
                df = panel[sym]
                bar = df.loc[current_dt]
                entry_px = float(bar["close"])
                atr = cached_atr[sym].get(current_dt, entry_px * 0.02)
                if not atr or pd.isna(atr) or atr <= 0:
                    atr = entry_px * 0.02
                risk_amount = INITIAL_EQUITY_USD * active_mrpt
                stop_dist = STOP_ATR_MULT * atr
                fx(sym, entry_px)  # require a real conversion before queuing
                pending[sym] = {"direction": direction, "stop_dist": stop_dist,
                                "risk_amount": risk_amount, "decision_date": _date_str(current_dt)}
                
        # Gross notional
        gross_notional = sum(
            float(p["units"]) * float(p.get("last_close", p["entry_price"])) * fx(sym, p.get("last_close", p["entry_price"]))
            for sym, p in positions.items()
        )
        
        daily_row = {
            "date": _date_str(current_dt),
            "equity": cur_total_equity,
            "cash": equity,
            "day_pnl": day_pnl,
            "cum_pnl": cur_total_equity - INITIAL_EQUITY_USD,
            "drawdown": current_dd,
            "gross_notional": gross_notional,
            "open_count": len(positions),
            "notes": f"open {len(positions)}, closed today {len(closed_this_bar)}, dd {current_dd*100:.2f}%"
        }
        state["equity_curve"].append(daily_row)
        new_daily_rows.append(daily_row)
        previous_nav = cur_total_equity
        
    state["equity"] = cur_total_equity
    state["cash"] = equity
    state["peak"] = peak_equity
    state["last_processed_date"] = _date_str(to_process[-1])
    return state, new_daily_rows

def display_daily_rows(state: dict) -> list[dict]:
    """Return daily equity rows formatted for frontend charts."""
    validate_book_f_state(state)
    rows = copy.deepcopy(state.get("equity_curve", []))
    if rows:
        # Attach state_extra to the latest row so the frontend reads closed trades
        rows[-1]["state_extra"] = {
            "book": state["book"],
            "status": state["status"],
            "account_currency": state["account_currency"],
            "initial_equity": state["initial_equity"],
            "peak": state["peak"],
            "trades": copy.deepcopy(state.get("trades", [])),
            "last_processed_date": state["last_processed_date"]
        }
    return rows

def display_position_rows(state: dict, updated_at: str | None = None) -> list[dict]:
    """Return open positions formatted as table rows for the web UI."""
    validate_book_f_state(state)
    stamp = updated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for inst, p in sorted(state.get("positions", {}).items()):
        last_px = float(p.get("last_close", p["entry_price"]))
        units = float(p["units"])
        entry_px = float(p["entry_price"])
        stop_px = float(p["stop_loss"])
        is_long = p["direction"] == "long"
        unrealized_pnl = lot_pnl(p, last_px) * float(p["quote_to_account_rate"])
        total_pnl = float(p.get("realized_pnl", 0.0)) + unrealized_pnl
        
        # Classification
        if "/" in inst:
            asset_class = "crypto" if any(c in inst for c in ["BTC", "ETH", "SOL", "BNB", "ADA", "LINK"]) else "forex"
        else:
            asset_class = "stocks"
            
        rows.append({
            "instrument": inst,
            "symbol": inst,
            "updated_at": stamp,
            "direction": p["direction"],
            "units": units,
            "initial_units": float(p.get("initial_units", units)),
            "entry_price": entry_px,
            "entry_time": p["entry_time"],
            "stop": stop_px,
            "initial_stop": (entry_px - float(p["initial_risk"])) if is_long else (entry_px + float(p["initial_risk"])),
            "last_px": last_px,
            "bars_open": int(p.get("bars_open", 0)),
            "partial_taken": bool(p.get("partial_taken", False)),
            "pyramided": bool(p.get("pyramided", False)),
            "realized_pnl_total": float(p.get("realized_pnl", 0.0)),
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": total_pnl,
            "account_currency": "USD",
            "asset_class": asset_class,
            "strategy": "Book F Prop Shield Elite (Convex Pyramiding)",
        })
    return rows

def runtime_payload(state: dict) -> dict:
    """Bundle daily curve, open positions, and state into one JSON payload."""
    validate_book_f_state(state)
    return {
        "daily": display_daily_rows(state),
        "positions": display_position_rows(state),
        "state": state
    }
