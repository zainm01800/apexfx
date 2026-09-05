#!/usr/bin/env python3
"""Local Book S repaired-paper driver and fresh hourly/daily data helpers.

Production uses run_repaired_paper.py and isolated atomic runtime documents.
This compatibility driver refuses legacy schema/fallback reseeding and can
initialize only a separate explicitly requested local ledger. Paper only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import warnings

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd
from dotenv import load_dotenv

load_dotenv(ENGINE_DIR / ".env")

from apex_quant.config import get_config
from apex_quant.data import ParquetStore, clean, get_adapter
from run_paper_portfolio import _top_up
from apex_quant.models.book_s_session_smc import (
    BOOK_LABEL,
    CORE_UNIVERSE,
    advance_book_s_forward,
    compute_pending_radar,
    new_book_s_state,
    runtime_payload,
    validate_book_s_state,
)
from apex_quant.storage import paper_store
from apex_quant.models.paper_readiness import require_restored_state, require_daily_panel, require_hourly_panel

STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio_s" / "state.json"
PUBLIC_SNAPSHOT_PATH = ENGINE_DIR.parent / "public" / "book-s-paper-snapshot.json"
DEFAULT_SEED_DATE = "2026-08-01"


def _load_local_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        val = json.loads(path.read_text(encoding="utf-8"))
        validate_book_s_state(val)
        return val
    except Exception as e:
        raise ValueError(f"Invalid saved Book S state; refusing fallback/reseed") from e


def _save_local_state(path: Path, state: dict) -> None:
    validate_book_s_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _restore_remote_state() -> dict | None:
    try:
        payload = paper_store.fetch_book_s_runtime()
        if isinstance(payload, dict) and "state" in payload:
            state = payload["state"]
            validate_book_s_state(state)
            return state
    except Exception as e:
        raise ValueError("Authoritative remote state invalid/unavailable; refusing fallback/reseed") from e
    return None


def load_panels(store: ParquetStore) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    hourly = {}
    daily = {}
    now = pd.Timestamp.now(tz="UTC")
    adapter = get_adapter("yahoo")
    for sym in CORE_UNIVERSE:
        df_h = store.load(sym, "1h")
        start = df_h.index[-1] - pd.Timedelta(days=3) if df_h is not None and not df_h.empty else now - pd.Timedelta(days=60)
        fetched = adapter.get_history(sym, start, now, "1h")
        df_h = pd.concat([df_h, fetched]) if df_h is not None else fetched
        df_h = df_h[~df_h.index.duplicated(keep="last")].sort_index()
        df_h = df_h[df_h.index + pd.Timedelta(hours=1, minutes=2) <= now]
        if df_h is not None and not df_h.empty:
            df_h = clean(df_h)
            df_h.sort_index(inplace=True)
            hourly[sym] = df_h
            
        df_d = _top_up(store, adapter, sym, now.normalize(), now)
        if df_d is not None and not df_d.empty:
            df_d = clean(df_d)
            df_d.sort_index(inplace=True)
            daily[sym] = df_d[df_d.index < now.normalize()]
            
    require_hourly_panel(hourly, CORE_UNIVERSE, now)
    require_daily_panel(daily, CORE_UNIVERSE, now.normalize())
    return hourly, daily


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance Book S Session SMC $100k forward paper")
    parser.add_argument("--as-of", default="", help="Process bars up to this cutoff (default: latest)")
    parser.add_argument("--seed-date", default=DEFAULT_SEED_DATE, help="Seed date if initializing fresh state")
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--no-supabase", action="store_true")
    parser.add_argument("--prefer-supabase", action="store_true")
    parser.add_argument("--reseed", action="store_true", help="Force fresh reseed from seed-date")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without ledger or mirror writes")
    args = parser.parse_args(argv)

    state_path = Path(args.state)
    cfg = get_config()
    store = ParquetStore(cfg.store_path)

    print("Loading 1H and Daily FX panels for Book S...", flush=True)
    hourly_panel, daily_panel = load_panels(store)
    if not hourly_panel:
        print("Error: No 1H data available in store for Book S universe.", flush=True)
        return 1

    print(f"Loaded {len(hourly_panel)} instruments into Book S hourly panel.", flush=True)

    all_dates = set()
    for df in hourly_panel.values():
        all_dates.update(df.index)
    calendar = sorted(list(all_dates))
    max_dt = calendar[-1]

    if args.as_of:
        cutoff = pd.Timestamp(args.as_of, tz="UTC") if max_dt.tzinfo else pd.Timestamp(args.as_of)
    else:
        cutoff = max_dt

    # State resolution
    state = None
    origin = "fresh"
    if args.reseed and state_path.exists():
        raise ValueError("Cannot overwrite an existing ledger; choose a separate state path")
    if not args.reseed:
        if args.prefer_supabase and not args.no_supabase:
            state = _restore_remote_state()
            if state is not None:
                origin = "supabase"
            else:
                raise ValueError("Authoritative Supabase state unavailable; local fallback prohibited")
        if state is None:
            state = _load_local_state(state_path)
            if state is not None:
                origin = "local_json"

    require_restored_state(state, initialize=args.reseed, no_remote=args.no_supabase,
                           state_path=state_path, original_path=STATE_PATH)
    if state is None:
        if args.as_of or args.seed_date != DEFAULT_SEED_DATE:
            raise ValueError("Repaired forward initialization cannot be backdated")
        state = new_book_s_state(pd.Timestamp.now(tz="UTC"))
        state["last_processed_time"] = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S")
        if not args.dry_run:
            _save_local_state(state_path, state)
        print("Separate repaired ledger initialized; no historical trades imported.", flush=True)
        return 0
    else:
        print(f"Restored Book S state from {origin}. Last processed: {state.get('last_processed_time')}", flush=True)

    # Advance the state
    print(f"Advancing Book S forward up to {cutoff}...", flush=True)
    state, new_daily = advance_book_s_forward(state, hourly_panel, daily_panel, cutoff)

    print(f"Advanced {len(new_daily)} daily bars.", flush=True)
    print(f"Current Equity:   ${state['equity']:,.2f} USD")
    print(f"Uninvested Cash:  ${state['cash']:,.2f} USD")
    print(f"Open Positions:   {len(state['positions'])}")
    print(f"Total Trades:     {len(state['trades'])}")
    
    wins = [t for t in state['trades'] if t.get('win')]
    losses = [t for t in state['trades'] if not t.get('win')]
    wr = (len(wins) / len(state['trades']) * 100) if state['trades'] else 0.0
    pf = (sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses))) if losses and sum(t['pnl'] for t in losses) != 0 else 0.0
    print(f"Closed Win Rate:  {wr:.1f}% | Profit Factor: {pf:.2f}", flush=True)

    # Compute real-time pending setups and breakout trigger levels for the session
    state["pending_radar"] = compute_pending_radar(hourly_panel, daily_panel)
    print(f"Computed {len(state['pending_radar'])} pending setup triggers for Session Radar.", flush=True)

    if args.dry_run:
        print("DRY RUN: no local ledger, public snapshot or remote writes", flush=True)
        return 0

    # Save local state
    _save_local_state(state_path, state)
    print(f"State saved locally to {state_path}", flush=True)

    # Export public snapshot JSON
    payload = runtime_payload(state)
    PUBLIC_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_SNAPSHOT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Public snapshot written to {PUBLIC_SNAPSHOT_PATH}", flush=True)

    # Mirror to Supabase
    if not args.no_supabase:
        print("Mirroring Book S runtime payload to Supabase...", flush=True)
        ok_remote = paper_store.write_book_s_runtime(payload)
        print(f"Supabase mirror status: runtime fallback={'OK' if ok_remote else 'FAILED'}", flush=True)
        if not ok_remote:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
