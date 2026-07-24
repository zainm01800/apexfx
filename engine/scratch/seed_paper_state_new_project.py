"""One-off seed: push the local paper-portfolio state UP to the new Supabase project.

Migration 2026-07-24 (old project dtiu… egress-blocked -> new project cuvchjha…).
The nightly GitHub Action (.github/workflows/paper-portfolio.yml) restores the
stepper FROM apex_paper_daily / apex_paper_positions on its ephemeral runner
(run_paper_portfolio._restore_from_supabase), so those tables must mirror
engine/data_store/paper_portfolio/state.json BEFORE the first CI run against
the new project. This script does exactly that and nothing else:

  * READ-ONLY toward state.json (the frozen experiment's state is never touched)
  * writes ONLY apex_paper_positions + apex_paper_daily via the stepper's own
    storage layer (apex_quant.storage.paper_store), so auth and URL resolution
    are the same code path the nightly job uses (SUPABASE_URL +
    SUPABASE_SERVICE_KEY from engine/.env)

Row shapes mirror run_paper_portfolio._position_rows / _daily_rows:
  * one apex_paper_daily row per equity_curve point; the LATEST row also carries
    cash (state.realized), n_open, gross_exposure_x and the full state_extra
    restore payload (book/params/peak/halted/cost_total/pending/trades/per_inst/
    constraint_log) — the exact keys _restore_from_supabase reads
  * one apex_paper_positions row per open position, direction as the plain
    string ("long"/"short") _posrow_to_posd expects back

Usage:
    cd engine
    .venv-mac/bin/python scratch/seed_paper_state_new_project.py          # seed + verify
    .venv-mac/bin/python scratch/seed_paper_state_new_project.py --check  # verify only

Prereq: supabase/MIGRATE_2026-07-24.sql already pasted in the new project's SQL
editor (else the upserts 404 and this says so plainly).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENGINE_DIR / ".env")

from apex_quant.storage import paper_store  # noqa: E402
from apex_quant.storage.supabase_store import _SUPA_URL  # noqa: E402

STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio" / "state.json"
STATE_EXTRA_KEYS = (
    "book", "params", "initial_equity", "peak", "halted", "cost_total",
    "pending", "trades", "per_inst", "constraint_log",
)


def _position_rows(state: dict, now_iso: str) -> list[dict]:
    rows = []
    for inst, p in state["open_positions"].items():
        rows.append({
            "instrument": inst,
            "direction": p["direction"],
            "units": p["units"], "initial_units": p["initial_units"],
            "entry_price": p["entry_price"],
            "entry_time": p["entry_time"],
            "entry_idx": int(p.get("entry_idx", 0)),
            "stop": p["stop"], "initial_stop": p["initial_stop"], "target": p["target"],
            "risk_abs": p["risk_abs"], "tf": p["tf"], "last_px": p["last_px"],
            "bars_open": int(p.get("bars_open", 0)),
            "tms_p1": bool(p.get("tms_p1", False)),
            "tms_p2": bool(p.get("tms_p2", False)),
            "tms_be": bool(p.get("tms_be", False)),
            "realized_pnl_total": p.get("realized_pnl_total", 0.0),
            "tms_log": p.get("tms_log", []),
            "updated_at": now_iso,
        })
    return rows


def _daily_rows(state: dict) -> list[dict]:
    curve = state["equity_curve"]                      # [[date, equity], ...] asc
    initial = float(state["initial_equity"])
    extra = {k: state[k] for k in STATE_EXTRA_KEYS if k in state}
    rows, prev_eq, peak = [], initial, 0.0
    n = len(curve)
    for i, (date, eq) in enumerate(curve):
        eq = float(eq)
        peak = max(peak, eq)
        last = i == n - 1
        rows.append({
            "date": date,
            "equity": round(eq, 2),
            "cash": round(float(state["realized"]), 2) if last else None,
            "n_open": len(state["open_positions"]) if last else None,
            "gross_exposure_x": (
                round(sum(abs(p["units"] * p["last_px"])
                          for p in state["open_positions"].values()) / eq, 4)
                if last and eq else None),
            "day_pnl": round(eq - prev_eq, 2),
            "cum_pnl": round(eq - initial, 2),
            "drawdown_from_peak": round(max(0.0, 1.0 - eq / peak), 6) if peak else 0.0,
            "notes": ("seeded from local state.json (Supabase migration 2026-07-24)"
                      if last else "seed curve point"),
            "metrics": None,
            "state_extra": extra if last else None,
        })
        prev_eq = eq
    return rows


def check(state: dict) -> bool:
    curve = state["equity_curve"]
    latest = paper_store.fetch_latest_daily()
    positions = paper_store.fetch_open_positions()
    ok = True
    if not latest:
        print("  apex_paper_daily: NO ROWS (or table missing)")
        ok = False
    else:
        want_date, want_eq = curve[-1][0], round(float(curve[-1][1]), 2)
        got_date, got_eq = str(latest.get("date")), float(latest.get("equity") or 0)
        has_extra = bool(latest.get("state_extra"))
        print(f"  apex_paper_daily latest: {got_date} equity {got_eq} "
              f"(want {want_date} / {want_eq}) state_extra={'yes' if has_extra else 'NO'}")
        ok &= got_date == want_date and abs(got_eq - want_eq) < 0.01 and has_extra
    n_remote = len(positions or [])
    n_local = len(state["open_positions"])
    print(f"  apex_paper_positions: {n_remote} rows (want {n_local})")
    ok &= n_remote == n_local
    return ok


def main() -> int:
    state = json.loads(STATE_PATH.read_text())
    print(f"target: {_SUPA_URL}")
    print(f"local state: last_processed {state['last_processed_date']} | "
          f"{len(state['open_positions'])} open | {len(state['equity_curve'])} curve points")

    if "--check" in sys.argv:
        print("check only:")
        return 0 if check(state) else 1

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pos_rows = _position_rows(state, now_iso)
    daily_rows = _daily_rows(state)

    ok_pos = paper_store.upsert_positions(pos_rows)
    ok_del = paper_store.delete_positions_not_open([r["instrument"] for r in pos_rows])
    ok_day = paper_store.upsert_daily(daily_rows)
    print(f"upserts: positions {'ok' if ok_pos else 'FAILED'}, "
          f"prune {'ok' if ok_del else 'FAILED'}, daily {'ok' if ok_day else 'FAILED'}")
    if not (ok_pos and ok_del and ok_day):
        print("  (the tables probably do not exist yet — paste "
              "supabase/MIGRATE_2026-07-24.sql in the new project's SQL editor first)")
        return 1

    print("verify:")
    return 0 if check(state) else 1


if __name__ == "__main__":
    sys.exit(main())
