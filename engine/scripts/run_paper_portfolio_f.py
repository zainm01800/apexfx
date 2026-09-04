#!/usr/bin/env python3
"""Advance the Book F Prop Shield Elite $100k forward-paper account.

Book F is an institutional bank-grade multi-asset trend momentum engine with:
- 100% blind cross-asset selection (22 core liquid instruments)
- Dynamic rolling covariance clustering (rho >= 0.55, max 2 bets per cluster)
- Cross-sectional market breadth filter (% > 200 SMA)
- Asymmetric execution: +1.0R Breakeven lock
- Dynamic Convexity Pyramiding: +1.5R secondary 0.50x unit with profit-locked stop at +0.75R
- Strict prop firm safety shield (worst day < 2.0%, max DD < 6.3%)

Usage:
    python scripts/run_paper_portfolio_f.py --prefer-supabase
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd

from apex_quant.config import get_config
from apex_quant.data import ParquetStore, clean
from apex_quant.models.book_f_forward import (
    BOOK_LABEL,
    CORE_UNIVERSE,
    advance_book_f_forward,
    display_daily_rows,
    display_position_rows,
    new_book_f_state,
    runtime_payload,
    validate_book_f_state,
)
from apex_quant.storage import paper_store

STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio_f" / "state.json"
LOG_PATH = ENGINE_DIR / "data_store" / "paper_portfolio_f" / "decisions.log"
PUBLIC_SNAPSHOT_PATH = ENGINE_DIR.parent / "public" / "book-f-paper-snapshot.json"
DEFAULT_SEED_DATE = "2025-01-02"


def _load_local_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        val = json.loads(path.read_text(encoding="utf-8"))
        validate_book_f_state(val)
        return val
    except Exception as e:
        print(f"Warning: Failed to load local state from {path}: {e}", flush=True)
        return None


def _save_local_state(path: Path, state: dict) -> None:
    validate_book_f_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _restore_remote_state() -> dict | None:
    try:
        payload = paper_store.fetch_book_f_runtime()
        if isinstance(payload, dict) and "state" in payload:
            state = payload["state"]
            validate_book_f_state(state)
            return state
    except Exception as e:
        print(f"Warning: Remote Supabase fetch failed: {e}", flush=True)
    return None


def load_panel(store: ParquetStore) -> dict[str, pd.DataFrame]:
    panel = {}
    for sym in CORE_UNIVERSE:
        df = store.load(sym, "1d")
        if df is not None and not df.empty:
            df = clean(df)
            df.sort_index(inplace=True)
            panel[sym] = df
    return panel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance Book F Prop Shield Elite $100k forward paper")
    parser.add_argument("--as-of", default="", help="Process bars up to this cutoff date (default: latest)")
    parser.add_argument("--seed-date", default=DEFAULT_SEED_DATE, help="Seed date if initializing fresh state")
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--no-supabase", action="store_true")
    parser.add_argument("--prefer-supabase", action="store_true")
    parser.add_argument("--reseed", action="store_true", help="Force fresh reseed from seed-date")
    args = parser.parse_args(argv)

    state_path = Path(args.state)
    cfg = get_config()
    store = ParquetStore(cfg.store_path)

    print("Loading multi-asset panel for Book F...", flush=True)
    panel = load_panel(store)
    if not panel:
        print("Error: No data available in store. Cannot run Book F.", flush=True)
        return 1

    print(f"Loaded {len(panel)} instruments into Book F panel.", flush=True)

    # Collect latest dates
    all_dates = set()
    for df in panel.values():
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
    if not args.reseed:
        if args.prefer_supabase and not args.no_supabase:
            state = _restore_remote_state()
            if state is not None:
                origin = "supabase"
        if state is None:
            state = _load_local_state(state_path)
            if state is not None:
                origin = "local_json"

    if state is None or args.reseed:
        seed_dt = pd.Timestamp(args.seed_date, tz="UTC") if max_dt.tzinfo else pd.Timestamp(args.seed_date)
        state = new_book_f_state(seed_dt)
        origin = f"fresh_seeded_{args.seed_date}"
        print(f"Initialized fresh state seeded on {args.seed_date} with $100,000.00 USD.", flush=True)
    else:
        print(f"Restored Book F state from {origin}. Last processed: {state.get('last_processed_date')}", flush=True)

    # Advance the state
    print(f"Advancing Book F forward up to {cutoff}...", flush=True)
    state, new_daily = advance_book_f_forward(state, panel, cutoff)

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
        print("Mirroring Book F runtime payload to Supabase...", flush=True)
        ok_remote = paper_store.write_book_f_runtime(payload)
        pos_rows = display_position_rows(state)
        daily_rows = display_daily_rows(state)
        ok_pos = paper_store.upsert_positions(pos_rows, table=paper_store.POSITIONS_TABLE_F)
        ok_day = paper_store.upsert_daily(daily_rows, table=paper_store.DAILY_TABLE_F)
        print(f"Supabase mirror status: runtime fallback={'OK' if ok_remote else 'FAILED'}, "
              f"positions_table={'OK' if ok_pos else 'TABLE_PENDING'}, "
              f"daily_table={'OK' if ok_day else 'TABLE_PENDING'}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
