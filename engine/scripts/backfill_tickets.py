"""One-off repair: populate `apex_research_memory.ticket` for existing rows.

STEP 1 — run this SQL in the Supabase SQL editor (REST cannot run DDL, and the
anon key has no rights to it, so this is the one step that must be done by hand):

    ALTER TABLE apex_research_memory ADD COLUMN IF NOT EXISTS ticket bigint;
    CREATE INDEX IF NOT EXISTS idx_memory_ticket ON apex_research_memory(ticket);

STEP 2 — run this script to fill the column for historical rows:

    cd engine
    .venv-mac/bin/python scripts/backfill_tickets.py           # DRY RUN (default)
    .venv-mac/bin/python scripts/backfill_tickets.py --apply   # write

Why
---
The setup<->trade linkage was only ever stored inside the free-text `lesson` field
as an HTML comment (`<!-- TICKET_ID: n -->`). That is fragile in three ways:
  * it only exists once a lesson has been generated;
  * it is inherited by scraping the PREVIOUS lesson, so a bad link, once written,
    is copied forward forever and then presented to the dashboard as authoritative;
  * it was originally derived from a broken heuristic (first symbol+direction trade
    opened within 12h), which bound each setup to a NEIGHBOURING trade's ticket.

This script re-derives the link from each setup's exact SL+TP signature. The engine
sends sl/tp with the order and MT4 stores them verbatim, so (symbol, direction, sl,
tp) identifies the trade: measured on live data a 0.1-pip tolerance uniquely
identifies 78/88 (88.6%) of trades, and loosening it only creates ambiguity.

Rows with no signature, or whose signature matches no trade, are left NULL on
purpose — an honest "unlinked" beats a confident wrong link.
"""

from __future__ import annotations

import os
import sys
from collections import Counter

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

SUPABASE_URL = "https://cuvchjhaojhmxfgczndy.supabase.co"
# Prefer the service-role key: the 2026-07-17 RLS lockdown makes anon SELECT-only.
SUPABASE_ANON = os.environ.get("SUPABASE_SERVICE_KEY") or (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM"
)
MEMORY_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_research_memory"
TRADES_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_mt4_trades"

headers = {
    "apikey": SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
    "Content-Type": "application/json",
}

SLTP_TOL_PIPS = 0.1

DDL = """ALTER TABLE apex_research_memory ADD COLUMN IF NOT EXISTS ticket bigint;
CREATE INDEX IF NOT EXISTS idx_memory_ticket ON apex_research_memory(ticket);"""


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _clean(sym: str) -> str:
    return (sym or "").replace("-g", "").replace(".m", "").replace(".ecn", "").replace("/", "").upper()


def _pip(sym: str) -> float:
    return 0.01 if "JPY" in (sym or "").upper() else 0.0001


def _has_ticket_column() -> bool:
    r = httpx.get(f"{MEMORY_ENDPOINT}?select=id,ticket&limit=1", headers=headers, timeout=30)
    return r.status_code == 200


def backfill(apply: bool = False) -> None:
    if not _has_ticket_column():
        print("The `ticket` column does not exist yet.\n")
        print("Run this in the Supabase SQL editor first, then re-run this script:\n")
        print(DDL)
        return

    from apex_quant.storage.supabase_util import fetch_all_rows
    trades = fetch_all_rows(f"{TRADES_ENDPOINT}?order=open_time.desc", headers)
    setups = fetch_all_rows(f"{MEMORY_ENDPOINT}?symbol=ilike.*%2F*&order=created_at.desc", headers)
    print(f"{len(setups)} setups, {len(trades)} MT4 trades.\n")

    plan, unmatched, ambiguous = [], 0, 0
    for s in setups:
        s_sl, s_tp = _f(s.get("stop_loss")), _f(s.get("target_price"))
        if s_sl <= 0 or s_tp <= 0:
            unmatched += 1
            continue
        sym = _clean(s.get("symbol"))
        verdict = str(s.get("verdict", "")).upper().strip()
        tol = SLTP_TOL_PIPS * _pip(sym)
        hits = [
            t for t in trades
            if _clean(t.get("symbol")) == sym
            and ("BUY" if t.get("cmd") == 0 else "SELL") == verdict
            and _f(t.get("sl")) > 0 and _f(t.get("tp")) > 0
            and abs(_f(t["sl"]) - s_sl) <= tol and abs(_f(t["tp"]) - s_tp) <= tol
        ]
        if not hits:
            unmatched += 1
            continue
        if len(hits) > 1:
            ambiguous += 1
            hits.sort(key=lambda t: abs(_f(t.get("open_price")) - _f(s.get("price"))))
        best = hits[0]
        if str(s.get("ticket") or "") == str(best["ticket"]):
            continue  # already correct
        plan.append((s, best["ticket"]))

    print(f"  to link / relink : {len(plan)}")
    print(f"  ambiguous (tie-broken on entry): {ambiguous}")
    print(f"  left NULL (no signature / no match): {unmatched}")

    if plan:
        print(f"\n  {'setup':30s} {'symbol':10s} -> ticket")
        print("  " + "-" * 56)
        for s, tk in plan[:25]:
            print(f"  {str(s['id'])[:29]:30s} {str(s.get('symbol')):10s} -> {tk}")
        if len(plan) > 25:
            print(f"  ... and {len(plan) - 25} more")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write.")
        return

    ok = 0
    for s, tk in plan:
        r = httpx.patch(f"{MEMORY_ENDPOINT}?id=eq.{s['id']}", headers=headers,
                        json={"ticket": tk}, timeout=30)
        if r.status_code in (200, 204):
            ok += 1
        else:
            print(f"  [ERROR] {s['id']}: {r.status_code} {r.text[:100]}")
    print(f"\nDone. Linked {ok}/{len(plan)} setups to their MT4 ticket.")
    print("Cards will now join exactly; update_lessons.py will refresh any stale text.")


if __name__ == "__main__":
    backfill(apply="--apply" in sys.argv)
