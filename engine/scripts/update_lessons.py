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
GROQ_MODEL = "llama-3.1-8b-instant"

headers = {
    "apikey": SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
    "Content-Type": "application/json"
}


def _groq_complete(prompt: str, system: str, retries: int = 3) -> str | None:
    """Call Groq directly with backoff on rate-limit."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1000,
        "temperature": 0.3,
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
            # Strip <think> blocks (some models include CoT)
            if "<think>" in content:
                import re
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            return content
        except Exception as e:
            print(f"  [Groq Exception] {type(e).__name__}: {e}")
            return None
    return None


_OUTCOME_LABELS = {
    "tp_hit":      "Hit Take Profit — closed in full profit",
    "sl_hit":      "Hit Stop Loss — closed at a loss",
    "expired":     "Trade expired due to time limit before SL/TP",
    "invalidated": "Trade was manually closed or managed out before SL/TP",
}

# Outcomes that represent a managed/neutral close (not a clean win or clean loss)
_NEUTRAL_OUTCOMES = {"invalidated", "expired"}


def _classify_trade(trade: dict) -> str:
    """Return 'win', 'neutral', or 'loss' for a closed trade.

    Categories:
      win     — ONLY when outcome == 'tp_hit' (full target hit).
      loss    — ONLY when outcome == 'sl_hit' (full stop loss hit).
      neutral — any other outcome (e.g. 'invalidated', 'expired'), including
                trades that were closed early, stopped at breakeven,
                time-stopped, or had partials taken.
    """
    outcome = str(trade.get("outcome", "")).lower().strip()
    if outcome == "tp_hit":
        return "win"
    if outcome == "sl_hit":
        return "loss"
    return "neutral"


def _needs_structured_lesson(t: dict) -> bool:
    """Return True if this trade's lesson is missing, wrong format, or wrong polarity."""
    lesson = t.get("lesson") or ""
    if not lesson.strip():
        return True
    if "Post-Mortem:" in lesson:
        return True
    if "<strong>" not in lesson:
        return True
    if "£" not in lesson:
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

    return stored_cat != _classify_trade(t)


def _fetch_mt4_profit(sym: str, setup_id: str, headers: dict) -> float | None:
    """Query apex_mt4_trades to find the actual profit for the matched trade."""
    parts = setup_id.split('_')
    if len(parts) < 2:
        return None
    try:
        setup_time = float(parts[-1])
        if setup_time > 1000000000000:
            setup_time /= 1000.0
    except ValueError:
        return None
        
    clean_sym = sym.replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()
    trades_url = f"https://dtiuwllodzqpbwohzrgj.supabase.co/rest/v1/apex_mt4_trades?order=open_time.desc&limit=100"
    try:
        r = httpx.get(trades_url, headers=headers)
        if r.status_code == 200:
            trades = r.json()
            min_diff = 86400.0  # max 24 hours
            matched_profit = None
            for t in trades:
                t_sym = t.get("symbol", "").replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()
                if t_sym == clean_sym:
                    open_time = float(t.get("open_time") or 0.0)
                    diff = abs(open_time - setup_time)
                    if diff < min_diff:
                        min_diff = diff
                        matched_profit = float(t.get("profit") or 0.0)
            return matched_profit
    except Exception as e:
        print(f"  [WARN] Failed to fetch MT4 trades for matching: {e}")
    return None


def _build_lesson(trade: dict) -> str | None:
    """Generate a structured 4-part HTML post-mortem using Groq."""
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

    # Try parsing profit from initial lesson string if profit is 0.0
    initial_lesson = trade.get("lesson") or ""
    if profit_val == 0.0 and initial_lesson and "Resolved automatically" in initial_lesson:
        import re
        m = re.search(r"Profit:\s*(?:£)?\s*(-?[\d\.]+)", initial_lesson)
        if m:
            try:
                profit_val = float(m.group(1))
            except ValueError:
                pass
                
    # If profit is still 0.0, fetch it from apex_mt4_trades by matching setup_id
    if profit_val == 0.0:
        matched_profit = _fetch_mt4_profit(sym, trade["id"], headers)
        if matched_profit is not None:
            profit_val = matched_profit

    outcome_human = _OUTCOME_LABELS.get(outcome, outcome)
    category = _classify_trade(trade)  # 'win' | 'neutral' | 'loss'

    prompt = f"""You are analyzing a resolved trade with the following parameters:
Symbol: {sym}
Timeframe: {trade.get('timeframe', '1h')}
Direction: {direction}
Entry Price: {entry}
Target Price (TP): {tp}
Stop Loss (SL): {sl}
Exit Price: {trade.get('outcome_price', '?')}
Outcome: {outcome_human}
Net profit/loss: £{profit_val:.2f}

Technical Setup: {summary}
Market Structure: {tech}

Instructions:
1. "what_happened": Write one detailed sentence describing exactly what happened. You MUST mention the exact profit/loss amount (£{profit_val:.2f}) and state whether this represents a minor, moderate, or significant win/loss. Describe the price direction relative to entry and exit.
2. "the_reason": Explain the technical and market structure reason behind why the price moved this way and why the trade was closed (e.g., if managed out, analyze why the manual exit triggered at this price, and whether the momentum or support/resistance levels shifted).
3. "key_lesson": Critically evaluate whether the decision to close was correct. Compare the exit price with the TP and SL. Did exiting early save the account from a larger loss (correct defense), or did it cut a win short before reaching TP (premature exit)? Offer one specific actionable rule.
4. "action_plan": State exactly what the engine or trader must adjust (e.g., adjusting Bollinger Bands, moving average triggers, timeframe confluences, or spread limits) on similar setups in the future. Be highly specific to {sym} and the {direction} direction.

Your response MUST be a single JSON object. Do NOT use generic template phrases. Tailor the review to this specific trade's £{profit_val:.2f} outcome."""

    system = (
        "You are an elite quantitative trading post-mortem analyst. Your job is to write deep, specific reviews of closed trades. "
        "NEVER use generic templates or raw system codes like sl_hit. "
        "You must analyze the actual profit/loss amount (£ value) and evaluate the quality of the risk management decisions (defense vs premature exits). "
        "Reply ONLY with a valid JSON object containing keys: what_happened, the_reason, key_lesson, action_plan."
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
        clean = resp.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        o = json.loads(clean)
        # Support both old and new key names
        wwr = html.escape(_safe_str(o.get("what_happened") or o.get("what_went_wrong_or_right") or ""))
        ywr = html.escape(_safe_str(o.get("the_reason") or o.get("why_it_went_wrong_or_right") or ""))
        imp = html.escape(_safe_str(o.get("key_lesson") or o.get("improvement_or_preservation") or ""))
        ap  = html.escape(_safe_str(o.get("action_plan") or ""))

        if category == "win":
            return (
                f"<strong>✅ What Went Right:</strong> {wwr}<br>"
                f"<strong>📊 Why It Worked:</strong> {ywr}<br>"
                f"<strong>🔒 What to Preserve:</strong> {imp}<br>"
                f"<strong>🎯 Action Plan:</strong> {ap}"
            )
        elif category == "neutral":
            return (
                f"<strong>🔄 What Happened:</strong> {wwr}<br>"
                f"<strong>📐 Why It Was Managed Out:</strong> {ywr}<br>"
                f"<strong>⚖️ Was the Decision Correct?</strong> {imp}<br>"
                f"<strong>🎯 Action Plan for Similar Setups:</strong> {ap}"
            )
        else:  # loss
            return (
                f"<strong>❌ What Went Wrong:</strong> {wwr}<br>"
                f"<strong>🔍 Why It Went Wrong:</strong> {ywr}<br>"
                f"<strong>💡 What Can Be Improved:</strong> {imp}<br>"
                f"<strong>🎯 Action Plan to Prevent Recurrence:</strong> {ap}"
            )
    except Exception as e:
        print(f"  [WARN] JSON parse failed: {e}")
        return f"<strong>Post-Mortem:</strong> {html.escape(resp.strip()[:300])}"


def update_lessons():
    """Upgrade any resolved trade whose lesson is missing or not yet in structured HTML format.
    
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
        f"&order=created_at.desc&limit=200"
    )
    r = httpx.get(url, headers=headers)
    if r.status_code != 200:
        print(f"  [ERROR] Failed to fetch trades: {r.status_code}")
        return

    trades = r.json()
    # Only process trades that don't yet have the structured 4-part HTML lesson
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
        print(f"  Generating lesson for {sym} ({outcome})...")

        lesson = _build_lesson(trade)
        time.sleep(2)  # Throttle to stay within Groq RPM limits

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
