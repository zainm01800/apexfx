import os
import sys
import json
import html
import time
from pathlib import Path
import httpx
from dotenv import load_dotenv

# Add engine directory to sys.path so we can import apex_quant
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load .env file from engine/ directory
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SUPABASE_URL = "https://dtiuwllodzqpbwohzrgj.supabase.co"
SUPABASE_ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k"
MEMORY_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_research_memory"
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
# llama-3.1-8b-instant: 500k TPM, much higher limit than 70b
GROQ_MODEL = "qwen/qwen3-32b"

headers = {
    "apikey": SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
    "Content-Type": "application/json",
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0"
}

from datetime import datetime, timezone
import pandas as pd
from apex_quant.data import clean, get_adapter
from apex_quant.config import get_config

class SmartDataProvider:
    def __init__(self):
        cfg = get_config()
        try:
            self.oanda = get_adapter("oanda")
        except Exception:
            self.oanda = None
        self.yahoo = get_adapter("yahoo")
        self.default_name = cfg.data.provider

    def get_history(self, instrument: str, start, end, timeframe):
        cfg = get_config()
        asset_class = cfg.asset_class_of(instrument)
        sym_clean = instrument.replace("_", "/")
        if asset_class in ("forex", "crypto") and self.default_name == "oanda" and self.oanda is not None:
            try:
                df = self.oanda.get_history(sym_clean, start, end, timeframe)
                if df is not None and len(df) >= 10:
                    return df
            except Exception:
                pass
        return self.yahoo.get_history(sym_clean, start, end, timeframe)

def map_timeframe(tf: str) -> str:
    tf_clean = str(tf).lower().strip()
    if "15m" in tf_clean or "5m" in tf_clean:
        return "15m"
    if "1h" in tf_clean or "4h" in tf_clean:
        return "1h"
    if "1d" in tf_clean:
        return "1d"
    if "1w" in tf_clean:
        return "1w"
    return "1d"

def _safe_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0

def check_hindsight_trajectory(trade: dict) -> dict | None:
    """Analyze price action after trade exit to see if it would have hit TP or SL in hindsight."""
    outcome = trade.get("outcome")
    if outcome not in ("tp_hit", "sl_hit", "expired", "invalidated"):
        return None

    outcome_date_str = trade.get("outcome_date")
    if not outcome_date_str:
        return None

    sym = trade.get("symbol")
    direction = trade.get("verdict")
    sl = _safe_float(trade.get("stop_loss"))
    tp = _safe_float(trade.get("target_price"))
    entry = _safe_float(trade.get("price"))
    tf = map_timeframe(trade.get("timeframe", "1d"))

    if not sym or not direction or sl <= 0.0 or tp <= 0.0 or entry <= 0.0:
        return None

    try:
        try:
            exit_dt = datetime.fromisoformat(outcome_date_str.replace("Z", "+00:00"))
        except ValueError:
            try:
                exit_dt = datetime.fromtimestamp(float(outcome_date_str), tz=timezone.utc)
            except ValueError:
                return None

        exit_ts = exit_dt.timestamp()
        # datetime.utcnow() returns a NAIVE datetime holding UTC wall-clock, and
        # .timestamp() then interprets a naive value as LOCAL time — so utcnow()
        # .timestamp() is wrong by the machine's UTC offset (an hour off under BST).
        # It compounded with the broker-clock skew in outcome_date, shifting the
        # hindsight scan window by hours.
        now_ts = datetime.now(timezone.utc).timestamp()

        # Check if enough time has passed to even scan (e.g. 5 minutes minimum to prevent spamming)
        if now_ts - exit_ts < 300:
            return None

        # Give it a safe lookback window based on timeframe
        max_horizon_bars = {"15m": 96, "1h": 72, "1d": 15, "1w": 8}
        max_bars = max_horizon_bars.get(tf, 72)
        
        # We start scanning from exit time
        start_date = exit_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        dp = SmartDataProvider()
        df = dp.get_history(sym, start=start_date, end=end_date, timeframe=tf)
        if df is None or df.empty:
            return None

        df_cleaned = clean(df)
        if df_cleaned.empty:
            return None

        df_naive = df_cleaned.copy()
        if df_naive.index.tz is not None:
            df_naive.index = df_naive.index.tz_localize(None)

        df_timestamps = df_naive.index.view("int64") // 10**9
        df_after = df_naive.loc[df_timestamps >= (exit_ts - 60)]
        if df_after.empty:
            return None

        mfe_pips = 0.0
        mae_pips = 0.0
        hindsight_outcome = "drifting"
        bars_to_resolution = 0
        hindsight_dt = None

        pip_size = 0.01 if "JPY" in sym.upper() else 0.0001
        for idx, (timestamp, bar) in enumerate(df_after.iterrows()):
            high_p = float(bar["high"])
            low_p = float(bar["low"])
            close_p = float(bar["close"])

            if direction in ("BUY", "LONG"):
                fav_dist = high_p - entry
                adv_dist = entry - low_p
            else:
                fav_dist = entry - low_p
                adv_dist = high_p - entry

            mfe_pips = max(mfe_pips, fav_dist / pip_size)
            mae_pips = max(mae_pips, adv_dist / pip_size)

            if direction in ("BUY", "LONG"):
                if low_p <= sl:
                    hindsight_outcome = "sl_hit"
                    bars_to_resolution = idx + 1
                    hindsight_dt = timestamp.isoformat()
                    break
                elif high_p >= tp:
                    hindsight_outcome = "tp_hit"
                    bars_to_resolution = idx + 1
                    hindsight_dt = timestamp.isoformat()
                    break
            else: # SELL / SHORT
                if high_p >= sl:
                    hindsight_outcome = "sl_hit"
                    bars_to_resolution = idx + 1
                    hindsight_dt = timestamp.isoformat()
                    break
                elif low_p <= tp:
                    hindsight_outcome = "tp_hit"
                    bars_to_resolution = idx + 1
                    hindsight_dt = timestamp.isoformat()
                    break

            if idx >= max_bars:
                hindsight_outcome = "drifting_limit"
                bars_to_resolution = idx + 1
                break

        finalized = hindsight_outcome in ("tp_hit", "sl_hit", "drifting_limit")
        return {
            "hindsight_checked": finalized,
            "hindsight_outcome": hindsight_outcome,
            "hindsight_mfe_pips": round(max(0.0, mfe_pips), 1),
            "hindsight_mae_pips": round(max(0.0, mae_pips), 1),
            "hindsight_bars": bars_to_resolution,
            "hindsight_time": hindsight_dt or datetime.utcnow().isoformat(),
            "hindsight_max_bars": max_bars,
            "hindsight_last_checked_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        print(f"  [WARN] Hindsight check failed for {sym}: {e}")
        return None

_LLM_CLIENT = None

def _get_llm():
    global _LLM_CLIENT
    if _LLM_CLIENT is None:
        try:
            from apex_quant.ai.client import build_llm
            from apex_quant.config import get_config
            cfg = get_config()
            _LLM_CLIENT = build_llm(cfg.ai)
        except Exception as e:
            print(f"  [WARN] Failed to initialize apex_quant LLM client: {e}")
    return _LLM_CLIENT


def _groq_complete(prompt: str, system: str, retries: int = 3) -> str | None:
    """Call standard LLM client (Gemini/DeepSeek) with direct Groq API fallback."""
    llm = _get_llm()
    if llm and llm.available:
        try:
            res = llm.complete(prompt, system=system)
            if res:
                # Strip <think> blocks (some models include CoT)
                if "<think>" in res:
                    import re
                    res = re.sub(r"<think>.*?</think>", "", res, flags=re.DOTALL).strip()
                return res
        except Exception as e:
            print(f"  [WARN] LLM client completion failed: {e}")

    # Fallback to direct Groq API call with backoff
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1000,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
