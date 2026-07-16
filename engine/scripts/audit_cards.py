"""Audit and check card linkages in Supabase database.

Matches closed MT4 trades with research memory setups to verify ticket
and signature linkage correctness. Used to monitor match database accuracy.
This script is NOT called by the live scanner loop.
"""

import os
import sys
import httpx

SUPABASE_URL = "https://dtiuwllodzqpbwohzrgj.supabase.co"
SUPABASE_ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k"
headers = {
    "apikey": SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
    "Content-Type": "application/json"
}

def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0

def _clean(sym: str) -> str:
    return (sym or "").replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()

def _pip(sym: str) -> float:
    return 0.01 if "JPY" in (sym or "").upper() else 0.0001

def run_audit():
    # Fetch all setups and all trades
    trades = httpx.get(f"{SUPABASE_URL}/rest/v1/apex_mt4_trades?order=open_time.desc&limit=1000", headers=headers, timeout=60).json()
    setups = httpx.get(f"{SUPABASE_URL}/rest/v1/apex_research_memory?order=created_at.desc&limit=1000", headers=headers, timeout=60).json()

    print(f"Loaded {len(setups)} setups and {len(trades)} trades.")
    
    # Analyze matches for closed trades
    closed_trades = [t for t in trades if t.get("close_time")]
    print(f"Closed trades: {len(closed_trades)}")
    
    correct_ticket = 0
    wrong_ticket = 0
    correct_sig = 0
    wrong_sig = 0
    unlinked = 0
    
    setups_by_id = {s["id"]: s for s in setups}
    setups_by_ticket = {s.get("ticket"): s for s in setups if s.get("ticket") is not None}
    
    for t in closed_trades:
        tk = t["ticket"]
        sym = _clean(t["symbol"])
        verdict = "BUY" if t["cmd"] == 0 else "SELL"
        t_sl = _f(t["sl"])
        t_tp = _f(t["tp"])
        
        # 1. Match via Ticket first
        s_by_tk = setups_by_ticket.get(tk)
        
        # 2. Match via Signature fallback
        tol = 0.1 * _pip(sym)
        sig_matches = [
            s for s in setups
            if _clean(s.get("symbol")) == sym
            and ("BUY" if s.get("verdict", "") in ("BUY", "LONG") else "SELL") == verdict
            and _f(s.get("stop_loss")) > 0
            and abs(_f(s.get("stop_loss")) - t_sl) <= tol
            and abs(_f(s.get("target_price")) - t_tp) <= tol
        ]
        
        if s_by_tk:
            # Double check if ticket matches signature
            is_valid_sig = (
                _clean(s_by_tk.get("symbol")) == sym
                and ("BUY" if s_by_tk.get("verdict", "") in ("BUY", "LONG") else "SELL") == verdict
                and abs(_f(s_by_tk.get("stop_loss")) - t_sl) <= tol
            )
            if is_valid_sig:
                correct_ticket += 1
            else:
                wrong_ticket += 1
                print(f"WRONG TICKET: Trade {tk} {sym} matched to setup {s_by_tk['id']} but SL/TP signature mismatch!")
        elif sig_matches:
            # Matched via signature but no ticket column linkage
            best_sig = sig_matches[0]
            # Verify if it has another ticket
            if best_sig.get("ticket") is None:
                correct_sig += 1
            else:
                wrong_sig += 1
                print(f"WRONG SIG: Trade {tk} {sym} matches signature of setup {best_sig['id']} which is linked to ticket {best_sig.get('ticket')}!")
        else:
            unlinked += 1

    print("\n" + "="*50)
    print("AUDIT RESULTS:")
    print(f"  Total Closed Trades: {len(closed_trades)}")
    print(f"  CORRECT (Ticket matched): {correct_ticket}")
    print(f"  WRONG (Ticket mismatched): {wrong_ticket}")
    print(f"  CORRECT (Signature matched, unlinked ticket): {correct_sig}")
    print(f"  WRONG (Signature matched, conflicting ticket): {wrong_sig}")
    print(f"  UNLINKED: {unlinked}")
    print("="*50)

if __name__ == "__main__":
    run_audit()
