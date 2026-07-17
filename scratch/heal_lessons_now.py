import sys
from pathlib import Path
# Insert the 'engine' directory into path so apex_quant can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine" / "scripts"))

import update_lessons as ul
import time
import httpx

def run_heal():
    print("Fetching resolved trades from Supabase...")
    url = (
        f"{ul.MEMORY_ENDPOINT}"
        f"?outcome=in.(tp_hit,sl_hit,expired,invalidated)"
        f"&order=created_at.desc"
    )
    try:
        from apex_quant.storage.supabase_util import fetch_all_rows
        trades = fetch_all_rows(url, ul.headers)
    except Exception as e:
        print(f"  [ERROR] Failed to fetch trades: {e}")
        return

    # Filter for trades needing lessons or lesson updates
    need_lessons = [t for t in trades if ul._needs_structured_lesson(t)]
    print(f"Total resolved setups in DB: {len(trades)}")
    print(f"Setups needing lesson healing: {len(need_lessons)}")
    
    if not need_lessons:
        print("No setups need lesson healing!")
        return

    # Let's heal up to 10 lessons immediately
    batch = need_lessons[:10]
    print(f"Healing {len(batch)} lessons in this batch...")
    
    for trade in batch:
        tid = trade["id"]
        sym = trade.get("symbol", "?")
        outcome = trade.get("outcome", "?")
        print(f"  Healing lesson for {tid} {sym} ({outcome})...")
        
        lesson = ul._build_lesson(trade)
        time.sleep(6) # Rate limit protection
        
        if not lesson:
            print(f"  [SKIP] Could not generate lesson for {tid}")
            continue
            
        patch_r = httpx.patch(
            f"{ul.MEMORY_ENDPOINT}?id=eq.{tid}",
            headers=ul.headers,
            json={"lesson": lesson},
        )
        if patch_r.status_code in (200, 204):
            print(f"  ✓ Successfully healed lesson for {sym} ({tid})")
        else:
            print(f"  [ERROR] Patch failed for {tid}: {patch_r.status_code}")

if __name__ == "__main__":
    run_heal()
