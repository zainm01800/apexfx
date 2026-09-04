#!/usr/bin/env python3
"""Advance Book V6 or V10 as an isolated GBP100k forward-paper account.

This command has no broker imports or order route.  Normal mode requires the
Supabase service-role key, verifies the namespaced JSONB write by reading it
back, and only then updates the optional local mirror.  ``--dry-run`` performs
all data/state validation without writing either store.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(ENGINE_DIR / ".env")
except ModuleNotFoundError:  # pragma: no cover - optional local convenience
    pass

from apex_quant.forward_v14 import (  # noqa: E402
    advance,
    book_spec,
    enforce_persistence_deadline,
    new_state,
    public_payload,
)
from apex_quant.forward_v14.data import fetch_market_data  # noqa: E402
from apex_quant.forward_v14.storage import (  # noqa: E402
    fetch_remote,
    load_local,
    save_local,
    state_sha256,
    write_remote_verified,
)


def _default_state_path(book_id: str) -> Path:
    return ENGINE_DIR / "data_store" / f"paper_portfolio_{book_id}" / "state.json"


def _pending_identity(state: dict | None) -> tuple[str, str] | None:
    pending = state.get("pending_batch") if state else None
    if not pending:
        return None
    return pending.get("decision_date"), pending.get("eligible_fill_session")


def _has_new_pending(remote_state: dict | None, state: dict) -> bool:
    current = _pending_identity(state)
    return current is not None and current != _pending_identity(remote_state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance a V14 GBP forward-paper book")
    parser.add_argument("--book", required=True, choices=("v6", "v10"))
    parser.add_argument("--state", default="", help="optional local mirror path")
    parser.add_argument("--dry-run", action="store_true", help="validate and step in memory; write nothing")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="explicit offline development mode; never read or write Supabase",
    )
    args = parser.parse_args(argv)
    if args.dry_run and args.local_only:
        parser.error("--dry-run and --local-only are mutually exclusive")

    spec = book_spec(args.book)
    state_path = Path(args.state) if args.state else _default_state_path(spec.book_id)
    print(f"{spec.label} | GBP100,000 | EXPERIMENTAL FORWARD PAPER | NO BROKER")
    market = fetch_market_data()
    print(
        f"fresh bundle: settled XNYS through {market.latest_completed_session.date()} | "
        f"retrieved {market.retrieved_at_utc.isoformat()}"
    )

    local = load_local(state_path, spec)
    remote_state = None
    origin = "fresh activation"
    if not args.local_only:
        remote = fetch_remote(spec)
        if remote.status == "unavailable":
            print(f"ERROR: authoritative remote state unavailable: {remote.detail}", file=sys.stderr)
            return 1
        if remote.status == "found":
            remote_state = remote.payload["state"]
            if local is not None and state_sha256(local) != state_sha256(remote_state):
                print(
                    "ERROR: local and authoritative Supabase state differ; refusing to choose or rewind",
                    file=sys.stderr,
                )
                return 1
            origin = "Supabase"
        elif local is not None:
            print(
                "ERROR: Supabase runtime row is missing while a local state exists; "
                "refusing implicit initialization from local",
                file=sys.stderr,
            )
            return 1
    elif local is not None:
        remote_state = local
        origin = "local-only"

    operation_time = datetime.now(timezone.utc)
    parent_hash = state_sha256(remote_state) if remote_state is not None else None
    if remote_state is None:
        state = new_state(spec, market, now=operation_time)
        rows = [state["daily"][-1]]
    else:
        state, rows = advance(
            remote_state,
            spec,
            market,
            now=operation_time,
            pending_was_durable=(origin == "Supabase"),
        )
        if state_sha256(state) != parent_hash:
            state["parent_state_sha256"] = parent_hash
            state["revision"] = int(remote_state["revision"]) + 1
    # A pre-close/manual rerun may restore a valid pending order after its
    # eligible open but before that session has a settled bar.  It was already
    # durable before the open and must not be mistaken for a newly planned
    # instruction.  The save deadline applies only to a new pending identity.
    if _has_new_pending(remote_state, state):
        state = enforce_persistence_deadline(
            state, spec, now=datetime.now(timezone.utc)
        )
    if remote_state is not None and state_sha256(state) != parent_hash and state["revision"] == remote_state["revision"]:
        state["parent_state_sha256"] = parent_hash
        state["revision"] = int(remote_state["revision"]) + 1
    payload = public_payload(state, spec, generated_at=datetime.now(timezone.utc))
    latest = payload["daily"][-1]
    print(
        f"origin {origin} | new rows {len(rows)} | last {latest['date']} | "
        f"equity GBP{latest['equity']:,.2f} | open {latest['n_open']} | "
        f"pending {len(payload['pending'])} | halted {latest['halted']}"
    )

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "book_id": spec.book_id,
                    "state_sha256": state_sha256(state),
                    "metadata": payload["metadata"],
                },
                sort_keys=True,
            )
        )
        return 0
    if args.local_only:
        save_local(state_path, state, spec)
        print(f"local-only state saved: {state_path}")
        return 0

    if remote_state is not None and state_sha256(state) == parent_hash:
        print("idempotent no-op: authoritative state already covers the latest settled session")
        return 0

    write_remote_verified(payload, spec)
    save_local(state_path, state, spec)
    print(
        f"verified namespaced Supabase runtime {spec.runtime_id}; "
        f"local mirror saved {state_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
