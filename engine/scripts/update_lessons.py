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
      win    — hit TP or closed with a clearly profitable result.
      neutral — manually closed / invalidated / expired before SL or TP;
                could be near-breakeven, small profit, or small managed loss.
                The key is it did NOT hit the stop loss and was managed out.
      loss   — hit the actual stop loss (outcome == sl_hit) or closed with
                a clearly negative result via a non-managed route.
    """
    outcome = trade.get("outcome", "")
    profit_raw = trade.get("profit") or trade.get("pnl") or 0
    try:
        profit_val = float(profit_raw)
    except (TypeError, ValueError):
        profit_val = 0.0

    if outcome == "tp_hit":
        return "win"
    if outcome == "sl_hit":
        return "loss"
    # Invalidated / expired / manually closed
    if outcome in _NEUTRAL_OUTCOMES:
        # If profit is clearly positive it's a managed WIN
        if profit_val > 0:
            return "win"
        # Negative but managed out (not SL) → neutral, not a clean loss
        return "neutral"
    # Unknown outcome — fall back to profit sign
    if profit_val > 0:
        return "win"
    if profit_val < 0:
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


def _build_lesson(trade: dict) -> str | None:
    """Generate a structured 4-part HTML post-mortem using Groq.

    Three output formats based on trade outcome:
      ✅ WIN     — hit TP or closed clearly profitable
      🔄 NEUTRAL — managed out / invalidated / expired without hitting SL
      ❌ LOSS    — hit stop loss or closed with a clear loss
    """
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

    outcome_human = _OUTCOME_LABELS.get(outcome, outcome)
    category = _classify_trade(trade)  # 'win' | 'neutral' | 'loss'

    profit_sign = f"+{profit_val:.2f}" if profit_val >= 0 else f"{profit_val:.2f}"

    if category == "win":
        result_description = f"WINNING TRADE (P&L: {profit_sign}) — {outcome_human}"
        tone_instruction = (
            "This trade was PROFITABLE. Focus on what the trader did right, "
            "why the setup worked, and what patterns/behaviours to preserve and repeat."
        )
    elif category == "neutral":
        result_description = (
            f"MANAGED/NEUTRAL TRADE (P&L: {profit_sign}) — {outcome_human}. "
            "The trade did NOT hit the stop loss. It was managed out or "
            "invalidated before SL/TP."
        )
        tone_instruction = (
            "This trade was NEUTRAL — it did not hit the stop loss and was not a "
            "clean TP hit either. It was managed or closed early. Focus on the "
            "risk management decision, whether the early exit was correct, and how "
            "to handle similar setups in the future. Do NOT treat this as a full loss."
        )
    else:  # loss
        result_description = f"LOSING TRADE (P&L: {profit_sign}) — {outcome_human}"
        tone_instruction = (
            "This trade was a LOSS. Focus on what went wrong technically, "
            "why the setup failed, and what concrete changes will prevent this in future."
        )

    prompt = f"""Trade Details:
Symbol: {sym}
Direction: {direction}
Entry: {entry}  SL: {sl}  TP: {tp}
Result: {result_description}

Analysis: {summary}
Technical: {tech}

{tone_instruction}

Reply ONLY with a JSON object with exactly these 4 keys:
- "what_happened": one clear sentence describing what actually happened in plain English. Do NOT use raw codes like 'sl_hit', 'tp_hit', 'invalidated'.
- "the_reason": the core technical or market structure reason behind the outcome
- "key_lesson": one concrete actionable rule to preserve, improve, or manage better next time
- "action_plan": what the engine will specifically do differently (or keep doing) on the next similar setup

No prose, no markdown, valid JSON only."""

    system = (
        "You are a professional quant trading post-mortem analyst. "
        f"The trade category is: {category.upper()}. "
        "NEVER use raw system codes like sl_hit, tp_hit, invalidated in your response — always write in plain English. "
        "Tailor your tone strictly to the trade category: wins are celebratory/instructive, "
        "neutrals are analytical/balanced, losses are corrective/constructive. "
        "Reply ONLY with valid JSON with keys: what_happened, the_reason, key_lesson, action_plan."
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
