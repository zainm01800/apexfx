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

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import sys
import os
import json
import threading
import time
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from dotenv import load_dotenv
    # Load .env file before imports to ensure all APEX_ env variables are in os.environ
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


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
from apex_quant.risk import RiskManager, AccountState, MarketState, Signal, Direction, OpenPosition
from apex_quant.risk.bayesian_sizer import BayesianRiskSizer
from apex_quant.features.microstructure import YangZhangVol

cfg = get_config()
EQUITIES_SET = set(cfg.data.equities) if hasattr(cfg.data, "equities") and cfg.data.equities else set()

def is_us_market_open() -> bool:
    """Check if the US stock market is open (NYSE 9:30 AM to 4:00 PM EST/EDT, Monday to Friday)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        import pytz
        ZoneInfo = lambda tz_name: pytz.timezone(tz_name)
    try:
        tz = ZoneInfo("America/New_York")
        now = datetime.now(tz)
        if now.weekday() >= 5:  # Saturday & Sunday
            return False
        minutes = now.hour * 60 + now.minute
        return 570 <= minutes < 960
    except Exception as e:
        print(f"[WARN] Error checking US market hours: {e}")
        return True  # Fallback to True

def is_forex_market_open() -> bool:
    """Check if the Forex market is open (Sunday 5:00 PM EST/EDT to Friday 5:00 PM EST/EDT)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        import pytz
        ZoneInfo = lambda tz_name: pytz.timezone(tz_name)
    try:
        tz = ZoneInfo("America/New_York")
        now = datetime.now(tz)
        weekday = now.weekday()
        hour = now.hour
        # Saturday: Closed
        if weekday == 5:
            return False
        # Sunday: Open after 5:00 PM EST (17:00)
        if weekday == 6:
            return hour >= 17
        # Friday: Closed after 5:00 PM EST (17:00)
        if weekday == 4:
            return hour < 17
        return True  # Monday to Thursday: Always Open
    except Exception as e:
        print(f"[WARN] Error checking Forex market hours: {e}")
        return True  # Fallback to True

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
    """Build the scan portfolio from config.yaml universe for all 4 timeframes, overlaying optimized configurations when available."""
    portfolio = []
    seen = set()
    
    # 1. Load all optimized configurations from sweep
    if _OPTIMISED_LOOKUP:
        for (symbol, tf), _ in _OPTIMISED_LOOKUP.items():
            if tf == "15m":
                style = "scalp"
            elif tf == "1h":
                style = "intraday"
            else:
                style = "swing"
            portfolio.append({"instrument": symbol, "style": style, "timeframe": tf})
            seen.add((symbol.upper(), tf.lower()))
            
    # 2. Add all 4 timeframes for all instruments in config.yaml
    all_symbols = []
    if hasattr(cfg.data, "instruments") and cfg.data.instruments:
        all_symbols.extend(cfg.data.instruments)
    if hasattr(cfg.data, "equities") and cfg.data.equities:
        all_symbols.extend(cfg.data.equities)
    if hasattr(cfg.data, "crypto") and cfg.data.crypto:
        all_symbols.extend(cfg.data.crypto)
        
    unique_symbols = []
    seen_syms = set()
    for sym in all_symbols:
        if sym.upper() not in seen_syms:
            unique_symbols.append(sym)
            seen_syms.add(sym.upper())
            
    styles = [
        ("15m", "scalp"),
        ("1h", "intraday"),
        ("1d", "swing"),
        ("1w", "position")
    ]
    
    for sym in unique_symbols:
        for tf, style in styles:
            key = (sym.upper(), tf.lower())
            if key not in seen:
                portfolio.append({"instrument": sym, "style": style, "timeframe": tf})
                seen.add(key)
            
    return portfolio

# Legacy portfolio (fallback when no optimised configs exist)
ROBUST_CORE_PORTFOLIO_LEGACY = [
    {"instrument": "EUR/USD", "style": "swing",    "timeframe": "1d"},
    {"instrument": "GBP/USD", "style": "swing",    "timeframe": "1d"},
    {"instrument": "USD/JPY", "style": "swing",    "timeframe": "1d"},
    {"instrument": "USD/CHF", "style": "swing",    "timeframe": "1d"},
    {"instrument": "AUD/USD", "style": "swing",    "timeframe": "1d"},
    {"instrument": "USD/CAD", "style": "swing",    "timeframe": "1d"},
    {"instrument": "NZD/USD", "style": "swing",    "timeframe": "1d"},
    {"instrument": "GBP/JPY", "style": "swing",    "timeframe": "1d"},
    {"instrument": "EUR/GBP", "style": "swing",    "timeframe": "1d"},
    {"instrument": "EUR/JPY", "style": "swing",    "timeframe": "1d"},
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

# ── Smart Data Provider Router ──
class SmartDataProvider:
    def __init__(self):
        try:
            self.oanda = get_adapter("oanda")
        except Exception:
            self.oanda = None
        self.yahoo = get_adapter("yahoo")
        self.default_name = cfg.data.provider

    def get_history(self, instrument: str, start, end, timeframe):
        asset_class = cfg.asset_class_of(instrument)
        # Route Forex to OANDA if configured and available
        if asset_class == "forex" and self.default_name == "oanda" and self.oanda is not None:
            sym_clean = instrument.replace("_", "/")
            return self.oanda.get_history(sym_clean, start, end, timeframe)
        else:
            # Route equities, ETFs, and crypto fallback to Yahoo
            sym_clean = instrument.replace("_", "/")
            return self.yahoo.get_history(sym_clean, start, end, timeframe)

data_provider = SmartDataProvider()
print(f"[DATA] Smart Data Provider active (Routing Forex -> OANDA, Equities/ETFs -> Yahoo)")


# ── Executor dispatch ─────────────────────────────────────────────────────────
def _create_executor():
    """Create the configured executor based on ``config.execution``.

    Returns
    -------
    MT4Executor | ZMQBridge | MockExecutor | None
        ``None`` when execution is disabled.
    """
    if not cfg.execution.enabled:
        print("[EXECUTOR] Execution is DISABLED in config — no orders will be sent.")
        return None

    provider = cfg.execution.provider
    if provider == "mt4":
        print(f"[EXECUTOR] Using MT4Executor (common_dir from config/env)")
        return MT4Executor()
    elif provider == "zmq":
        try:
            from apex_quant.execution.zmq_bridge import ZMQBridge
            print(f"[EXECUTOR] Using ZMQBridge (TCP push server)")
            return ZMQBridge()
        except ImportError as e:
            print(f"[EXECUTOR ERROR] ZMQBridge import failed: {e}. Falling back to MT4Executor.")
            return MT4Executor()
    elif provider == "mock":
        print(f"[EXECUTOR] Using MockExecutor — orders will be logged, not sent to MT4")
        return MockExecutor(default_volume=cfg.execution.mt4.default_volume)
    else:
        print(f"[EXECUTOR] Unknown provider {provider!r} — no orders will be sent.")
        return None

_EXECUTOR = _create_executor()

# ── Bayesian Sizer Global Setup ──
_BAYESIAN_SIZER = BayesianRiskSizer(
    frac_kelly=0.25,
    min_risk=0.005,
    max_risk=0.02,
    max_drawdown=0.15,
    min_trades_for_adaptation=5  # adapt quickly using historical demo data
)

def fetch_resolved_trades_for_equity():
    """Fetch all resolved setups (wins and losses) from Supabase."""
    url = f"{MEMORY_ENDPOINT}?outcome=in.(tp_hit,sl_hit)&limit=1000"
    try:
        r = httpx.get(url, headers=headers)
        if r.status_code == 200:
            return r.json()
        print(f"Error fetching resolved setups: Supabase {r.status_code} - {r.text}")
    except Exception as e:
        print(f"Connection error to Supabase: {e}")
    return []

def calculate_virtual_equity(trades, initial_equity=300000.0, risk_pct=0.01):
    """Compute virtual compounded equity from historical trade performance.
    Defaults to $300k starting capital (three 100k accounts)."""
    equity = initial_equity
    peak_equity = initial_equity
    
    # Sort chronologically by entry timestamp
    trades.sort(key=lambda t: parse_trade_entry_ts(t))
    
    for t in trades:
        outcome = t.get("outcome")
        if outcome not in ("tp_hit", "sl_hit"):
            continue
            
        # Parse risk_reward (e.g. "1:1.5")
        rr = 1.5
        rr_str = t.get("risk_reward", "")
        if ":" in rr_str:
            try:
                parts = rr_str.split(":")
                val1 = float(parts[0])
                val2 = float(parts[1])
                rr = max(val1, val2) / min(val1, val2)
            except Exception:
                pass
                
        risk_amount = equity * risk_pct
        if outcome == "tp_hit":
            equity += risk_amount * rr
        elif outcome == "sl_hit":
            equity -= risk_amount
            
        if equity > peak_equity:
            peak_equity = equity
            
    return equity, peak_equity

def units_to_lots(symbol: str, units: float, cost_model: str) -> float:
    """Convert raw position units to MT4 lot sizes."""
    if cost_model == "pips" or "/" in symbol:
        lots = units / 100000.0
        return max(0.01, round(lots, 2))
    else:
        return max(1.0, round(units, 0))

def initialize_bayesian_sizer_from_supabase():
    """Initialize win-rate trackers from Supabase history."""
    resolved_trades = fetch_resolved_trades_for_equity()
    if not resolved_trades:
        return
    # Sort chronologically
    resolved_trades.sort(key=lambda t: parse_trade_entry_ts(t))
    for t in resolved_trades:
        symbol = t["symbol"].upper()
        win = t["outcome"] == "tp_hit"
        _BAYESIAN_SIZER.record_outcome(symbol, win)
    print(f"[BAYESIAN SIZER] Initialised trackers with {len(resolved_trades)} historical trades from Supabase.")


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


def add_validation_to_trade(trade, verdict, confidence, assessment="confirmed"):
    """Append a validation/re-check record to an open trade in Supabase."""
    trade_id = trade["id"]
    current_vals = trade.get("validations") or []
    if isinstance(current_vals, str):
        try:
            current_vals = json.loads(current_vals)
        except Exception:
            current_vals = []
            
    new_val = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "verdict": verdict,
        "confidence": int(confidence),
        "assessment": assessment
    }
    
    # Avoid duplicate validations with the same verdict within the last 1 hour
    if current_vals:
        try:
            last_val = current_vals[-1]
            last_ts = pd.to_datetime(last_val["ts"])
            if (datetime.utcnow() - last_ts.replace(tzinfo=None)).total_seconds() < 3600:
                if last_val["verdict"] == verdict:
                    return
        except Exception:
            pass
            
    current_vals.append(new_val)
    
    patch_url = f"{MEMORY_ENDPOINT}?id=eq.{trade_id}"
    try:
        r = httpx.patch(patch_url, headers=headers, json={"validations": current_vals})
        if r.status_code in (200, 204):
            print(f"  [VALIDATION] Logged re-check verdict {verdict} ({confidence}%) for trade {trade_id}")
    except Exception as e:
        print(f"  [WARN] Failed to write validation to database: {e}")


def _normalise_symbol(symbol: str) -> str:
    """Convert internal symbol format to MT4-compatible ticker."""
    return symbol.upper().replace("/", "")


def open_new_trade(symbol, direction, entry_price, stop_loss, target_price, timeframe, confidence, rr, volume=None):
    """POST new trade entry to Supabase and dispatch to live executor."""
    trade_id = f"{symbol.upper()}_{int(time.time())}"
    
    payload = {
        "id": trade_id,
        "symbol": symbol.upper(),
        "asset_type": (
            "equity" if symbol.upper() in EQUITIES_SET else
            ("crypto" if "/" in symbol and any(c in symbol.upper() for c in ("BTC", "ETH", "AVAX", "SOL", "ADA", "DOGE", "XRP", "BNB")) else
             "forex" if "/" in symbol else "equity")
        ),
        "analysis_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "price": float(entry_price),
        "verdict": "BUY" if str(direction).upper() in ("LONG", "BUY") else "SELL",
        "confidence": int(confidence),
        "entry_zone": f"{entry_price:.4f}",
        "stop_loss": float(stop_loss),
        "target_price": float(target_price),
        "risk_reward": f"1:{rr:.1f}",
        "timeframe": timeframe,
        "summary": f"Automated entry trigger via APEX Quant Robust Core on {timeframe} timeframe.",
        "technical_analysis": f"Regime detection classifies market structure. Momentum/Mean-Reversion signals aligned.",
        "setup_features": {"auto": True},
        "outcome": "pending"
    }
    
    try:
        r = httpx.post(MEMORY_ENDPOINT, headers=headers, json=payload)
        if r.status_code in (200, 201, 204):
            print(f"  [triggered] Logged new {direction} trade on {symbol} at entry {entry_price}")
            # Dispatch to live executor (MT4, ZMQ, or mock) when enabled.
            if _EXECUTOR is not None:
                mt4_symbol = _normalise_symbol(symbol)
                mt4_cmd = "buy" if str(direction).upper() in ("LONG", "BUY") else "sell"
                try:
                    result = _EXECUTOR.submit_order(
                        symbol=mt4_symbol,
                        cmd=mt4_cmd,
                        volume=volume,       # dynamic Bayesian position size
                        sl=float(stop_loss),
                        tp=float(target_price),
                    )
                    print(f"  [EXECUTOR] Order dispatched — {mt4_cmd.upper()} {mt4_symbol} with size {volume} lots → {result}")
                    try:
                        patch_url = f"{MEMORY_ENDPOINT}?id=eq.{trade_id}"
                        httpx.patch(patch_url, headers=headers, json={"filled_at": int(time.time())})
                    except Exception as patch_err:
                        print(f"  [WARN] Failed to update filled_at in database: {patch_err}")
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
        
        df = clean(data_provider.get_history(sym, start=start_date, end=end_date, timeframe=tf))
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
    """Check open positions against current market price concurrently with cached historical data."""
    if not open_trades:
        print("No pending open trades in database.")
        return
        
    print(f"Checking {len(open_trades)} active trades in parallel...")
    
    # 1. Group trades by (symbol, timeframe)
    grouped_trades = {}
    us_open = is_us_market_open()
    fx_open = is_forex_market_open()
    
    for t in open_trades:
        sym = t["symbol"]
        is_eq = sym.upper() in EQUITIES_SET
        is_fx = "/" in sym and not is_eq
        
        # Skip checking if market is closed
        if is_eq and not us_open:
            continue
        if is_fx and not fx_open:
            continue
            
        tf = map_timeframe(t.get("timeframe", "1d"))
        key = (sym, tf)
        if key not in grouped_trades:
            grouped_trades[key] = []
        grouped_trades[key].append(t)
        
    # 2. Fetch history for each group in parallel
    history_cache = {}
    history_lock = threading.Lock()
    
    def fetch_group_history(key):
        sym, tf = key
        trades = grouped_trades[key]
        
        # Find earliest entry timestamp
        earliest_entry = None
        for t in trades:
            try:
                entry_ts = parse_trade_entry_ts(t)
                if earliest_entry is None or entry_ts < earliest_entry:
                    earliest_entry = entry_ts
            except Exception:
                pass
                
        if earliest_entry is None:
            earliest_entry = datetime.utcnow().timestamp()
            
        now_ts = datetime.utcnow().timestamp()
        age_seconds = now_ts - earliest_entry
        
        if tf == "15m":
            lookback_days = min(50, max(3, int(age_seconds / 86400) + 1))
        elif tf == "1h":
            lookback_days = min(700, max(7, int(age_seconds / 86400) + 1))
        else:
            lookback_days = max(30, int(age_seconds / 86400) + 1)
            
        start_date = (datetime.utcnow() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        
        try:
            df = clean(data_provider.get_history(sym, start=start_date, end=end_date, timeframe=tf))
            if not df.empty:
                with history_lock:
                    history_cache[key] = df
        except Exception as e:
            print(f"Error fetching history for check cache on {sym} ({tf}): {e}")

    # Fetch all histories in parallel
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_group_history, key) for key in grouped_trades.keys()]
        for _ in as_completed(futures):
            pass
            
    # 3. Now check each trade using the cached history data
    def check_trade_with_cache(t):
        sym = t["symbol"]
        trade_id = t["id"]
        direction = t["verdict"]
        sl = float(t["stop_loss"])
        tp = float(t["target_price"])
        tf = map_timeframe(t.get("timeframe", "1d"))
        key = (sym, tf)
        
        df = history_cache.get(key)
        if df is None or df.empty:
            return
            
        try:
            entry_ts = parse_trade_entry_ts(t)
            df_timestamps = df.index.tz_localize(None).view("int64") // 10**9
            df_after = df.loc[df_timestamps >= (entry_ts - 60)]
            
            if df_after.empty:
                return
                
            for timestamp, bar in df_after.iterrows():
                high_p = float(bar["high"])
                low_p = float(bar["low"])
                bar_time = timestamp.tz_localize(None).isoformat()
                
                if direction == "BUY" or direction == "LONG":
                    if low_p <= sl:
                        resolve_trade(trade_id, "sl_hit", sl, bar_time)
                        return
                    elif high_p >= tp:
                        resolve_trade(trade_id, "tp_hit", tp, bar_time)
                        return
                elif direction == "SELL" or direction == "SHORT":
                    if high_p >= sl:
                        resolve_trade(trade_id, "sl_hit", sl, bar_time)
                        return
                    elif low_p <= tp:
                        resolve_trade(trade_id, "tp_hit", tp, bar_time)
                        return
        except Exception as e:
            print(f"Error checking status for {sym}: {e}")

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(check_trade_with_cache, t) for t in open_trades]
        for _ in as_completed(futures):
            pass

def fetch_lessons_pool():
    try:
        url = f"{MEMORY_ENDPOINT}?select=symbol,verdict,outcome,lesson&lesson=not.is.null&limit=1000"
        r = httpx.get(url, headers=headers)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [WARN] Failed to load lessons pool: {e}")
    return []

def get_similar_lessons(symbol, verdict, pool, limit=3):
    if not pool:
        return []
    matched = [l for l in pool if l.get("symbol") == symbol and l.get("verdict") == verdict]
    if len(matched) < limit:
        matched.extend([l for l in pool if l.get("symbol") == symbol and l not in matched])
    if len(matched) < limit:
        matched.extend([l for l in pool if l.get("verdict") == verdict and l not in matched])
    if len(matched) < limit:
        matched.extend([l for l in pool if l not in matched])
    return matched[:limit]

def apply_deepseek_structural_veto(symbol, direction, df, cfg):
    """Evaluate structural risk flags (counter-trend, chop, volatility, falling knife)
    against past lessons using DeepSeek LLM."""
    from apex_quant.ai.client import DeepSeekLLM
    from apex_quant.ml.dataset import compute_feature_frame
    
    # 1. Initialize LLM
    llm = DeepSeekLLM(cfg=cfg.ai)
    if not llm.available:
        return True, "LLM not available (fail-ALLOW)"
        
    try:
        # 2. Compute features for the latest row
        features_df = compute_feature_frame(df, cfg)
        if features_df.empty:
            return True, "No features computed (fail-ALLOW)"
            
        row_features = features_df.iloc[-1].to_dict()
        feat_str = ", ".join([f"{k}: {v:.5f}" for k, v in row_features.items() if np.isfinite(v)])
        
        # 3. Calculate risk flags programmatically
        mom = next((v for k, v in row_features.items() if k.startswith("mom_") and not k.startswith("mom_vs_")), 0.0)
        rvol = next((v for k, v in row_features.items() if k.startswith("rvol_")), 0.05)
        trend_slope = next((v for k, v in row_features.items() if k.startswith("trend_slope_")), 0.0)
        dist_ma = next((v for k, v in row_features.items() if k.startswith("dist_ma_")), 0.0)
        
        verdict = "BUY" if str(direction).upper() in ("LONG", "BUY") else "SELL"
        is_counter_trend = (verdict == "BUY" and trend_slope < -0.00001) or (verdict == "SELL" and trend_slope > 0.00001)
        is_dead_range_chop = abs(trend_slope) < 0.00002 and rvol < 0.02
        is_volatility_spike = rvol > 0.25
        is_overextended_dump = (verdict == "BUY" and dist_ma < -2.0 and mom < -0.015) or (verdict == "SELL" and dist_ma > 2.0 and mom > 0.015)
        
        flags = {
            "is_counter_trend": is_counter_trend,
            "is_dead_range_chop": is_dead_range_chop,
            "is_volatility_spike": is_volatility_spike,
            "is_overextended_dump": is_overextended_dump
        }
        
        # If no risk flags are True, we can fast-track ALLOW without calling LLM
        if not any(flags.values()):
            return True, "No risk flags triggered."
            
        flags_str = "\n".join([f"- {k}: {v}" for k, v in flags.items()])
        
        # 4. Fetch lessons from database
        lessons_pool = fetch_lessons_pool()
        similar_lessons = get_similar_lessons(symbol, verdict, lessons_pool, limit=3)
        
        lessons_str = ""
        for idx, l in enumerate(similar_lessons):
            lessons_str += f"{idx+1}. [{l['symbol']} {l['verdict']} -> {l['outcome']}]: \"{l['lesson']}\"\n"
            
        # 5. Build prompt
        prompt = f"""
We are considering executing a new {verdict} trade on {symbol}.

Current Market Indicators:
{feat_str}

Pre-Calculated Risk Flags:
{flags_str}

Indicator Glossary & Context:
- mom_X: Price return over the last X periods. A negative value represents a recent pullback/dip, which is common and expected for pullback entry strategies.
- mom_vs_X: Normalized momentum relative to volatility.
- rvol_X / pvol_X: Realised/Parkinson historical volatility.
- trend_slope_X: Slope of the major trend. Positive values indicate an overall upward structural trend bias (bullish structure).
- dist_ma_X: Distance from the major moving average. A negative value indicates price is trading below its MA (confirming a pullback/discount entry).

Here are relevant lessons from past resolved trades:
{lessons_str}

DIRECTIVE: Act as a hedge fund risk manager. You must VETO this trade if any of the Pre-Calculated Risk Flags are True:
- is_counter_trend: True (the trade goes against the major trend direction)
- is_dead_range_chop: True (the market is flat and illiquid, meaning signals are random noise)
- is_volatility_spike: True (volatility is too high, indicating extreme risk)
- is_overextended_dump: True (the price is falling too fast like a falling knife, showing structural weakness)

Otherwise, ALLOW the trade. Do not veto healthy setups where all risk flags are False.

Return ONLY a strict JSON object:
{{
  "verdict": "VETO" or "ALLOW",
  "reason": "1-sentence explanation of your assessment referring to the specific risk flag"
}}
"""
        system = "You are a pragmatic risk manager. Reply only with valid JSON containing 'verdict' and 'reason'."
        
        resp = llm.complete(prompt, system=system, temperature=0.1, max_tokens=300)
        if not resp:
            return True, "AI call failed (fail-ALLOW)"
            
        clean_resp = resp.strip()
        if clean_resp.startswith("```"):
            clean_resp = clean_resp.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            
        data = json.loads(clean_resp)
        verdict_res = data.get("verdict", "ALLOW").upper()
        reason = data.get("reason", "No reason provided")
        
        return (verdict_res != "VETO"), reason
    except Exception as e:
        return True, f"Error running structural veto check: {e}"

def scan_single_asset(item, active_trades_map):
    """Worker to scan a single portfolio asset for signals."""
    sym = item["instrument"]
    style = item["style"]
    tf = item["timeframe"]
    
    # ── Check Market Hours ──
    is_eq = sym.upper() in EQUITIES_SET
    is_fx = "/" in sym and not is_eq
    
    if is_eq and not is_us_market_open():
        return
        
    if is_fx and not is_forex_market_open():
        return
        
    print(f"  [SCANNING] {sym} ({tf}) -> Running strategy sweep...")
    params = get_params_for_trade(style, tf, sym)
    try:
        # Look back enough days for warmup
        lookback_days = 20 if tf in ("5m", "15m") else (60 if tf == "1h" else 300)
        start_date = (datetime.utcnow() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        
        df = clean(data_provider.get_history(sym, start=start_date, end=end_date, timeframe=tf))
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
        
        # Check if the data is stale
        now_utc = datetime.utcnow()
        latest_time_utc = latest_time.tz_convert("UTC") if latest_time.tzinfo else latest_time.tz_localize("UTC")
        age_seconds = (now_utc - latest_time_utc.replace(tzinfo=None)).total_seconds()
        
        # Max allowed age based on timeframe (with generous buffer for broker chart lag)
        max_age = {
            "5m": 1200,      # 20 mins
            "15m": 2700,     # 45 mins
            "1h": 10800,     # 3 hours
            "1d": 129600,    # 36 hours (handles weekend close / daily data lag)
            "1w": 691200     # 8 days
        }.get(tf, 86400)
        
        if age_seconds > max_age:
            print(f"  [SKIPPED] {sym} ({tf}) -> Latest bar data is stale (age: {age_seconds/3600:.1f} hours old)")
            return
            
        sig = strat.generate(pit, latest_time, instrument=sym)
        
        # ── Apply DeepSeek sentiment veto filter ──────────────────────
        sig = apply_deepseek_sentiment(sig, sym, fetch_headlines, cfg=cfg)
        # ───────────────────────────────────────────────────────────────
        
        # ── Apply DeepSeek structural risk veto filter ────────────────
        if sig.direction.value.upper() != "FLAT":
            permitted, reason = apply_deepseek_structural_veto(sym, sig.direction.value, df, cfg)
            if not permitted:
                print(f"  [STRUCTURAL VETO] Vetoed trade for {sym}: {reason}")
                sig = sig.model_copy(update={
                    "direction": Direction.FLAT,
                    "probability": 0.5,
                    "confidence": 0.0,
                    "rationale": sig.rationale + f" | STRUCTURAL-VETO: {reason}"
                })
        # ───────────────────────────────────────────────────────────────
        
        # Check if we have an active trade for this symbol/timeframe
        active_trade = active_trades_map.get((sym.upper(), tf.lower()))
        
        if active_trade:
            # Recheck logic for existing open trade
            trade_verdict = active_trade["verdict"].upper()
            sig_dir = sig.direction.value.upper()
            
            # Map signal to actions
            if sig_dir == "FLAT":
                print(f"  [VALIDATION] {sym} ({tf}) -> Signal is FLAT. Suggesting early close.")
                add_validation_to_trade(active_trade, "CLOSE_TRADE", 100, assessment="invalidated")
                
                # Execute MT4 exit
                if _EXECUTOR is not None:
                    mt4_symbol = _normalise_symbol(sym)
                    try:
                        _EXECUTOR.close_position(symbol=mt4_symbol)
                        print(f"  [EXECUTOR] Position closed for {mt4_symbol}")
                    except Exception as ex:
                        print(f"  [EXECUTOR WARN] Failed to close position on MT4: {ex}")
                        
                resolve_trade(active_trade["id"], "invalidated", float(df["close"].iloc[-1]), datetime.utcnow().isoformat())
                return
                
            elif (trade_verdict in ("BUY", "LONG") and sig_dir == "SHORT") or \
                 (trade_verdict in ("SELL", "SHORT") and sig_dir == "LONG"):
                # Reversal signal! Close active and trigger opposite.
                print(f"  [VALIDATION] {sym} ({tf}) -> Reversal signal detected ({sig_dir}). Closing active trade.")
                add_validation_to_trade(active_trade, "CLOSE_TRADE", int(sig.probability * 100), assessment="invalidated")
                
                # Execute MT4 exit
                if _EXECUTOR is not None:
                    mt4_symbol = _normalise_symbol(sym)
                    try:
                        _EXECUTOR.close_position(symbol=mt4_symbol)
                    except Exception as ex:
                        print(f"  [EXECUTOR WARN] Failed to close position on MT4: {ex}")
                        
                resolve_trade(active_trade["id"], "invalidated", float(df["close"].iloc[-1]), datetime.utcnow().isoformat())
                # Fall through to trigger the new trade in the opposite direction!
                pass
                
            else:
                # Same direction, keep holding
                print(f"  [VALIDATION] {sym} ({tf}) -> Continuing to hold {trade_verdict} position.")
                add_validation_to_trade(active_trade, "HOLD_TRADE", int(sig.probability * 100), assessment="confirmed")
                return
                
        if sig.direction.value.upper() == "FLAT":
            print(f"  [FLAT] {sym} ({tf}) -> Strategy signal is flat (no setup)")
            return
            
        if sig.direction.value.upper() != "FLAT":
            close_p = float(df["close"].iloc[-1])
            tr = np.maximum(df["high"] - df["low"], np.maximum(abs(df["high"] - df["close"].shift(1)), abs(df["low"] - df["close"].shift(1))))
            atr = float(tr.rolling(14).mean().iloc[-1])
            
            if not (np.isfinite(atr) and atr > 0):
                atr = close_p * 0.02
                
            stop_dist = params["atr_stop_mult"] * atr
            target_dist = sig.reward_risk * stop_dist
            
            if sig.direction.value.upper() == "LONG":
                sl = close_p - stop_dist
                tp = close_p + target_dist
            else:
                sl = close_p + stop_dist
                tp = close_p - target_dist
                
            # ── Bayesian Risk Sizing Integration ──
            try:
                # 1. Fetch current active trades for correlation check
                print(f"  [SIGNAL] {sym} -> Direction: {sig.direction.value.upper()} | Win Prob: {sig.probability:.1%} | R:R: {sig.reward_risk:.1f}:1")
                open_trades_list = fetch_open_trades()

                open_positions = []
                # Fetch resolved trades history to compound virtual equity curve
                all_resolved_trades = fetch_resolved_trades_for_equity()
                virtual_equity, peak_equity = calculate_virtual_equity(all_resolved_trades)

                for ot in open_trades_list:
                    sym_ot = ot["symbol"]
                    price_ot = float(ot["price"])
                    sl_ot = float(ot["stop_loss"]) if ot.get("stop_loss") else None
                    asset_class_ot = cfg.asset_class_of(sym_ot)
                    
                    trade_notional = 1000.0
                    if sl_ot and abs(price_ot - sl_ot) > 1e-6:
                        stop_dist_ot = abs(price_ot - sl_ot)
                        risk_cap = 0.01 * virtual_equity
                        units = risk_cap / stop_dist_ot
                        if asset_class_ot == "forex":
                            units = min(units, 500000.0)
                        else:
                            units = min(units, 1000.0)
                        trade_notional = units * price_ot
                    else:
                        if asset_class_ot == "forex":
                            trade_notional = price_ot * 10000.0
                        else:
                            trade_notional = price_ot * 1.0
                            
                    open_positions.append(OpenPosition(
                        instrument=sym_ot,
                        direction=Direction.LONG if ot["verdict"] in ("BUY", "LONG") else Direction.SHORT,
                        notional=trade_notional
                    ))
                
                account_state = AccountState(
                    equity=virtual_equity,
                    peak_equity=peak_equity,
                    open_positions=open_positions
                )
                
                # 2. Volatility estimate via Yang-Zhang
                yz_vol_calc = YangZhangVol(window=21)
                ann_vol = yz_vol_calc.compute(pit, latest_time)
                if not np.isfinite(ann_vol) or ann_vol <= 0:
                    ann_vol = 0.20 # 20% default
                
                market_state = MarketState(
                    instrument=sym,
                    price=close_p,
                    ann_vol=ann_vol,
                    atr=atr,
                    correlations={}
                )
                
                # 3. Create signal
                direction_enum = Direction.LONG if sig.direction.value.upper() == "LONG" else Direction.SHORT
                risk_sig = Signal(
                    instrument=sym,
                    direction=direction_enum,
                    probability=sig.probability,
                    reward_risk=sig.reward_risk,
                    confidence=getattr(sig, 'confidence', 0.5),
                    rationale=getattr(sig, 'rationale', "")
                )
                
                # 4. Permit through Risk Manager
                risk_manager = RiskManager(cfg.risk, bayesian_sizer=_BAYESIAN_SIZER)
                permitted_pos = risk_manager.permit(risk_sig, account_state, market_state)
                
                if not permitted_pos.permitted:
                    print(f"  [RISK VETO] Risk manager vetoed trade for {sym}: {permitted_pos.rationale}")
                    return
                
                # Convert units to lots
                cost_model = cfg.mechanics_for(sym).cost_model if hasattr(cfg, 'mechanics_for') else 'pips'
                sized_volume = units_to_lots(sym, permitted_pos.units, cost_model)
                print(f"  [RISK SIZED] Bayesian Risk Manager allocated {permitted_pos.risk_fraction:.2%} risk. "
                      f"Equity: ${virtual_equity:,.2f}. Lots: {sized_volume}.")
            except Exception as re:
                print(f"  [WARN] Risk manager sizing failed, fallback to defaults: {re}")
                import traceback
                traceback.print_exc()
                sized_volume = None
            # ──────────────────────────────────────
            
            open_new_trade(
                symbol=sym,
                direction=sig.direction.value,
                entry_price=close_p,
                stop_loss=sl,
                target_price=tp,
                timeframe=tf,
                confidence=int(sig.probability * 100),
                rr=sig.reward_risk,
                volume=sized_volume
            )

    except Exception as e:
        print(f"  Error scanning {sym}: {e}")

def scan_robust_core(open_trades):
    """Scan all systems for new entry signals concurrently."""
    print("\nScanning Robust Core Portfolio for new setups in parallel...")
    active_trades_map = {(t["symbol"].upper(), str(t.get("timeframe", "1d")).lower()): t for t in open_trades}
    
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(scan_single_asset, item, active_trades_map) for item in ROBUST_CORE_PORTFOLIO]
        for _ in as_completed(futures):
            pass

def run_once():
    print("\n" + "="*80)
    print(f"APEX QUANT - LIVE PAPER TRADING SCAN started at {datetime.utcnow().isoformat()} UTC")
    print("="*80)
    
    # ── Bayesian Sizer Setup ──
    try:
        initialize_bayesian_sizer_from_supabase()
    except Exception as e:
        print(f"[WARN] Failed to initialize Bayesian Sizer trackers: {e}")
    # ──────────────────────────
    
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
