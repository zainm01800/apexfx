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
from apex_quant.config import get_config, load_config
from apex_quant.data import local_setup_ledger, supabase_guard
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

# ── Web asset taxonomy (Crypto / Forex / ETF / Stock) ─────────────────────────
# The dashboard "Learning by setup" panel groups by asset_type; engine rows used
# to say "Equity" while web rows say Stock/ETF, splitting every stat in two. New
# engine rows use the web labels; the ETF set mirrors public/dashboard.js.
ETF_SYMBOLS = {
    "SPY", "QQQ", "IWM", "GLD", "SLV", "USO", "TLT", "HYG", "LQD", "XLF", "XLE",
    "XLK", "XLV", "XLI", "XLC", "ARKK", "VTI", "VOO", "VNQ", "EEM", "EFA", "GDX",
    "GDXJ", "XBI", "IBB", "DIA", "SMH", "SOXX",
}

def _web_asset_type(symbol: str) -> str:
    """Web taxonomy asset label for *symbol*: Crypto / Forex / ETF / Stock."""
    sym = (symbol or "").strip().upper()
    if sym in ETF_SYMBOLS:
        return "ETF"
    cls = cfg.asset_class_of(symbol)
    return {"forex": "Forex", "crypto": "Crypto", "equity": "Stock"}.get(cls, "Stock")

# ── IBKR ticket namespace ─────────────────────────────────────────────────────
# apex_research_memory.ticket joins to apex_mt4_trades.ticket for realized P&L.
# IBKR virtual tickets (permIds) live in the same int range as MT4 tickets, so
# they are offset into their own namespace before being written to the column:
# no collision with any real MT4 ticket, and the posterior's pnl join can never
# match an IBKR-resolved row to somebody else's MT4 trade (it falls back to the
# row's own setup_features.profit_pnl instead — see initialize_bayesian_sizer).
IBKR_TICKET_OFFSET = 9_000_000_000_000

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

#: THE CERTIFIED BOOK — "Book H + gold" (book_h_gold_252), 39 instruments, DAILY ONLY.
#: This is the universe every gated figure in data_store/ describes (£587/mo, Sharpe 0.893,
#: 12.0% forward p95 drawdown, ~11 trades/month at 0.75% risk). It is pinned in code, not
#: read from config.yaml, because the research/scan universe there grows freely (95 symbols
#: x 4 timeframes = 468 systems) and must never silently redefine what the live book trades.
#: Changing this list is a new pre-registered experiment, not an edit.
#:
#: NOTE ON MATIC/USD: the gate scripts' universe list contains 40 names, but MATIC/USD had no
#: cached 1d data at gate time, so only 39 instruments actually traded and every certified
#: figure is a 39-instrument result. It is omitted here deliberately — exactly as
#: run_paper_portfolio.py does via EXCLUDED — so that a future MATIC data fix cannot silently
#: widen the live book past what was gated.
BOOK_H_GOLD_39 = [
    # equities + UCITS ETFs (21)
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD", "PLTR", "TSM",
    "NFLX", "UBER", "ISWD.L", "ISDU.L", "ISDE.L", "XLK", "XLE", "XBI", "SMH", "SOXX",
    "SGLD.L",
    # crypto (11)
    "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD", "AVAX/USD",
    "DOGE/USD", "LINK/USD", "ARB/USD", "SUI/USD",
    # fx majors (7)
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD",
]


def build_book_portfolio() -> list[dict]:
    """The certified book as scan items: 39 instruments, 1d only, swing style."""
    return [{"instrument": s, "style": "swing", "timeframe": "1d"} for s in BOOK_H_GOLD_39]


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

    #: A primary-source frame older than this is treated as unusable and Yahoo is tried
    #: instead. Deliberately below the scan's own 36h "1d" staleness limit, so a stale
    #: primary is replaced BEFORE the scan rejects the instrument outright.
    STALE_AFTER_S = 30 * 3600

    @staticmethod
    def _age_seconds(df) -> float:
        """Age of the newest bar, or +inf when that cannot be determined."""
        try:
            last = df.index[-1]
            last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
            return (pd.Timestamp.now(tz="UTC") - last).total_seconds()
        except Exception:                                        # noqa: BLE001
            return float("inf")

    def get_history(self, instrument: str, start, end, timeframe):
        asset_class = cfg.asset_class_of(instrument)
        sym_clean = instrument.replace("_", "/")

        # Try the primary source for forex/crypto, then fall back to Yahoo.
        #
        # This used to accept ANY frame with >= 10 rows — checking row count but never
        # freshness, despite the comment claiming "fall back if OANDA returns no/stale data".
        # A populated-but-stale primary response was therefore returned intact, and the scan
        # then rejected the instrument as stale, so the fallback that would have supplied
        # fresh data never ran. Both conditions are now enforced.
        if (asset_class in ("forex", "crypto") and self.default_name == "oanda"
                and self.oanda is not None):
            try:
                df = self.oanda.get_history(sym_clean, start, end, timeframe)
                if df is not None and len(df) >= 10:
                    age = self._age_seconds(df)
                    if age <= self.STALE_AFTER_S:
                        return df
                    print(f"  [DATA] OANDA data for {instrument} ({timeframe}) is stale "
                          f"({age/3600:.1f}h) — trying Yahoo...")
                else:
                    print(f"  [DATA] OANDA returned insufficient data for {instrument} "
                          f"({timeframe}), falling back to Yahoo...")
            except Exception as e:
                print(f"  [DATA] OANDA failed for {instrument} ({timeframe}): {e} "
                      f"— falling back to Yahoo...")

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

    # Env wins over the cached config singleton so the rollback/migration
    # switch (and the smoke test's per-call override) works without a restart.
    provider = os.environ.get("APEX_EXECUTION__PROVIDER") or cfg.execution.provider
    if provider == "mt4":
        print(f"[EXECUTOR] Using MT4Executor (common_dir from config/env)")
        return MT4Executor()
    elif provider == "ibkr":
        try:
            from apex_quant.execution.ibkr_bridge import IBKRLiveBridge
            print("[EXECUTOR] Using IBKRLiveBridge (IBKR paper account)")
            bridge = IBKRLiveBridge()
            try:
                acct = bridge.connect()
                print(f"[EXECUTOR] IBKR connected — account {acct}")
            except Exception as e:
                # Clean degradation: return the unconnected bridge so the ledger
                # stays readable for resolution; order dispatch will fail loudly
                # per-call until the gateway is up, exactly like a dead MT4 dir.
                print(f"[EXECUTOR ERROR] IBKR connect failed: {e} — orders will fail until the gateway is up")
            return bridge
        except ImportError as e:
            print(f"[EXECUTOR ERROR] IBKRLiveBridge import failed: {e}. Falling back to MT4Executor.")
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

def _is_ibkr_executor() -> bool:
    """True when the live executor is the IBKR bridge (provider=ibkr)."""
    return type(_EXECUTOR).__name__ == "IBKRLiveBridge"

# Setups this process has already resolved via the IBKR ledger — guards the
# 5-second sync loop against re-PATCHing a setup between write and next fetch.
_resolved_setup_ids: set = set()

# ── Bayesian Sizer Global Setup ──
#: max_risk tracks config.yaml's `max_risk_per_trade` rather than a literal. RiskManager
#: step 4 caps the Bayesian output at that value anyway, so a hardcoded ceiling above it was
#: dead weight that would silently become live if the cap order ever changed. 2026-07-23:
#: config moved 1.00% -> 0.75% and this did not follow it, which is exactly that hazard.
_BAYESIAN_SIZER = BayesianRiskSizer(
    frac_kelly=0.25,
    min_risk=min(0.005, get_config().risk.max_risk_per_trade),
    max_risk=get_config().risk.max_risk_per_trade,
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

def calculate_virtual_equity(trades, initial_equity=300000.0, risk_pct=None):
    """Compute virtual compounded equity from historical trade performance.
    Defaults to $300k starting capital (three 100k accounts).

    ``risk_pct`` defaults to the CURRENT configured ``max_risk_per_trade``. Note this is a
    uniform-policy reconstruction ("what would this trade history be worth at today's
    sizing"), NOT a historical replay — real sizing was 2% before 2026-07-19, 1% until
    2026-07-23, 0.75% after. Pass an explicit value to reconstruct a specific regime.
    """
    if risk_pct is None:
        risk_pct = get_config().risk.max_risk_per_trade
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
        if tk is None:
            # Research/simulated outcome (web scan or unlinked expired setup) —
            # it informs lessons and panels, but must NEVER size or veto orders.
            # Only executed, ticket-linked trades feed the posterior.
            pending += 1
            continue
        pnl = None
        try:
            pnl = ticket_to_pnl.get(int(tk))
        except (ValueError, TypeError):
            pass
        if pnl is None:
            # Executed trade with no MT4 pnl join — IBKR-paper fills (ticket in
            # the IBKR_TICKET_OFFSET namespace, which can never join to
            # apex_mt4_trades) and MT4 rows outside the closed-trade fetch both
            # land here. Their resolver wrote the exact realized figure into
            # setup_features.profit_pnl from the broker/ledger record, so use
            # it rather than degrading the payoff estimate to "unknown".
            try:
                sf = t.get("setup_features") or {}
                if isinstance(sf, str):
                    sf = json.loads(sf)
                sf_pnl = sf.get("profit_pnl")
                if sf_pnl is not None:
                    pnl = float(sf_pnl)
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
    print(f"[BAYESIAN SIZER] Learned from {recorded} executed (ticket-linked) trades "
          f"(win rate {rate:.1f}%); {pending} research/pending rows excluded from sizing.")
    
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
    url = f"{MEMORY_ENDPOINT}?outcome=eq.pending&verdict=in.(BUY,SELL,LONG,SHORT,SPECULATIVE_BUY,SPECULATIVE_SELL,SPECULATIVE_LONG,SPECULATIVE_SHORT)"
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
    """Read the live open engine positions in ``mt4_positions.json`` shape.

    With ``provider=ibkr`` the rows come from the IBKRLiveBridge virtual-ticket
    ledger instead of the MT4 EA file (same shape: ticket, symbol, volume,
    open_price, sl, tp, cmd, profit). Otherwise reads the file the EA writes
    every 500 ms (may be empty if no open trades or file not found).
    """
    bridge_positions = getattr(_EXECUTOR, "get_positions_mt4", None)
    if callable(bridge_positions):
        try:
            return bridge_positions()
        except Exception as e:
            print(f"  [WARN] IBKR positions read failed: {e}")
            return []
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
                _EXECUTOR.submit_order(symbol=mt4_sym, cmd="close", volume=live_lots or 0.1, ticket=ticket)
                tms_log.append({"action": "time_stop", "bars_open": bars_open, "pnl_r": round(pnl_r, 2)})
                # Resolved on a later loop by the provider resolver (MT4 sync / IBKR ledger)
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


def open_new_trade(symbol, direction, entry_price, stop_loss, target_price, timeframe, confidence, rr, volume=None, style=None, regime=None):
    """POST new trade entry to Supabase and dispatch to live executor."""
    trade_id = f"{symbol.upper()}_{int(time.time())}"
    asset_label = _web_asset_type(symbol)

    payload = {
        "id": trade_id,
        "symbol": symbol.upper(),
        "asset_type": asset_label,
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
        "setup_features": {
            "auto": True,
            "style": style or "swing",
            "regime": regime or "unknown",
            "asset": asset_label,
        },
        "outcome": "pending"
    }

    # RECORD, THEN DISPATCH. The invariant is "never place an order you cannot account for" —
    # NOT "Supabase must be up". Gating dispatch on the cloud INSERT meant a database outage
    # silently dropped every signal (twelve in one scan during the 402 quota block) even though
    # IBKR was connected and the setup id is generated locally. Cloud first, durable local
    # ledger as fallback; dispatch proceeds if EITHER holds the record, and is abandoned only
    # when BOTH fail.
    recorded = False
    try:
        if supabase_guard.is_blocked():
            recorded = local_setup_ledger.record_setup(
                payload, reason="supabase quota breaker open")
            if recorded:
                print(f"  [LEDGER] Supabase blocked — setup {trade_id} recorded locally.")
        else:
            r = httpx.post(MEMORY_ENDPOINT, headers=headers, json=payload)
            if r.status_code in (200, 201, 204):
                recorded = True
            else:
                supabase_guard.note_response(r.status_code, cfg.execution.supabase_cooldown_s)
                recorded = local_setup_ledger.record_setup(
                    payload, reason=f"supabase HTTP {r.status_code}")
                print(f"  [LEDGER] Supabase INSERT failed ({r.status_code}) — setup "
                      f"{trade_id} recorded locally; order proceeds."
                      if recorded else
                      f"  [ABORT] Supabase INSERT failed ({r.status_code}) AND local ledger "
                      f"write failed — NOT dispatching {symbol}.")
    except Exception as e:                                        # noqa: BLE001
        recorded = local_setup_ledger.record_setup(payload, reason=f"exception: {e}")
        print(f"  [LEDGER] Supabase unreachable ({e}) — setup {trade_id} recorded locally."
              if recorded else
              f"  [ABORT] Supabase unreachable AND local ledger write failed — "
              f"NOT dispatching {symbol}.")

    try:
        if recorded:
            print(f"  [triggered] Logged new {direction} trade on {symbol} at entry {entry_price}")
            # Dispatch to live executor (MT4, ZMQ, IBKR, or mock) when enabled.
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

                    filled_patch = {"filled_at": int(time.time())}
                    if cfg.execution.provider == "ibkr" and _is_ibkr_executor() and result is not None:
                        # Fills handshake: the bridge binds a virtual ticket
                        # (IBKR permId) on a REAL fill. The ticket + fill price
                        # ride the same patch as filled_at so the setup's
                        # fill<->row linkage lands atomically; on a venue
                        # rejection NOTHING is stamped (no phantom fill) and the
                        # IBKR resolver expires the setup after the grace.
                        filled_patch = None
                        try:
                            ack = _EXECUTOR.wait_for_ack(
                                handle=result,
                                timeout_s=float(getattr(cfg.execution, "mt4_ack_timeout_s", 10.0)),
                            )
                            if ack and ack.get("ok") and ack.get("ticket") is not None:
                                filled_patch = {
                                    "filled_at": int(time.time()),
                                    "setup_features": {
                                        **payload["setup_features"],
                                        "mt4_ticket": int(ack["ticket"]),
                                        "fill_price": ack.get("fill_price"),
                                    },
                                }
                                print(f"  [EXECUTOR] IBKR virtual ticket {int(ack['ticket'])} "
                                      f"linked to setup {trade_id} (fill {ack.get('fill_price')})")
                            else:
                                print(f"  [EXECUTOR] IBKR order NOT filled (status={ack.get('status') if ack else 'no ack'}) "
                                      f"— setup stays pending; the IBKR resolver will expire it.")
                        except Exception as ack_err:
                            print(f"  [EXECUTOR WARN] IBKR fills handshake failed: {ack_err} "
                                  f"(resolver will fall back to the SL+TP signature)")
                    if filled_patch is not None:
                        try:
                            patch_url = f"{MEMORY_ENDPOINT}?id=eq.{trade_id}"
                            httpx.patch(patch_url, headers=headers, json=filled_patch)
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
        # `recorded` is False only when BOTH the cloud INSERT and the local ledger failed —
        # the one case where refusing to trade is correct. (Do not reference `r` here: on the
        # breaker path no request was made and it is unbound.)
        print(f"  [ABORT] No durable record for {symbol} (cloud AND local both failed) — "
              f"order not dispatched.")
    except Exception as e:
        print(f"Connection error creating new trade: {e}")
    return False


def _patch_ibkr_ticket(trade_id: str, ibkr_ticket: int) -> None:
    """Merge the IBKR virtual ticket into the setup's setup_features.mt4_ticket.

    This is the fill<->setup linkage for the IBKR resolver (the bridge ledger is
    keyed by the same id). The raw permId stays in setup_features; the namespaced
    form (IBKR_TICKET_OFFSET + permId) is only written to the `ticket` column at
    RESOLUTION time by resolve_closed_ibkr_setups.
    """
    try:
        r = httpx.get(f"{MEMORY_ENDPOINT}?id=eq.{trade_id}&select=setup_features", headers=headers, timeout=15)
        features = {}
        if r.status_code == 200 and r.json():
            features = r.json()[0].get("setup_features") or {}
            if isinstance(features, str):
                features = json.loads(features)
        features = {**features, "mt4_ticket": ibkr_ticket}
        pr = httpx.patch(f"{MEMORY_ENDPOINT}?id=eq.{trade_id}", headers=headers,
                         json={"setup_features": features})
        if pr.status_code in (200, 204):
            print(f"  [EXECUTOR] IBKR virtual ticket {ibkr_ticket} linked to setup {trade_id}")
        else:
            print(f"  [WARN] Failed to persist IBKR ticket on {trade_id}: {pr.status_code}")
    except Exception as e:
        print(f"  [WARN] Could not link IBKR ticket to {trade_id}: {e}")

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

#: Where the day's opening equity is anchored. MUST be persisted: this loop runs every ~900s
#: and the process can restart mid-session. If the anchor were held in memory, a restart after
#: a 3% loss would re-anchor at the CURRENT (already-down) equity, reset the measured daily
#: loss to zero, and re-enable trading — silently defeating the whole stop on exactly the day
#: it exists for.
DAILY_ANCHOR_PATH = ENGINE_DIR / "data_store" / "daily_equity_anchor.json"


def daily_equity_anchor(live_equity: float, now: "datetime | None" = None) -> float:
    """Equity at the start of the current UTC session, persisted across restarts.

    Returns ``live_equity`` itself on the first call of a new day (and writes it), otherwise
    the stored anchor. Any read/write failure degrades to ``live_equity``, which disables the
    daily check rather than blocking all trading on an I/O error.
    """
    from apex_quant.risk.daily_stop import read_anchor, resolve_anchor
    existing = read_anchor(DAILY_ANCHOR_PATH, now)
    anchor = resolve_anchor(DAILY_ANCHOR_PATH, live_equity, now)
    if existing is None:
        print(f"  [DAILY] session anchor set: £{anchor:,.2f}")
    return anchor


def enforce_daily_loss_stop(live_equity: float, open_trades: list) -> bool:
    """Check the daily-loss rule and act. Returns True when the session is halted.

    Blocking new entries is NOT sufficient — the positions already open are what carry the
    loss further. Flattening is therefore the correct behaviour, but it is an irreversible
    market action, so it is gated behind ``risk.daily_loss_flatten`` and defaults to OFF:
    without it this alerts loudly and blocks new entries only.
    """
    from apex_quant.risk.daily_stop import breached, daily_loss as _daily_loss
    limit = float(getattr(cfg.risk, "daily_loss_limit", 0.0) or 0.0)
    if limit <= 0.0:
        return False
    anchor = daily_equity_anchor(live_equity)
    if not breached(anchor, live_equity, limit):
        return False
    loss = _daily_loss(anchor, live_equity)

    print("=" * 72, flush=True)
    print(f"  [DAILY LOSS STOP] down {loss:.2%} today (anchor £{anchor:,.2f} -> "
          f"£{live_equity:,.2f}); limit {limit:.2%}. NO NEW ENTRIES this session.", flush=True)

    if not bool(getattr(cfg.risk, "daily_loss_flatten", False)):
        print("  [DAILY LOSS STOP] flatten is DISABLED (risk.daily_loss_flatten=false) — "
              "open positions are LEFT RUNNING and can breach the firm's limit. "
              "Enable it before trading a funded account.", flush=True)
        print("=" * 72, flush=True)
        return True

    for ot in (open_trades or []):
        sym = ot.get("symbol") or ot.get("instrument")
        if not sym:
            continue
        try:
            _EXECUTOR.close_position(symbol=sym)
            print(f"  [DAILY LOSS STOP] flatten -> {sym}", flush=True)
        except Exception as e:                                      # noqa: BLE001
            print(f"  [DAILY LOSS STOP] FAILED to close {sym}: {e}", flush=True)
    print("=" * 72, flush=True)
    return True


def fetch_live_account_state(default_equity=100000.0) -> tuple[float, float, float]:
    """Retrieve actual live account equity, balance, and peak balance/equity from Supabase or local MT4 file."""
    bridge_state = getattr(_EXECUTOR, "get_account_state", None)
    if callable(bridge_state):
        # IBKR paper account is the live book — size from the venue's own numbers
        # (the bridge keeps the running peak in its ledger for the DD breaker).
        try:
            eq, bal, peak = bridge_state()
            if eq > 0 and bal > 0:
                return eq, bal, max(peak, eq, bal)
        except Exception as e:
            print(f"  [WARN] IBKR account state failed, falling back to files/Supabase: {e}")
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
        end_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
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
        sl = _safe_float(t.get("stop_loss")) or 0.0
        tp = _safe_float(t.get("target_price")) or 0.0
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

    # --- TMS: apply dynamic trade management to all still-open trades ---
    # We reuse the already-fetched history_cache so no extra API calls are needed.
    if _EXECUTOR is not None:
        for t in open_trades:
            sym = t["symbol"]
            tf  = map_timeframe(t.get("timeframe", "1d"))
            key = (sym, tf)
            df  = history_cache.get(key)
            if df is not None and not df.empty:
                apply_trade_management(t, df)

def fetch_lessons_pool():
    try:
        url = f"{MEMORY_ENDPOINT}?select=symbol,verdict,outcome,lesson&lesson=not.is.null&limit=1000"
        r = httpx.get(url, headers=headers)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [WARN] Failed to load lessons pool: {e}")
    return []


def fetch_symbol_knowledge(symbol: str) -> str:
    """Fetch the synthesised strategic knowledge summary for this symbol.
    Returns plain text summary or empty string if not yet available."""
    try:
        clean = symbol.replace("/","").replace("-g","").upper()
        url = f"{SUPABASE_URL}/rest/v1/apex_symbol_knowledge?symbol=eq.{clean}&select=summary,n_trades,win_rate"
        r = httpx.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            rows = r.json()
            if rows:
                row = rows[0]
                summary = (row.get("summary") or "").strip()
                n = row.get("n_trades", 0)
                wr = row.get("win_rate", 0)
                if summary:
                    return f"[SYMBOL KNOWLEDGE: {symbol} | {n} trades | {wr*100:.0f}% win rate]\n{summary}"
    except Exception as e:
        print(f"  [WARN] Could not fetch symbol knowledge for {symbol}: {e}")
    return ""


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

def _strip_lesson_html(text: str) -> str:
    """Convert an HTML lesson to clean plain text for LLM consumption."""
    if not text:
        return ""
    text = _HTML_COMMENT_RE.sub("", text)   # strip <!-- TICKET_ID: … -->
    text = _HTML_TAG_RE.sub("", text)        # strip all HTML tags
    import html as _html_mod
    text = _html_mod.unescape(text)          # decode &amp; &lt; etc.
    # Collapse whitespace / multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return text


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
    against past lessons using the best available LLM (Gemini / Groq / DeepSeek)."""
    # KILL-SWITCH (audit A-C1). config.execution.llm_structural_veto defaults to FALSE
    # because the research verdict was DROP: these lessons invent thresholds from n=1 and
    # can flatten any signal. The switch was declared and documented as "the veto function
    # stays intact but only runs when this is explicitly switched on" — but nothing ever
    # checked it, so the veto ran unconditionally. On 2026-07-23 it vetoed DOGE, AVAX and
    # XRP on an is_volatility_spike flag while the flag was set to false.
    #
    # It also makes live diverge from the certified book: no gate script or backtester
    # applies this veto, so every trade it blocks is one the £587/mo result assumes taken.
    if not bool(getattr(cfg.execution, "llm_structural_veto", False)):
        return True, "structural veto disabled (execution.llm_structural_veto=false)"

    from apex_quant.ai.client import build_llm
    from apex_quant.ml.dataset import compute_feature_frame

    # 1. Initialize LLM — uses DeepSeek if key set, else falls back to Gemini → Groq
    llm = build_llm(cfg.ai)
    if not llm or not llm.available:
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
        
        flags_str = "\n".join([f"- {k}: {v}" for k, v in flags.items()])
        
        # 4. Fetch lessons from database (always — lessons can veto even clean markets)
        lessons_pool = fetch_lessons_pool()
        similar_lessons = get_similar_lessons(symbol, verdict, lessons_pool, limit=3)
        # Strip HTML so the LLM reads clean text, not markup
        symbol_lessons = [l for l in similar_lessons if l.get("symbol") == symbol]
        
        lessons_str = ""
        for idx, l in enumerate(similar_lessons):
            clean_lesson = _strip_lesson_html(l.get("lesson") or "")
            lessons_str += f"{idx+1}. [{l['symbol']} {l['verdict']} -> {l['outcome']}]: \"{clean_lesson[:300]}\"\n"

        # Fast-track ALLOW only when no risk flags AND no same-symbol lessons exist.
        # If we have lessons for this exact symbol, always consult the LLM so it can
        # veto based on a losing streak (e.g. 11 losses in a row on CAD/JPY).
        if not any(flags.values()) and not symbol_lessons:
            return True, "No risk flags triggered and no symbol-specific lessons."

        # 5. Fetch synthesised symbol knowledge (covers ALL historical trades, not just 3 lessons)
        symbol_knowledge = fetch_symbol_knowledge(symbol)

        # 6. Build prompt — knowledge summary goes first so it anchors the LLM's judgement
        prompt = f"""
We are considering executing a new {verdict} trade on {symbol}.

{f'HISTORICAL KNOWLEDGE BASE FOR {symbol} (synthesised from all past trades and lessons):{chr(10)}{symbol_knowledge}{chr(10)}' if symbol_knowledge else ''}
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

Recent individual trade lessons:
{lessons_str}

DIRECTIVE: Act as a hedge fund risk manager evaluating this trade.
VETO if any Pre-Calculated Risk Flag is True OR if the Historical Knowledge Base clearly shows this symbol/direction has a pattern of failure under current conditions.
ALLOW if the flags are clear and historical knowledge supports or is neutral on this setup.

Return ONLY a strict JSON object:
{{
  "verdict": "VETO" or "ALLOW",
  "reason": "1-sentence explanation referencing either the risk flag or the historical pattern"
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

def scan_single_asset(item, active_trades_map, corr_matrix=None):
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
        # Look back enough days for warmup and HTF MA trend calculation
        if tf in ("5m", "15m"):
            lookback_days = 30
        elif tf == "1h":
            lookback_days = 350
        elif tf == "1d":
            lookback_days = 400
        else: # 1w
            lookback_days = 1000
            
        start_date = (datetime.utcnow() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
        df = clean(data_provider.get_history(sym, start=start_date, end=end_date, timeframe=tf))
        if len(df) < params["warmup"] + 15:
            return
            
        pit = PointInTimeAccessor(df)
        base_strat = RegimeGatedMomentum(
            momentum_lookback=params["momentum_lookback"],
            vol_window=params["vol_window"],
            holding_horizon=params["holding_horizon"],
            reward_risk=params["reward_risk"],
            regime_method="rule_based",
            timeframe=tf,
            bypass_calibration=True,
            instrument=sym
        )
        
        # Determine HTF mapping
        htf_rule = None
        htf_ma_window = 200
        if tf == "15m":
            htf_rule = "1h"
        elif tf == "1h":
            htf_rule = "1d"
        elif tf == "1d":
            htf_rule = "1w"
            htf_ma_window = 50
            
        strat = MultiTimeframeMomentum(
            base_strategy=base_strat,
            htf_rule=htf_rule,
            htf_ma_window=htf_ma_window,
            instrument=sym
        )
        strat.fit(pit, df.index[:-1])
        
        # Evaluate latest signal
        latest_time = df.index[-1]
        
        # Check if the data is stale
        now_utc = datetime.utcnow()
        latest_time_utc = latest_time.tz_convert("UTC") if latest_time.tzinfo else latest_time.tz_localize("UTC")
        age_seconds = (now_utc - latest_time_utc.replace(tzinfo=None)).total_seconds()
        
        # Max allowed age based on timeframe.
        # Forex cross-pairs (GBP/JPY, CHF/JPY, etc.) can have data delays on Yahoo Finance
        # so we use wider windows for intraday forex to avoid false [SKIPPED] during live hours.
        is_fx_instrument = "/" in sym
        max_age = {
            "5m":  1200  if not is_fx_instrument else  7200,   # 20min / 2hrs
            "15m": 2700  if not is_fx_instrument else 14400,   # 45min / 4hrs
            "1h":  10800 if not is_fx_instrument else 21600,   # 3hrs  / 6hrs
            "1d":  129600,   # 36 hours (handles weekend close / daily data lag)
            "1w":  691200    # 8 days
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
            # Determine if this trade was opened by the engine (auto) or by the EA/manually
            setup_features = active_trade.get("setup_features") or {}
            if isinstance(setup_features, str):
                try:
                    import json as _json
                    setup_features = _json.loads(setup_features)
                except Exception:
                    setup_features = {}
            engine_owned = bool(setup_features.get("auto", False))

            # Recheck logic for existing open trade
            trade_verdict = active_trade["verdict"].upper()
            sig_dir = sig.direction.value.upper()
            
            # Map signal to actions
            if sig_dir == "FLAT":
                if engine_owned:
                    print(f"  [VALIDATION] {sym} ({tf}) -> Signal is FLAT. Engine-owned trade: suggesting early close.")
                    add_validation_to_trade(active_trade, "CLOSE_TRADE", 100, assessment="invalidated")
                    
                    # Execute MT4 exit only for engine-owned positions
                    if _EXECUTOR is not None:
                        mt4_symbol = _normalise_symbol(sym)
                        try:
                            _EXECUTOR.close_position(symbol=mt4_symbol)
                            print(f"  [EXECUTOR] Position closed for {mt4_symbol}")
                        except Exception as ex:
                            print(f"  [EXECUTOR WARN] Failed to close position on MT4: {ex}")
                            
                    resolve_trade(active_trade["id"], "invalidated", float(df["close"].iloc[-1]), datetime.utcnow().isoformat())
                    return
                else:
                    # EA/manual trade — signal went flat but we leave it alone to hit SL/TP naturally
                    print(f"  [VALIDATION] {sym} ({tf}) -> Signal is FLAT. EA-managed trade, leaving to run to SL/TP.")
                    add_validation_to_trade(active_trade, "HOLD_TRADE", 50, assessment="ea_managed")
                    return
                
            elif (trade_verdict in ("BUY", "LONG") and sig_dir == "SHORT") or \
                 (trade_verdict in ("SELL", "SHORT") and sig_dir == "LONG"):
                if engine_owned:
                    # Reversal signal on engine-owned trade — close and flip.
                    print(f"  [VALIDATION] {sym} ({tf}) -> Reversal signal ({sig_dir}). Engine-owned trade: closing and flipping.")
                    add_validation_to_trade(active_trade, "CLOSE_TRADE", int(sig.probability * 100), assessment="invalidated")
                    
                    # Execute MT4 exit only for engine-owned positions
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
                    # EA/manual trade going against current signal — note it but don't interfere
                    print(f"  [VALIDATION] {sym} ({tf}) -> Reversal signal ({sig_dir}) vs EA-managed {trade_verdict}. Leaving EA trade to run.")
                    add_validation_to_trade(active_trade, "HOLD_TRADE", int(sig.probability * 100), assessment="ea_managed_reversal")
                    return
                
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
            
            # Fetch live account state (equity, balance, peak_equity)
            live_equity, live_balance, live_peak_equity = fetch_live_account_state()
                 
            # Fetch resolved trades history to compound virtual equity curve (for comparison)
            all_resolved_trades = fetch_resolved_trades_for_equity()
            virtual_equity, peak_equity = calculate_virtual_equity(all_resolved_trades)

            open_trades_list = fetch_open_trades()

            # DAILY-LOSS STOP — checked before anything is sized. Halts the session (and
            # flattens, if risk.daily_loss_flatten is on) rather than relying on the
            # from-peak breaker, which cannot see a bad day that began at a fresh high.
            # `return`, not `continue`: this is scan_single_asset(), called per instrument,
            # not a loop body. Returning skips THIS instrument; the stop fires again for
            # every other instrument in the cycle, so the whole session is blocked.
            if enforce_daily_loss_stop(live_equity, open_trades_list):
                return

            # Filter out Forex setups that are not actually open on the venue
            # (MT4 file under provider=mt4, the bridge ledger under provider=ibkr)
            active_mt4_symbols = set()
            try:
                for p in (_get_mt4_positions() or []):
                    p_sym = p.get("symbol", "").replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()
                    active_mt4_symbols.add(p_sym)
            except Exception:
                pass
            
            filtered_open_trades = []
            for ot in open_trades_list:
                sym_ot = ot["symbol"]
                asset_class_ot = cfg.asset_class_of(sym_ot)
                if asset_class_ot == "forex":
                    clean_sym = sym_ot.replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()
                    if clean_sym not in active_mt4_symbols:
                        # Skip this Forex setup because it is not active on MT4
                        continue
                filtered_open_trades.append(ot)
            open_trades_list = filtered_open_trades

            open_positions = []

            for ot in open_trades_list:
                sym_ot = ot["symbol"]
                price_ot = _safe_float(ot.get("price")) or 0.0
                sl_ot = _safe_float(ot.get("stop_loss"))
                asset_class_ot = cfg.asset_class_of(sym_ot)
                
                quote_ot = get_quote_currency(sym_ot)
                rate_ot = get_quote_to_account_rate(quote_ot, "GBP")
                
                risk_gbp = 0.0
                if sl_ot and abs(price_ot - sl_ot) > 1e-6:
                    stop_dist_ot_gbp = abs(price_ot - sl_ot) * rate_ot
                    risk_cap = cfg.risk.max_risk_per_trade * live_equity
                    units = risk_cap / stop_dist_ot_gbp if stop_dist_ot_gbp > 0 else 1000.0
                    if asset_class_ot == "forex":
                        units = min(units, 500000.0)
                    else:
                        units = min(units, 1000.0)
                    trade_notional = units * (price_ot * rate_ot)
                    risk_gbp = units * stop_dist_ot_gbp
                else:
                    price_ot_gbp = price_ot * rate_ot
                    if asset_class_ot == "forex":
                        trade_notional = price_ot_gbp * 10000.0
                    else:
                        trade_notional = price_ot_gbp * 1.0
                        
                open_positions.append(OpenPosition(
                    instrument=sym_ot,
                    direction=Direction.LONG if ot["verdict"] in ("BUY", "LONG") else Direction.SHORT,
                    notional=trade_notional,
                    risk=risk_gbp,
                    timeframe=map_timeframe(ot.get("timeframe", "1h")),
                ))
            
            account_state = AccountState(
                equity=live_equity,
                peak_equity=live_peak_equity,
                open_positions=open_positions,
                # The prop daily rule is measured from the session's OPENING equity, which
                # the from-peak breaker cannot see. RiskManager step 0.5 vetoes on this.
                day_start_equity=daily_equity_anchor(live_equity) or None,
            )
            
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
                
                # 2. Volatility estimate via Yang-Zhang
                yz_vol_calc = YangZhangVol(window=21)
                ann_vol = yz_vol_calc.compute(pit, latest_time)
                if not np.isfinite(ann_vol) or ann_vol <= 0:
                    ann_vol = 0.20 # 20% default
                
                quote_cand = get_quote_currency(sym)
                rate_cand = get_quote_to_account_rate(quote_cand, "GBP")
                
                market_state = MarketState(
                    instrument=sym,
                    price=close_p,
                    ann_vol=ann_vol,
                    atr=atr,
                    quote_to_account_rate=rate_cand,
                    correlations=(corr_matrix or {}).get(sym, {})
                )
                
                # 3. Create signal
                direction_enum = Direction.LONG if sig.direction.value.upper() == "LONG" else Direction.SHORT
                risk_sig = Signal(
                    instrument=sym,
                    direction=direction_enum,
                    probability=sig.probability,
                    reward_risk=sig.reward_risk,
                    confidence=getattr(sig, 'confidence', 0.5),
                    rationale=getattr(sig, 'rationale', ""),
                    timeframe=tf,  # pass timeframe for per-bucket slot check
                )
                
                # 4. Permit through Risk Manager
                live_risk_cfg = cfg.risk.model_copy(update={"min_position": cfg.execution.live_min_position})
                risk_manager = RiskManager(live_risk_cfg, bayesian_sizer=_BAYESIAN_SIZER)
                permitted_pos = risk_manager.permit(risk_sig, account_state, market_state, t=latest_time)
                
                if not permitted_pos.permitted:
                    print(f"  [RISK VETO] Risk manager vetoed trade for {sym}: {permitted_pos.rationale}. Sizing details: {permitted_pos.sizing_detail}")
                    return
                
                # Convert units to lots
                cost_model = cfg.mechanics_for(sym).cost_model if hasattr(cfg, 'mechanics_for') else 'pips'
                raw_lots = units_to_lots(sym, permitted_pos.units, cost_model)
                
                # Apply lot-step rounding (Priority 3)
                from apex_quant.risk.sizing import round_lot_size
                sized_volume = round_lot_size(raw_lots, min_lot=0.01, lot_step=0.01)
                
                if sized_volume <= 0.0:
                    print(f"  [RISK VETO] Rounded lot size {sized_volume} is below min_lot (0.01) for {sym} (raw: {raw_lots:.4f} lots). Vetoing entry.")
                    return
                
                print(f"  [RISK SIZED] Bayesian Risk Manager allocated {permitted_pos.risk_fraction:.2%} risk. "
                      f"Live Equity: £{live_equity:,.2f} (Drawdown: {account_state.drawdown:.2%}). Lots: {sized_volume} (raw: {raw_lots:.4f}).")
            except Exception as re:
                print(f"  [WARN] Risk manager sizing failed, fallback to defaults: {re}")
                import traceback
                traceback.print_exc()
                sized_volume = None
            # ──────────────────────────────────────
            
            # Regime at entry — the rule-based classifier the strategy itself
            # gates on, persisted so "Learning by setup" can group by it.
            regime_name = None
            try:
                regime_name = base_strat._regime.classify(pit, latest_time).name
            except Exception:
                regime_name = None

            open_new_trade(
                symbol=sym,
                direction=sig.direction.value,
                entry_price=close_p,
                stop_loss=sl,
                target_price=tp,
                timeframe=tf,
                confidence=int(sig.probability * 100),
                rr=sig.reward_risk,
                volume=sized_volume,
                style=style,
                regime=regime_name,
            )

    except Exception as e:
        print(f"  Error scanning {sym}: {e}")

def is_asset_in_active_session(symbol: str) -> bool:
    """Determine if a given symbol is currently within its primary liquid trading hours (London time).
    
    Session Rules (London Time / Europe/London):
    1. Cryptos (BTC, ETH, etc.): Active 24/7.
    2. JPY/AUD/NZD Crosses: Active 24/5, except the 9:00 PM to 10:00 PM rollover dead zone.
    3. US Equities/ETFs/Gold (AAPL, SPY, GLD, etc.): Active 2:30 PM to 9:30 PM.
    4. Western Forex (EUR/USD, GBP/USD, etc.): Active 8:00 AM to 10:00 PM, except the 9:00 PM to 10:00 PM rollover dead zone.
    """
    now_london = datetime.now(ZoneInfo("Europe/London"))
    h = now_london.hour
    m = now_london.minute
    sym_upper = symbol.upper()
    
    # 1. Category A: Cryptos (Active 24/7)
    cryptos = ["BTC", "ETH", "SOL", "SUI", "ADA", "AVAX", "LINK", "XRP", "ARB", "MATIC", "DOGE", "BNB"]
    is_crypto = any(crypto in sym_upper for crypto in cryptos)
    if is_crypto:
        return True
        
    # Check if we are in the daily rollover dead zone (10:00 PM to 11:00 PM UK Time / 22:00 to 22:59)
    # 5:00 PM New York time is exactly 10:00 PM London time in both GMT and BST.
    # We avoid opening new trades during this hour due to massive spread widening.
    if h == 22:
        return False
        
    # Friday close protection: Avoid opening new trades after 8:00 PM London time on Fridays
    # to protect against weekend gap risk (market closes at 10 PM London time / 5 PM NY time).
    weekday = now_london.weekday()
    if weekday == 4 and h >= 20:
        return False
        
    # 2. Category C: JPY & Asia-Pacific Forex Pairs (JPY, AUD, NZD)
    # Active 24 hours a day (except rollover / Friday close handled above)
    has_asia_pac = any(ccy in sym_upper for ccy in ["JPY", "AUD", "NZD"])
    if has_asia_pac:
        return True
        
    # 3. Category B: US Equities, Commodities, and Index ETFs
    # Active US Hours: 2:30 PM (14:30) to 9:30 PM (21:30) London Time.
    equities_etfs = ["AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD", "PLTR", "SPY", "QQQ", "SMH", "SOXX", "GLD", "XBI", "XLK", "XLE", "XLF"]
    is_equity_etf = any(eq in sym_upper for eq in equities_etfs)
    if is_equity_etf:
        return 14 <= h < 21 or (h == 14 and m >= 30)
        
    # 4. Category D: Western Forex Pairs (EUR/USD, GBP/USD, USD/CHF, USD/CAD, EUR/GBP, etc.)
    # Active London + NY hours: 8:00 AM (08:00) to 10:00 PM (22:00) London Time.
    return 8 <= h < 22

def scan_robust_core(open_trades):
    """Scan all systems for new entry signals concurrently."""
    live_tfs = cfg.data.live_timeframes
    filtered_portfolio = []
    skipped_tfs = set()
    for item in ROBUST_CORE_PORTFOLIO:
        tf = str(item.get("timeframe", "1d")).lower()
        if live_tfs is not None:
            allowed_tfs = [x.lower() for x in live_tfs]
            if tf not in allowed_tfs:
                skipped_tfs.add(tf)
                continue
        filtered_portfolio.append(item)
        
    for tf in sorted(list(skipped_tfs)):
        print(f"  [INFO] Skipping scan for timeframe '{tf}' (not in data.live_timeframes)")
        
    active_items = [item for item in filtered_portfolio if is_asset_in_active_session(item["instrument"])]
    skipped_items = [item for item in filtered_portfolio if not is_asset_in_active_session(item["instrument"])]
    
    if skipped_items:
        print(f"\n  [INFO] Gating: Skipping new trade scans for {len(skipped_items)} systems currently outside session hours (Western Forex/US Equities).")
        
    print(f"\nScanning {len(active_items)} Robust Core systems in parallel...")
    active_trades_map = {(t["symbol"].upper(), str(t.get("timeframe", "1d")).lower()): t for t in open_trades}

    # Also include ALL pending Supabase setups (equities, crypto, forex) so the engine
    # never generates a duplicate setup for a symbol/timeframe already queued.
    try:
        pending_url = f"{MEMORY_ENDPOINT}?outcome=eq.pending&verdict=in.(BUY,SELL,LONG,SHORT,SPECULATIVE_BUY,SPECULATIVE_SELL,SPECULATIVE_LONG,SPECULATIVE_SHORT)"
        pending_r = httpx.get(pending_url, headers=headers)
        if pending_r.status_code == 200:
            for ps in pending_r.json():
                key = (ps["symbol"].upper(), str(ps.get("timeframe", "1d")).lower())
                if key not in active_trades_map:
                    active_trades_map[key] = ps
    except Exception as _e:
        print(f"  [WARN] Could not fetch pending setups for dedup check: {_e}")
    
    try:
        corr_matrix = get_portfolio_correlation_matrix()
    except Exception as e:
        print(f"  [WARN] Failed to compute portfolio correlation matrix: {e}")
        corr_matrix = {}
        
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(scan_single_asset, item, active_trades_map, corr_matrix) for item in active_items]
        for _ in as_completed(futures):
            pass

_style_map_cache = {}
_style_map_last_fetched = 0.0

def sync_mt4_trades(silent=False):
    """Sync live open positions and closed history from MT4 shared files to Supabase."""
    global _style_map_cache, _style_map_last_fetched
    common_dir = cfg.execution.mt4.common_dir if hasattr(cfg.execution, "mt4") and hasattr(cfg.execution.mt4, "common_dir") else ""
    if not common_dir:
        return
        
    positions_file = os.path.join(common_dir, "mt4_positions.json")
    history_file = os.path.join(common_dir, "mt4_history.json")
    
    headers_upsert = {
        **headers,
        "Prefer": "resolution=merge-duplicates"
    }
    
    def get_clean_symbol(sym):
        return sym.replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()
        
    # Fetch recent analyses to match style (scalp, intraday, swing) once every 15 minutes
    scans_list = []
    now_ts = time.time()
    if not _style_map_cache or (now_ts - _style_map_last_fetched > 900):
        try:
            r = httpx.get(f"{SUPABASE_URL}/rest/v1/apex_research_memory?select=symbol,timeframe,setup_features,stop_loss,target_price&order=analysis_date.desc&limit=150", headers=headers, timeout=10.0)
            if r.status_code == 200:
                scans_list = r.json()
                _style_map_cache = scans_list
                _style_map_last_fetched = now_ts
            else:
                scans_list = _style_map_cache
        except Exception as e:
            scans_list = _style_map_cache
            if not silent:
                print(f"  [WARN] Failed to fetch recent analyses for style matching: {e}")
    else:
        scans_list = _style_map_cache

    def get_style_for_trade(p_sym, p_sl, p_tp):
        p_clean = get_clean_symbol(p_sym)
        best_scan = None
        best_score = float('inf')
        
        import re
        def parse_fl(val):
            if val is None: return 0.0
            if isinstance(val, (int, float)): return float(val)
            val_str = str(val).strip()
            if not val_str: return 0.0
            try: return float(val_str)
            except ValueError:
                m = re.findall(r'[-+]?\d*\.\d+|\d+', val_str)
                return float(m[0]) if m else 0.0

        for scan in scans_list:
            s_clean = get_clean_symbol(scan.get("symbol", ""))
            if s_clean != p_clean:
                continue
            s_sl = parse_fl(scan.get("stop_loss"))
            s_tp = parse_fl(scan.get("target_price"))
            
            # Match score by relative difference in SL and TP
            rel_diff_sl = abs(s_sl - p_sl) / (s_sl if s_sl > 0 else 1.0)
            rel_diff_tp = abs(s_tp - p_tp) / (s_tp if s_tp > 0 else 1.0)
            score = rel_diff_sl + rel_diff_tp
            if score < best_score:
                best_score = score
                best_scan = scan
                
        if best_scan and best_score < 0.01:
            sf = best_scan.get("setup_features") or {}
            if isinstance(sf, str):
                try: sf = json.loads(sf)
                except: sf = {}
            style = sf.get("style", "")
            if not style:
                tf = best_scan.get("timeframe", "1d")
                if tf == "1d": style = "swing"
                elif tf == "1h": style = "intraday"
                elif tf == "15m": style = "scalp"
                else: style = "swing"
            return style.lower()
            
        return "swing"
        
    # 1. Sync Open Positions
    if os.path.exists(positions_file):
        try:
            positions = safe_load_json(positions_file)
            for p in positions:
                p["status"] = "open"
                magic = p.get("magic", 0)
                if magic != 88888:
                    p["style"] = "manual"
                else:
                    p["style"] = get_style_for_trade(p.get("symbol", ""), float(p.get("sl", 0.0)), float(p.get("tp", 0.0)))
            if positions:
                r = httpx.post(f"{SUPABASE_URL}/rest/v1/apex_mt4_trades", headers=headers_upsert, json=positions)
                if r.status_code not in (200, 201, 204):
                    supabase_guard.note_response(r.status_code, cfg.execution.supabase_cooldown_s)
                    print(f"  [WARN] Failed to sync open positions to Supabase: {r.text}")
                elif not silent:
                    print(f"  [INFO] Synced {len(positions)} open positions from MT4 to Supabase.")
        except Exception as e:
            print(f"  [WARN] Error syncing open positions: {e}")
            
    # 2. Sync Closed History
    if os.path.exists(history_file):
        try:
            closed_trades = safe_load_json(history_file)
            for c in closed_trades:
                c["status"] = "closed"
                magic = c.get("magic", 0)
                if magic != 88888:
                    c["style"] = "manual"
                else:
                    c["style"] = get_style_for_trade(c.get("symbol", ""), float(c.get("sl", 0.0)), float(c.get("tp", 0.0)))
            if closed_trades:
                r = httpx.post(f"{SUPABASE_URL}/rest/v1/apex_mt4_trades", headers=headers_upsert, json=closed_trades)
                if r.status_code not in (200, 201, 204):
                    supabase_guard.note_response(r.status_code, cfg.execution.supabase_cooldown_s)
                    print(f"  [WARN] Failed to sync closed history to Supabase: {r.text}")
                elif not silent:
                    print(f"  [INFO] Synced {len(closed_trades)} closed history trades from MT4 to Supabase.")
        except Exception as e:
            print(f"  [WARN] Error syncing closed history: {e}")

    # 3. Sync Account Info
    account_file = os.path.join(common_dir, "mt4_account.json")
    if os.path.exists(account_file):
        try:
            account_data = safe_load_json(account_file)
            account_data["id"] = 1
            account_data["updated_at"] = datetime.utcnow().isoformat()
            r = httpx.post(f"{SUPABASE_URL}/rest/v1/apex_mt4_account", headers=headers_upsert, json=[account_data])
            if r.status_code not in (200, 201, 204):
                supabase_guard.note_response(r.status_code, cfg.execution.supabase_cooldown_s)
                print(f"  [WARN] Failed to sync account info to Supabase: {r.text}")
            elif not silent:
                print(f"  [INFO] Synced live MT4 account stats to Supabase.")
        except Exception as e:
            print(f"  [WARN] Error syncing account info: {e}")

_TICKET_COLUMN_OK = None


def _memory_has_ticket_column() -> bool:
    """Detect once whether apex_research_memory has the `ticket` column.

    A real ticket column is the permanent fix for setup<->trade linkage: it lets the
    dashboard join a card to its post-mortem exactly, instead of re-deriving the link
    from an SL/TP signature. Until the column is added (see scripts/backfill_tickets.py
    for the one-line DDL) we simply skip writing it, so live resolution keeps working
    unchanged on the older schema.
    """
    global _TICKET_COLUMN_OK
    if _TICKET_COLUMN_OK is None:
        try:
            r = httpx.get(f"{MEMORY_ENDPOINT}?select=id,ticket&limit=1", headers=headers, timeout=15)
            _TICKET_COLUMN_OK = r.status_code == 200
        except Exception:
            _TICKET_COLUMN_OK = False
        if not _TICKET_COLUMN_OK:
            print("  [INFO] apex_research_memory.ticket column not present — skipping ticket "
                  "persistence (see scripts/backfill_tickets.py to enable exact card matching).")
    return _TICKET_COLUMN_OK


def resolve_closed_mt4_setups():
    """Look at MT4 positions and history files to automatically resolve pending setups."""
    common_dir = cfg.execution.mt4.common_dir if hasattr(cfg.execution, "mt4") and hasattr(cfg.execution.mt4, "common_dir") else ""
    if not common_dir:
        return
        
    positions_file = os.path.join(common_dir, "mt4_positions.json")
    history_file = os.path.join(common_dir, "mt4_history.json")
    
    mt4_active_symbols = set()
    if os.path.exists(positions_file):
        try:
            positions = safe_load_json(positions_file)
            for p in positions:
                sym = p.get("symbol", "")
                clean = sym.replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()
                mt4_active_symbols.add(clean)
        except Exception as e:
            print(f"  [WARN] Error reading MT4 positions for setup resolution: {e}")
            
    mt4_history = []
    if os.path.exists(history_file):
        try:
            mt4_history = safe_load_json(history_file)
        except Exception as e:
            print(f"  [WARN] Error reading MT4 history for setup resolution: {e}")

    # Broker clock vs UTC (config; warns if this batch proves it wrong).
    broker_offset = mt4_utc_offset_seconds(mt4_history)
            
    # Fetch pending setups
    url = f"{MEMORY_ENDPOINT}?outcome=eq.pending&verdict=in.(BUY,SELL,LONG,SHORT,SPECULATIVE_BUY,SPECULATIVE_SELL,SPECULATIVE_LONG,SPECULATIVE_SHORT)"
    try:
        r = httpx.get(url, headers=headers)
        if r.status_code != 200:
            return
        pending = r.json()
    except Exception as e:
        print(f"  [WARN] Connection error fetching pending setups: {e}")
        return
        
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    for s in pending:
        s_id = s["id"]
        sym = s["symbol"]
        clean_sym = sym.replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()
        created_at_str = s.get("created_at")
        verdict = s.get("verdict", "BUY").upper()
        
        try:
            clean_ts = created_at_str.replace("Z", "+00:00")
            setup_dt = datetime.fromisoformat(clean_ts)
            setup_timestamp = setup_dt.timestamp()
            age_seconds = (now_utc - setup_dt).total_seconds()
        except Exception:
            setup_timestamp = 0.0
            age_seconds = 600.0
            
        # Give MT4 at least 3 minutes to fetch and open the trade
        if age_seconds < 180:
            continue
            

            
        # Try to match in MT4 history by open time proximity
        # Match this setup to its MT4 trade by its EXACT SL+TP signature.
        #
        # The engine SENDS sl/tp with the order and MT4 records them verbatim, so
        # (symbol, direction, sl, tp) identifies which trade a setup became. Measured
        # on live data: a 0.1-pip tolerance uniquely identifies ~89% of trades, and
        # LOOSENING it makes matching worse, not better.
        #
        # The previous rule — first symbol+direction trade opened within 12h — picked
        # an arbitrary trade whenever several were open on the same pair. That is the
        # origin of the corruption: each setup got bound to a NEIGHBOURING trade's
        # ticket, so post-mortems quoted another trade's exit price and P&L.
        s_sl = float(s.get("stop_loss") or 0.0)
        s_tp = float(s.get("target_price") or 0.0)
        s_price = float(s.get("price") or 0.0)
        pip = 0.01 if "JPY" in clean_sym else 0.0001
        sig_tol = 0.1 * pip

        matched_history = None
        if s_sl > 0 and s_tp > 0:
            candidates = []
            for h in mt4_history:
                h_sym = h.get("symbol", "")
                h_clean = h_sym.replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()
                if h_clean != clean_sym:
                    continue
                if ("BUY" if h.get("cmd") == 0 else "SELL") != verdict:
                    continue
                try:
                    h_sl = float(h.get("sl") or 0.0)
                    h_tp = float(h.get("tp") or 0.0)
                except (TypeError, ValueError):
                    continue
                if h_sl <= 0 or h_tp <= 0:
                    continue
                if abs(h_sl - s_sl) > sig_tol or abs(h_tp - s_tp) > sig_tol:
                    continue  # HARD filter: the signature must match
                candidates.append(h)
            if candidates:
                # Tie-break a reused signature on entry proximity.
                matched_history = min(
                    candidates, key=lambda h: abs(float(h.get("open_price") or 0.0) - s_price)
                )
                    
        patch_url = f"{MEMORY_ENDPOINT}?id=eq.{s_id}"
        if matched_history:
            profit = float(matched_history.get("profit", 0.0))
            close_price = float(matched_history.get("close_price", 0.0))
            # Normalise the broker clock to real UTC exactly once, here, where it
            # enters the engine. This line previously had two stacked bugs:
            #   1. close_time is a BROKER epoch (~+3h), not UTC;
            #   2. fromtimestamp() with no tz renders it in the LOCAL machine
            #      timezone, and the "Z" then falsely declared it UTC.
            # The result (outcome_date) is what check_hindsight_trajectory uses to
            # decide where to start scanning price, so a skewed value silently shifts
            # the whole hindsight window — and those verdicts now feed the Bayesian
            # sizer's learning.
            close_ts_utc = float(matched_history.get("close_time") or 0.0) - broker_offset
            close_time_iso = datetime.fromtimestamp(close_ts_utc, tz=timezone.utc).isoformat()
            
            # Determine dynamic outcome based on exit price vs TP and SL targets
            verdict = s.get("verdict", "BUY").upper()
            tp = float(s.get("target_price") or 0.0)
            sl = float(s.get("stop_loss") or 0.0)
            
            tolerance = close_price * 0.0002
            is_sell = verdict in ("SELL", "SHORT")
            
            if is_sell:
                if tp > 0 and close_price <= (tp + tolerance):
                    outcome = "tp_hit"
                elif sl > 0 and close_price >= (sl - tolerance):
                    outcome = "sl_hit"
                else:
                    outcome = "invalidated"
            else:
                if tp > 0 and close_price >= (tp - tolerance):
                    outcome = "tp_hit"
                elif sl > 0 and close_price <= (sl + tolerance):
                    outcome = "sl_hit"
                else:
                    outcome = "invalidated"
            
            payload = {
                "outcome": outcome,
                "outcome_price": close_price,
                "outcome_date": close_time_iso,
                "lesson": f"Resolved automatically: Matched MT4 ticket {matched_history.get('ticket')} exit on {sym}. Profit: £{profit:.2f}."
            }
            # Persist the linkage in a real column, not just inside the lesson text.
            # The ticket is known for certain HERE and nowhere else; storing it only in
            # free text meant it was lost whenever a lesson was regenerated.
            if _memory_has_ticket_column():
                payload["ticket"] = matched_history.get("ticket")
            httpx.patch(patch_url, headers=headers, json=payload)
            print(f"  [RESOLVE] Setup {s_id} automatically resolved as {outcome} via MT4 ticket.")
        else:
            # Mark as expired since it is no longer in open positions or recent history
            payload = {
                "outcome": "expired",
                "outcome_price": float(s.get("price") or 0.0),
                "outcome_date": now_utc.isoformat() + "Z",
                "lesson": "Resolved automatically: Setup expired or was not filled on MT4 terminal."
            }
            httpx.patch(patch_url, headers=headers, json=payload)
            print(f"  [RESOLVE] Setup {s_id} marked as expired (no active MT4 trade).")


def _load_ibkr_ledger() -> dict:
    """The IBKR bridge's virtual-ticket ledger as {int ticket: position dict}.

    Read-only. Uses the live bridge's ledger path when the bridge is in-process,
    else the default engine/data_store/ibkr_live_book.json. Each vp carries:
    symbol, direction, entry_price, initial_units, remaining_units, stop, target,
    status ("open"/"closed"), exit_price, exit_reason ("stop"/"target"/"close"/
    "external"), opened_at, and a fills list (entry/partial/stop/target/close
    with qty+price+ts).
    """
    path = getattr(_EXECUTOR, "_ledger_path", None) or (
        ENGINE_DIR / "data_store" / "ibkr_live_book.json")
    try:
        raw = safe_load_json(str(path)) or {}
    except Exception as e:
        print(f"  [WARN] IBKR ledger unreadable ({path}): {e}")
        return {}
    out = {}
    for tk, vp in (raw.get("tickets") or {}).items():
        try:
            vp["ticket"] = int(vp.get("ticket") or tk)
            out[int(tk)] = vp
        except (TypeError, ValueError):
            continue
    return out


def _match_ibkr_signature(setup: dict, book: dict) -> dict | None:
    """Self-healing fallback: match a setup to a ledger ticket by its exact
    SL+TP signature (the engine sends sl/tp with the order and the ledger stores
    them verbatim — the same 0.1-pip rule the MT4 resolver measured at ~89%
    unique). Only used when setup_features.mt4_ticket is missing (e.g. the ack
    patch failed after a real fill); returns None rather than guessing."""
    clean_sym = str(setup.get("symbol", "")).replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()
    verdict = str(setup.get("verdict", "BUY")).upper()
    try:
        s_sl = float(setup.get("stop_loss") or 0.0)
        s_tp = float(setup.get("target_price") or 0.0)
        s_px = float(setup.get("price") or 0.0)
    except (TypeError, ValueError):
        return None
    if s_sl <= 0 or s_tp <= 0:
        return None
    pip = 0.01 if "JPY" in clean_sym else 0.0001
    tol = 0.1 * pip
    candidates = []
    for vp in book.values():
        v_sym = str(vp.get("symbol", "")).replace("/", "").upper()
        if v_sym != clean_sym:
            continue
        if ("BUY" if vp.get("direction") == "long" else "SELL") != ("BUY" if verdict in ("BUY", "LONG") else "SELL"):
            continue
        try:
            v_sl = float(vp.get("stop") or 0.0)
            v_tp = float(vp.get("target") or 0.0)
        except (TypeError, ValueError):
            continue
        if v_sl <= 0 or v_tp <= 0:
            continue
        if abs(v_sl - s_sl) > tol or abs(v_tp - s_tp) > tol:
            continue
        candidates.append(vp)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return min(candidates, key=lambda v: abs(float(v.get("entry_price") or 0.0) - s_px))


def _ibkr_realized_pnl_gbp(vp: dict) -> float | None:
    """Realized P&L of a ledger ticket in GBP, summed over its actual exit fills
    (partials included), converted quote->GBP. None when the entry fill is
    unknown — an honest gap beats a fabricated figure."""
    try:
        entry = float(vp.get("entry_price") or 0.0)
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None
    sign = 1.0 if vp.get("direction") == "long" else -1.0
    total = 0.0
    used = False
    for f in vp.get("fills") or []:
        if f.get("kind") == "entry" or f.get("price") is None:
            continue
        total += sign * (float(f["price"]) - entry) * float(f.get("qty") or 0.0)
        used = True
    if not used:
        ex = vp.get("exit_price")
        if ex is None:
            return None
        total = sign * (float(ex) - entry) * float(vp.get("initial_units") or 0.0)
    quote = get_quote_currency(str(vp.get("symbol", "")))
    rate = get_quote_to_account_rate(quote, "GBP")
    return round(total * rate, 2)


def resolve_closed_ibkr_setups():
    """Resolve pending setups from the IBKR bridge's virtual-ticket ledger.

    The MT4 resolver can never fire again (MT4 is dead — its history file never
    grows, so every new daemon trade rotted pending -> expired). On IBKR the
    exits execute venue-side as the bracket's STP/LMT children; the bridge folds
    those fills into its ledger (engine/data_store/ibkr_live_book.json). This
    resolver joins each pending setup to its ledger ticket via
    setup_features.mt4_ticket (written by open_new_trade's fills handshake;
    SL+TP signature fallback), and writes the real outcome:

      * classified against the ORIGINAL barriers with the same tolerance the
        MT4 resolver uses — a trailed/breakeven stop therefore resolves
        "invalidated" (managed), never a fake tp_hit/sl_hit;
      * `ticket` = IBKR_TICKET_OFFSET + permId — a separate namespace that can
        never collide with real MT4 ticket ints, so the Bayesian posterior's
        ticket->apex_mt4_trades pnl join can't mismatch; the row's own
        setup_features.profit_pnl (exact, from the ledger fills) is what the
        posterior falls back to. These ARE executed trades: they feed the
        posterior, by parity with the old MT4 behavior;
      * exit_reason "external" (venue flat, bridge saw no fill) is LEFT pending
        for the price-based path (check_open_trades), as the bridge documents.
    """
    book = _load_ibkr_ledger()
    if not book:
        return

    url = f"{MEMORY_ENDPOINT}?outcome=eq.pending&verdict=in.(BUY,SELL,LONG,SHORT,SPECULATIVE_BUY,SPECULATIVE_SELL,SPECULATIVE_LONG,SPECULATIVE_SHORT)"
    try:
        r = httpx.get(url, headers=headers)
        if r.status_code != 200:
            return
        pending = r.json()
    except Exception as e:
        print(f"  [WARN] Connection error fetching pending setups: {e}")
        return

    now_utc = datetime.now(timezone.utc)
    for s in pending:
        s_id = s["id"]
        if s_id in _resolved_setup_ids:
            continue  # already resolved by this process (5s sync-loop guard)
        sym = s["symbol"]
        verdict = s.get("verdict", "BUY").upper()
        created_at_str = s.get("created_at")
        try:
            setup_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            age_seconds = (now_utc - setup_dt).total_seconds()
        except Exception:
            age_seconds = 600.0
        # Give the bridge at least 3 minutes to fill and bind the ticket
        if age_seconds < 180:
            continue

        features = s.get("setup_features") or {}
        if isinstance(features, str):
            try:
                features = json.loads(features)
            except Exception:
                features = {}

        vpk = features.get("mt4_ticket")
        vp = None
        if vpk is not None:
            try:
                vp = book.get(int(vpk))
            except (TypeError, ValueError):
                vp = None
        if vp is None:
            # No linked ticket (ack patch failed or pre-IBKR row): try the
            # SL+TP signature against the ledger before giving up on it.
            vp = _match_ibkr_signature(s, book)
            if vp is not None:
                print(f"  [IBKR RESOLVE] Adopted ledger ticket {vp['ticket']} for {s_id} via SL+TP signature.")
                _patch_ibkr_ticket(s_id, int(vp["ticket"]))

        if vp is not None and vp.get("status") == "open":
            continue  # bracket still working venue-side — leave pending

        patch_url = f"{MEMORY_ENDPOINT}?id=eq.{s_id}"
        if vp is not None and vp.get("status") == "closed":
            exit_reason = str(vp.get("exit_reason") or "close")
            exit_px = vp.get("exit_price")
            if exit_px is None:
                # External close: the bridge saw no fill — resolution falls back
                # to the price path (check_open_trades), per the bridge design.
                print(f"  [IBKR RESOLVE] Ticket {vp['ticket']} ({sym}) closed externally — "
                      f"leaving {s_id} to the price-based path.")
                continue
            exit_px = float(exit_px)

            # Classify against the ORIGINAL barriers (same tolerance as MT4).
            tolerance = exit_px * 0.0002
            is_sell = verdict in ("SELL", "SHORT")
            tp = float(s.get("target_price") or 0.0)
            sl = float(s.get("stop_loss") or 0.0)
            if is_sell:
                if tp > 0 and exit_px <= (tp + tolerance):
                    outcome = "tp_hit"
                elif sl > 0 and exit_px >= (sl - tolerance):
                    outcome = "sl_hit"
                else:
                    outcome = "invalidated"
            else:
                if tp > 0 and exit_px >= (tp - tolerance):
                    outcome = "tp_hit"
                elif sl > 0 and exit_px <= (sl + tolerance):
                    outcome = "sl_hit"
                else:
                    outcome = "invalidated"

            exit_iso = now_utc.isoformat()
            for f in reversed(vp.get("fills") or []):
                if f.get("kind") not in (None, "entry") and f.get("ts"):
                    exit_iso = str(f["ts"])
                    break

            pnl = _ibkr_realized_pnl_gbp(vp)
            pnl_txt = f"Profit: £{pnl:.2f}." if pnl is not None else "P&L unavailable (entry fill unknown)."
            new_features = {**features, "mt4_ticket": int(vp["ticket"]),
                            "exit_price": exit_px, "exit_reason": exit_reason}
            if pnl is not None:
                new_features["profit_pnl"] = pnl
            payload = {
                "outcome": outcome,
                "outcome_price": exit_px,
                "outcome_date": exit_iso,
                "setup_features": new_features,
                "lesson": f"Resolved automatically: IBKR ticket {int(vp['ticket'])} {exit_reason} exit on {sym}. {pnl_txt}",
            }
            # Namespaced ticket linkage: executed trade -> feeds the posterior,
            # but can never join to a real MT4 row.
            if _memory_has_ticket_column():
                payload["ticket"] = IBKR_TICKET_OFFSET + int(vp["ticket"])
            pr = httpx.patch(patch_url, headers=headers, json=payload)
            if pr is None or pr.status_code in (200, 204):
                _resolved_setup_ids.add(s_id)
            print(f"  [IBKR RESOLVE] Setup {s_id} resolved as {outcome} via IBKR ticket {vp['ticket']} "
                  f"({exit_reason} @ {exit_px}).")
        else:
            # No ledger ticket at all: order was rejected/never filled (or a
            # research row that was never dispatched) — expire after the grace,
            # exactly as the MT4 resolver did for unfilled setups.
            payload = {
                "outcome": "expired",
                "outcome_price": float(s.get("price") or 0.0),
                "outcome_date": now_utc.isoformat() + "Z",
                "lesson": "Resolved automatically: no fill on IBKR — setup expired "
                          "(order rejected or no ticket ever bound).",
            }
            pr = httpx.patch(patch_url, headers=headers, json=payload)
            if pr is None or pr.status_code in (200, 204):
                _resolved_setup_ids.add(s_id)
            print(f"  [IBKR RESOLVE] Setup {s_id} marked as expired (no IBKR ticket).")


def ensure_active_ibkr_setups_pending():
    """Restore setups that were resolved while their IBKR ticket is still open.

    Mirror of ensure_active_mt4_setups_pending against the bridge ledger: a
    setup whose setup_features.mt4_ticket names a STILL-OPEN virtual ticket
    must be pending — anything else (a premature expiry, a manual mis-click on
    the dashboard) is reverted so the live trade keeps tracking to its real
    venue-side exit.
    """
    book = _load_ibkr_ledger()
    if not book:
        return
    open_tickets = {
        t for t, vp in book.items()
        if vp.get("status") == "open" and float(vp.get("remaining_units") or 0.0) > 0
    }
    if not open_tickets:
        return

    url = f"{MEMORY_ENDPOINT}?order=created_at.desc&limit=250"
    try:
        r = httpx.get(url, headers=headers)
        if r.status_code != 200:
            return
        scans_list = r.json()
    except Exception as e:
        print(f"  [WARN] Connection error fetching recent analyses for IBKR pending check: {e}")
        return

    for s in scans_list:
        outcome = s.get("outcome")
        if outcome in (None, "pending"):
            continue
        features = s.get("setup_features") or {}
        if isinstance(features, str):
            try:
                features = json.loads(features)
            except Exception:
                features = {}
        try:
            vpk = int(features.get("mt4_ticket"))
        except (TypeError, ValueError):
            continue
        if vpk not in open_tickets:
            continue
        try:
            pr = httpx.patch(
                f"{MEMORY_ENDPOINT}?id=eq.{s['id']}", headers=headers,
                json={"outcome": "pending", "outcome_price": None, "outcome_date": None})
            if pr is None or pr.status_code in (200, 204):
                _resolved_setup_ids.discard(s["id"])
                print(f"  [RESTORE] Restored setup {s['id']} back to pending "
                      f"(IBKR ticket {vpk} still open; was {outcome}).")
        except Exception as e:
            print(f"  [WARN] Failed to restore IBKR setup {s.get('id')}: {e}")


def ensure_active_mt4_setups_pending():
    """Verify that all open positions in MT4 have their corresponding setups marked as pending in Supabase.
    
    If they were accidentally marked as expired or invalidated, restore them to pending.
    """
    common_dir = cfg.execution.mt4.common_dir if hasattr(cfg.execution, "mt4") and hasattr(cfg.execution.mt4, "common_dir") else ""
    if not common_dir:
        return
        
    positions_file = os.path.join(common_dir, "mt4_positions.json")
    if not os.path.exists(positions_file):
        return
        
    try:
        positions = safe_load_json(positions_file)
        if not positions:
            return
    except Exception as e:
        print(f"  [WARN] Error reading MT4 positions for setup check: {e}")
        return

    # Fetch recent analyses (resolved and pending) from the last 7 days
    url = f"{MEMORY_ENDPOINT}?order=created_at.desc&limit=250"
    try:
        r = httpx.get(url, headers=headers)
        if r.status_code != 200:
            return
        scans_list = r.json()
    except Exception as e:
        print(f"  [WARN] Connection error fetching recent analyses for pending check: {e}")
        return

    import re
    def parse_fl(val):
        if val is None: return 0.0
        if isinstance(val, (int, float)): return float(val)
        val_str = str(val).strip()
        if not val_str: return 0.0
        try: return float(val_str)
        except ValueError:
            m = re.findall(r'[-+]?\d*\.\d+|\d+', val_str)
            return float(m[0]) if m else 0.0

    def get_clean_symbol(sym):
        return sym.replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()

    for p in positions:
        magic = p.get("magic", 0)
        # Only check positions opened by the engine (magic 88888)
        if magic != 88888:
            continue
            
        p_sym = p.get("symbol", "")
        p_clean = get_clean_symbol(p_sym)
        p_sl = float(p.get("sl", 0.0))
        p_tp = float(p.get("tp", 0.0))
        
        best_scan = None
        best_score = float('inf')
        
        for scan in scans_list:
            s_clean = get_clean_symbol(scan.get("symbol", ""))
            if s_clean != p_clean:
                continue
            s_sl = parse_fl(scan.get("stop_loss"))
            s_tp = parse_fl(scan.get("target_price"))
            
            # Match score by relative difference in SL and TP
            rel_diff_sl = abs(s_sl - p_sl) / (s_sl if s_sl > 0 else 1.0)
            rel_diff_tp = abs(s_tp - p_tp) / (s_tp if s_tp > 0 else 1.0)
            score = rel_diff_sl + rel_diff_tp
            if score < best_score:
                best_score = score
                best_scan = scan
                
        # If we have a close match (e.g. SL/TP diff < 1%), check its outcome
        if best_scan and best_score < 0.01:
            s_id = best_scan["id"]
            outcome = best_scan.get("outcome")
            
            # If the trade is active in MT4 but not marked as pending in Supabase, restore it!
            if outcome != "pending" and outcome is not None:
                patch_url = f"{MEMORY_ENDPOINT}?id=eq.{s_id}"
                patch_payload = {
                    "outcome": "pending",
                    "outcome_price": None,
                    "outcome_date": None
                }
                try:
                    patch_r = httpx.patch(patch_url, headers=headers, json=patch_payload)
                    if patch_r.status_code in (200, 204):
                        print(f"  [RESTORE] Restored active MT4 trade {s_id} back to pending (was {outcome}).")
                except Exception as e:
                    print(f"  [WARN] Failed to patch active MT4 trade setup {s_id}: {e}")


def run_once():
    print("\n" + "="*80)
    print(f"APEX QUANT - LIVE PAPER TRADING SCAN started at {datetime.now(ZoneInfo('Europe/London')).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("="*80)
    
    # ── Config Drift Guard ──
    try:
        from apex_quant.config import load_config
        disk_cfg = load_config()
        diffs = []
        for field in disk_cfg.risk.model_fields:
            disk_val = getattr(disk_cfg.risk, field)
            mem_val = getattr(cfg.risk, field)
            if disk_val != mem_val:
                diffs.append(f"risk.{field}: disk={disk_val} vs memory={mem_val}")
        if disk_cfg.execution.live_min_position != cfg.execution.live_min_position:
            diffs.append(f"execution.live_min_position: disk={disk_cfg.execution.live_min_position} vs memory={cfg.execution.live_min_position}")
        if diffs:
            print("=" * 80)
            print("  ⚠️ WARNING: CONFIGURATION DRIFT DETECTED!")
            print("  The following settings differ on disk compared to the running process:")
            for d in diffs:
                print(f"    - {d}")
            print("  A restart is required to apply these changes because configuration is cached in memory.")
            print("=" * 80)
    except Exception as e:
        print(f"[WARN] Config drift guard failed: {e}")

    # ── Sync execution stats + resolve closed setups (provider-specific) ──
    try:
        sync_mt4_trades()
        with _resolution_lock:
            if cfg.execution.provider == "ibkr":
                resolve_closed_ibkr_setups()
                ensure_active_ibkr_setups_pending()
            else:
                resolve_closed_mt4_setups()
                ensure_active_mt4_setups_pending()
    except Exception as e:
        print(f"[WARN] Failed to sync execution stats: {e}")
        
    # ── Bayesian Sizer Setup ──
    try:
        initialize_bayesian_sizer_from_supabase()
    except Exception as e:
        print(f"[WARN] Failed to initialize Bayesian Sizer trackers: {e}")
    # ──────────────────────────
    
    open_trades = fetch_open_trades()
    check_open_trades(open_trades)
    scan_robust_core(open_trades)
    
    # ── Automated Post-Mortem Learning (Self-Review of wins & losses) ──
    try:
        print("[AI LEARNING] Running automated post-mortem lessons generation...")
        from scripts.update_lessons import update_lessons
        with _resolution_lock:
            update_lessons()
        print("[AI LEARNING] Lessons pool updated successfully.")
    except Exception as e:
        print(f"[WARN] AI automated post-mortem updater failed: {e}")

    # ── Symbol Knowledge Synthesis (aggregates all lessons + MT4 trades per symbol) ──
    try:
        print("[AI LEARNING] Refreshing symbol knowledge summaries...")
        from scripts.build_symbol_knowledge import run as _refresh_knowledge
        _refresh_knowledge()
        print("[AI LEARNING] Symbol knowledge updated.")
    except Exception as e:
        print(f"[WARN] Symbol knowledge refresh failed: {e}")


_resolution_lock = threading.Lock()

def start_mt4_sync_daemon():
    """Background execution-sync daemon.

    Previously a hardcoded 5-second loop that ALSO rebuilt lessons and symbol knowledge on
    every pass — full-table Supabase reads 720x/hour. That is what exhausted the egress quota
    (HTTP 402), and because it retried regardless of the 402 it kept the project pinned in the
    restricted state it was trying to escape.

    Three changes: cadence is config-driven (default 60s, was 5s), the expensive
    lesson/knowledge rebuild runs on its own much slower clock (default 30min), and a quota
    breaker skips Supabase entirely while it is refusing us.
    """
    interval = max(5, int(getattr(cfg.execution, "sync_interval_s", 60)))
    knowledge_every = max(interval, int(getattr(cfg.execution, "knowledge_interval_s", 1800)))

    def sync_loop():
        print(f"[INFO] Background execution-sync daemon started "
              f"(sync {interval}s, knowledge rebuild {knowledge_every}s).")
        last_knowledge = 0.0
        was_blocked = False
        while True:
            try:
                if supabase_guard.is_blocked():
                    # Say it once per block, not once per cycle.
                    if not was_blocked:
                        print(f"  [SYNC] {supabase_guard.describe()} — pausing Supabase sync.")
                        was_blocked = True
                else:
                    if was_blocked:
                        print("  [SYNC] Supabase cooldown elapsed; resuming sync.")
                        was_blocked = False

                    sync_mt4_trades(silent=True)

                    if _resolution_lock.acquire(blocking=False):
                        try:
                            if cfg.execution.provider == "ibkr":
                                resolve_closed_ibkr_setups()
                            else:
                                resolve_closed_mt4_setups()

                            # Lessons + symbol knowledge are full-table reads. They do not
                            # need to keep pace with fill detection.
                            now = time.time()
                            if now - last_knowledge >= knowledge_every:
                                last_knowledge = now
                                from scripts.update_lessons import update_lessons
                                update_lessons()
                                try:
                                    from scripts.build_symbol_knowledge import run as _refresh
                                    _refresh()
                                except Exception:
                                    pass
                        finally:
                            _resolution_lock.release()
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=sync_loop, daemon=True)
    t.start()

def main():
    parser = argparse.ArgumentParser(description="Live Paper Trading Engine")
    parser.add_argument("--prop", action="store_true",
                        help="Prop Firm Mode: 1.00%% risk per trade, 7.5%% drawdown cap")
    parser.add_argument("--loop", action="store_true", help="Run the engine continuously in a loop")
    parser.add_argument("--interval", type=int, default=900, help="Scan interval in seconds (default: 900)")
    parser.add_argument("--book", action="store_true",
                        help="Trade ONLY the certified book (39 instruments, 1d) instead of "
                             "the full research scan (95 symbols x 4 timeframes = 468 "
                             "systems). This is the universe every gated figure describes.")
    parser.add_argument("--daily-stop", type=float, default=None, metavar="FRAC",
                        help="Enable the daily-loss stop at this fraction (e.g. 0.025) "
                             "WITHOUT adopting the rest of the prop profile. Blocks new "
                             "entries once the session is down that much.")
    parser.add_argument("--daily-stop-flatten", action="store_true",
                        help="With --daily-stop, also CLOSE open positions on breach. "
                             "Irreversible market action; off by default.")
    args = parser.parse_args()

    # Prop Firm Mode (restored 2026-07-22 — originally added in a Gemini session).
    # Caps the Bayesian sizer to the prop rules. These three numbers ARE gated /
    # config-backed; the CAGR and max-DD figures that previously appeared in this
    # banner were NOT (they came from un-ledgered parameter sweeps that also pruned
    # the worst instruments after seeing their results), so they are not restored.
    if args.book:
        # Replace the scan list in place — every consumer reads this module global.
        global ROBUST_CORE_PORTFOLIO
        ROBUST_CORE_PORTFOLIO[:] = build_book_portfolio()
        print(f"[BOOK] Pinned to the certified book: {len(ROBUST_CORE_PORTFOLIO)} "
              f"instruments, 1d only (was the full research scan).")

    if args.prop:
        # Read the firm rules from config.prop.yaml rather than restating them here, so the
        # profile and the running sizer cannot drift apart. Prop mode deliberately keeps 1%
        # per trade even though config.yaml is now 0.75% — that is the firm's contract, not
        # this book's optimum, and it must NOT track config.yaml.
        #
        # 2026-07-23 FIX: this used to set ONLY the Bayesian sizer's ceiling, leaving the
        # RiskManager on config.yaml. So --prop silently ran with the BASE portfolio cap
        # (6.5% not 4.0%), the BASE breaker (20% not 6%) and NO daily-loss stop, while the
        # startup banner claimed "PROP MODE ENABLED". The whole risk section is now swapped.
        global cfg
        _prop = load_config(ENGINE_DIR / "config.prop.yaml")
        cfg = _prop
        _BAYESIAN_SIZER.max_risk = _prop.risk.max_risk_per_trade
        _BAYESIAN_SIZER.min_risk = min(0.0050, _prop.risk.max_risk_per_trade)
        _BAYESIAN_SIZER.max_drawdown = 0.075

    # --daily-stop enables ONLY the daily-loss rule, without adopting the rest of the prop
    # profile. That matters for the frozen paper experiment: switching to --prop would also
    # change risk-per-trade and the caps, breaking the experiment of record mid-flight.
    if args.daily_stop is not None:
        cfg = cfg.model_copy(update={"risk": cfg.risk.model_copy(update={
            "daily_loss_limit": args.daily_stop,
            "daily_loss_flatten": bool(args.daily_stop_flatten),
        })})

    # ── Startup Banner ──
    print("=" * 80)
    print("  APEX QUANT ENGINE — STARTUP BANNER")
    print(f"  Config Version:            {cfg.version}")
    print(f"  Max Total Exposure:        {cfg.risk.max_total_exposure}")
    print(f"  Max Correlated Exposure:   {cfg.risk.max_correlated_exposure}")
    print(f"  Effective Min Position:    {cfg.execution.live_min_position}")
    print(f"  Drawdown Breaker / Limit:  {cfg.risk.drawdown_breaker} / {cfg.risk.drawdown_reducing_limit}")
    print(f"  MT4 Server UTC Offset:     {cfg.execution.mt4.server_utc_offset_hours} hours")
    # Risk is read from config, never hardcoded: the previous banner printed
    # "STANDARD (2.00% Risk)" while config.yaml said 1.0%. An overstated risk figure
    # is exactly what preceded the -£1,613 live loss in June, so this line now
    # reports the value actually in force.
    print(f"  Prop Mode:                 "
          f"{'ENABLED — ' if args.prop else 'off — '}"
          f"risk/trade {cfg.risk.max_risk_per_trade * 100:.2f}%"
          f"{' (capped 1.00%, DD stop 7.5%)' if args.prop else ''}")
    print("=" * 80)

    # Start real-time MT4 background synchronisation
    start_mt4_sync_daemon()

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
