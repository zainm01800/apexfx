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
    max_drawdown=0.15,
    min_trades_for_adaptation=5  # adapt quickly using historical demo data
)

def fetch_resolved_trades_for_equity():
    """Fetch all resolved setups (wins and losses) from Supabase."""
    url = f"{MEMORY_ENDPOINT}?outcome=in.(tp_hit,sl_hit)"
    try:
        from apex_quant.storage.supabase_util import fetch_all_rows
        return fetch_all_rows(url, headers)
    except Exception as e:
        print(f"Error fetching resolved setups: {e}")
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
    if cost_model == "pips":
        lots = units / 100000.0
        return max(0.01, round(lots, 2))
    else:
        # Crypto and equities: 1 lot = 1 unit.
        # Allow micro-lots (down to 0.01) for crypto, but integer shares for equities.
        is_crypto = "/USD" in symbol.upper() or "/BTC" in symbol.upper() or "-USD" in symbol.upper()
        if is_crypto:
            return max(0.01, round(units, 2))
        else:
            return max(1.0, round(units, 0))

def fetch_trades_for_learning():
    """Every resolved setup + its post-exit hindsight scan, for the Bayesian sizer."""
    url = (f"{MEMORY_ENDPOINT}?outcome=in.(tp_hit,sl_hit,invalidated,expired)"
           f"&select=id,symbol,outcome,setup_features,ticket")
    try:
        from apex_quant.storage.supabase_util import fetch_all_rows
        return fetch_all_rows(url, headers)
    except Exception as e:
        print(f"Error fetching trades for learning: {e}")
    return []


def fetch_closed_mt4_trades():
    """Fetch all closed MT4 trades from Supabase to match setups with profit/loss."""
    url = f"{SUPABASE_URL}/rest/v1/apex_mt4_trades?status=eq.closed&select=ticket,profit"
    try:
        from apex_quant.storage.supabase_util import fetch_all_rows
        return fetch_all_rows(url, headers)
    except Exception as e:
        print(f"Error fetching closed MT4 trades: {e}")
    return []


def initialize_bayesian_sizer_from_supabase():
    """Teach the sizer from history — including the trades we exited early.

    Every managed exit used to be invisible here, so the sizer only ever saw the
    trades that ran to a barrier. The hindsight rescan (see
    scripts/update_lessons.check_hindsight_trajectory) waits a timeframe-appropriate
    number of bars after the exit and reports whether the setup WOULD have hit its
    target or its stop, which converts those exits back into honest evidence.
    """
    resolved_trades = fetch_trades_for_learning()
    if not resolved_trades:
        return
    resolved_trades.sort(key=lambda t: parse_trade_entry_ts(t))

    # Fetch closed trades to map ticket to realized profit/loss
    closed_trades = fetch_closed_mt4_trades()
    ticket_to_pnl = {}
    for ct in closed_trades:
        tk_val = ct.get("ticket")
        if tk_val is not None:
            try:
                ticket_to_pnl[int(tk_val)] = float(ct.get("profit", 0.0))
            except (ValueError, TypeError):
                pass

    recorded = pending = 0
    wins = good = premature = 0
    for t in resolved_trades:
        symbol = str(t.get("symbol", "")).upper()
        win = resolve_learning_outcome(t)
        if win is None:
            pending += 1          # awaiting the hindsight scan — no information yet
            continue
            
        tk = t.get("ticket")
        pnl = None
        if tk is not None:
            try:
                pnl = ticket_to_pnl.get(int(tk))
            except (ValueError, TypeError):
                pass

        _BAYESIAN_SIZER.record_outcome(symbol, win, pnl=pnl)
        recorded += 1
        wins += int(win)
        q = exit_decision_quality(t)
        if q == "good":
            good += 1
        elif q == "premature":
            premature += 1

    rate = (wins / recorded * 100.0) if recorded else 0.0
    print(f"[BAYESIAN SIZER] Learned from {recorded} resolved trades "
          f"(win rate {rate:.1f}%); {pending} still awaiting a hindsight verdict.")
    
    # Log payoff details for sample instrument
    active_instruments = [str(t.get("symbol", "")).upper() for t in resolved_trades if t.get("symbol")]
    if active_instruments:
        sample_inst = active_instruments[-1]
        desc = _BAYESIAN_SIZER.describe(sample_inst)
        if desc.get("n_pnl_trades", 0) > 0:
            print(f"[BAYESIAN SIZER] Realized payoff status for {sample_inst}: "
                  f"avg_win={desc.get('avg_win')}, avg_loss={desc.get('avg_loss')}, "
                  f"realized_payoff={desc.get('realized_payoff')} (adaptation trades: {desc.get('n_pnl_trades')})")

    answered = good + premature
    if answered:
        print(f"[EXIT QUALITY] Of {answered} early exits the market has since answered: "
              f"{good} saved money ({good / answered * 100:.0f}%), {premature} were premature.")


def fetch_open_trades():
    """Fetch unresolved Forex setups from Supabase (since Oanda only trades Forex)."""
    url = f"{MEMORY_ENDPOINT}?outcome=eq.pending&verdict=in.(BUY,SELL)"
    try:
        r = httpx.get(url, headers=headers)
        if r.status_code == 200:
            trades = r.json()
            forex_symbols = set(cfg.data.instruments)
            return [t for t in trades if t.get("symbol") in forex_symbols]
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



# ---------------------------------------------------------------------------
#  Trade Management System (TMS) — Helpers
# ---------------------------------------------------------------------------

def _get_mt4_positions() -> list[dict]:
    """Read the live mt4_positions.json written by the EA every 500 ms.

    Returns a list of position dicts (may be empty if no open trades or file
    not found).  Each dict contains at least: ticket, symbol, volume,
    open_price, sl, tp, cmd, profit.
    """
    common_dir = cfg.execution.mt4.common_dir if hasattr(cfg.execution, "mt4") else None
    if not common_dir:
        return []
    pos_path = Path(common_dir) / "mt4_positions.json"
    if not pos_path.exists():
        return []
    try:
        return json.loads(pos_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _compute_atr_tms(df: pd.DataFrame, window: int = 14) -> float:
    """Compute ATR(window) from a standard OHLCV DataFrame."""
    try:
        high  = df["high"].values
        low   = df["low"].values
        close = df["close"].values
        prev  = np.concatenate([[close[0]], close[:-1]])
        tr    = np.maximum.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])
        atr   = float(pd.Series(tr).rolling(window, min_periods=1).mean().iloc[-1])
        return atr if np.isfinite(atr) and atr > 0 else 0.0
    except Exception:
        return 0.0


def _detect_volatility_squeeze(df: pd.DataFrame, bb_window: int = 20, kc_window: int = 20) -> bool:
    """Return True when Bollinger Bands are inside Keltner Channels (volatility squeeze).

    A squeeze means a big breakout move is building up.  During a squeeze we
    tighten the trail to protect any accumulated profit.
    """
    try:
        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        # Bollinger Bands
        bb_mid = close.rolling(bb_window).mean()
        bb_std = close.rolling(bb_window).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std

        # Keltner Channels (ATR-based)
        atr_series = _compute_atr_tms(df.tail(kc_window + 10), window=kc_window)
        kc_upper = bb_mid + 1.5 * atr_series
        kc_lower = bb_mid - 1.5 * atr_series

        # Squeeze when BB is inside KC
        in_squeeze = (bb_upper.iloc[-1] < kc_upper) and (bb_lower.iloc[-1] > kc_lower)
        return bool(in_squeeze)
    except Exception:
        return False


# TMS config constants (tunable)
_TMS_PARTIAL1_R       = 1.0    # Close 50 % of position at 1R profit
_TMS_PARTIAL1_PCT     = 0.50
_TMS_PARTIAL2_R       = 1.5    # Close another 25 % at 1.5R profit
_TMS_PARTIAL2_PCT     = 0.25
_TMS_BE_BUFFER_PIPS   = 0.0003  # ~3 pips breakeven buffer (0.3 pips for JPY pairs)
_TMS_CHANDELIER_MULT  = 2.0    # Handled natively by EA — Python only sends modify_sl on non-native trail
_TMS_SQUEEZE_MULT     = 1.0    # Tighten to 1×ATR from price during squeeze
_TMS_TIME_STOP_BARS   = {"15m": 24, "1h": 24, "1d": 10, "1w": 5}


def apply_trade_management(trade: dict, df: pd.DataFrame) -> None:
    """Apply all 5 TMS techniques to a single open trade.

    Techniques:
      1. Partial close (50 %) + move SL to breakeven at 1R profit.
      2. Second partial close (25 %) + lock 0.5R profit at 1.5R profit.
      3. ATR Chandelier trail  — delegated to EA natively; Python sends modify_sl
         when the chandelier level has risen above the current DB stop.
      4. Time-based exit       — close the whole position if stagnant for N bars.
      5. Volatility squeeze    — tighten trail to 1×ATR when squeeze is detected.

    All MT4 commands are written via the global ``_EXECUTOR`` (MT4Executor).
    Supabase state is updated in-place on ``trade`` for subsequent loops.
    """
    if _EXECUTOR is None:
        return

    try:
        symbol     = trade.get("symbol", "")
        trade_id   = trade.get("id", "")
        direction  = trade.get("verdict", "BUY").upper()
        entry      = _safe_float(trade.get("price")) or 0.0
        sl         = _safe_float(trade.get("stop_loss")) or 0.0
        tp         = _safe_float(trade.get("target_price")) or 0.0
        tf         = trade.get("timeframe", "1d")
        tf_mapped  = map_timeframe(tf)

        if entry <= 0 or sl <= 0 or tp <= 0 or df.empty:
            return

        # --- Compute key metrics ---
        risk_dist   = abs(entry - sl)
        if risk_dist <= 0:
            return

        current_price = float(df["close"].iloc[-1])
        pnl_dist = (current_price - entry) if direction in ("BUY", "LONG") else (entry - current_price)
        pnl_r    = pnl_dist / risk_dist          # Current profit in R units

        atr = _compute_atr_tms(df, window=14)

        # --- Retrieve state flags from Supabase record ---
        tms_meta       = trade.get("setup_features") or {}
        partial1_done  = tms_meta.get("tms_p1", False)
        partial2_done  = tms_meta.get("tms_p2", False)
        be_done        = tms_meta.get("tms_be", False)
        bars_open      = tms_meta.get("bars_open", 0) + 1  # increment each loop

        # --- Find live MT4 ticket ---
        mt4_sym    = _normalise_symbol(symbol)
        positions  = _get_mt4_positions()
        ticket     = None
        live_lots  = 0.0
        for pos in positions:
            pos_sym = pos.get("symbol", "").replace("-g", "").replace(".m", "").replace(".", "")
            clean   = mt4_sym.replace("-g", "").replace(".m", "").replace(".", "")
            if pos_sym.upper() == clean.upper():
                ticket    = pos.get("ticket")
                live_lots = float(pos.get("volume") or 0.0)
                break

        tms_log = tms_meta.get("tms_log", [])

        # ----------------------------------------------------------------
        # Technique 1: Partial 1 (50 %) + Move SL to Breakeven at 1R
        # ----------------------------------------------------------------
        if not partial1_done and pnl_r >= _TMS_PARTIAL1_R and ticket and live_lots > 0:
            close_lots = round(live_lots * _TMS_PARTIAL1_PCT, 2)
            if close_lots >= 0.01:
                try:
                    _EXECUTOR.partial_close(symbol=mt4_sym, ticket=ticket, volume=close_lots)
                    live_lots = round(live_lots - close_lots, 2)
                    partial1_done = True
                    entry_be = (entry + _TMS_BE_BUFFER_PIPS) if direction in ("BUY", "LONG") else (entry - _TMS_BE_BUFFER_PIPS)
                    tms_log.append({"action": "partial_close_50pct", "r": round(pnl_r, 2), "lots": close_lots})
                    print(f"  [TMS] P1: Closed {close_lots} lots of {symbol} at {pnl_r:.2f}R. Moving SL to breakeven.")
                except Exception as e:
                    print(f"  [TMS] P1 partial_close failed: {e}")

            # Move SL to breakeven (even if partial_close had a minor error, do the SL move)
            if not be_done and ticket:
                be_sl = (entry + _TMS_BE_BUFFER_PIPS) if direction in ("BUY", "LONG") else (entry - _TMS_BE_BUFFER_PIPS)
                try:
                    _EXECUTOR.modify_sl(symbol=mt4_sym, ticket=ticket, new_sl=round(be_sl, 5))
                    be_done = True
                    sl = be_sl  # update local reference
                    tms_log.append({"action": "breakeven_sl", "new_sl": round(be_sl, 5)})
                    print(f"  [TMS] Breakeven stop set to {be_sl:.5f} on {symbol}")
                except Exception as e:
                    print(f"  [TMS] Breakeven SL failed: {e}")

        # ----------------------------------------------------------------
        # Technique 2: Partial 2 (25 %) + Lock 0.5R at 1.5R
        # ----------------------------------------------------------------
        if partial1_done and not partial2_done and pnl_r >= _TMS_PARTIAL2_R and ticket and live_lots > 0:
            close_lots2 = round(live_lots * (_TMS_PARTIAL2_PCT / (1.0 - _TMS_PARTIAL1_PCT)), 2)  # 25% of original
            if close_lots2 >= 0.01:
                try:
                    _EXECUTOR.partial_close(symbol=mt4_sym, ticket=ticket, volume=close_lots2)
                    live_lots = round(live_lots - close_lots2, 2)
                    partial2_done = True
                    tms_log.append({"action": "partial_close_25pct", "r": round(pnl_r, 2), "lots": close_lots2})
                    print(f"  [TMS] P2: Closed {close_lots2} lots of {symbol} at {pnl_r:.2f}R.")
                except Exception as e:
                    print(f"  [TMS] P2 partial_close failed: {e}")

            # Lock 0.5R profit as new SL
            if partial2_done and ticket and atr > 0:
                half_r = risk_dist * 0.5
                locked_sl = (entry + half_r) if direction in ("BUY", "LONG") else (entry - half_r)
                if (direction in ("BUY", "LONG") and locked_sl > sl) or \
                   (direction in ("SELL", "SHORT") and locked_sl < sl):
                    try:
                        _EXECUTOR.modify_sl(symbol=mt4_sym, ticket=ticket, new_sl=round(locked_sl, 5))
                        sl = locked_sl
                        tms_log.append({"action": "lock_0.5R_sl", "new_sl": round(locked_sl, 5)})
                        print(f"  [TMS] SL locked at +0.5R = {locked_sl:.5f} on {symbol}")
                    except Exception as e:
                        print(f"  [TMS] Lock SL failed: {e}")

        # ----------------------------------------------------------------
        # Technique 3: ATR Chandelier Trail (Python assist after partial 1)
        # The EA handles native trailing every 500ms; Python sends a
        # modify_sl if the chandelier level has moved beyond the DB-stored SL.
        # ----------------------------------------------------------------
        if partial1_done and ticket and atr > 0 and len(df) >= 22:
            if direction in ("BUY", "LONG"):
                swing_high  = float(df["high"].rolling(22).max().iloc[-1])
                chandelier  = swing_high - (_TMS_CHANDELIER_MULT * atr)
                if chandelier > sl and chandelier < current_price:
                    try:
                        _EXECUTOR.modify_sl(symbol=mt4_sym, ticket=ticket, new_sl=round(chandelier, 5))
                        sl = chandelier
                        tms_log.append({"action": "chandelier_trail", "new_sl": round(chandelier, 5)})
                        print(f"  [TMS] Chandelier trail: SL -> {chandelier:.5f} on {symbol}")
                    except Exception as e:
                        print(f"  [TMS] Chandelier trail failed: {e}")
            else:
                swing_low  = float(df["low"].rolling(22).min().iloc[-1])
                chandelier = swing_low + (_TMS_CHANDELIER_MULT * atr)
                if chandelier < sl and chandelier > current_price:
                    try:
                        _EXECUTOR.modify_sl(symbol=mt4_sym, ticket=ticket, new_sl=round(chandelier, 5))
                        sl = chandelier
                        tms_log.append({"action": "chandelier_trail", "new_sl": round(chandelier, 5)})
                        print(f"  [TMS] Chandelier trail: SL -> {chandelier:.5f} on {symbol}")
                    except Exception as e:
                        print(f"  [TMS] Chandelier trail failed: {e}")

        # ----------------------------------------------------------------
        # Technique 4: Time-Based Exit (kill stagnant trades)
        # ----------------------------------------------------------------
        max_bars = _TMS_TIME_STOP_BARS.get(tf_mapped, 10)
        if bars_open > max_bars and pnl_r < 0.25 and ticket:
            print(f"  [TMS] Time Stop: {symbol} open {bars_open} bars, only {pnl_r:.2f}R. Closing.")
            try:
                _EXECUTOR.submit_order(symbol=mt4_sym, cmd="close", volume=live_lots or 0.1)
                tms_log.append({"action": "time_stop", "bars_open": bars_open, "pnl_r": round(pnl_r, 2)})
                # Will be resolved on next SL/TP check loop via MT4 sync
            except Exception as e:
                print(f"  [TMS] Time stop close failed: {e}")

        # ----------------------------------------------------------------
        # Technique 5: Volatility Squeeze — tighten trail to 1×ATR
        # ----------------------------------------------------------------
        if partial1_done and ticket and atr > 0 and _detect_volatility_squeeze(df):
            tight_trail = atr * _TMS_SQUEEZE_MULT
            if direction in ("BUY", "LONG"):
                tight_sl = current_price - tight_trail
                if tight_sl > sl and tight_sl < current_price:
                    try:
                        _EXECUTOR.modify_sl(symbol=mt4_sym, ticket=ticket, new_sl=round(tight_sl, 5))
                        sl = tight_sl
                        tms_log.append({"action": "squeeze_tighten", "new_sl": round(tight_sl, 5)})
                        print(f"  [TMS] Squeeze tighten: SL -> {tight_sl:.5f} on {symbol}")
                    except Exception as e:
                        print(f"  [TMS] Squeeze tighten failed: {e}")
            else:
                tight_sl = current_price + tight_trail
                if tight_sl < sl and tight_sl > current_price:
                    try:
                        _EXECUTOR.modify_sl(symbol=mt4_sym, ticket=ticket, new_sl=round(tight_sl, 5))
                        sl = tight_sl
                        tms_log.append({"action": "squeeze_tighten", "new_sl": round(tight_sl, 5)})
                        print(f"  [TMS] Squeeze tighten: SL -> {tight_sl:.5f} on {symbol}")
                    except Exception as e:
                        print(f"  [TMS] Squeeze tighten failed: {e}")

        # ----------------------------------------------------------------
        # Persist TMS state back to Supabase
        # ----------------------------------------------------------------
        new_tms_meta = {
            **tms_meta,
            "tms_p1": partial1_done,
            "tms_p2": partial2_done,
            "tms_be": be_done,
            "bars_open": bars_open,
            "current_sl": round(sl, 5),
            "tms_log": tms_log[-20:],   # keep last 20 actions
        }
        try:
            patch_url = f"{MEMORY_ENDPOINT}?id=eq.{trade_id}"
            httpx.patch(patch_url, headers=headers, json={"setup_features": new_tms_meta})
        except Exception as e:
            print(f"  [TMS] Failed to persist TMS state for {symbol}: {e}")

    except Exception as e:
        print(f"  [TMS] Unhandled error in apply_trade_management for {trade.get('symbol', '?')}: {e}")


def _normalise_symbol(symbol: str) -> str:
    """Convert internal symbol format to MT4-compatible ticker."""
    sym = symbol.upper().replace("/", "")
    suffix = cfg.execution.mt4.suffix if hasattr(cfg.execution, "mt4") and hasattr(cfg.execution.mt4, "suffix") else ""
    return f"{sym}{suffix}"


def open_new_trade(symbol, direction, entry_price, stop_loss, target_price, timeframe, confidence, rr, volume=None, style=None):
    """POST new trade entry to Supabase and dispatch to live executor."""
    trade_id = f"{symbol.upper()}_{int(time.time())}"
    
    payload = {
        "id": trade_id,
        "symbol": symbol.upper(),
        "asset_type": (
            "Equity" if symbol.upper() in EQUITIES_SET else
            ("Crypto" if "/" in symbol and any(c in symbol.upper() for c in ("BTC", "ETH", "AVAX", "SOL", "ADA", "DOGE", "XRP", "BNB")) else
             "Forex" if "/" in symbol else "Equity")
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
        "setup_features": {"auto": True, "style": style or "swing"},
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
                    # Compute TP1 = 1R from entry (50% partial target)
                    entry_p = float(entry_price)
                    sl_p    = float(stop_loss)
                    risk_1r = abs(entry_p - sl_p)
                    if mt4_cmd == "buy":
                        tp1_price = entry_p + risk_1r
                    else:
                        tp1_price = entry_p - risk_1r
                    tp1_vol = round((volume or 0.1) * 0.50, 2)

                    result = _EXECUTOR.submit_order(
                        symbol=mt4_symbol,
                        cmd=mt4_cmd,
                        volume=volume,
                        sl=float(stop_loss),
                        tp=float(target_price),
                        tp1=round(tp1_price, 5),
                        tp1_volume=max(tp1_vol, 0.01),
                        be_buffer=0.0003,
                        trail_atr_mult=2.0,
                        trail_lookback=22,
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

def safe_load_json(file_path: str, retries: int = 3, delay: float = 0.1):
    """Load JSON from a file with retries to avoid race conditions with MT4 writing."""
    for i in range(retries):
        try:
            with open(file_path, "r") as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except Exception:
            pass
        time.sleep(delay)
    with open(file_path, "r") as f:
        return json.load(f)

def fetch_live_account_state(default_equity=100000.0) -> tuple[float, float, float]:
    """Retrieve actual live account equity, balance, and peak balance/equity from Supabase or local MT4 file."""
    common_dir = cfg.execution.mt4.common_dir if hasattr(cfg.execution, "mt4") and hasattr(cfg.execution.mt4, "common_dir") else ""
    if common_dir:
        account_file = os.path.join(common_dir, "mt4_account.json")
        if os.path.exists(account_file):
            try:
                account_data = safe_load_json(account_file)
                eq = float(account_data.get("equity", default_equity))
                bal = float(account_data.get("balance", default_equity))
                start_bal = float(account_data.get("start_balance", default_equity))
                peak_eq = max(start_bal, bal, eq)
                if eq > 0 and bal > 0:
                    return eq, bal, peak_eq
            except Exception:
                pass
                
    # Fallback to Supabase
    url = f"{SUPABASE_URL}/rest/v1/apex_mt4_account?id=eq.1&select=*"
    try:
        r = httpx.get(url, headers=headers)
        if r.status_code == 200 and r.json():
            data = r.json()[0]
            eq = float(data.get("equity", default_equity))
            bal = float(data.get("balance", default_equity))
            start_bal = float(data.get("start_balance", default_equity))
            peak_eq = max(start_bal, bal, eq)
            if eq > 0 and bal > 0:
                return eq, bal, peak_eq
    except Exception as e:
        print(f"  [WARN] Failed to fetch live account stats from database: {e}")
        
    return default_equity, default_equity, default_equity

def get_quote_currency(symbol: str) -> str:
    """Extract quote currency from a symbol, handling various formats (EUR/USD, EURUSD, EURUSD-g)."""
    s = symbol.split("-")[0].split(".")[0].split("_")[0].upper().strip()
    if "/" in s:
        return s.split("/")[-1]
    if len(s) == 6:
        return s[3:]
    return "GBP"

def get_quote_to_account_rate(quote: str, account_currency: str = "GBP") -> float:
    """Retrieve the exchange rate converting 1 unit of quote currency to account currency."""
    if not quote or quote.upper() == account_currency.upper():
        return 1.0
    pair = f"{account_currency.upper()}/{quote.upper()}"
    try:
        start_dt = (datetime.utcnow() - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
        end_dt = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        df = clean(data_provider.get_history(pair, start=start_dt, end=end_dt, timeframe="1d"))
        if not df.empty:
            rate_gbp_quote = float(df["close"].iloc[-1])
            if rate_gbp_quote > 0:
                return 1.0 / rate_gbp_quote
    except Exception as e:
        print(f"  [WARN] Failed to fetch live rate for {pair}: {e}")
    # Static fallback values if database or provider fails
    fallbacks = {
        ("USD", "GBP"): 1.0 / 1.30,
        ("CHF", "GBP"): 1.0 / 1.14,
        ("CAD", "GBP"): 1.0 / 1.77,
        ("NZD", "GBP"): 1.0 / 2.10,
        ("JPY", "GBP"): 1.0 / 206.0,
        ("EUR", "GBP"): 1.0 / 0.84
    }
    return fallbacks.get((quote.upper(), account_currency.upper()), 1.0)

_correlation_matrix_cache = {}
_correlation_matrix_last_fetched = 0.0

def get_portfolio_correlation_matrix(lookback_days=30):
    """Compute rolling correlation matrix between core portfolio symbols using last lookback_days daily closes."""
    global _correlation_matrix_cache, _correlation_matrix_last_fetched
    now = time.time()
    if now - _correlation_matrix_last_fetched < 3600 and _correlation_matrix_cache:
        return _correlation_matrix_cache
        
    print("  [INFO] Computing live portfolio correlation matrix...")
    start_date = (datetime.utcnow() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
    unique_symbols = list(set([item["instrument"] for item in ROBUST_CORE_PORTFOLIO]))
    price_series = {}
    for sym in unique_symbols:
        try:
            df = clean(data_provider.get_history(sym, start=start_date, end=end_date, timeframe="1d"))
            if not df.empty:
                price_series[sym] = df["close"]
        except Exception as e:
            print(f"  [WARN] Failed to fetch correlation history for {sym}: {e}")
            
    if not price_series:
        return {}
        
    try:
        combined_df = pd.DataFrame(price_series).ffill().bfill()
        corr_matrix = combined_df.corr().to_dict()
        _correlation_matrix_cache = corr_matrix
        _correlation_matrix_last_fetched = now
        print(f"  [INFO] Portfolio correlation matrix computed successfully. Cache updated.")
        return corr_matrix
    except Exception as e:
        print(f"  [WARN] Error computing correlation matrix: {e}")
        return {}

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
    sl = _safe_float(t.get("stop_loss")) or 0.0
    tp = _safe_float(t.get("target_price")) or 0.0
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
        end_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
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
