"""Regenerates AI lessons in bulk for Resolved setups; NOT called by the live scanner loop."""

import os, sys, json, time, re
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import httpx
from scripts.update_lessons import _build_lesson, _needs_structured_lesson

SUPABASE_URL  = "https://dtiuwllodzqpbwohzrgj.supabase.co"
SUPABASE_ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0."
    "fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k"
)
headers = {
    "apikey": SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
    "Content-Type": "application/json"
}

MEMORY_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_research_memory"

def run_bulk():
    print("Fetching resolved trades needing lesson generation...")
    # Fetch setups with outcome and resolve status
    url = (
        f"{MEMORY_ENDPOINT}"
        f"?outcome=in.(tp_hit,sl_hit,expired,invalidated)"
        f"&order=created_at.desc&limit=1000"
    )
    r = httpx.get(url, headers=headers)
    if r.status_code != 200:
        print(f"Failed to fetch setups: {r.status_code}")
        return

    trades = r.json()
    candidates = [t for t in trades if _needs_structured_lesson(t)]
    
    total = len(candidates)
    print(f"Found {total} setups needing lessons.")
    if not candidates:
        print("All lessons are already up to date!")
        return

    # Process in chunks to handle Groq rate limits
    chunk_size = 15
    for i in range(0, total, chunk_size):
        chunk = candidates[i:i+chunk_size]
        print(f"\nProcessing chunk {i//chunk_size + 1} of {(total + chunk_size - 1)//chunk_size} (size: {len(chunk)})")
        
        for trade in chunk:
            tid = trade["id"]
            sym = trade.get("symbol", "?")
            outcome = trade.get("outcome", "?")
            print(f"  Generating V3 lesson for {tid} {sym} ({outcome})...")
            
            lesson = _build_lesson(trade)
            if not lesson:
                print(f"  [SKIP] Could not build lesson for {tid}")
                time.sleep(2)
                continue
                
            patch_r = httpx.patch(
                f"{MEMORY_ENDPOINT}?id=eq.{tid}",
                headers=headers,
                json={"lesson": lesson}
            )
            if patch_r.status_code in (200, 204):
                print(f"    ✓ Saved lesson")
            else:
                print(f"    [ERROR] Patch failed: {patch_r.status_code}")
            
            # Groq rate limit throttle
            time.sleep(6)
            
        print("Sleeping 10s between chunks to cool down rate limits...")
        time.sleep(10)

if __name__ == "__main__":
    run_bulk()
