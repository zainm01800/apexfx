#!/usr/bin/env python3
"""Advance the Book S (Session SMC & Order Flow Engine) $100k forward-paper account.

Book S is a systematic intraday/session quantitative engine with:
- Microstructure edge: Asian Session (00:00 - 06:59 UTC) accumulation liquidity bounds.
- Execution Killzone: London Opening session (07:00 - 11:59 UTC).
- Regime Gate: Higher-Timeframe (Daily) 50 EMA trend filter.
- Dynamic Invalidation: Stop placed at opposite Asian extreme or 1.2x ATR.
- Asymmetric Target: 1:1.80 Risk:Reward ($630 profit target on $350 risk).
- Prop Safety Shield: Strictly 0.35% risk ($350 per trade on $100k capital).
- Fail-Closed Daily Circuit Breaker: Halt new entries if intraday drawdown touches -$1,800 (-1.8%).
- Universe: 6 Highly Liquid FX Pairs (GBP/USD, EUR/USD, USD/CHF, USD/JPY, USD/CAD, AUD/USD).

Usage:
    python scripts/run_paper_portfolio_s.py --prefer-supabase
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

import pandas as pd
from dotenv import load_dotenv

load_dotenv(ENGINE_DIR / ".env")

from apex_quant.config import get_config
from apex_quant.data import ParquetStore, clean
from apex_quant.models.book_s_session_smc import (
    BOOK_LABEL,
    CORE_UNIVERSE,
    advance_book_s_forward,
    new_book_s_state,
    runtime_payload,
    validate_book_s_state,
)
from apex_quant.storage import paper_store

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
        print(f"Warning: Failed to load local state from {path}: {e}", flush=True)
        return None


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
        print(f"Warning: Remote Supabase fetch failed: {e}", flush=True)
    return None


def load_panels(store: ParquetStore) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    hourly = {}
    daily = {}
    for sym in CORE_UNIVERSE:
        df_h = store.load(sym, "1h")
        if df_h is not None and not df_h.empty:
            df_h = clean(df_h)
            df_h.sort_index(inplace=True)
            hourly[sym] = df_h
            
        df_d = store.load(sym, "1d")
        if df_d is not None and not df_d.empty:
            df_d = clean(df_d)
            df_d.sort_index(inplace=True)
            daily[sym] = df_d
            
    return hourly, daily


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance Book S Session SMC $100k forward paper")
    parser.add_argument("--as-of", default="", help="Process bars up to this cutoff (default: latest)")
    parser.add_argument("--seed-date", default=DEFAULT_SEED_DATE, help="Seed date if initializing fresh state")
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--no-supabase", action="store_true")
    parser.add_argument("--prefer-supabase", action="store_true")
    parser.add_argument("--reseed", action="store_true", help="Force fresh reseed from seed-date")
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
        state = new_book_s_state(seed_dt)
        origin = f"fresh_seeded_{args.seed_date}"
        print(f"Initialized fresh Book S state seeded on {args.seed_date} with $100,000.00 USD.", flush=True)
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
