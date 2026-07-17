"""Persistence for the forward paper portfolio.

Tables live in ``supabase/apex_paper_portfolio.sql`` (``apex_paper_positions``,
``apex_paper_daily``). Reuses the same project URL + public anon key as
``supabase_store`` (the anon key is shipped to the browser app, so it is not a
secret; SUPABASE_URL / SUPABASE_ANON_KEY env vars override).

Every function degrades to ``False`` / ``None`` on ANY error (table missing,
offline, 4xx) and never raises: the local JSON state is the primary store, and
the daily GitHub Action restores from these tables, so a Supabase outage must
not kill a paper step.
"""

from __future__ import annotations

from apex_quant.storage.supabase_store import _SUPA_ANON, _SUPA_URL

POSITIONS_TABLE = "apex_paper_positions"
DAILY_TABLE = "apex_paper_daily"


def _url(table: str) -> str:
    return f"{_SUPA_URL}/rest/v1/{table}"


def _headers(*, prefer: str | None = None) -> dict:
    h = {
        "apikey": _SUPA_ANON,
        "Authorization": f"Bearer {_SUPA_ANON}",
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


def upsert_positions(rows: list[dict]) -> bool:
    """Insert/refresh the currently-open position rows (primary key: instrument)."""
    return _post_upsert(POSITIONS_TABLE, rows)


def delete_positions_not_open(open_instruments: list[str]) -> bool:
    """Remove rows for positions that are no longer open (state is updated in place)."""
    quoted = ",".join(f'"{i}"' for i in open_instruments) or '""'
    try:
        import httpx

        with httpx.Client(timeout=20) as c:
            r = c.delete(
                _url(POSITIONS_TABLE),
                headers=_headers(prefer="return=minimal"),
                params={"instrument": f"not.in.({quoted})"},
            )
            return r.status_code in (200, 204)
    except Exception:
        return False


def upsert_daily(rows: list[dict]) -> bool:
    """Append the daily snapshot(s). Primary key is ``date``, so re-running a
    day merges rather than duplicating (the local stepper is already idempotent,
    this is belt-and-braces)."""
    return _post_upsert(DAILY_TABLE, rows)


def fetch_latest_daily() -> dict | None:
    rows = _get(DAILY_TABLE, {"order": "date.desc", "limit": "1"})
    return rows[0] if rows else None


def fetch_daily_curve() -> list | None:
    """All daily rows (date, equity) ascending - used to rebuild the equity curve."""
    return _get(DAILY_TABLE, {"select": "date,equity", "order": "date.asc"})


def fetch_open_positions() -> list | None:
    return _get(POSITIONS_TABLE, {"select": "*"})
