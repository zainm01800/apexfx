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
from datetime import datetime, timezone
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
import re
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
from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum
from apex_quant.risk import RiskManager, AccountState, MarketState, Signal, Direction, OpenPosition
from apex_quant.risk.bayesian_sizer import BayesianRiskSizer
from apex_quant.risk.learning import exit_decision_quality, resolve_learning_outcome
from apex_quant.execution.mt4_clock import mt4_utc_offset_seconds
from apex_quant.features.microstructure import YangZhangVol

cfg = get_config()
EQUITIES_SET = set(cfg.data.equities) if hasattr(cfg.data, "equities") and cfg.data.equities else set()

def _safe_float(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    if not val_str:
        return None
    try:
        return float(val_str)
    except ValueError:
        import re
        m = re.findall(r"[-+]?\d*\.\d+|\d+", val_str)
        if m:
            try:
                return float(m[0])
            except ValueError:
                pass
        return None

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

from zoneinfo import ZoneInfo

def log_message(*args, **kwargs):
    """Log message to both console and data_store/live_engine.log."""
    msg = " ".join(str(a) for a in args)
    timestamp = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M:%S %Z")
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
        sym_clean = instrument.replace("_", "/")
        # Try OANDA first for forex and crypto, fall back to Yahoo if OANDA returns no/stale data
        if asset_class in ("forex", "crypto") and self.default_name == "oanda" and self.oanda is not None:
            try:
                df = self.oanda.get_history(sym_clean, start, end, timeframe)
                if df is not None and len(df) >= 10:
                    return df
                print(f"  [DATA] OANDA returned insufficient data for {instrument} ({timeframe}), falling back to Yahoo...")
            except Exception as e:
                print(f"  [DATA] OANDA failed for {instrument} ({timeframe}): {e} — falling back to Yahoo...")
        # Fallback: Yahoo Finance (also primary for equities/ETFs/crypto)
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
