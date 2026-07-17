import os
import sys
import json
import time
from datetime import datetime
import httpx
from dotenv import load_dotenv
from pathlib import Path

# Add engine directory to sys.path
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

# Load .env
load_dotenv(ENGINE_DIR / ".env")

from apex_quant.config import get_config
from apex_quant.ai.client import DeepSeekLLM

SUPABASE_URL = "https://dtiuwllodzqpbwohzrgj.supabase.co"
# Prefer the service-role key: the 2026-07-17 RLS lockdown makes anon SELECT-only.
SUPABASE_ANON = os.environ.get("SUPABASE_SERVICE_KEY") or "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k"
MEMORY_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_research_memory"

headers = {
    "apikey": SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
    "Content-Type": "application/json"
}

def fetch_all_lessons():
    print("Fetching lessons pool from Supabase...")
    try:
        url = f"{MEMORY_ENDPOINT}?outcome=in.(tp_hit,sl_hit,expired,invalidated)&limit=2000"
        r = httpx.get(url, headers=headers)
        if r.status_code == 200:
            trades = r.json()
            lessons = []
            for t in trades:
                sym = t.get("symbol")
                les = t.get("lesson")
                outcome = t.get("outcome")
                verdict = t.get("verdict")
                if sym and les and les.strip():
                    lessons.append({
                        "symbol": sym,
                        "verdict": verdict,
                        "outcome": outcome,
                        "lesson": les.strip()
                    })
            print(f"✓ Loaded {len(lessons)} raw lessons.")
            return lessons
        print(f"[ERROR] Supabase response: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"[ERROR] Failed to fetch lessons: {e}")
    return []

def group_lessons(lessons):
    groups = {}
    for l in lessons:
        sym = l["symbol"]
        if sym not in groups:
            groups[sym] = []
        groups[sym].append(l)
    return groups

def consolidate_group(symbol, lessons_list, llm):
    lessons_str = ""
    for idx, l in enumerate(lessons_list):
        lessons_str += f"- [{l['verdict']} -> {l['outcome']}]: {l['lesson']}\n"
        
    prompt = f"""
We have compiled multiple historical lessons-learned for the trading instrument {symbol}.
Here is the list of raw, unstructured lessons:
{lessons_str}

DIRECTIVE: Consolidate, deduplicate, and merge these raw lessons into a clean, unified "Trading Playbook" of up to 5 strict, bullet-pointed rules.
Rules should be specific, technical, and refer to indicators (e.g. ADX, RSI, moving averages, volume, or session timing) when applicable.
Do not include generic fluff. Make them actionable warnings for the trading bot.

Return ONLY a strict JSON object with a single key "rules" mapping to an array of strings:
{{
  "rules": [
    "Rule 1 explanation...",
    "Rule 2 explanation..."
  ]
}}
"""
    system = "You are an expert quantitative research analyst. Reply only with valid JSON containing 'rules'."
    
    try:
        resp = llm.complete(prompt, system=system, temperature=0.1, max_tokens=1000)
        if not resp:
            return []
            
        clean_resp = resp.strip()
        if clean_resp.startswith("```"):
            clean_resp = clean_resp.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            
        data = json.loads(clean_resp)
        return data.get("rules", [])
    except Exception as e:
        print(f"  [ERROR] Consolidating {symbol}: {e}")
        return []

def main():
    cfg = get_config()
    llm = DeepSeekLLM(cfg=cfg.ai)
    
    if not llm.available:
        print("[ERROR] DeepSeek API key not found in engine/.env.")
        return
        
    lessons = fetch_all_lessons()
    if not lessons:
        print("No lessons to consolidate.")
        return
        
    groups = group_lessons(lessons)
    print(f"Grouped lessons into {len(groups)} distinct instruments.")
    
    playbooks = {}
    
    # Save directory
    out_dir = ENGINE_DIR / "data_store"
    out_dir.mkdir(parents=True, exist_ok=True)
    playbook_file = out_dir / "playbooks.json"
    
    # Load existing to avoid re-generating everything if we run again
    if playbook_file.exists():
        try:
            with open(playbook_file, "r", encoding="utf-8") as f:
                playbooks = json.load(f)
            print(f"Loaded existing playbooks for {len(playbooks)} instruments.")
        except Exception:
            pass
            
    total = len(groups)
    done = 0
    
    print("\nStarting consolidation run...")
    for sym, l_list in groups.items():
        done += 1
        # Skip if already generated and has rules, unless instrument has new lessons
        if sym in playbooks and len(l_list) <= 3: 
            print(f"  [{done}/{total}] {sym} -> Skipping (already consolidated)")
            continue
            
        print(f"  [{done}/{total}] Consolidating {len(l_list)} lessons for {sym}...", end=" ", flush=True)
        t0 = time.time() if "time" in sys.modules else datetime.utcnow()
        
        rules = consolidate_group(sym, l_list, llm)
        if rules:
            playbooks[sym] = {
                "rules": rules,
                "consolidated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "raw_lesson_count": len(l_list)
            }
            # Save incrementally
            with open(playbook_file, "w", encoding="utf-8") as f:
                json.dump(playbooks, f, indent=2)
            print(f"Done (generated {len(rules)} rules)")
        else:
            print("Failed")
            
    print(f"\n✓ Playbook consolidation complete! Saved {len(playbooks)} playbooks to {playbook_file}")

if __name__ == "__main__":
    main()
