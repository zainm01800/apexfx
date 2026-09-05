"""Repaired Book S USD session-breakout forward-paper state machine.

Closed-hour London breakouts use a causal prior-daily EMA and complete Asian
session range. Fixed FX units target $500 stop risk including model spread;
entries fill next hour. Restart-safe cash and daily entry locks, stop-first
OHLC ordering and Friday closing exits are applied. These are paper proxies,
not proof of intrabar funded compliance or a profitable trading edge.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any
import numpy as np
import pandas as pd
from .paper_accounting import VERSION
from .book_s_execution import advance_hours

BOOK_LABEL = "book_s_session_smc_100k"
INITIAL_EQUITY_USD = 100_000.0
RISK_PER_TRADE_USD = 500.0       # 0.50% risk per trade on $100k (profit-optimized prop sizing)
TARGET_RR = 1.80                 # 1:1.80 Reward-to-Risk ratio
MAX_CONCURRENT_POSITIONS = 4     # Increased to 4 concurrent positions to capture more breakouts
DAILY_CIRCUIT_BREAKER_USD = 1800.0 # -1.8% daily loss guard (vs FTMO -5.0%)
MAX_HOLDING_HOURS = 16           # Clean intraday exit after 16 hours
SCHEMA_VERSION = 2

CORE_UNIVERSE = [
    "GBP/USD", "EUR/USD", "USD/CHF", "USD/JPY", "USD/CAD", "AUD/USD"
]

PIP_SIZES = {
    "GBP/USD": 0.0001,
    "USD/CHF": 0.0001,
    "EUR/USD": 0.0001,
    "USD/CAD": 0.0001,
    "AUD/USD": 0.0001,
    "USD/JPY": 0.01,
}

SPREAD_PIPS = {
    "GBP/USD": 1.0,
    "USD/CHF": 1.2,
    "EUR/USD": 0.8,
    "USD/CAD": 1.2,
    "AUD/USD": 1.0,
    "USD/JPY": 1.0,
}


def _date_str(val: pd.Timestamp | str | datetime) -> str:
    ts = pd.Timestamp(val)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def new_book_s_state(seed_date: pd.Timestamp | str = "2026-07-28") -> dict[str, Any]:
    """Initialize a pristine Book S state seeded at $100,000 USD."""
    ts = pd.Timestamp(seed_date)
    date_str = ts.strftime("%Y-%m-%d")
    return {
        "schema_version": SCHEMA_VERSION,
        "accounting_version": VERSION,
        "book": BOOK_LABEL,
        "strategy": "Session SMC & Order Flow Engine",
        "status": "active_forward_paper_session_smc",
        "account_currency": "USD",
        "initial_equity": INITIAL_EQUITY_USD,
        "cash": INITIAL_EQUITY_USD,
        "equity": INITIAL_EQUITY_USD,
        "peak": INITIAL_EQUITY_USD,
        "base_risk_usd": RISK_PER_TRADE_USD,
        "target_rr": TARGET_RR,
        "max_positions": MAX_CONCURRENT_POSITIONS,
        "last_processed_time": f"{date_str} 00:00:00",
        "positions": {},
        "pending": {},
        "daily_guard": {},
        "last_traded_date": {},
        "trades": [],
        "equity_curve": [{
            "date": date_str,
            "timestamp": f"{date_str} 00:00:00",
            "equity": INITIAL_EQUITY_USD,
            "cash": INITIAL_EQUITY_USD,
            "day_pnl": 0.0,
            "cum_pnl": 0.0,
            "drawdown": 0.0,
            "open_count": 0,
            "notes": "Book S Session SMC initialized at $100,000 USD"
        }]
    }


def validate_book_s_state(state: dict[str, Any]) -> None:
    if int(state.get("schema_version", -1)) != SCHEMA_VERSION:
        raise ValueError(f"Unsupported Book S schema version: {state.get('schema_version')}")
    if state.get("book") != BOOK_LABEL:
        raise ValueError(f"Invalid Book S label: {state.get('book')}")
    if state.get("account_currency") != "USD":
        raise ValueError("Book S must be denominated in USD")
    if state.get("accounting_version") != VERSION:
        raise ValueError("Book S requires a separate repaired ledger; legacy history cannot be relabelled")
    for pos in state.get("positions", {}).values():
        if not np.isfinite(pos.get("units", np.nan)) or pos["units"] <= 0:
            raise ValueError("Book S position lacks fixed entry-sized units")


def advance_book_s_forward(
    state: dict[str, Any],
    hourly_panel: dict[str, pd.DataFrame],
    daily_panel: dict[str, pd.DataFrame],
    cutoff_time: pd.Timestamp
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Step Book S across all unadvanced 1H bars up to cutoff_time."""
    validate_book_s_state(state)
    state = copy.deepcopy(state)
    
    # 1. Precompute Daily Trend & Asian Ranges for each pair
    precomputed = {}
    for sym, df_1h in hourly_panel.items():
        if df_1h.empty:
            continue
        df_1h = df_1h.copy().sort_index()
        
        # Match Daily 50 EMA trend filter (strictly causal yesterday close, 0 lookahead)
        df_1d = daily_panel.get(sym)
        if df_1d is not None and not df_1d.empty:
            df_1d = df_1d.copy().sort_index()
            df_1d["ema50"] = df_1d["close"].ewm(span=50, adjust=False).mean()
            df_1d["htf_bull"] = (df_1d["close"] > df_1d["ema50"])
            htf_map = {}
            for day in set(df_1h.index.date):
                prior = df_1d[df_1d.index.date < day]
                if len(prior) >= 50:
                    htf_map[day] = bool(prior.iloc[-1]["htf_bull"])
        else:
            htf_map = {}
            
        df_1h["bar_date"] = df_1h.index.date
        df_1h["bar_hour"] = df_1h.index.hour
        df_1h["htf_bull"] = df_1h["bar_date"].map(htf_map)
        
        # Asian session high & low (00:00 - 06:59 UTC)
        asian_mask = (df_1h["bar_hour"] >= 0) & (df_1h["bar_hour"] < 7)
        asian_bars = df_1h[asian_mask]
        asian_high = asian_bars.groupby("bar_date")["high"].max().rename("asian_high")
        asian_low = asian_bars.groupby("bar_date")["low"].min().rename("asian_low")
        complete = asian_bars.groupby("bar_date").size() == 7
        asian_high = asian_high.where(complete)
        asian_low = asian_low.where(complete)
        df_1h = df_1h.join(asian_high, on="bar_date")
        df_1h = df_1h.join(asian_low, on="bar_date")
        
        # ATR(14) on 1H
        tr = pd.concat([
            df_1h["high"] - df_1h["low"],
            (df_1h["high"] - df_1h["close"].shift(1)).abs(),
            (df_1h["low"] - df_1h["close"].shift(1)).abs()
        ], axis=1).max(axis=1)
        df_1h["atr"] = tr.rolling(14).mean()
        
        precomputed[sym] = df_1h
        
    # Unify all timestamps across available hourly panel
    all_ts = set()
    for df in precomputed.values():
        all_ts.update(df.index)
    calendar = sorted(list(all_ts))
    if not calendar:
        return state, []
        
    if calendar[0].tzinfo:
        ts_cutoff = pd.Timestamp(cutoff_time)
        cutoff = ts_cutoff if ts_cutoff.tzinfo else ts_cutoff.tz_localize("UTC")
        ts_last = pd.Timestamp(state["last_processed_time"])
        last_time = ts_last if ts_last.tzinfo else ts_last.tz_localize("UTC")
    else:
        cutoff = pd.Timestamp(cutoff_time).tz_localize(None)
        last_time = pd.Timestamp(state["last_processed_time"]).tz_localize(None)

    to_process = [t for t in calendar if t > last_time and t <= cutoff]
    if not to_process:
        return state, []
        
    return advance_hours(
        state, precomputed, to_process, universe=CORE_UNIVERSE,
        risk=RISK_PER_TRADE_USD, rr=TARGET_RR, max_positions=MAX_CONCURRENT_POSITIONS,
        daily_limit=DAILY_CIRCUIT_BREAKER_USD, max_hours=MAX_HOLDING_HOURS,
        pip_sizes=PIP_SIZES, spreads=SPREAD_PIPS, stamp=_date_str,
    )


def compute_pending_radar(
    hourly_panel: dict[str, pd.DataFrame],
    daily_panel: dict[str, pd.DataFrame]
) -> list[dict[str, Any]]:
    """Compute real-time pending setups and breakout trigger levels for the London session."""
    radar = []
    for sym in CORE_UNIVERSE:
        df_h = hourly_panel.get(sym)
        df_d = daily_panel.get(sym)
        if df_h is None or df_h.empty or df_d is None or df_d.empty:
            continue
            
        df_h = df_h.copy().sort_index()
        df_d = df_d.copy().sort_index()
        
        df_d["ema50"] = df_d["close"].ewm(span=50, adjust=False).mean()
        df_d["htf_bull"] = (df_d["close"] > df_d["ema50"])
        daily_map = {}
        for day in set(df_h.index.date):
            prior = df_d[df_d.index.date < day]
            if len(prior) >= 50:
                daily_map[day] = bool(prior.iloc[-1]["htf_bull"])
        
        df_h["bar_date"] = df_h.index.date
        df_h["bar_hour"] = df_h.index.hour
        df_h["htf_bull"] = df_h["bar_date"].map(daily_map)
        
        asian_bars = df_h[(df_h["bar_hour"] >= 0) & (df_h["bar_hour"] < 7)]
        ah = asian_bars.groupby("bar_date")["high"].max().rename("asian_high")
        al = asian_bars.groupby("bar_date")["low"].min().rename("asian_low")
        df_h = df_h.join(ah, on="bar_date")
        df_h = df_h.join(al, on="bar_date")
        
        tr = pd.concat([
            df_h["high"] - df_h["low"],
            (df_h["high"] - df_h["close"].shift(1)).abs(),
            (df_h["low"] - df_h["close"].shift(1)).abs()
        ], axis=1).max(axis=1)
        df_h["atr"] = tr.rolling(14).mean()
        
        last_bar = df_h.iloc[-1]
        today_asian = asian_bars[asian_bars["bar_date"] == df_h.index[-1].date()]
        if len(today_asian) != 7 or any(pd.isna(last_bar[k]) for k in ("htf_bull", "atr", "asian_high", "asian_low")):
            continue
        c = float(last_bar["close"])
        ah_val = float(last_bar["asian_high"]) if not pd.isna(last_bar["asian_high"]) else c
        al_val = float(last_bar["asian_low"]) if not pd.isna(last_bar["asian_low"]) else c
        atr_val = float(last_bar["atr"]) if not pd.isna(last_bar["atr"]) else (c * 0.005)
        htf_bull = bool(last_bar["htf_bull"])
        
        pip_val = PIP_SIZES.get(sym, 0.0001)
        fmt = "{:.4f}" if pip_val == 0.0001 else "{:.2f}"
        
        if htf_bull:
            trigger_px = ah_val + 0.1 * atr_val
            dist_pips = (trigger_px - c) / pip_val
            bias = "BULLISH"
            direction = "LONG"
            condition = f"1H Close > {fmt.format(trigger_px)} (Asian High Break)"
        else:
            trigger_px = al_val - 0.1 * atr_val
            dist_pips = (c - trigger_px) / pip_val
            bias = "BEARISH"
            direction = "SHORT"
            condition = f"1H Close < {fmt.format(trigger_px)} (Asian Low Break)"
            
        radar.append({
            "pair": sym,
            "instrument": sym,
            "direction": direction,
            "bias": bias,
            "current_price": float(fmt.format(c)),
            "asian_high": float(fmt.format(ah_val)),
            "asian_low": float(fmt.format(al_val)),
            "trigger_price": float(fmt.format(trigger_px)),
            "dist_pips": round(dist_pips, 1),
            "condition": condition,
            "status": "Arming for session breakout" if dist_pips > 0 else "Trigger level reached"
        })
    return radar


def runtime_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Generate the complete runtime payload for Supabase and frontend consumption."""
    validate_book_s_state(state)
    trades = state.get("trades", [])
    wins = [t for t in trades if t.get("win")]
    losses = [t for t in trades if not t.get("win")]
    wr = (len(wins) / len(trades) * 100.0) if trades else 0.0
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    pf = (gross_w / gross_l) if gross_l > 0 else None
    
    return {
        "state": copy.deepcopy(state),
        "book": BOOK_LABEL,
        "strategy": "Session SMC & Order Flow Engine",
        "currency": "USD",
        "initial_equity": float(state.get("initial_equity", INITIAL_EQUITY_USD)),
        "equity": float(state.get("equity", INITIAL_EQUITY_USD)),
        "cash": float(state.get("cash", INITIAL_EQUITY_USD)),
        "peak": float(state.get("peak", INITIAL_EQUITY_USD)),
        "drawdown_pct": round(((float(state.get("peak", INITIAL_EQUITY_USD)) - float(state.get("equity", INITIAL_EQUITY_USD))) / float(state.get("peak", INITIAL_EQUITY_USD))) * 100.0, 2),
        "open_count": len(state.get("positions", {})),
        "total_trades": len(trades),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2) if pf is not None else None,
        "positions": state.get("positions", {}),
        "trades": trades,
        "pending_radar": state.get("pending_radar", []),
        "equity_curve": state.get("equity_curve", [])[-100:],
        "daily": state.get("equity_curve", [])[-100:],
        "last_updated": _date_str(datetime.now(timezone.utc))
    }
