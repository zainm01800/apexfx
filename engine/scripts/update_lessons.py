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


def _needs_structured_lesson(t: dict) -> bool:
    """Return True if this trade's lesson is missing or not yet in the structured 4-part HTML format."""
    lesson = t.get("lesson") or ""
    if not lesson.strip():
        return True
    if "Post-Mortem:" in lesson:
        return True
    return "<strong>" not in lesson


def _build_lesson(trade: dict) -> str | None:
    """Generate a structured 4-part HTML post-mortem using Groq."""
    sym = trade.get("symbol", "?")
    direction = trade.get("verdict", "?")
    entry = trade.get("price", "?")
    sl = trade.get("stop_loss", "?")
    tp = trade.get("target_price", "?")
    outcome = trade.get("outcome", "?")
    summary = (trade.get("summary") or "")[:400]
    tech = (trade.get("technical_analysis") or "")[:400]

    # Determine win/loss from ACTUAL profit first, fall back to outcome code.
    # Using profit > 0 is the ground truth — outcome codes like 'invalidated'
    # can appear on winning manual closes, partial takes, etc.
    profit_raw = trade.get("profit") or trade.get("pnl") or 0
    try:
        profit_val = float(profit_raw)
    except (TypeError, ValueError):
        profit_val = 0.0

    # Outcome code → human description (never let raw codes appear in lesson)
    _OUTCOME_LABELS = {
        "tp_hit":      "Hit Take Profit — closed in full profit",
        "sl_hit":      "Hit Stop Loss — closed at a loss",
        "expired":     "Trade expired due to time limit",
        "invalidated": "Trade was manually closed or invalidated before SL/TP",
    }
    outcome_human = _OUTCOME_LABELS.get(outcome, outcome)

    # Final win/loss decision: profit is the authority
    if profit_val > 0:
        is_win = True
        trade_result_text = f"WINNING TRADE (+{profit_val:.2f}) — {outcome_human}"
    elif profit_val < 0:
        is_win = False
        trade_result_text = f"LOSING TRADE ({profit_val:.2f}) — {outcome_human}"
    else:
        # Fallback to outcome code if profit is zero/missing
        is_win = outcome == "tp_hit"
        trade_result_text = outcome_human

    prompt = f"""Trade Details:
Symbol: {sym}
Direction: {direction}
Entry: {entry}  SL: {sl}  TP: {tp}
Result: {trade_result_text}

Analysis: {summary}
Technical: {tech}

This was a {'WINNING' if is_win else 'LOSING'} trade.

Reply ONLY with a JSON object with exactly these 4 keys:
- "what_happened": one sentence describing what actually happened (e.g. 'Price moved strongly in our direction and hit take profit' or 'Price reversed and stopped us out'). Do NOT use raw codes like 'sl_hit', 'tp_hit', 'invalidated'.
- "the_reason": the core technical or market structure reason behind the outcome
- "key_lesson": one concrete actionable rule to preserve or improve next time
- "action_plan": what the engine will specifically do differently (or keep doing) next time

No prose, no markdown, valid JSON only."""

    system = (
        "You are a professional quant trading post-mortem analyst. "
        f"The trade being analysed is a {'WINNING' if is_win else 'LOSING'} trade. "
        "Analyse closed trades and extract structured lessons. "
        "NEVER use raw system codes like sl_hit, tp_hit, invalidated in your response — always write in plain English. "
        "Reply ONLY with valid JSON with keys: what_happened, the_reason, key_lesson, action_plan."
    )

    def _safe_str(val) -> str:
        if val is None:
            return ""
        # If it's a string, try to parse it as JSON first in case it's a nested JSON string
        if isinstance(val, str):
            val_stripped = val.strip()
            if (val_stripped.startswith("{") and val_stripped.endswith("}")) or (val_stripped.startswith("[") and val_stripped.endswith("]")):
                try:
                    val = json.loads(val_stripped)
                except Exception:
                    pass
        if isinstance(val, dict):
            parts = []
            for k, v in val.items():
                k_clean = str(k).replace("_", " ").title()
                v_str = _safe_str(v)
                parts.append(f"{k_clean}: {v_str}")
            return " · ".join(parts)
        if isinstance(val, list):
            return " · ".join(_safe_str(x) for x in val)
        # Strip any raw outcome codes that leaked through
        raw_codes = {"sl_hit", "tp_hit", "invalidated", "expired"}
        result = str(val).strip()
        for code in raw_codes:
            result = result.replace(code, _OUTCOME_LABELS.get(code, code))
        return result

    resp = _groq_complete(prompt, system)
    if not resp:
        return None

    try:
        clean = resp.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        o = json.loads(clean)
        # Support both old key names (what_went_wrong_or_right) and new (what_happened)
        wwr = html.escape(_safe_str(o.get("what_happened") or o.get("what_went_wrong_or_right") or ""))
        ywr = html.escape(_safe_str(o.get("the_reason") or o.get("why_it_went_wrong_or_right") or ""))
        imp = html.escape(_safe_str(o.get("key_lesson") or o.get("improvement_or_preservation") or ""))
        ap  = html.escape(_safe_str(o.get("action_plan") or ""))
        if is_win:
            return (
                f"<strong>✅ What Went Right:</strong> {wwr}<br>"
                f"<strong>📊 Why It Worked:</strong> {ywr}<br>"
                f"<strong>🔒 What to Preserve:</strong> {imp}<br>"
                f"<strong>🎯 Action Plan:</strong> {ap}"
            )
        else:
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
