import os
import sys
import json
from datetime import datetime
from pathlib import Path
import httpx
from dotenv import load_dotenv

# Add engine directory to sys.path so we can import apex_quant
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load .env file from engine/ directory
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from apex_quant.config import get_config
from apex_quant.ai.client import DeepSeekLLM

SUPABASE_URL = "https://dtiuwllodzqpbwohzrgj.supabase.co"
SUPABASE_ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k"
MEMORY_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_research_memory"

headers = {
    "apikey": SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
    "Content-Type": "application/json"
}

def update_lessons():
    # Load configuration
    cfg = get_config()
    llm = DeepSeekLLM(cfg=cfg.ai)
    
    if not llm.available:
        print("[ERROR] DeepSeek LLM is not configured/available. Please check your config.yaml.")
        return
        
    print("Fetching resolved trades missing lessons from Supabase...")
    # Fetch rows that are resolved (tp_hit, sl_hit, expired, invalidated) but have no lesson
    url = f"{MEMORY_ENDPOINT}?outcome=in.(tp_hit,sl_hit,expired,invalidated)"
    r = httpx.get(url, headers=headers)
    if r.status_code != 200:
        print(f"Error fetching trades: {r.status_code} - {r.text}")
        return
        
    trades = r.json()
    need_lessons = [t for t in trades if not t.get("lesson")]
    
    if not need_lessons:
        print("All resolved trades already have post-mortem lessons! Knowledge is fully up to date.")
        return
        
    print(f"Found {len(need_lessons)} resolved trades missing lessons. Generating post-mortems...")
    
    count = 0
    for trade in need_lessons:
        trade_id = trade["id"]
        sym = trade["symbol"]
        direction = trade["verdict"]
        entry = trade.get("price")
        sl = trade.get("stop_loss")
        tp = trade.get("target_price")
        outcome = trade.get("outcome")
        summary = trade.get("summary") or ""
        tech = trade.get("technical_analysis") or ""
        
        prompt = f"""
Trade Details:
Symbol: {sym}
Direction: {direction}
Entry Price: {entry}
Stop Loss: {sl}
Take Profit: {tp}
Outcome: {outcome}

Analysis Summary:
{summary}

Technical Context:
{tech}

Write the lesson: what did the thesis get right or wrong? Summarize the ONE key transferable lesson to watch for on a structurally-similar setup next time. Strict JSON only.
"""
        system = 'You are a blunt trading post-mortem analyst. You review a CLOSED trade idea against what actually happened and extract the single most useful, transferable lesson. Be specific and honest — name the mistake if there was one. Reply ONLY with strict JSON: {"lesson":"<1-2 sentences>"}.'
        
        print(f"Analyzing {sym} ({trade_id}) -> Outcome: {outcome}...")
        resp = llm.complete(prompt, system=system, temperature=0.3)
        if not resp:
            print(f"  [WARN] Failed to get response from AI for {trade_id}")
            continue
            
        # Parse JSON response
        lesson = ""
        try:
            # Clean up markdown code block wrapping if present
            clean_resp = resp.strip()
            if clean_resp.startswith("```"):
                clean_resp = clean_resp.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            o = json.loads(clean_resp)
            lesson = o.get("lesson", "").strip()
        except Exception:
            # Fallback: use raw response
            lesson = resp.strip()[:150]
            
        if not lesson:
            print(f"  [WARN] Extracted empty lesson for {trade_id}")
            continue
            
        # Patch lesson to Supabase
        patch_url = f"{MEMORY_ENDPOINT}?id=eq.{trade_id}"
        patch_payload = {"lesson": lesson}
        try:
            up_r = httpx.patch(patch_url, headers=headers, json=patch_payload)
            if up_r.status_code in (200, 204):
                print(f"  \u2713 Saved Lesson: \"{lesson}\"")
                count += 1
            else:
                print(f"  [ERROR] Failed to save lesson: {up_r.status_code} - {up_r.text}")
        except Exception as e:
            print(f"  [ERROR] Connection error: {e}")
            
    print(f"\nDone! Successfully updated lessons for {count} trades.")

if __name__ == "__main__":
    update_lessons()
