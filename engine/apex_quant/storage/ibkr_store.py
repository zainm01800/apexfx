"""Push IBKR paper-account state to Supabase for the website IBKR Terminal.

Tables live in ``supabase/apex_ibkr.sql`` (``apex_ibkr_account``,
``apex_ibkr_positions``, ``apex_ibkr_trades``). Reuses the same project URL +
public anon key as ``paper_store`` (the anon key is shipped to the browser
app, so it is not a secret; SUPABASE_URL / SUPABASE_ANON_KEY env vars
override).

The website is serverless (Vercel) and can ONLY read Supabase — it can never
reach the local IB Gateway. These functions are the bridge: the mirror
(``scripts/run_ibkr_mirror.py``) calls them after a run, or on demand via
``--sync-only``. Every function degrades to ``False`` on ANY error and never
raises: the local mirror record is the primary store and a Supabase outage
must not kill a mirror run.
"""

from __future__ import annotations

from apex_quant.storage.supabase_store import _SUPA_ANON, _SUPA_URL

ACCOUNT_TABLE = "apex_ibkr_account"
POSITIONS_TABLE = "apex_ibkr_positions"
TRADES_TABLE = "apex_ibkr_trades"


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


def sync_account(row: dict) -> bool:
    """Upsert the singleton account snapshot (primary key id=1)."""
    return _post_upsert(ACCOUNT_TABLE, [{**row, "id": 1}])


def sync_positions(rows: list[dict]) -> bool:
    """Replace the open-position set: upsert current rows, then delete rows
    for instruments no longer held (positions are state, not history)."""
    if not _post_upsert(POSITIONS_TABLE, rows):
        return False
    open_instruments = [r["instrument"] for r in rows]
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


def sync_trades(rows: list[dict]) -> bool:
    """Upsert fill records (primary key exec_id, so re-syncing a run merges
    instead of duplicating)."""
    return _post_upsert(TRADES_TABLE, rows)
