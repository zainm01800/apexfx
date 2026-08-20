"""Materialize Book C's queued entries in the internal paper portfolio.

This command never talks to a broker.  It obtains the selected session's
official opening prints from the configured market-data adapter, applies the
same simulated fill/cost mechanics as the nightly PaperPortfolio step, saves
the local JSON state, and mirrors open rows to the Book C Supabase tables.

The completed daily bar remains unprocessed.  The normal nightly job will mark
the positions at the close and generate the next decisions without managing a
new position against price action that occurred before its entry.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.paper import PaperPortfolio  # noqa: E402
from apex_quant.data import ParquetStore, clean, get_adapter  # noqa: E402
from apex_quant.storage import paper_store  # noqa: E402
from run_paper_portfolio import START_EQUITY, _position_rows, _utc  # noqa: E402
from run_paper_portfolio_c import (  # noqa: E402
    BOOK_CRYPTO,
    BOOK_EQUITIES,
    BOOK_LABEL,
    BOOK_PARAMS,
    EXCLUDED,
    HALT_DRAWDOWN,
    LOG_PATH,
    STATE_PARAMS,
    STATE_PATH,
    WARMUP,
    TrendBook,
    _cfg,
    _migrate_promoted_risk_state,
)
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402


def _state_extra(stepper: PaperPortfolio) -> dict:
    state = stepper.to_state()
    return {k: state[k] for k in (
        "book", "params", "initial_equity", "peak", "halted", "cost_total",
        "pending", "trades", "per_inst", "constraint_log", "last_processed_date",
    )}


def _opening_prices(adapter, instruments: list[str], entry_date: pd.Timestamp,
                    now: pd.Timestamp) -> dict[str, float]:
    prices: dict[str, float] = {}
    fetch_start = entry_date - pd.Timedelta(days=3)
    for inst in instruments:
        frame = adapter.get_history(inst, fetch_start, now, "1d")
        if frame.empty:
            raise RuntimeError(f"no market data returned for {inst}")
        normalized = pd.DatetimeIndex(frame.index).normalize()
        matches = frame.loc[normalized == entry_date.normalize()]
        if matches.empty:
            raise RuntimeError(f"no {entry_date.date()} opening bar returned for {inst}")
        px = float(matches.iloc[-1]["open"])
        if not np.isfinite(px) or px <= 0:
            raise RuntimeError(f"invalid {entry_date.date()} opening price for {inst}: {px!r}")
        prices[inst] = px
    return prices


def _mirror(stepper: PaperPortfolio, entry_date: pd.Timestamp) -> tuple[bool, bool, bool]:
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = _position_rows(stepper, now_iso)
    ok_pos = paper_store.upsert_positions(rows, table=paper_store.POSITIONS_TABLE_C)
    ok_prune = paper_store.delete_positions_not_open(
        [r["instrument"] for r in rows], table=paper_store.POSITIONS_TABLE_C
    )

    latest = paper_store.fetch_latest_daily(table=paper_store.DAILY_TABLE_C)
    if latest:
        # Preserve the last settled daily observation; only refresh its opaque
        # restore payload. Open positions live in their own current-state table.
        allowed = (
            "date", "equity", "cash", "n_open", "gross_exposure_x", "day_pnl",
            "cum_pnl", "drawdown_from_peak", "metrics",
        )
        daily = {k: latest.get(k) for k in allowed}
        prior_note = str(latest.get("notes") or "").strip()
        marker = f"queued entries opened internally at {entry_date.date()} session open"
        daily["notes"] = f"{prior_note} | {marker}" if prior_note else marker
        daily["state_extra"] = _state_extra(stepper)
        ok_daily = paper_store.upsert_daily([daily], table=paper_store.DAILY_TABLE_C)
    else:
        ok_daily = False
    return ok_pos, ok_prune, ok_daily


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Open queued Book C entries in the internal paper book (never a broker)."
    )
    parser.add_argument("--entry-date", default="", help="UTC session date (default: today)")
    parser.add_argument("--state", default=str(STATE_PATH), help="Book C local state JSON")
    parser.add_argument("--no-supabase", action="store_true", help="skip dashboard mirror")
    args = parser.parse_args(argv)

    now = pd.Timestamp.now(tz="UTC")
    entry_date = _utc(args.entry_date).normalize() if args.entry_date else now.normalize()
    state_path = Path(args.state)
    state = PaperPortfolio.load_state_file(state_path)
    state, migration_note = _migrate_promoted_risk_state(state)
    if state is None:
        print("Book C has no current promoted state to open; run its daily seed first.")
        return 1
    if migration_note:
        print(f"state migration: {migration_note}")

    cfg = _cfg()
    store = ParquetStore(cfg.store_path)
    instruments = [i for i in (BOOK_EQUITIES + BOOK_CRYPTO + FX_MAJORS_7) if i not in EXCLUDED]
    panel: dict[str, pd.DataFrame] = {}
    for inst in instruments:
        frame = clean(store.load(inst, "1d"))
        frame = frame[frame.index < entry_date]
        if not frame.empty:
            panel[inst] = frame

    missing_panel = sorted(set(state.get("universe", [])) - set(panel))
    if missing_panel:
        print(f"cached panel is missing state instruments: {', '.join(missing_panel)}")
        return 1

    strategies = TrendBook(panel, **BOOK_PARAMS).strategies()
    stepper = PaperPortfolio(
        panel, strategies, cfg=cfg,
        timeframes={k: "1d" for k in panel}, warmup=WARMUP,
        state=state, book=BOOK_LABEL, params=STATE_PARAMS,
        halt_drawdown=HALT_DRAWDOWN, initial_equity=START_EQUITY,
    )

    pending = list(stepper.pending_entries)
    entries: list[dict] = []
    if pending:
        adapter = get_adapter("yahoo")
        prices = _opening_prices(adapter, pending, entry_date, now)
        entries = stepper.open_pending_at(entry_date, prices)
        stepper.save_state(state_path)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            for item in entries:
                handle.write(
                    f"{entry_date.date()} | INTRADAY PAPER ENTRY {item['instrument']} "
                    f"{item['direction']} {item['units']} @ {item['entry_price']} "
                    f"(official open {item['raw_open']})\n"
                )
    elif not stepper.open_positions:
        print("Book C has neither queued entries nor open positions; nothing to do.")
        return 1

    if not args.no_supabase:
        mirror = _mirror(stepper, entry_date)
        print(
            "dashboard mirror: positions %s, prune %s, state %s"
            % tuple("ok" if value else "FAILED" for value in mirror)
        )
        if not all(mirror):
            return 1

    if entries:
        print(f"opened {len(entries)} Book C internal paper positions at {entry_date.date()} opens:")
        for item in entries:
            print(
                f"  {item['instrument']} {item['direction']} | units {item['units']} | "
                f"official open {item['raw_open']:.6f} | simulated fill {item['entry_price']:.6f}"
            )
    else:
        print(f"no-op: {len(stepper.open_positions)} Book C positions were already open; mirror refreshed")
    print("broker orders sent: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
