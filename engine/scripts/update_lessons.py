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
    }
    gh = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    for attempt in range(retries):
        try:
            r = httpx.post(url, json=payload, headers=gh, timeout=60)
            if r.status_code == 429:
                wait = 15 * (attempt + 1)
                print(f"  [Rate Limit] Waiting {wait}s (attempt {attempt+1}/{retries})...")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                print(f"  [Groq Error] HTTP {r.status_code}: {r.text[:150]}")
                return None
            content = r.json()["choices"][0]["message"]["content"]
            if "<think>" in content:
                import re
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            return content
        except Exception as e:
            print(f"  [Groq Exception] {type(e).__name__}: {e}")
            return None
    return None


# Stamped into every generated lesson. Bump this whenever the prompt or format
# changes materially: _needs_structured_lesson() treats an older/absent marker as
# "needs regeneration", so an improved prompt rolls out across the whole history on
# its own instead of leaving a permanent mix of old and new analysis.
#
# v2 (2026-07-14): facts templated from the broker record instead of restated by the
# model; post-exit counterfactual computed and declared authoritative (the model was
# claiming trades "would have hit TP" on +42 pips of a 138-pip target); base rates
# injected so a single trade is judged against the book; single-trade parameter
# tuning explicitly forbidden.
_LESSON_VERSION = "LESSON_V3"

_OUTCOME_LABELS = {
    "tp_hit":      "Hit Take Profit — closed in full profit",
    "sl_hit":      "Hit Stop Loss — closed at a loss",
    "expired":     "Trade expired due to time limit before SL/TP",
    "invalidated": "Trade was manually closed or managed out before SL/TP",
}

# Outcomes that represent a managed/neutral close (not a clean win or clean loss)
_NEUTRAL_OUTCOMES = {"invalidated", "expired"}


def _classify_trade(trade: dict, profit_val: float) -> str:
    """Return 'win', 'neutral', or 'loss' for a closed trade based on the outcome/close reason."""
    outcome = str(trade.get("outcome", "")).lower().strip()
    if outcome == "tp_hit":
        return "win"
    elif outcome == "sl_hit":
        return "loss"
    else:
        return "neutral"


def _needs_structured_lesson(t: dict) -> bool:
    """Return True if this trade's lesson is missing, wrong format, or needs hindsight update."""
    lesson = t.get("lesson") or ""
    if not lesson.strip():
        return True
    if "Post-Mortem:" in lesson:
        return True
    # Older prompt version -> regenerate. Lets a prompt improvement propagate across
    # the whole history via the normal loop, rather than leaving old, worse analysis
    # (fabricated counterfactuals, n=1 parameter tuning) sitting on the cards forever.
    if _LESSON_VERSION not in lesson:
        return True
    if "<strong>" not in lesson:
        return True
    if "£" not in lesson:
        return True

    # Corrupt lesson: exit price printed as None
    if "at None" in lesson:
        return True

    # Corrupt lesson: £0.00 shown but the setup has no profit recorded
    # (applies to ALL outcomes - invalidated/expired trades can also have real P&L)
    if "£0.00" in lesson:
        profit_raw = t.get("profit") or t.get("pnl")
        # If profit field is None/missing, the lesson definitely used a fallback of 0
        if profit_raw is None:
            return True
        try:
            if abs(float(profit_raw)) < 0.01:
                # profit really is zero - only allow £0.00 if outcome is neutral
                outcome_inner = str(t.get("outcome", "")).lower()
                if outcome_inner in ("tp_hit", "sl_hit"):
                    return True  # TP/SL should never be exactly £0.00
        except (TypeError, ValueError):
            return True

    # Corrupt lesson: absurd pip count (more than 5 digits)
    import re as _re2
    if _re2.search(r'[\d]{5,}\s*pips', lesson):
        return True

    # Regenerate lesson if hindsight check has run/finalized but isn't yet reflected in the lesson
    features = t.get("setup_features") or {}
    hindsight_checked = features.get("hindsight_checked", False)
    if hindsight_checked and "Hindsight Check:" not in lesson:
        return True

    # Detect which category is encoded in the stored lesson HTML
    first_100 = lesson[:100]
    if "✅" in first_100:
        stored_cat = "win"
    elif "🔄" in first_100:
        stored_cat = "neutral"
    elif "❌" in first_100:
        stored_cat = "loss"
    else:
        return True

    # Extract profit to classify
    profit_raw = t.get("profit") or t.get("pnl") or 0
    try:
        profit_val = float(profit_raw)
    except (TypeError, ValueError):
        profit_val = 0.0

    if profit_val == 0.0:
        import re
        m = re.search(r"Profit:\s*(?:£)?\s*(-?[\d\.]+)", lesson)
        if m:
            try:
                profit_val = float(m.group(1))
            except ValueError:
                pass

    if profit_val == 0.0:
        matched_profit = _fetch_mt4_profit(t, headers)
        if matched_profit is not None:
            profit_val = matched_profit

    cat = _classify_trade(t, profit_val)
    if stored_cat != cat:
        return True

    # Stale linkage: the lesson is bound to a ticket that the authoritative SL+TP
    # signature disagrees with, so its exit price and P&L were taken from ANOTHER
    # trade. Format and category look fine, so nothing else here catches it —
    # without this check the corrupted lessons would never be regenerated.
    import re as _re
    truth = _match_mt4_trade(t, headers)
    m = _re.search(r"TICKET_ID:\s*(\d+)", lesson)
    if m and truth and str(truth.get("ticket")) != m.group(1):
        return True

    # Stale FIGURES. A lesson is generated the moment a setup resolves, but MT4 keeps
    # updating the trade afterwards — partial exits settle and the final fill lands —
    # so a lesson written mid-flight freezes a P&L that later changes (e.g. quoting
    # £-20.69 for a trade that finished at £-73.22, while the card header shows the
    # real number). The ticket, category and version checks all PASS in that case, so
    # without this the wrong figures would sit on the card forever.
    if truth:
        real = float(truth.get("profit") or 0.0)
        # The first £ amount is the templated "What Happened" P&L; later ones are
        # base-rate stats.
        mp = _re.search(r"£\s*(-?[\d,]+\.\d{2})", lesson)
        if mp:
            try:
                quoted = float(mp.group(1).replace(",", ""))
            except ValueError:
                return True
            if abs(quoted - real) > 0.01:
                return True
    return False


# Matching tolerance. The engine SENDS sl/tp with the order and MT4 stores them
# verbatim, so (symbol, direction, sl, tp) is effectively a primary key for "which
# trade did this setup become". Measured on live data: at 0.1 pip it uniquely
# identifies 78/88 (88.6%) of trades; loosening to 2.0 pips DROPS that to 64/88 with
# 18 ambiguous. Tighter is strictly better.
#
# Entry price is deliberately NOT a filter: it slips, so it cannot identify a trade.
# The previous heuristic hard-filtered on entry +/-150 pips and treated sl/tp as a
# soft score (scoring a MISSING value as a perfect match), which systematically
# linked each setup to a NEIGHBOURING trade's ticket — e.g. every GBP/NZD setup was
# bound to the next setup's ticket, so lessons quoted another trade's exit and P&L.
_SLTP_TOL_PIPS = 0.1


_MT4_TRADES_CACHE = None
_MT4_TRADES_URL = ("https://dtiuwllodzqpbwohzrgj.supabase.co/rest/v1/apex_mt4_trades"
                   "?order=open_time.desc&limit=500")


def _mt4_trades(headers: dict) -> list:
    """MT4 trade list, fetched once per process. Matching is called for every trade
    in the batch, so re-fetching 500 rows per call was pure waste."""
    global _MT4_TRADES_CACHE
    if _MT4_TRADES_CACHE is None:
        try:
            from apex_quant.storage.supabase_util import fetch_all_rows
            url = f"https://dtiuwllodzqpbwohzrgj.supabase.co/rest/v1/apex_mt4_trades?order=open_time.desc&or=(ticket.neq.{int(time.time())})"
            _MT4_TRADES_CACHE = fetch_all_rows(url, headers)
        except Exception as e:
            print(f"  [WARN] Failed to fetch MT4 trades: {e}")
            _MT4_TRADES_CACHE = []
    return _MT4_TRADES_CACHE


def _pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in (symbol or "").upper() else 0.0001


def _clean_symbol(symbol: str) -> str:
    return (symbol or "").replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()


def _match_mt4_trade(setup: dict, headers: dict) -> dict | None:
    """Return the MT4 trade this setup actually became, matched on its exact SL+TP
    signature — or None.

    None is a correct, expected answer: most setups are analysed but never executed.
    Returning None is strictly better than guessing, because a wrong match silently
    puts another trade's exit price and P&L into the post-mortem.
    """
    clean_sym = _clean_symbol(setup.get("symbol", ""))
    m_verdict = str(setup.get("verdict", "")).upper().strip()
    try:
        m_sl = float(setup.get("stop_loss") or 0.0)
        m_tp = float(setup.get("target_price") or 0.0)
        m_price = float(setup.get("price") or 0.0)
    except (TypeError, ValueError):
        return None

    url = ("https://dtiuwllodzqpbwohzrgj.supabase.co/rest/v1/apex_mt4_trades"
           "?order=open_time.desc&limit=500")

    # Fast path: the setup carries a real ticket (written at resolution time, when the
    # linkage is known for certain). Exact join — no heuristic needed.
    linked_ticket = setup.get("ticket")
    if linked_ticket:
        hit = next((t for t in _mt4_trades(headers)
                    if str(t.get("ticket")) == str(linked_ticket)), None)
        if hit:
            return hit
        # fall through to the signature match

    if m_sl <= 0 or m_tp <= 0:
        return None  # no signature -> cannot be matched honestly

    tol = _SLTP_TOL_PIPS * _pip_size(clean_sym)
    try:
