"""Synthesizes all trading history into per-symbol knowledge; called by the live scanner loop."""

import os, sys, json, time, re, html as _html
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import httpx

SUPABASE_URL  = "https://dtiuwllodzqpbwohzrgj.supabase.co"
# Prefer the service-role key: the 2026-07-17 RLS lockdown makes anon SELECT-only.
SUPABASE_ANON = os.environ.get("SUPABASE_SERVICE_KEY") or (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0."
    "fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k"
)
GROQ_KEY   = os.environ.get("GROQ_API_KEY", "")
# 2026-07-19: qwen/qwen3-32b retired by Groq (404 on every call) -> 70b-versatile.
GROQ_MODEL = "llama-3.3-70b-versatile"

BASE_HDR   = {"apikey": SUPABASE_ANON, "Authorization": f"Bearer {SUPABASE_ANON}", "Content-Type": "application/json"}
UPSERT_HDR = {**BASE_HDR, "Prefer": "resolution=merge-duplicates"}

_HTML_RE    = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

def strip_html(text):
    if not text: return ""
    text = _COMMENT_RE.sub("", text)
    text = _HTML_RE.sub("", text)
    text = _html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text.strip())

def clean_sym(s):
    return (s or "").replace("-g","").replace(".m","").replace(".ecn","").replace("/","").upper()

def groq_complete(prompt, system):
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {"model": GROQ_MODEL, "messages": [{"role":"system","content":system},{"role":"user","content":prompt}],
                "max_tokens": 900, "temperature": 0.2}
    gh = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            r = httpx.post(url, json=payload, headers=gh, timeout=60)
            if r.status_code == 429:
                wait = 20*(attempt+1); print(f"  [Rate limit] waiting {wait}s..."); time.sleep(wait); continue
            if r.status_code != 200:
                print(f"  [Groq error] {r.status_code}: {r.text[:100]}"); return None
            content = r.json()["choices"][0]["message"]["content"]
            # Strip <think> blocks (chain-of-thought leakage)
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            # Strip markdown bold/italic so the LLM reads clean plain text
            content = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", content)
            # Strip markdown headers
            content = re.sub(r"^#{1,4}\s+", "", content, flags=re.MULTILINE)
            return content
        except Exception as e:
            print(f"  [Groq exception] {e}"); return None
    return None

def fetch_mt4_trades():
    r = httpx.get(f"{SUPABASE_URL}/rest/v1/apex_mt4_trades?status=eq.closed&order=open_time.asc&limit=2000",
                  headers=BASE_HDR, timeout=60)
    return r.json() if r.status_code == 200 else []

def fetch_lessons():
    r = httpx.get(f"{SUPABASE_URL}/rest/v1/apex_research_memory?lesson=not.is.null"
                  f"&outcome=in.(tp_hit,sl_hit,invalidated,expired)&order=created_at.asc&limit=2000",
                  headers=BASE_HDR, timeout=60)
    return r.json() if r.status_code == 200 else []

def synthesise(symbol, mt4_trades, lessons):
    wins   = [t for t in mt4_trades if float(t.get("profit",0)) > 0]
    losses = [t for t in mt4_trades if float(t.get("profit",0)) < 0]
    total_pnl = sum(float(t.get("profit",0)) for t in mt4_trades)

    # Build MT4 trade summary lines
    trade_lines = ""
    for t in mt4_trades:
        pnl  = float(t.get("profit",0))
        op   = float(t.get("open_price",0))
        cp   = float(t.get("close_price",0))
        sl   = float(t.get("sl",0))
        tp   = float(t.get("tp",0))
        vol  = float(t.get("volume",0))
        dur_h = round((t["close_time"] - t["open_time"])/3600, 1) if t.get("close_time") and t.get("open_time") else 0
        direction = "BUY" if t.get("cmd")==0 else "SELL"
        pip  = 0.01 if "JPY" in symbol.upper() else 0.0001
        sl_pips = round(abs(op-sl)/pip, 0) if sl else 0
        dt   = datetime.fromtimestamp(t["open_time"], tz=timezone.utc).strftime("%Y-%m-%d") if t.get("open_time") else "?"
        trade_lines += f"  {dt} {direction} vol={vol} SL={sl_pips}pips pnl=£{pnl:+.2f} duration={dur_h}h\n"

    # Build lessons summary
    lessons_text = ""
    for i, l in enumerate(lessons[:8], 1):
        clean = strip_html(l.get("lesson",""))
        if clean:
            lessons_text += f"\n[Lesson {i} | {l.get('verdict','?')} | {l.get('outcome','?')} | tf={l.get('timeframe','?')}]\n{clean[:350]}\n"

    prompt = f"""You are a quantitative trading strategist reviewing the COMPLETE live trading history for {symbol}.

EXECUTION HISTORY (from live MT4 account — every real trade taken):
Total trades: {len(mt4_trades)}
Wins (profit > 0): {len(wins)}
Losses (profit < 0): {len(losses)}
Total P&L: £{total_pnl:+.2f}
Win rate: {len(wins)/len(mt4_trades)*100:.1f}% if {len(mt4_trades)} > 0 else 0

Individual trades:
{trade_lines}

AI POST-MORTEM LESSONS (where available from the engine's learning system):
{lessons_text if lessons_text else "(No post-mortem lessons available yet for this symbol)"}

Based on ALL of the above, write a STRATEGIC KNOWLEDGE SUMMARY for {symbol}.
This will be read by the trading engine BEFORE opening any new trade on this symbol.

Your summary must clearly state:
1. PATTERN OF WINS: What conditions, timeframes, directions, and market states produced wins?
2. PATTERN OF LOSSES: What patterns consistently caused losses? (e.g. oversized lots on tight stops, trading against trend, specific hours)
3. KEY RISKS: What specific dangers does this symbol present? (volatility, spread, news sensitivity, lot sizing issues)
4. RECOMMENDED CAUTION LEVEL: Low / Medium / High — with specific reasoning from the data
5. WHAT THE ENGINE SHOULD WATCH FOR NEXT TIME: One concrete thing to check before the next trade

Rules:
- Be specific. Reference actual numbers from the trade history (durations, P&L, pip distances, volumes).
- Do NOT recommend disabling the symbol — the engine must keep trading and learning.
- Plain text only. Under 300 words. No markdown formatting.
"""
    system = (
        "You are a rigorous quantitative trading strategist. "
        "Synthesise execution history and lessons into actionable, specific knowledge. "
        "Be honest about failure patterns. Reference actual data. Plain text only."
    )
    return groq_complete(prompt, system)

def upsert_knowledge(symbol, summary, n_trades, win_rate):
    payload = {"symbol": symbol, "summary": summary, "n_trades": n_trades,
               "win_rate": round(win_rate, 4), "updated_at": datetime.now(timezone.utc).isoformat()}
    r = httpx.post(f"{SUPABASE_URL}/rest/v1/apex_symbol_knowledge",
                   json=payload, headers=UPSERT_HDR, timeout=30)
    return r.status_code in (200, 201, 204)

def run():
    if not GROQ_KEY:
        print("[ERROR] GROQ_API_KEY not set."); return

    print("Fetching live MT4 trade history...")
    mt4_all = fetch_mt4_trades()
    print(f"  -> {len(mt4_all)} closed MT4 trades")

    print("Fetching AI post-mortem lessons...")
    lessons_all = fetch_lessons()
    print(f"  -> {len(lessons_all)} lessons found\n")

    # Group MT4 trades by clean symbol
    by_sym = defaultdict(list)
    for t in mt4_all:
        sym = clean_sym(t.get("symbol",""))
        if sym: by_sym[sym].append(t)

    # Group lessons by clean symbol
    lessons_by_sym = defaultdict(list)
    for l in lessons_all:
        sym = clean_sym(l.get("symbol",""))
        if sym: lessons_by_sym[sym].append(l)

    print(f"Processing {len(by_sym)} symbols...\n")
    for sym, trades in sorted(by_sym.items()):
        wins     = sum(1 for t in trades if float(t.get("profit",0)) > 0)
        win_rate = wins / len(trades) if trades else 0.0
        lessons  = lessons_by_sym.get(sym, [])
        print(f"  {sym}: {len(trades)} trades ({win_rate*100:.0f}% win), {len(lessons)} lessons -> synthesising...")

        summary = synthesise(sym, trades, lessons)
        if not summary:
            print(f"    [SKIP] LLM returned nothing for {sym}"); continue

        ok = upsert_knowledge(sym, summary, len(trades), win_rate)
        status = "SAVED" if ok else "ERROR"
        print(f"    [{status}] Knowledge summary stored for {sym}")
        time.sleep(8)

    print("\nDone. All symbol knowledge summaries are up to date.")

if __name__ == "__main__":
    run()
