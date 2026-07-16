"""One-off repair: re-derive `outcome` for ALREADY-RESOLVED setups.

Why this exists
---------------
`resolve_closed_mt4_setups()` only ever queries `?outcome=eq.pending`, so a row is
classified exactly once — at resolution time — and never revisited. Any row that was
resolved by an older build (before the exit-price tolerance logic landed) is stuck
with a stale, wrong `outcome` forever.

That matters now because `update_lessons._classify_trade()` trusts `outcome`
completely (tp_hit=win / sl_hit=loss / else=neutral). A row wrongly marked `tp_hit`
therefore gets a confident "What Went Right" post-mortem on a trade that never
reached its target — which is exactly the bug seen on the cards.

This script recomputes `outcome` from the row's own stored `outcome_price` versus its
`target_price` / `stop_loss`, using the SAME tolerance rule as the live resolver, and
patches only the rows whose lesson CATEGORY (win/neutral/loss) is actually wrong.
Fixing `outcome` is enough: `update_lessons._needs_structured_lesson()` detects the
category mismatch against the stored emoji and regenerates the lesson on its own.

Usage
-----
    cd engine
    .venv-mac/bin/python scripts/backfill_outcomes.py           # DRY RUN (default)
    .venv-mac/bin/python scripts/backfill_outcomes.py --apply   # actually patch
"""

from __future__ import annotations

import os
import sys
from collections import Counter

import httpx

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

SUPABASE_URL = "https://dtiuwllodzqpbwohzrgj.supabase.co"
SUPABASE_ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k"
)
MEMORY_ENDPOINT = f"{SUPABASE_URL}/rest/v1/apex_research_memory"

headers = {
    "apikey": SUPABASE_ANON,
    "Authorization": f"Bearer {SUPABASE_ANON}",
    "Content-Type": "application/json",
}


def _category(outcome: str) -> str:
    """Mirror of update_lessons._classify_trade: the lesson bucket for an outcome."""
    o = str(outcome or "").lower().strip()
    if o == "tp_hit":
        return "win"
    if o == "sl_hit":
        return "loss"
    return "neutral"


def _derive_outcome(verdict: str, close_price: float, tp: float, sl: float) -> str:
    """EXACT copy of the live resolver's rule (run_live_paper_trading.py)."""
    tolerance = close_price * 0.0002
    is_sell = str(verdict or "").upper() in ("SELL", "SHORT")
    if is_sell:
        if tp > 0 and close_price <= (tp + tolerance):
            return "tp_hit"
        if sl > 0 and close_price >= (sl - tolerance):
            return "sl_hit"
        return "invalidated"
    if tp > 0 and close_price >= (tp - tolerance):
        return "tp_hit"
    if sl > 0 and close_price <= (sl + tolerance):
        return "sl_hit"
    return "invalidated"


def _f(val) -> float:
    try:
        return float(val or 0.0)
    except (TypeError, ValueError):
        return 0.0


def backfill(apply: bool = False) -> None:
    url = (
        f"{MEMORY_ENDPOINT}"
        f"?outcome=in.(tp_hit,sl_hit,invalidated,expired)"
        f"&order=created_at.desc"
    )
    try:
        from apex_quant.storage.supabase_util import fetch_all_rows
        rows = fetch_all_rows(url, headers)
    except Exception as e:
        print(f"[ERROR] fetch failed: {e}")
        return
    print(f"Fetched {len(rows)} resolved setups.\n")

    changes, skipped_no_price, unchanged = [], 0, 0
    for row in rows:
        stored = str(row.get("outcome") or "").lower().strip()
        close_price = _f(row.get("outcome_price"))
        tp, sl = _f(row.get("target_price")), _f(row.get("stop_loss"))
        verdict = row.get("verdict")

        # Can't re-derive without an exit price or any target to compare against.
        if close_price <= 0 or (tp <= 0 and sl <= 0):
            skipped_no_price += 1
            continue

        derived = _derive_outcome(verdict, close_price, tp, sl)
        # Only touch rows whose LESSON CATEGORY is actually wrong — minimise writes
        # and never churn expired<->invalidated (both are 'neutral').
        if _category(derived) == _category(stored):
            unchanged += 1
            continue
        changes.append((row, stored, derived))

    print(f"  unchanged (category already correct): {unchanged}")
    print(f"  skipped (no exit price / no TP+SL):   {skipped_no_price}")
    print(f"  NEEDING REPAIR:                        {len(changes)}\n")

    if not changes:
        print("Nothing to fix.")
        return

    moves = Counter(f"{_category(s)} -> {_category(d)}" for _row, s, d in changes)
    print("Category shifts:")
    for k, v in moves.most_common():
        print(f"  {k:22s} {v}")

    print(f"\n{'symbol':12s} {'dir':5s} {'exit':>10s} {'TP':>10s} {'SL':>10s}  {'stored':>12s} -> {'derived':<12s}")
    print("-" * 88)
    for row, stored, derived in changes[:40]:
        print(f"{str(row.get('symbol'))[:11]:12s} {str(row.get('verdict'))[:4]:5s} "
              f"{_f(row.get('outcome_price')):10.5f} {_f(row.get('target_price')):10.5f} "
              f"{_f(row.get('stop_loss')):10.5f}  {stored:>12s} -> {derived:<12s}")
    if len(changes) > 40:
        print(f"... and {len(changes) - 40} more")

    if not apply:
        print(f"\nDRY RUN — nothing written. Re-run with --apply to patch {len(changes)} rows.")
        return

    print(f"\nApplying {len(changes)} patches...")
    ok = 0
    for row, _stored, derived in changes:
        pr = httpx.patch(
            f"{MEMORY_ENDPOINT}?id=eq.{row['id']}",
            headers=headers,
            json={"outcome": derived},
            timeout=30,
        )
        if pr.status_code in (200, 204):
            ok += 1
        else:
            print(f"  [ERROR] {row.get('symbol')}: {pr.status_code} {pr.text[:120]}")
    print(f"Done. Patched {ok}/{len(changes)} rows.")
    print("update_lessons.py will regenerate the affected lessons on its next cycle.")


if __name__ == "__main__":
    backfill(apply="--apply" in sys.argv)
