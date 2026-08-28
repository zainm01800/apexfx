#!/usr/bin/env python3
"""Advance the frozen Book R-252 $100k USD forward-paper account.

Book R is a long-only monthly USD ETF momentum control.  This runner collects
the forward evidence that the retrospective audit was missing.  It persists a
separate state document, has no broker integration, and cannot mutate Books
A/B/C.

Normal CI usage::

    python scripts/run_paper_portfolio_r.py --prefer-supabase
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

import exchange_calendars as xcals  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import ParquetStore, clean, get_adapter  # noqa: E402
from apex_quant.research.book_r_forward import (  # noqa: E402
    BOOK_LABEL,
    BOOK_SPEC,
    advance_book_r_forward,
    display_daily_rows,
    display_position_rows,
    runtime_payload,
    validate_forward_state,
)
from apex_quant.research.book_r_usd_etf import USD_ETF_UNIVERSE, common_panel  # noqa: E402
from apex_quant.storage import paper_store  # noqa: E402
from run_paper_portfolio import _top_up, _utc  # noqa: E402


STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio_r" / "state.json"
LOG_PATH = ENGINE_DIR / "data_store" / "paper_portfolio_r" / "decisions.log"
MIN_BARS = max(BOOK_SPEC.lookback, BOOK_SPEC.vol_window) + 1


def _load_local(path: Path) -> dict | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    validate_forward_state(value)
    return value


def _save_local(path: Path, state: dict) -> None:
    validate_forward_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _restore_remote() -> dict | None:
    payload = paper_store.fetch_book_r_runtime()
    state = payload.get("state") if isinstance(payload, dict) else None
    if state is not None:
        validate_forward_state(state)
    return state


def _xnys_month_ends(start: pd.Timestamp, end: pd.Timestamp) -> set[str]:
    """Return official NYSE final sessions, including holiday-shortened months."""
    calendar = xcals.get_calendar("XNYS")
    # exchange-calendars parses session labels as timezone-naive dates.  The
    # price panel itself stays UTC; only these calendar lookup arguments shed
    # their timezone.
    start_date = pd.Timestamp(start).tz_localize(None).normalize()
    end_date = pd.Timestamp(end).tz_localize(None).normalize()
    sessions = calendar.sessions_in_range(start_date, end_date)
    frame = pd.DataFrame({"session": sessions})
    frame["month"] = frame["session"].dt.tz_localize(None).dt.to_period("M")
    final_sessions = frame.groupby("month", observed=True)["session"].max()
    return {pd.Timestamp(value).strftime("%Y-%m-%d") for value in final_sessions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance Book R-252 $100k forward paper")
    parser.add_argument("--as-of", default="",
                        help="process bars strictly before this date 00:00 UTC (default: today)")
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--no-supabase", action="store_true")
    parser.add_argument("--prefer-supabase", action="store_true")
    args = parser.parse_args(argv)

    now = pd.Timestamp.now(tz="UTC")
    cutoff = _utc(args.as_of).normalize() if args.as_of else now.normalize()
    state_path = Path(args.state)
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    adapter = get_adapter("yahoo")

    print("=" * 76, flush=True)
    print(
        f"BOOK R-252 FORWARD PAPER | $100,000 USD | cutoff {cutoff.date()} | "
        f"now {now.isoformat(timespec='seconds')}",
        flush=True,
    )
    print("paper simulation only; no broker orders", flush=True)
    print("=" * 76, flush=True)

    raw_panel: dict[str, pd.DataFrame] = {}
    for inst in USD_ETF_UNIVERSE:
        frame = clean(_top_up(store, adapter, inst, cutoff, now))
        frame = frame[frame.index < cutoff]
        if len(frame) < MIN_BARS:
            print(f"  abort {inst}: {len(frame)} closed bars (< {MIN_BARS})", flush=True)
            return 1
        raw_panel[inst] = frame
    panel = common_panel(raw_panel, USD_ETF_UNIVERSE)
    index = next(iter(panel.values())).index
    latest = index[-1]
    print(f"panel: {len(panel)} USD ETFs | common sessions {len(index)} | latest {latest.date()}")

    local_state = _load_local(state_path)
    state = local_state
    origin = "local" if state is not None else "fresh"
    if args.prefer_supabase and not args.no_supabase:
        remote = _restore_remote()
        if remote is not None:
            state, origin = remote, "supabase"
        elif local_state is not None:
            origin = "local-fallback (Supabase state empty)"
    elif state is None and not args.no_supabase:
        state = _restore_remote()
        origin = "supabase" if state is not None else "fresh"

    month_ends = _xnys_month_ends(index[0], cutoff + pd.Timedelta(days=40))
    state, rows = advance_book_r_forward(panel, state, month_end_sessions=month_ends)
    if not rows:
        print(
            f"state restored from {origin}; no new common sessions since "
            f"{state['last_processed_date']} (idempotent no-op)",
            flush=True,
        )
        return 0

    _save_local(state_path, state)
    log_lines = [
        f"{row['date']} | equity ${row['equity']:,.2f} | cash ${row['cash']:,.2f} | "
        f"open {row['n_open']} | {row['notes']}"
        for row in rows
    ]
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(log_lines) + "\n")
    for line in log_lines:
        print(line, flush=True)

    if not args.no_supabase:
        ok = paper_store.write_book_r_runtime(runtime_payload(state))
        print(f"supabase namespaced Book R mirror: {'ok' if ok else 'FAILED'}", flush=True)
        if not ok:
            print("local state is saved, but CI must not be considered durable until mirror succeeds")
            return 1

    positions = display_position_rows(state)
    latest_row = display_daily_rows(state)[-1]
    print(
        f"active: {BOOK_LABEL} | equity ${latest_row['equity']:,.2f} | "
        f"cash ${latest_row['cash']:,.2f} | positions {len(positions)} | "
        f"costs ${state['cost_total']:,.2f}",
        flush=True,
    )
    if state["pending"]:
        print("pending next-open target:", ", ".join(state["pending"]), flush=True)
    else:
        print("pending next-open target: none (wait for month-end close)", flush=True)
    print(f"state saved: {state_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
