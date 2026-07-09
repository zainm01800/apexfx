"""
APEX Quant — Live Paper Trading Execution Engine
=================================================
Runs on a schedule or interval loop:
1. Loads current active (open) trades from Supabase.
2. Fetches latest pricing to check if open trades hit their TP (take profit) or SL (stop loss) in parallel.
3. If hit, issues a PATCH request to resolve the trade (tp_hit / sl_hit).
4. Runs the RegimeGatedMomentum strategy on the 21 Robust Core assets to check for new entries.
5. If a new trade signal (LONG or SHORT) is generated:
   - Sizes it using 1% risk of a virtual $100k account.
   - Computes entry, TP, and SL targets based on ATR.
   - Issues a POST request to Supabase to log the open position.

Usage:
  python scripts/run_live_paper_trading.py
  python scripts/run_live_paper_trading.py --loop --interval 14400 # loops every 4 hours
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import numpy as np
import pandas as pd

# Bootstrap path
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.ai.sentiment_filter import apply_deepseek_sentiment
from apex_quant.config import get_config
from apex_quant.data import clean, get_adapter
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.execution.mt4_executor import MT4Executor
from apex_quant.execution.mock_executor import MockExecutor
from apex_quant.strategies.baseline import RegimeGatedMomentum

# API settings
SUPABASE_URL = "https://dtiuwllodzqpbwohzrgj.supabase.co"
SUPABASE_ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k"
MEMORY_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_research_memory"

# ── News headline fetcher (for DeepSeek sentiment filter) ─────────────────────
def fetch_headlines(instrument: str) -> list[str]:
    """Fetch recent news headlines for *instrument* from the APEX app's /api/news
    endpoint.  Returns an empty list on any failure (fail-ALLOW)."""
    app_url = cfg.sentiment.app_url if hasattr(cfg, 'sentiment') else cfg.ai.app_url
    if not app_url or not app_url.startswith("http"):
        return []
    try:
        base = app_url.rstrip("/")
        with httpx.Client(timeout=8.0) as client:
            res = client.get(f"{base}/api/news", params={"sym": instrument, "type": "Forex"})
            if res.status_code != 200:
                return []
            items = res.json()
            return [i.get("title", "") for i in (items if isinstance(items, list) else []) if i.get("title")]
    except Exception:
        return []


# ── Dual-Logging & Notification Overrides ────────────────────────────────────
LOG_FILE = ENGINE_DIR / "data_store" / "live_engine.log"

import subprocess

def log_message(*args, **kwargs):
    """Log message to both console and data_store/live_engine.log."""
    msg = " ".join(str(a) for a in args)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {msg}"
    # Call original built-in print
    import builtins
    builtins.print(line, **kwargs)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# Globally override print within this module
print = log_message

def show_windows_notification(title, message):
    """Show a native Windows Toast notification via PowerShell."""
    try:
        ps_script = f"""
        [void] [System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")
        $objNotification = New-Object System.Windows.Forms.NotifyIcon
        $objNotification.Icon = [System.Drawing.SystemIcons]::Information
        $objNotification.BalloonTipIcon = "Info"
        $objNotification.BalloonTipTitle = "{title}"
        $objNotification.BalloonTipText = "{message}"
        $objNotification.Visible = $True
        $objNotification.ShowBalloonTip(5000)
        """
        subprocess.run(["powershell", "-Command", ps_script], capture_output=True)
    except Exception:
        pass

headers = {
    "apikey": SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
    "Content-Type": "application/json"
}

# ── Optimised parameter configs (loaded from full-universe sweep) ─────────────
OPTIMISED_CONFIGS_FILE = ENGINE_DIR / "data_store" / "high_frequency_optimized_configs.json"

# Fallback style params used when no optimised config is found
STYLE_PARAMS_FALLBACK = {
    "scalp":    {"momentum_lookback": 14, "vol_window": 14,  "holding_horizon": 36, "warmup": 70,  "atr_stop_mult": 2.5, "reward_risk": 1.5},
    "intraday": {"momentum_lookback": 24, "vol_window": 24,  "holding_horizon": 72, "warmup": 80,  "atr_stop_mult": 2.5, "reward_risk": 2.0},
    "swing":    {"momentum_lookback": 63, "vol_window": 63,  "holding_horizon": 10, "warmup": 120, "atr_stop_mult": 3.0, "reward_risk": 2.0},
    "position": {"momentum_lookback": 126,"vol_window": 126, "holding_horizon": 40, "warmup": 180, "atr_stop_mult": 3.0, "reward_risk": 2.0},
}

def _load_optimised_configs():
    """Load optimised parameter configs from the sweep JSON file."""
    if not OPTIMISED_CONFIGS_FILE.exists():
        print("[INFO] No optimised configs found — using fallback style params.")
        return {}
    try:
        with open(OPTIMISED_CONFIGS_FILE, "r", encoding="utf-8") as f:
            configs = json.load(f)
        # Build lookup: (symbol, timeframe) -> parameters
        lookup = {}
        for c in configs:
            key = (c["symbol"], c["timeframe"])
            p = c["parameters"]
            lookup[key] = {
                "momentum_lookback": p.get("momentum_lookback", 28),
                "vol_window":        p.get("momentum_lookback", 28),  # vol_window mirrors lookback
                "holding_horizon":   p.get("hold_horizon", 24),
                "atr_stop_mult":     2.5,
                "reward_risk":       p.get("reward_risk", 2.0),
                "warmup":            max(p.get("momentum_lookback", 28) + 20, 60),
            }
        print(f"[INFO] Loaded {len(lookup)} optimised configs from sweep.")
        return lookup
    except Exception as e:
        print(f"[WARN] Failed to load optimised configs: {e}")
        return {}

_OPTIMISED_LOOKUP = _load_optimised_configs()

def _build_portfolio_from_configs():
    """Build the scan portfolio from optimised configs, filling gaps with legacy core."""
    if not _OPTIMISED_LOOKUP:
        return ROBUST_CORE_PORTFOLIO_LEGACY

    portfolio = []
    seen = set()
    for (symbol, tf), _ in _OPTIMISED_LOOKUP.items():
        if tf == "15m":
            style = "scalp"
        elif tf == "1h":
            style = "intraday"
        else:
            style = "swing"
        key = (symbol, tf)
        if key not in seen:
            portfolio.append({"instrument": symbol, "style": style, "timeframe": tf})
            seen.add(key)
    return portfolio

# Legacy portfolio (fallback when no optimised configs exist)
ROBUST_CORE_PORTFOLIO_LEGACY = [
    {"instrument": "GOOGL",   "style": "swing",    "timeframe": "1d"},
    {"instrument": "ADA/USD", "style": "swing",    "timeframe": "1d"},
    {"instrument": "NVDA",    "style": "swing",    "timeframe": "1d"},
    {"instrument": "AMD",     "style": "swing",    "timeframe": "1d"},
    {"instrument": "MSFT",    "style": "position", "timeframe": "1d"},
    {"instrument": "XLK",     "style": "swing",    "timeframe": "1d"},
    {"instrument": "TSLA",    "style": "position", "timeframe": "1d"},
    {"instrument": "QQQ",     "style": "swing",    "timeframe": "1d"},
    {"instrument": "SPY",     "style": "swing",    "timeframe": "1d"},
    {"instrument": "GBP/USD", "style": "swing",    "timeframe": "1d"},
    {"instrument": "BTC/USD", "style": "scalp",    "timeframe": "15m"},
]

ROBUST_CORE_PORTFOLIO = _build_portfolio_from_configs()
print(f"[INFO] Portfolio loaded: {len(ROBUST_CORE_PORTFOLIO)} systems active.")

def get_params_for_trade(style, timeframe, instrument=""):
    """Retrieve parameter configurations — optimised first, fallback to style defaults."""
    # Try exact optimised lookup
    for tf_key in [timeframe, timeframe.lower()]:
        key = (instrument, tf_key)
        if key in _OPTIMISED_LOOKUP:
            return _OPTIMISED_LOOKUP[key]

    # Style-based fallback
    style_key = str(style).lower()
    if style_key in STYLE_PARAMS_FALLBACK:
        return STYLE_PARAMS_FALLBACK[style_key]

    # Timeframe fallback
    tf_clean = str(timeframe).lower()
    if "15m" in tf_clean or "5m" in tf_clean:
        return STYLE_PARAMS_FALLBACK["scalp"]
    elif "1h" in tf_clean or "4h" in tf_clean:
        return STYLE_PARAMS_FALLBACK["intraday"]
    elif "1d" in tf_clean:
        return STYLE_PARAMS_FALLBACK["swing"]
    elif "1w" in tf_clean:
        return STYLE_PARAMS_FALLBACK["position"]

    return STYLE_PARAMS_FALLBACK["swing"]

cfg = get_config()
yahoo_adapter = get_adapter("yahoo")

# ── Executor dispatch ─────────────────────────────────────────────────────────
def _create_executor():
    """Create the configured executor based on ``config.execution``.

    Returns
    -------
    MT4Executor | MockExecutor | None
        ``None`` when execution is disabled.
    """
    if not cfg.execution.enabled:
        print("[EXECUTOR] Execution is DISABLED in config — no orders will be sent.")
        return None

    provider = cfg.execution.provider
    if provider == "mt4":
        print(f"[EXECUTOR] Using MT4Executor (common_dir from config/env)")
        return MT4Executor()
    elif provider == "mock":
        print(f"[EXECUTOR] Using MockExecutor — orders will be logged, not sent to MT4")
        return MockExecutor(default_volume=cfg.execution.mt4.default_volume)
    else:
        print(f"[EXECUTOR] Unknown provider {provider!r} — no orders will be sent.")
        return None

_EXECUTOR = _create_executor()

def fetch_open_trades():
    """Fetch unresolved setups from Supabase."""
    url = f"{MEMORY_ENDPOINT}?outcome=eq.pending"
    try:
        r = httpx.get(url, headers=headers)
        if r.status_code == 200:
            return r.json()
        print(f"Error fetching open setups: Supabase {r.status_code} - {r.text}")
    except Exception as e:
        print(f"Connection error to Supabase: {e}")
    return []

def resolve_trade(trade_id, outcome, exit_price, exit_date):
    """PATCH trade outcome to Supabase."""
    url = f"{MEMORY_ENDPOINT}?id=eq.{trade_id}"
    payload = {
        "outcome": outcome,
        "outcome_price": float(exit_price),
        "outcome_date": exit_date
    }
    try:
        r = httpx.patch(url, headers=headers, json=payload)
        if r.status_code in (200, 204):
            print(f"  [resolved] Trade {trade_id} closed as {outcome} at price {exit_price}")
            return True
        print(f"Failed to update trade {trade_id}: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"Connection error to Supabase updating trade: {e}")
    return False


def _normalise_symbol(symbol: str) -> str:
    """Convert internal symbol format to MT4-compatible ticker."""
    return symbol.upper().replace("/", "")


def open_new_trade(symbol, direction, entry_price, stop_loss, target_price, timeframe, confidence, rr):
    """POST new trade entry to Supabase and dispatch to live executor."""
    trade_id = f"{symbol.upper()}_{int(time.time())}"
    
    payload = {
        "id": trade_id,
        "symbol": symbol.upper(),
        "asset_type": "crypto" if "/" in symbol else "equity",
        "analysis_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "price": float(entry_price),
        "verdict": "BUY" if direction == "LONG" else "SELL",
        "confidence": int(confidence),
        "entry_zone": f"{entry_price:.4f}",
        "stop_loss": float(stop_loss),
        "target_price": float(target_price),
        "risk_reward": f"1:{rr:.1f}",
        "timeframe": timeframe,
        "summary": f"Automated entry trigger via APEX Quant Robust Core on {timeframe} timeframe.",
        "technical_analysis": f"Regime detection classifies market structure. Momentum/Mean-Reversion signals aligned.",
        "outcome": "pending"
    }
    
    try:
        r = httpx.post(MEMORY_ENDPOINT, headers=headers, json=payload)
        if r.status_code in (200, 201, 204):
            print(f"  [triggered] Logged new {direction} trade on {symbol} at entry {entry_price}")
            # Dispatch to live executor (MT4 or mock) when enabled.
            if _EXECUTOR is not None:
                mt4_symbol = _normalise_symbol(symbol)
                mt4_cmd = "buy" if direction == "LONG" else "sell"
                try:
                    result = _EXECUTOR.submit_order(
                        symbol=mt4_symbol,
                        cmd=mt4_cmd,
                        volume=None,         # executor uses default_volume from config
                        sl=float(stop_loss),
                        tp=float(target_price),
                    )
                    print(f"  [EXECUTOR] Order dispatched — {mt4_cmd.upper()} {mt4_symbol} → {result}")
                except Exception as e:
                    print(f"  [EXECUTOR ERROR] Failed to dispatch order: {e}")
            # Show Native Windows Notification
            show_windows_notification(
                "APEX Quant: Trade Executed",
                f"Opened {direction} position on {symbol} @ {entry_price:.4f}\nSL: {stop_loss:.4f} | TP: {target_price:.4f}"
            )
            return True
        print(f"Failed to create new trade for {symbol}: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"Connection error creating new trade: {e}")
    return False

def parse_trade_entry_ts(row: dict) -> float:
    """Extract Unix timestamp of trade creation."""
    if "created_at" in row and row["created_at"]:
        try:
            return pd.to_datetime(row["created_at"]).timestamp()
        except Exception:
            pass
    import re
    m = re.search(r"_(\d{10,})$", str(row.get("id", "")))
    if m:
        return float(m.group(1))
    if "analysis_date" in row and row["analysis_date"]:
        try:
            return pd.to_datetime(row["analysis_date"]).timestamp()
        except Exception:
            pass
    return datetime.utcnow().timestamp()

def map_timeframe(tf_str: str) -> str:
    """Map database timeframe to Yahoo Finance interval."""
    tf = str(tf_str).lower()
    if "15m" in tf or "scalp" in tf:
        return "15m"
    if "1h" in tf or "intraday" in tf:
        return "1h"
    return "1d"

def check_single_trade(t):
    """Worker to check a single trade state using its native timeframe and wick data."""
    sym = t["symbol"]
    trade_id = t["id"]
    direction = t["verdict"]
    sl = float(t["stop_loss"])
    tp = float(t["target_price"])
    tf = map_timeframe(t.get("timeframe", "1d"))
    
    try:
        entry_ts = parse_trade_entry_ts(t)
        now_ts = datetime.utcnow().timestamp()
        age_seconds = now_ts - entry_ts
        
        if tf == "15m":
            lookback_days = min(50, max(3, int(age_seconds / 86400) + 1))
        elif tf == "1h":
            lookback_days = min(700, max(7, int(age_seconds / 86400) + 1))
        else:
            lookback_days = max(30, int(age_seconds / 86400) + 1)
            
        start_date = (datetime.utcnow() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        
        df = clean(yahoo_adapter.get_history(sym, start=start_date, end=end_date, timeframe=tf))
        if df.empty:
            return
            
        # Filter for candles starting on or after the entry timestamp (with a 1-minute buffer)
        df_timestamps = df.index.tz_localize(None).view("int64") // 10**9
        df_after = df.loc[df_timestamps >= (entry_ts - 60)]
        
        if df_after.empty:
            return
            
        curr_time = datetime.utcnow().isoformat()
        
        # Check chronologically
        for timestamp, bar in df_after.iterrows():
            high_p = float(bar["high"])
            low_p = float(bar["low"])
            bar_time = timestamp.tz_localize(None).isoformat()
            
            if direction == "BUY" or direction == "LONG":  # LONG
                if low_p <= sl:
                    resolve_trade(trade_id, "sl_hit", sl, bar_time)
                    return
                elif high_p >= tp:
                    resolve_trade(trade_id, "tp_hit", tp, bar_time)
                    return
            elif direction == "SELL" or direction == "SHORT":  # SHORT
                if high_p >= sl:
                    resolve_trade(trade_id, "sl_hit", sl, bar_time)
                    return
                elif low_p <= tp:
                    resolve_trade(trade_id, "tp_hit", tp, bar_time)
                    return
    except Exception as e:
        print(f"Error checking status for {sym}: {e}")

def check_open_trades(open_trades):
    """Check open positions against current market price concurrently."""
    if not open_trades:
        print("No pending open trades in database.")
        return
        
    print(f"Checking {len(open_trades)} active trades in parallel...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(check_single_trade, t) for t in open_trades]
        for _ in as_completed(futures):
            pass

def scan_single_asset(item, active_symbols):
    """Worker to scan a single portfolio asset for signals."""
    sym = item["instrument"]
    style = item["style"]
    tf = item["timeframe"]
    
    if sym.upper() in active_symbols:
        return
        
    params = get_params_for_trade(style, tf, sym)
    try:
        # Look back enough days for warmup
        lookback_days = 20 if tf in ("5m", "15m") else (60 if tf == "1h" else 300)
        start_date = (datetime.utcnow() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        
        df = clean(yahoo_adapter.get_history(sym, start=start_date, end=end_date, timeframe=tf))
        if len(df) < params["warmup"] + 15:
            return
            
        pit = PointInTimeAccessor(df)
        strat = RegimeGatedMomentum(
            momentum_lookback=params["momentum_lookback"],
            vol_window=params["vol_window"],
            holding_horizon=params["holding_horizon"],
            reward_risk=params["reward_risk"],
            regime_method="rule_based",
            timeframe=tf,
            bypass_calibration=True,
            instrument=sym
        )
        strat.fit(pit, df.index[:-1])
        
        # Evaluate latest signal
        latest_time = df.index[-1]
        sig = strat.generate(pit, latest_time, instrument=sym)
        
        # ── Apply DeepSeek sentiment veto filter ──────────────────────
        sig = apply_deepseek_sentiment(sig, sym, fetch_headlines, cfg=cfg)
        # ───────────────────────────────────────────────────────────────
        
        if sig.direction != "FLAT":
            close_p = float(df["close"].iloc[-1])
            tr = np.maximum(df["high"] - df["low"], np.maximum(abs(df["high"] - df["close"].shift(1)), abs(df["low"] - df["close"].shift(1))))
            atr = float(tr.rolling(14).mean().iloc[-1])
            
            if not (np.isfinite(atr) and atr > 0):
                atr = close_p * 0.02
                
            stop_dist = params["atr_stop_mult"] * atr
            target_dist = sig.reward_risk * stop_dist
            
            if sig.direction == "LONG":
                sl = close_p - stop_dist
                tp = close_p + target_dist
            else:
                sl = close_p + stop_dist
                tp = close_p - target_dist
                
            open_new_trade(
                symbol=sym,
                direction=sig.direction.value,
                entry_price=close_p,
                stop_loss=sl,
                target_price=tp,
                timeframe=tf,
                confidence=int(sig.probability * 100),
                rr=sig.reward_risk
            )
    except Exception as e:
        print(f"  Error scanning {sym}: {e}")

def scan_robust_core(open_trades):
    """Scan all 21 robust systems for new entry signals concurrently."""
    print("\nScanning Robust Core Portfolio for new setups in parallel...")
    active_symbols = {t["symbol"].upper() for t in open_trades}
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(scan_single_asset, item, active_symbols) for item in ROBUST_CORE_PORTFOLIO]
        for _ in as_completed(futures):
            pass

def run_once():
    print("\n" + "="*80)
    print(f"APEX QUANT - LIVE PAPER TRADING SCAN started at {datetime.utcnow().isoformat()} UTC")
    print("="*80)
    
    open_trades = fetch_open_trades()
    check_open_trades(open_trades)
    scan_robust_core(open_trades)
    
    print("\nScan completed successfully.")

def main():
    parser = argparse.ArgumentParser(description="Live Paper Trading Engine")
    parser.add_argument("--loop", action="store_true", help="Run the engine continuously in a loop")
    parser.add_argument("--interval", type=int, default=14400, help="Loop interval in seconds (default: 4 hours)")
    args = parser.parse_args()

    if args.loop:
        print(f"Running in loop mode. Scanning every {args.interval} seconds...")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"Uncaught loop error: {e}")
            print(f"Sleeping for {args.interval} seconds...")
            time.sleep(args.interval)
    else:
        run_once()

if __name__ == "__main__":
    main()
