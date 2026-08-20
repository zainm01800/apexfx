"""Persistence for the forward paper portfolio.

Tables live in ``supabase/apex_paper_portfolio.sql`` (``apex_paper_positions``,
``apex_paper_daily``). The challenger book (Book B, spill50 — prereg
engine/data_store/pre_registration_paper_challenger_2026-08-11.md) writes to its
own mirror pair, ``apex_paper_b_positions`` / ``apex_paper_b_daily``; every
function takes an optional ``table`` override and defaults to the A tables, so
the frozen proof's behavior is unchanged byte-for-byte. Auth prefers
SUPABASE_SERVICE_KEY (the 2026-07-17 RLS lockdown makes the public anon key
SELECT-only) and falls back to the public anon key — see
``apex_quant.storage._keys``.

Every function degrades to ``False`` / ``None`` on ANY error (table missing,
offline, 4xx) and never raises. Local runs use JSON as their primary store;
the daily GitHub Action explicitly prefers these tables so a tracked checkout
snapshot cannot rewind the forward record, with JSON retained as a fallback.
"""

from __future__ import annotations

from apex_quant.storage._keys import service_or_anon_key
from apex_quant.storage.supabase_store import _SUPA_URL

POSITIONS_TABLE = "apex_paper_positions"
DAILY_TABLE = "apex_paper_daily"

# Challenger book (Book B) mirror tables — same schema/RLS as the A pair.
POSITIONS_TABLE_B = "apex_paper_b_positions"
DAILY_TABLE_B = "apex_paper_b_daily"

# Champion Multi-Horizon book (Book C) mirror tables.
POSITIONS_TABLE_C = "apex_paper_c_positions"
DAILY_TABLE_C = "apex_paper_c_daily"


def _url(table: str) -> str:
    return f"{_SUPA_URL}/rest/v1/{table}"


def _headers(*, prefer: str | None = None) -> dict:
    key = service_or_anon_key()
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _post_upsert(table: str, rows: list[dict]) -> bool:
    if not rows:
        return True
    try:
        import httpx

        with httpx.Client(timeout=20) as c:
            r = c.post(
                _url(table),
                headers=_headers(prefer="resolution=merge-duplicates,return=minimal"),
                json=rows,
            )
            return r.status_code in (200, 201, 204)
    except Exception:
        return False


def _get(table: str, params: dict) -> list | None:
    try:
        import httpx

        with httpx.Client(timeout=20) as c:
            r = c.get(_url(table), headers=_headers(), params=params)
            if r.status_code != 200:
                return None
            return r.json()
    except Exception:
        return None


def upsert_positions(rows: list[dict], table: str = POSITIONS_TABLE) -> bool:
    """Insert/refresh the currently-open position rows (primary key: instrument)."""
    return _post_upsert(table, rows)


def delete_positions_not_open(open_instruments: list[str], table: str = POSITIONS_TABLE) -> bool:
    """Remove rows for positions that are no longer open (state is updated in place)."""
    quoted = ",".join(f'"{i}"' for i in open_instruments) or '""'
    try:
        import httpx

        with httpx.Client(timeout=20) as c:
            r = c.delete(
                _url(table),
                headers=_headers(prefer="return=minimal"),
                params={"instrument": f"not.in.({quoted})"},
            )
            return r.status_code in (200, 204)
    except Exception:
        return False


def upsert_daily(rows: list[dict], table: str = DAILY_TABLE) -> bool:
    """Append the daily snapshot(s). Primary key is ``date``, so re-running a
    day merges rather than duplicating (the local stepper is already idempotent,
    this is belt-and-braces)."""
    return _post_upsert(table, rows)


def fetch_latest_daily(table: str = DAILY_TABLE) -> dict | None:
    rows = _get(table, {"order": "date.desc", "limit": "1"})
    return rows[0] if rows else None


def fetch_daily_curve(table: str = DAILY_TABLE) -> list | None:
    """All daily rows (date, equity) ascending - used to rebuild the equity curve."""
    return _get(table, {"select": "date,equity", "order": "date.asc"})


def fetch_open_positions(table: str = POSITIONS_TABLE) -> list | None:
    return _get(table, {"select": "*"})
