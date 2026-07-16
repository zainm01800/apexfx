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
# v4 (2026-07-16): MT4 cache cleared per-lesson to prevent stale data corruption
# in long-lived batch runs; setup_features.exit_price / profit_pnl used as
# authoritative source when already backfilled from the broker record.
_LESSON_VERSION = "LESSON_V4"

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
# Set to True before each lesson build, False after — forces a fresh MT4 fetch per
# lesson so that stale cached data from earlier in a long batch run cannot corrupt
# the profit figure or exit price of a later lesson.
_MT4_CACHE_NEEDS_REFRESH = False


def _mt4_trades(headers: dict) -> list:
    """MT4 trade list. Re-fetched when _MT4_CACHE_NEEDS_REFRESH is True so that
    long-lived batch runs don't serve stale profit data to later lessons."""
    global _MT4_TRADES_CACHE, _MT4_CACHE_NEEDS_REFRESH
    if _MT4_TRADES_CACHE is None or _MT4_CACHE_NEEDS_REFRESH:
        _MT4_CACHE_NEEDS_REFRESH = False
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
        candidates = []
        for t in _mt4_trades(headers):
            if _clean_symbol(t.get("symbol")) != clean_sym:
                continue
            if m_verdict != ("BUY" if t.get("cmd") == 0 else "SELL"):
                continue
            try:
                t_sl = float(t.get("sl") or 0.0)
                t_tp = float(t.get("tp") or 0.0)
            except (TypeError, ValueError):
                continue
            if t_sl <= 0 or t_tp <= 0:
                continue
            if abs(t_sl - m_sl) > tol or abs(t_tp - m_tp) > tol:
                continue  # HARD filter: the signature must match
            candidates.append(t)

        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Rare: the same SL/TP signature was reused. Break the tie on entry proximity.
        return min(candidates, key=lambda t: abs(float(t.get("open_price") or 0.0) - m_price))
    except Exception as e:
        print(f"  [WARN] Failed to fetch MT4 trades for matching: {e}")
        return None


def _fetch_mt4_profit(setup: dict, headers: dict) -> float | None:
    """Profit of the MT4 trade this setup became, or None when unmatched."""
    t = _match_mt4_trade(setup, headers)
    return float(t.get("profit") or 0.0) if t else None


def _exit_kind(t: dict) -> str:
    """tp_hit / sl_hit / managed for a closed MT4 trade (same rule as the resolver)."""
    cp = float(t.get("close_price") or 0.0)
    tp = float(t.get("tp") or 0.0)
    sl = float(t.get("sl") or 0.0)
    if cp <= 0:
        return "managed"
    tol = cp * 0.0002
    if t.get("cmd") == 1:  # SELL
        if tp > 0 and cp <= tp + tol:
            return "tp_hit"
        if sl > 0 and cp >= sl - tol:
            return "sl_hit"
    else:
        if tp > 0 and cp >= tp - tol:
            return "tp_hit"
        if sl > 0 and cp <= sl + tol:
            return "sl_hit"
    return "managed"


def _agg(rows: list) -> dict | None:
    if not rows:
        return None
    pnl = [float(t.get("profit") or 0.0) for t in rows]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    return {
        "n": len(rows),
        "total": sum(pnl),
        "win_rate": len(wins) / len(rows) * 100.0,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
    }


def _base_rates_text(headers: dict, symbol: str, style: str) -> str:
    """The book's real track record, from the broker record.

    A post-mortem on ONE trade is a story about noise unless it is anchored to a base
    rate. Handing the model these numbers is what lets a lesson say "this is within
    normal variance for a 37%-win-rate book" instead of inventing a cause for what is
    statistically a coin flip — and stops it recommending 'hold longer' when holding
    to the stop is where all the losses actually are.
    """
    trades = [t for t in _mt4_trades(headers) if t.get("close_time")]
    if not trades:
        return "(no broker history available)"
    csym = _clean_symbol(symbol)
    book = _agg(trades)
    sym_a = _agg([t for t in trades if _clean_symbol(t.get("symbol")) == csym])
    sty_a = _agg([t for t in trades if (t.get("style") or "") == style]) if style else None
    managed = _agg([t for t in trades if _exit_kind(t) == "managed"])
    slh = _agg([t for t in trades if _exit_kind(t) == "sl_hit"])

    lines = []
    if book:
        need = ((100 - book["win_rate"]) / book["win_rate"]) if book["win_rate"] > 0 else 0
        got = (book["avg_win"] / abs(book["avg_loss"])) if book["avg_loss"] else 0
        lines.append(f"- Whole book: {book['n']} trades, win rate {book['win_rate']:.1f}%, total £{book['total']:,.2f}.")
        lines.append(f"- Break-even needs a {need:.2f}:1 payoff; the book actually gets {got:.2f}:1.")
    if sym_a:
        lines.append(f"- This symbol ({symbol}): {sym_a['n']} trades, win rate {sym_a['win_rate']:.1f}%, total £{sym_a['total']:,.2f}.")
    if sty_a:
        lines.append(f"- This style ({style}): {sty_a['n']} trades, win rate {sty_a['win_rate']:.1f}%, total £{sty_a['total']:,.2f}.")
    if managed:
        lines.append(f"- Managed/early exits book-wide: {managed['n']} trades, total £{managed['total']:,.2f} (avg £{managed['total']/managed['n']:,.2f}).")
    if slh:
        lines.append(f"- Trades left to hit the STOP: {slh['n']} trades, total £{slh['total']:,.2f} (avg £{slh['total']/slh['n']:,.2f}).")
    return "\n".join(lines)


def _build_lesson(trade: dict) -> str | None:
    """Generate a structured 4-part HTML post-mortem using Groq."""
    global _MT4_CACHE_NEEDS_REFRESH
    # Force a fresh MT4 fetch for this lesson so stale cached data from earlier
    # in a long batch run cannot corrupt exit price or profit figures.
    _MT4_CACHE_NEEDS_REFRESH = True

    sym       = trade.get("symbol", "?")
    direction = trade.get("verdict", "?")
    entry     = trade.get("price", "?")
    sl        = trade.get("stop_loss", "?")
    tp        = trade.get("target_price", "?")
    outcome   = trade.get("outcome", "?")
    summary   = (trade.get("summary") or "")[:400]
    tech      = (trade.get("technical_analysis") or "")[:400]

    profit_raw = trade.get("profit") or trade.get("pnl") or 0
    try:
        profit_val = float(profit_raw)
    except (TypeError, ValueError):
        profit_val = 0.0

    import re
    initial_lesson = trade.get("lesson") or ""

    # Fast path: when setup_features already contains authoritative MT4 figures
    # (backfilled by the backfill_tickets / setup job), use them directly rather
    # than re-running _match_mt4_trade — the matcher can return a different trade
    # when the SL/TP signature is shared, which causes the wrong P&L to be quoted.
    features = trade.get("setup_features") or {}
    sf_exit  = features.get("exit_price")
    sf_pnl   = features.get("profit_pnl")
    ticket_id = str(trade.get("ticket")) if trade.get("ticket") else None

    if ticket_id and sf_exit is not None and sf_pnl is not None:
        # Authoritative broker data already on the card — use it directly.
        try:
            profit_val = float(sf_pnl)
            exit_px = float(sf_exit)
            if exit_px > 0:
                trade = {**trade, "outcome_price": exit_px}
            # Still look up the MT4 trade for entry price accuracy
            matched_trade = _match_mt4_trade(trade, headers)
            if matched_trade:
                open_px = float(matched_trade.get("open_price") or 0.0)
                if open_px > 0:
                    entry = open_px
        except (TypeError, ValueError):
            pass
    else:
        # Resolve the MT4 trade from the setup's own SL+TP signature. This is
        # AUTHORITATIVE: previously the ticket was scraped out of the PREVIOUS lesson's
        # text, so once a bad match was baked in it was inherited forever and then
        # presented to the frontend as an "exact" ticket match. Deriving it from the
        # signature every time is self-healing — a wrong historical link gets corrected
        # on the next regeneration.
        matched_trade = _match_mt4_trade(trade, headers)
        if matched_trade:
            ticket_id = str(matched_trade.get("ticket"))
            profit_val = float(matched_trade.get("profit") or 0.0)
            # Quote the REAL broker fill, not the setup's *intended* levels. The setup row
            # stores the price the engine wanted (e.g. 1.19936); MT4 filled at 1.19948 and
            # closed at 1.19741. The dashboard card shows the broker's numbers, so the
            # lesson must use the same ones or the two visibly disagree.
            exit_px = float(matched_trade.get("close_price") or 0.0)
            if exit_px > 0:
                trade = {**trade, "outcome_price": exit_px}
            open_px = float(matched_trade.get("open_price") or 0.0)
            if open_px > 0:
                entry = open_px
        else:
            # No signature match -> fall back to any ticket recorded previously, but do
            # NOT invent a profit from a guessed trade.
            m_ticket = (re.search(r"Matched MT4 ticket (\d+)", initial_lesson)
                        or re.search(r"TICKET_ID:\s*(\d+)", initial_lesson))
            if m_ticket:
                ticket_id = m_ticket.group(1)

        if profit_val == 0.0 and initial_lesson and "Resolved automatically" in initial_lesson:
            m = re.search(r"Profit:\s*(?:£)?\s*(-?[\d\.]+)", initial_lesson)
            if m:
                try:
                    profit_val = float(m.group(1))
                except ValueError:
                    pass

        # For any setup where profit is still 0 or None, ALWAYS try the MT4 match.
        # This covers invalidated/expired trades where profit was never written back.
        if profit_val == 0.0 or profit_raw is None:
            matched_profit = _fetch_mt4_profit(trade, headers)
            if matched_profit is not None:
                profit_val = matched_profit

    outcome_human = _OUTCOME_LABELS.get(outcome, outcome)
    category = _classify_trade(trade, profit_val)  # 'win' | 'neutral' | 'loss'

    # -- Counterfactual: COMPUTED, not inferred -------------------------------
    #
    # The model used to be given "MFE +42 pips" with no idea the target was 138 pips
    # away, so it confidently concluded the trade "would have hit TP". It reached 30%
    # of the way. Distance-to-target is cheap to compute, so we state the verdict as a
    # fact and forbid the model from contradicting it.
    pip = _pip_size(sym)
    try:
        f_entry, f_tp, f_sl = float(entry), float(tp), float(sl)
    except (TypeError, ValueError):
        f_entry = f_tp = f_sl = 0.0
    dist_tp = abs(f_tp - f_entry) / pip if (f_tp > 0 and f_entry > 0) else 0.0
    dist_sl = abs(f_entry - f_sl) / pip if (f_sl > 0 and f_entry > 0) else 0.0

    features = trade.get("setup_features") or {}
    hindsight_info = "Post-exit trajectory: not yet scanned — do NOT speculate about what would have happened."
    if "hindsight_outcome" in features:
        h_outcome = features.get("hindsight_outcome")
        h_mfe = float(features.get("hindsight_mfe_pips") or 0.0)
        h_mae = float(features.get("hindsight_mae_pips") or 0.0)
        h_bars = features.get("hindsight_bars", 0)

        if h_outcome == "tp_hit":
            verdict = ("WOULD have reached the target after exit. Exiting early therefore "
                       "forfeited a winner — this is genuine evidence the exit was premature.")
        elif h_outcome == "sl_hit":
            verdict = ("WOULD have hit the stop after exit. Exiting early SAVED money — "
                       "the exit was correct, regardless of it booking a loss.")
        else:
            pct = (h_mfe / dist_tp * 100.0) if dist_tp > 0 else 0.0
            verdict = (
                f"Did NOT reach the target. Best case after exit was +{h_mfe:.1f} pips of the "
                f"{dist_tp:.0f} pips needed to reach TP ({pct:.0f}% of the way), then it reversed "
                f"{h_mae:.1f} pips against. It is NOT true that this trade 'would have hit TP' — "
                f"do not claim that."
            )
        hindsight_info = (
            f"POST-EXIT COUNTERFACTUAL (computed from price data — authoritative):\n"
            f"- {verdict}\n"
            f"- Max favourable excursion after exit: +{h_mfe:.1f} pips "
            f"(target required {dist_tp:.0f} pips; stop was {dist_sl:.0f} pips away).\n"
            f"- Max adverse excursion after exit: -{h_mae:.1f} pips. Bars elapsed: {h_bars}.\n"
        )

    base_rates = _base_rates_text(
        headers, sym, (matched_trade.get("style") if matched_trade else "") or ""
    )

    feat_lines = []
    if features:
        clean_feats = {k: v for k, v in features.items() if not k.startswith("hindsight")}
        if clean_feats:
            for k, v in clean_feats.items():
                feat_lines.append(f"- {k}: {v}")
    feat_str = "\n".join(feat_lines) if feat_lines else "None recorded"

    prompt = f"""FACTS (from the broker record — authoritative, never restate them differently):
Symbol: {sym} | Timeframe: {trade.get('timeframe', '1h')} | Direction: {direction}
Entry: {entry}   ->   Exit: {trade.get('outcome_price', '?')}
Target (TP): {tp}  ({dist_tp:.0f} pips from entry)
Stop (SL): {sl}  ({dist_sl:.0f} pips from entry)
How it closed: {outcome_human}
Net profit/loss: £{profit_val:.2f}

{hindsight_info}

TRACK RECORD — the base rate this single trade must be judged against:
{base_rates}

TECHNICAL SETUP & MARKET STRUCTURE AT ENTRY:
- Text Summary: {summary}
- Technical Context: {tech}

STRATEGY INDICATORS & SYSTEM FEATURES AT ENTRY:
{feat_str}

GLOSSARY FOR SYSTEM FEATURES:
- adx: Trend strength (0 to 1). Values > 0.25 indicate strong trending environments; < 0.20 indicate ranges/chop.
- rsi: Relative Strength Index (0 to 1). Overbought when > 0.70; oversold when < 0.30.
- pxVsSma50: Normalized price distance to 50 SMA.
- trendAlign: Trend alignment score (-1 for bearish, 1 for bullish, 0 for range).
- regime: Market regime classification (e.g. up/low-vol, down/high-vol).
- confluence: Convergence/confluence score of multiple signals (0 to 1).

Write the post-mortem as ONE JSON object with exactly these keys:

"the_reason": Why price behaved this way and why the position was closed here. Analyze the setup features and regime: was the entry counter-trend, was it caught in low-volatility chop, or did a volatility spike hit the stop? If the evidence does not identify a cause, say the signals were ambiguous — do NOT invent a cause.

"key_lesson": Judge the DECISION, not the outcome. Use the COUNTERFACTUAL above verbatim — never claim the trade "would have hit TP" unless it explicitly says so. Compare against the TRACK RECORD: if this result sits within normal variance for a book with this win rate, say so plainly.

"action_plan": Ask honestly whether this ONE trade justifies changing anything. The correct answer is almost always NO — tuning parameters on a single trade is overfitting. Only propose a change if the TRACK RECORD (not this trade) supports it, and state how many trades would be needed to test it.

Rules: valid JSON, double-quoted strings, no markdown, no invented numbers. Never contradict the FACTS or the COUNTERFACTUAL."""

    system = (
        "You are a quantitative trading analyst performing post-mortems on the APEX Quant engine. "
        "Your engine uses a RegimeGatedMomentum strategy with multi-timeframe trend gating and Bayesian risk sizing.\n\n"
        "Analyze the trade using both the technical text summary and the quantitative SYSTEM FEATURES. "
        "You must evaluate:\n"
        "1. Strategy Fit: Did the trade match the current trend/volatility regime? (e.g. counter-trend entry in strong trend, or momentum trading in a low-vol regime?)\n"
        "2. Multi-Timeframe Alignment: Did trendAlign and pxVsSma50 indicate a high-probability confluence?\n"
        "3. Sizing/SL appropriateness: Did the ATR/volatility warrant a wider stop, or did the sizer size it correctly?\n"
        "4. Early Exit/Management: If closed early, did the post-exit counterfactual prove the exit saved money or forfeited profit?\n\n"
        "Rules:\n"
        "- Reply ONLY with a valid JSON object with keys: the_reason, key_lesson, action_plan.\n"
        "- Do not contradict or re-state the supplied FACTS or COUNTERFACTUAL.\n"
        "- Never recommend parameter changes or stop/target adjustments based on a single trade (n=1 is overfitting). Keep the action plan grounded in whole-book track record statistics.\n"
        "- Avoid raw system codes like tp_hit, sl_hit, range/low-vol in your text; write natural financial prose."
    )

    def _safe_str(val) -> str:
        if val is None:
            return ""
        if isinstance(val, str):
            val_stripped = val.strip()
            if (val_stripped.startswith("{") and val_stripped.endswith("}")) or \
               (val_stripped.startswith("[") and val_stripped.endswith("]")):
                try:
                    val = json.loads(val_stripped)
                except Exception:
                    pass
        if isinstance(val, dict):
            parts = [f"{str(k).replace('_', ' ').title()}: {_safe_str(v)}" for k, v in val.items()]
            return " · ".join(parts)
        if isinstance(val, list):
            return " · ".join(_safe_str(x) for x in val)
        result = str(val).strip()
        for code, label in _OUTCOME_LABELS.items():
            result = result.replace(code, label)
        return result

    resp = _groq_complete(prompt, system)
    if not resp:
        return None

    try:
        clean_resp = resp.strip()
        if clean_resp.startswith("```"):
            clean_resp = clean_resp.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        o = json.loads(clean_resp)
        # "What happened" is TEMPLATED from the broker record, never generated. The
        # model cannot mis-state a number it was never asked to produce, so numeric
        # drift (a lesson quoting £-60.85 for an £-84.60 trade) becomes structurally
        # impossible rather than merely discouraged.
        def _px(v) -> str:
            """Prices come back as raw floats (2.2905200000000008) — round for display."""
            try:
                return f"{float(v):.5f}".rstrip("0").rstrip(".")
            except (TypeError, ValueError):
                return str(v)

        _abs = abs(profit_val)
        size_word = "minor" if _abs < 100 else ("moderate" if _abs < 500 else "significant")
        wl = "gain" if profit_val > 0 else ("loss" if profit_val < 0 else "flat result")
        exit_disp = _px(trade.get("outcome_price", "?"))
        if outcome == "tp_hit":
            _facts = (f"Hit the take-profit at {exit_disp} from an entry of {_px(entry)} — "
                      f"a {size_word} {wl} of £{profit_val:.2f}.")
        elif outcome == "sl_hit":
            _facts = (f"Hit the stop-loss at {exit_disp} from an entry of {_px(entry)} — "
                      f"a {size_word} {wl} of £{profit_val:.2f}.")
        else:
            _facts = (f"Closed manually at {exit_disp} from an entry of {_px(entry)}, before either "
                      f"the target ({_px(tp)}) or the stop ({_px(sl)}) was reached — "
                      f"a {size_word} {wl} of £{profit_val:.2f}.")
        wwr = html.escape(_facts)
        ywr = html.escape(_safe_str(o.get("the_reason") or o.get("why_it_went_wrong_or_right") or ""))
        imp = html.escape(_safe_str(o.get("key_lesson") or o.get("improvement_or_preservation") or ""))
        ap  = html.escape(_safe_str(o.get("action_plan") or ""))

        # Build Hindsight HTML line if finalized
        hindsight_html = ""
        if features.get("hindsight_checked", False):
            h_outcome = features.get("hindsight_outcome")
            h_mfe = features.get("hindsight_mfe_pips", 0.0)
            h_mae = features.get("hindsight_mae_pips", 0.0)
            
            outcome_icons = {
                "tp_hit": f"🟢 <strong>Hindsight Check:</strong> Trade eventually hit Take Profit (+{h_mfe} pips max run, -{h_mae} pips drawdown post-exit). Exit was premature.",
                "sl_hit": f"🔴 <strong>Hindsight Check:</strong> Trade eventually hit Stop Loss (-{h_mae} pips drawdown, +{h_mfe} pips max run post-exit). Exit saved money.",
                "drifting_limit": f"🟡 <strong>Hindsight Check:</strong> Trade drifted without hitting targets (+{h_mfe} pips max run, -{h_mae} pips drawdown post-exit)."
            }
            h_text = outcome_icons.get(h_outcome, f"🔍 <strong>Hindsight Check:</strong> Post-exit trajectory: {h_outcome} (MFE: +{h_mfe} pips, MAE: -{h_mae} pips).")
            hindsight_html = f"<br>{h_text}"

        if category == "win":
            html_res = (
                f"<strong>✅ What Went Right:</strong> {wwr}<br>"
                f"<strong>📊 Why It Worked:</strong> {ywr}<br>"
                f"<strong>🔒 What to Preserve:</strong> {imp}"
                f"{hindsight_html}<br>"
                f"<strong>🎯 Action Plan:</strong> {ap}"
            )
        elif category == "neutral":
            html_res = (
                f"<strong>🔄 What Happened:</strong> {wwr}<br>"
                f"<strong>📐 Why It Was Managed Out:</strong> {ywr}<br>"
                f"<strong>⚖️ Was the Decision Correct?</strong> {imp}"
                f"{hindsight_html}<br>"
                f"<strong>🎯 Action Plan for Similar Setups:</strong> {ap}"
            )
        else:  # loss
            html_res = (
                f"<strong>❌ What Went Wrong:</strong> {wwr}<br>"
                f"<strong>🔍 Why It Went Wrong:</strong> {ywr}<br>"
                f"<strong>💡 What Can Be Improved:</strong> {imp}"
                f"{hindsight_html}<br>"
                f"<strong>🎯 Action Plan to Prevent Recurrence:</strong> {ap}"
            )

        if ticket_id:
            html_res += f"\n<!-- TICKET_ID: {ticket_id} -->"
        html_res += f"\n<!-- {_LESSON_VERSION} -->"
        print(f"  [DEBUG] _build_lesson for {trade.get('id')}: ticket_id={ticket_id}")
        return html_res
    except Exception as e:
        print(f"  [WARN] JSON parse failed: {e}")
        raw_res = f"<strong>Post-Mortem:</strong> {html.escape(resp.strip()[:300])}"
        if ticket_id:
            raw_res += f"\n<!-- TICKET_ID: {ticket_id} -->"
        print(f"  [DEBUG-RAW] _build_lesson for {trade.get('id')}: ticket_id={ticket_id}")
        return raw_res


def update_lessons():
    """Upgrade any resolved trade whose lesson is missing or not yet in structured HTML format.
    
    Also runs hindsight checking to see what price did after exit.
    Called every loop cycle. Processes up to 20 trades per call so it doesn't
    block the loop. Remaining trades will be caught in the next cycle.
    """
    if not GROQ_KEY:
        print("[WARN] GROQ_API_KEY not set — skipping lesson generation.")
        return

    print("Fetching resolved trades from Supabase...")
    url = (
        f"{MEMORY_ENDPOINT}"
        f"?outcome=in.(tp_hit,sl_hit,expired,invalidated)"
        f"&symbol=ilike.*%2F*"
        f"&order=created_at.desc"
        f"&or=(id.neq.cachebust_{int(time.time())})"
    )
    try:
        from apex_quant.storage.supabase_util import fetch_all_rows
        trades = fetch_all_rows(url, headers)
    except Exception as e:
        print(f"  [ERROR] Failed to fetch trades: {e}")
        return

    # 1. Run hindsight checking for trades that don't have it finalized
    print("Running hindsight trajectory scans on resolved trades...")
    hindsight_updated = 0
    
    # Filter candidates and sort by creation date desc (most recent first)
    hindsight_candidates = []
    for t in trades:
        features = t.get("setup_features") or {}
        if features.get("hindsight_checked", False):
            continue
            
        last_checked_str = features.get("hindsight_last_checked_at")
        if last_checked_str:
            try:
                dt_str = last_checked_str.replace("Z", "+00:00")
                last_checked = datetime.fromisoformat(dt_str)
                elapsed_sec = (datetime.now(timezone.utc) - last_checked).total_seconds()
                
                tf = map_timeframe(t.get("timeframe", "1d"))
                intervals = {
                    "15m": 15 * 60,
                    "1h": 60 * 60,
                    "1d": 24 * 60 * 60,
                    "1w": 7 * 24 * 60 * 60
                }
                min_interval = intervals.get(tf, 60 * 60)
                if elapsed_sec < min_interval:
                    continue
            except Exception:
                pass
        hindsight_candidates.append(t)
        
    hindsight_candidates.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    
    # Process at most 15 candidates per cycle to avoid OANDA/Yahoo rate limits
    batch_hindsight = hindsight_candidates[:15]
    print(f"Found {len(hindsight_candidates)} trades needing hindsight checks. Scanning {len(batch_hindsight)} this cycle...")
    
    for trade in batch_hindsight:
        features = trade.get("setup_features") or {}
        # Run trajectory check
        hindsight_data = check_hindsight_trajectory(trade)
        if hindsight_data:
            # Merge into existing features
            new_features = {**features, **hindsight_data}
            patch_url = f"{MEMORY_ENDPOINT}?id=eq.{trade['id']}"
            patch_r = httpx.patch(patch_url, headers=headers, json={"setup_features": new_features})
            if patch_r.status_code in (200, 204):
                trade["setup_features"] = new_features
                hindsight_updated += 1
                print(f"  ✓ Hindsight checked for {trade['id']}: {hindsight_data['hindsight_outcome']} (MFE: +{hindsight_data['hindsight_mfe_pips']} pips)")
            else:
                print(f"  [ERROR] Failed to patch setup_features for {trade['id']}: {patch_r.status_code}")

    if hindsight_updated > 0:
        print(f"Scanned and updated hindsight trajectory for {hindsight_updated} trades.")

    # 2. Filter for trades needing lessons or lesson updates
    need_lessons = [t for t in trades if _needs_structured_lesson(t)]

    if not need_lessons:
        print("All recent resolved trades already have structured post-mortem lessons!")
        return

    # Cap at 20 per loop call to avoid blocking the main scan too long
    batch = need_lessons[:20]
    print(f"Found {len(need_lessons)} trades needing structured lessons. Processing {len(batch)} this cycle...")

    count = 0
    for trade in batch:
        tid = trade["id"]
        sym = trade.get("symbol", "?")
        outcome = trade.get("outcome", "?")
        print(f"  Generating lesson for {tid} {sym} ({outcome})...")

        lesson = _build_lesson(trade)
        time.sleep(6)  # Throttle to stay within Groq RPM/TPM limits

        if not lesson:
            print(f"  [SKIP] Could not generate lesson for {tid}")
            continue

        patch_r = httpx.patch(
            f"{MEMORY_ENDPOINT}?id=eq.{tid}",
            headers=headers,
            json={"lesson": lesson},
        )
        if patch_r.status_code in (200, 204):
            print(f"  ✓ Saved structured post-mortem for {sym}")
            count += 1
        else:
            print(f"  [ERROR] Patch failed: {patch_r.status_code} - {patch_r.text}")

    remaining = len(need_lessons) - len(batch)
    print(f"\nDone! Updated {count}/{len(batch)} lessons this cycle. {remaining} remaining for next cycle.")


if __name__ == "__main__":
    update_lessons()
