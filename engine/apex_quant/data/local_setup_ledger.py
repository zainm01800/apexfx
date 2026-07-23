"""Durable local record of trade setups, for when Supabase is unavailable.

`open_new_trade()` gated order dispatch on a successful Supabase INSERT:

    r = httpx.post(MEMORY_ENDPOINT, ...)
    if r.status_code in (200, 201, 204):
        _EXECUTOR.submit_order(...)

That makes a cloud database a hard dependency of trading. When Supabase restricted the project
(HTTP 402, exceed_egress_quota) the engine generated signals, sized them, and then silently
dropped every one — twelve in a single scan — because a logging call failed.

The invariant worth keeping is NOT "Supabase must be up". It is **never place an order you
cannot account for**. A local append-only ledger satisfies that just as well, so trading
survives a database outage while the record stays intact. The setup id is generated locally
(`SYMBOL_epoch`) and the IBKR bridge ledger is keyed by the same id, so fill linkage and the
resolver keep working with no cloud round-trip.

Append-only JSONL, fsync'd, one setup per line: a partially-written tail can be skipped
without losing earlier records, which a single re-serialised JSON blob cannot promise.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_lock = threading.Lock()

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data_store" / "local_setups.jsonl"


def record_setup(payload: dict, path: Path | None = None, reason: str = "") -> bool:
    """Append one setup. Returns True only if it is durably on disk.

    The return value gates order dispatch, so it must never report success optimistically:
    the write is flushed and fsync'd before returning.
    """
    p = Path(path) if path else DEFAULT_PATH
    row = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "local_fallback",
        "reason": reason,
        "setup": payload,
    }
    try:
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return True
    except Exception as e:                                   # noqa: BLE001
        print(f"  [LEDGER] FAILED to record setup locally: {e}")
        return False


def read_setups(path: Path | None = None) -> list[dict]:
    """All recorded setups. Skips corrupt lines rather than failing the whole file."""
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue                                          # truncated tail
    return out


def pending_ids(path: Path | None = None) -> list[str]:
    """Setup ids recorded locally — the backlog to reconcile once Supabase returns."""
    return [r["setup"]["id"] for r in read_setups(path)
            if isinstance(r.get("setup"), dict) and r["setup"].get("id")]
