"""Runner for live paper forward-testing books V24 (Noise-Band) and V30 (ATR Breakout).

Usage:
  python3 engine/scripts/run_intraday_forward.py --book v24 --activate
  python3 engine/scripts/run_intraday_forward.py --book v30 --activate
  python3 engine/scripts/run_intraday_forward.py --book all --step
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ENGINE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_DIR))
load_dotenv(ENGINE_DIR / ".env")

from apex_quant.forward_intraday.data import (
    DataUnavailable,
    compute_historical_warmup,
    current_ny_time,
    fetch_live_session_minutes,
    is_us_market_hours,
)
from apex_quant.forward_intraday.engine import (
    export_public_payload,
    new_state,
    step_session,
)
from apex_quant.forward_intraday.spec import BOOKS, BookSpec
from apex_quant.forward_intraday.storage import (
    fetch_remote,
    load_local,
    save_local,
    write_remote_verified,
)


def local_path(book_id: str) -> Path:
    return ENGINE_DIR / "data_store" / f"paper_portfolio_{book_id}" / "state.json"


def run_activate(spec: BookSpec, local_only: bool = False, force: bool = False) -> None:
    path = local_path(spec.book_id)
    now = datetime.now(timezone.utc)

    # Check if remote already exists unless force
    if not local_only and not force:
        remote = fetch_remote(spec)
        if remote.status == "found":
            print(f"[{spec.book_id}] Authoritative state already exists on Supabase; skipping re-seed.")
            return

    state = new_state(spec, now)
    payload = export_public_payload(state, spec)

    save_local(path, payload, spec)
    print(f"[{spec.book_id}] Saved initial £100,000 GBP state locally at {path}")

    if not local_only and os.environ.get("SUPABASE_SERVICE_KEY"):
        write_remote_verified(payload, spec)
        print(f"[{spec.book_id}] Authoritative initial state persisted to Supabase {spec.runtime_id}")


def run_step(spec: BookSpec, local_only: bool = False, dry_run: bool = False) -> None:
    path = local_path(spec.book_id)

    # 1. Load authoritative state
    payload = None
    if not local_only:
        remote = fetch_remote(spec)
        if remote.status == "found":
            payload = remote.payload

    if payload is None:
        payload = load_local(path, spec)

    if payload is None:
        print(f"[{spec.book_id}] No state found. Run with --activate first.")
        return

    state = payload["state"]
    if state.get("halted"):
        print(f"[{spec.book_id}] Account is HALTED by internal risk floor. No advancement.")
        return

    # 2. Get historical warmup (ATR14, volatility, prior close, noise sigmas, FX)
    ny_now = current_ny_time()
    today_str = ny_now.strftime("%Y-%m-%d")

    # If today was already processed, skip
    if state.get("last_processed_session") == today_str and not is_us_market_hours(ny_now):
        print(f"[{spec.book_id}] Session {today_str} already completed.")
        return

    try:
        warmup = compute_historical_warmup(today_str)
    except DataUnavailable as exc:
        print(f"[{spec.book_id}] Warmup data unavailable: {exc}. Blocked.")
        return

    # 3. Get live or completed session minute bars
    try:
        bars = fetch_live_session_minutes(today_str)
    except Exception as exc:
        print(f"[{spec.book_id}] Live bars fetch failed: {exc}. Blocked.")
        return

    if bars.empty or len(bars) < 30:
        print(f"[{spec.book_id}] Insufficient intraday bars for session {today_str} (bars: {len(bars)}). Waiting for market open.")
        # Update metadata timestamp
        payload["metadata"]["last_data_as_of"] = ny_now.isoformat()
        if not dry_run:
            save_local(path, payload, spec)
            if not local_only and os.environ.get("SUPABASE_SERVICE_KEY"):
                write_remote_verified(payload, spec)
        return

    # 4. Advance execution state
    new_st = step_session(state, spec, warmup, bars, today_str)
    new_payload = export_public_payload(new_st, spec)

    latest_equity = new_st["equity"]
    pnl = latest_equity - new_st["initial_equity"]
    print(f"[{spec.book_id}] Advanced {today_str}: Equity £{latest_equity:,.2f} (P&L: £{pnl:+,.2f})")

    if dry_run:
        print(f"[{spec.book_id}] Dry run completed without persistence.")
        return

    # 5. Persist locally and remotely
    save_local(path, new_payload, spec)
    if not local_only and os.environ.get("SUPABASE_SERVICE_KEY"):
        write_remote_verified(new_payload, spec)
        print(f"[{spec.book_id}] Persisted to Supabase.")


def main():
    parser = argparse.ArgumentParser(description="SPY Intraday Forward Paper Runner")
    parser.add_argument("--book", choices=["v24", "v30", "all"], default="all", help="Target book")
    parser.add_argument("--activate", action="store_true", help="Initialize £100,000 paper accounts")
    parser.add_argument("--step", action="store_true", help="Run intraday execution step")
    parser.add_argument("--local-only", action="store_true", help="Local persistence only")
    parser.add_argument("--force", action="store_true", help="Force re-seed even if exists")
    parser.add_argument("--dry-run", action="store_true", help="Calculate without persisting")
    args = parser.parse_args()

    books = [BOOKS["v24"], BOOKS["v30"]] if args.book == "all" else [BOOKS[args.book]]

    for spec in books:
        if args.activate:
            run_activate(spec, local_only=args.local_only, force=args.force)
        elif args.step:
            run_step(spec, local_only=args.local_only, dry_run=args.dry_run)
        else:
            # Default action: status check
            path = local_path(spec.book_id)
            data = load_local(path, spec)
            remote = fetch_remote(spec) if not args.local_only else None
            remote_status = remote.status if remote else "skipped"
            if data:
                eq = data.get("state", {}).get("equity", 100000.0)
                print(f"[{spec.book_id}] Local: Equity £{eq:,.2f}, Remote status: {remote_status}")
            else:
                print(f"[{spec.book_id}] Local: not found, Remote status: {remote_status}")


if __name__ == "__main__":
    main()
