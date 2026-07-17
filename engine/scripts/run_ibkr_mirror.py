"""Daily IBKR paper mirror for the FROZEN multi-asset trend book.

Parallel execution-realism record — NOT part of the frozen experiment (see
engine/data_store/pre_registration_paper_trend_2026-07-17.md, change log #2).
The engine-simulated paper portfolio (scripts/run_paper_portfolio.py) remains
the experiment of record; this script only OBSERVES its state file and
replicates the fills on an IBKR paper account (default DUQ278370, hard
allowlist in apex_quant/execution/ibkr_executor.py) so that real-vs-model
fill divergence can be measured. It never writes to the paper portfolio's
state.

After a run (and on demand via --sync-only) it pushes account + positions
(+ the run's fills) to Supabase (apex_quant/storage/ibkr_store.py, tables in
supabase/apex_ibkr.sql) so the serverless website's IBKR Terminal can read
them — the site can never reach the local Gateway. The Supabase push is
best-effort: it never changes the mirror's exit code.

What it mirrors (from engine/data_store/paper_portfolio/state.json)
-------------------------------------------------------------------
Let D = state["last_processed_date"] (the bar the daily step just processed):
  * ENTRIES: open_positions with entry_time == D — the positions the engine
    filled (at bar D's open, modelled costs) in this step.
  * EXITS:   trades with exit_time == D — the positions the engine closed in
    this step (stop / target / time / trail).
Exits are placed first, then entries (the engine's own intra-step order).

Execution convention and its honest caveat
------------------------------------------
Orders are MARKET, DAY tif (rationale in ibkr_executor's docstring). The
engine's fills are stamped at bar D's open; the mirror runs after the step,
so an equity order placed before the next session queues for THAT session's
open, and crypto/FX fill immediately, ~1 bar after the modelled fill. The
recorded divergence (IBKR avg fill vs engine-sim fill, in bps) therefore
bundles execution lag + real fill quality — it answers "what did replicating
the book's fills on a real venue actually cost", which is the question this
mirror exists for. Commissions charged are recorded per order.

Reconciliation and idempotency
------------------------------
* Before each entry: skipped (with a recorded warning) if IBKR already holds
  a same-direction position in the instrument, or ANY opposite position
  (the mirror never flips a position in one order).
* Before each exit: skipped if IBKR holds nothing in the instrument; closes
  size to the ACTUAL IBKR position, never to the engine's units.
* IBKR crypto (Paxos) is long-only: short crypto entries are skipped and
  recorded as venue-unsupported (a position divergence by design).
* Idempotent per processed bar: once bar D is mirrored
  (engine/data_store/ibkr_mirror/D.json written + mirror_state.json pointer
  advanced), re-runs are a strict no-op. A run that FAILS before writing
  (e.g. gateway down) leaves no record and may be re-run the same day; a
  missed day is never backfilled (it surfaces in post_run_position_check).
* After the run, engine book vs IBKR book is compared and every residual
  mismatch is recorded under post_run_position_check (engine partial exits
  shrink engine units intraday and are NOT traded by the v1 mirror — the
  size drift is reported, not hidden).

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_ibkr_mirror.py                 # mirror latest processed bar
    .venv-mac/bin/python scripts/run_ibkr_mirror.py --dry-run       # print the plan, connect to nothing
    .venv-mac/bin/python scripts/run_ibkr_mirror.py --sync-only     # push account+positions to Supabase, exit
    .venv-mac/bin/python scripts/run_ibkr_mirror.py --timeout-s 180

--sync-only connects to the Gateway, reads account + positions, pushes them
to Supabase and exits — refresh the website's IBKR Terminal anytime without
placing orders. If the Gateway is unreachable it logs and exits 0 (it must
never crash a calling pipeline).

Exit code 0 on success / no-op, 1 on hard failure (connect/allowlist/state;
--sync-only only fails on allowlist refusal or a failed Supabase push).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.execution.ibkr_executor import (  # noqa: E402
    IBKRAccountError,
    IBKRExecutor,
    contract_spec,
    round_quantity,
)

STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio" / "state.json"
MIRROR_DIR = ENGINE_DIR / "data_store" / "ibkr_mirror"
POINTER_NAME = "mirror_state.json"


def _to_date(s) -> str:
    """Normalize a state-file timestamp ('YYYY-MM-DD' or ISO datetime) to a
    'YYYY-MM-DD' date string."""
    return str(s)[:10]


def _signed(direction: str, qty: float) -> float:
    return qty if direction == "long" else -qty


# ── plan extraction (pure, unit-testable) ─────────────────────────────────────
def plan_for_day(state: dict) -> dict:
    """Extract the mirror plan for the bar the paper step just processed.

    Returns {"date": D, "entries": [...], "exits": [...]} — entries are
    open_positions filled on D, exits are trades closed on D.
    """
    last = state.get("last_processed_date")
    if not last:
        return {"date": None, "entries": [], "exits": []}
    day = _to_date(last)
    entries = []
    for inst, p in (state.get("open_positions") or {}).items():
        if _to_date(p.get("entry_time", "")) != day:
            continue
        direction = p["direction"]
        direction = direction.value if hasattr(direction, "value") else str(direction)
        entries.append({
            "instrument": inst,
            "direction": direction,
            "units": float(p["units"]),
            "engine_fill_price": float(p["entry_price"]),
            "stop": p.get("stop"),
            "target": p.get("target"),
        })
    exits = []
    for t in state.get("trades") or []:
        if _to_date(t.get("exit_time", "")) != day:
            continue
        exits.append({
            "instrument": t["instrument"],
            "direction": str(t["direction"]),          # direction being CLOSED
            "units": float(t.get("units") or 0.0),     # engine initial units (info only)
            "engine_fill_price": float(t["exit_price"]),
            "exit_reason": t.get("exit_reason", ""),
        })
    entries.sort(key=lambda e: e["instrument"])
    exits.sort(key=lambda e: e["instrument"])
    return {"date": day, "entries": entries, "exits": exits}


# ── divergence math ────────────────────────────────────────────────────────────
def _divergence_bps(engine_price: float, ibkr_price: float, action: str) -> dict:
    """Signed divergence and direction-adjusted cost of the IBKR fill vs the
    engine-sim fill. divergence_bps = (ibkr/engine - 1) * 1e4; cost_bps is
    positive when the real fill was WORSE than the model (bought higher /
    sold lower)."""
    div = (float(ibkr_price) / float(engine_price) - 1.0) * 1e4
    side = 1.0 if action == "BUY" else -1.0
    return {"divergence_bps": round(div, 3), "cost_bps": round(side * div, 3)}


# ── execution ─────────────────────────────────────────────────────────────────
def _order_record(kind: str, item: dict, action: str, handle, fill) -> dict:
    spec = contract_spec(item["instrument"])
    rec = {
        "kind": kind,                            # "entry" | "exit"
        "instrument": item["instrument"],
        "asset_class": spec["asset_class"],
        "direction": item["direction"],
        "action": action,
        "units_engine": item["units"],
        "quantity_sent": handle.quantity if handle else None,
        "engine_fill_price": item["engine_fill_price"],
        "ibkr_avg_fill_price": fill.avg_fill_price if fill else None,
        "ibkr_order_id": fill.order_id if fill else None,
        "ibkr_perm_id": fill.perm_id if fill else None,
        "status": fill.status if fill else "error",
        "raw_status": fill.raw_status if fill else "",
        "filled_quantity": fill.filled_quantity if fill else 0.0,
        "commission": fill.commission if fill else None,
        "commission_currency": fill.commission_currency if fill else None,
        "submitted_at": handle.submitted_at if handle else None,
    }
    if kind == "entry":
        rec["stop_recorded"] = item.get("stop")
        rec["target_recorded"] = item.get("target")
        rec["brackets_attached"] = False
    if fill is not None and fill.avg_fill_price is not None:
        rec.update(_divergence_bps(item["engine_fill_price"], fill.avg_fill_price, action))
    else:
        rec.update({"divergence_bps": None, "cost_bps": None})
    if item.get("exit_reason"):
        rec["exit_reason"] = item["exit_reason"]
    return rec


def _summary(orders: list[dict]) -> dict:
    by_class: dict[str, list[dict]] = {}
    for o in orders:
        if o.get("divergence_bps") is None:
            continue
        by_class.setdefault(o["asset_class"], []).append(o)
    out = {}
    for cls, rows in sorted(by_class.items()):
        abs_divs = [abs(r["divergence_bps"]) for r in rows]
        comms = [r["commission"] for r in rows if r.get("commission") is not None]
        out[cls] = {
            "n_filled": len(rows),
            "mean_abs_divergence_bps": round(sum(abs_divs) / len(abs_divs), 3),
            "max_abs_divergence_bps": round(max(abs_divs), 3),
            "total_commission": round(sum(comms), 2) if comms else None,
        }
    return out


# ── Supabase sync (website IBKR Terminal) ─────────────────────────────────────
def _web_asset_class(cls: str) -> str:
    """Engine asset classes ('equity'/'forex'/'crypto') -> website classes."""
    return "stocks" if cls == "equity" else cls


def sync_ibkr_state(executor, record: dict | None = None) -> bool:
    """Push account + positions (+ a run's fills, when *record* is given) to
    Supabase for the website's IBKR Terminal. Best-effort: NEVER raises and
    never touches orders — a Supabase outage must not crash a mirror run or
    a pipeline calling --sync-only. Returns True when every write succeeded.
    """
    from apex_quant.storage import ibkr_store

    ok = True
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. account snapshot (skip the write entirely if the fetch failed —
    #    never overwrite a good row with nulls)
    acct: dict = {}
    try:
        acct = executor.get_account()
    except Exception as e:  # noqa: BLE001 - report, keep going
        print(f"  sync: account fetch failed: {type(e).__name__}: {e}", flush=True)
    pnl: dict = {}
    try:
        pnl = executor.get_pnl() or {}
    except Exception:  # noqa: BLE001 - optional feed
        pnl = {}
    if acct.get("NetLiquidation") is not None:
        account_row = {
            "net_liquidation": acct.get("NetLiquidation"),
            "cash": acct.get("TotalCashValue"),
            "buying_power": acct.get("BuyingPower"),
            "daily_pnl": pnl.get("daily_pnl"),
            "unrealized_pnl": pnl.get("unrealized_pnl", acct.get("UnrealizedPnL")),
            "realized_pnl": pnl.get("realized_pnl", acct.get("RealizedPnL")),
            "currency": acct.get("currency") or "USD",
            "updated_at": now,
        }
        if not ibkr_store.sync_account(account_row):
            print("  sync: account upsert FAILED", flush=True)
            ok = False
    else:
        ok = False

    # 2. open positions (state, not history: stale rows are deleted)
    portfolio = None
    try:
        portfolio = executor.get_portfolio()
    except Exception as e:  # noqa: BLE001 - report, keep going
        print(f"  sync: portfolio fetch failed: {type(e).__name__}: {e}", flush=True)
    if portfolio is not None:
        pos_rows = [{
            "instrument": p["engine_symbol"],
            "direction": "long" if p["quantity"] > 0 else "short",
            "units": abs(p["quantity"]),
            "avg_price": p.get("avg_cost"),
            "market_value": p.get("market_value"),
            "unrealized_pnl": p.get("unrealized_pnl"),
            "asset_class": _web_asset_class(str(p.get("asset_class", ""))),
            "updated_at": now,
        } for p in portfolio if p.get("quantity")]
        if not ibkr_store.sync_positions(pos_rows):
            print("  sync: positions sync FAILED", flush=True)
            ok = False
    else:
        ok = False

    # 3. this run's fills (append-only; exec_id PK makes re-syncs idempotent)
    if record:
        trade_rows = []
        for o in record.get("orders") or []:
            if o.get("status") != "filled" or o.get("ibkr_avg_fill_price") is None:
                continue
            perm = o.get("ibkr_perm_id") or o.get("ibkr_order_id")
            exec_id = str(perm) if perm is not None else (
                f"{record.get('date')}-{o['instrument']}-{o['action']}")
            trade_rows.append({
                "exec_id": exec_id,
                "instrument": o["instrument"],
                "asset_class": _web_asset_class(str(o.get("asset_class", ""))),
                "side": o["action"],
                "qty": o.get("filled_quantity") or o.get("quantity_sent"),
                "price": o["ibkr_avg_fill_price"],
                "commission": o.get("commission"),
                "exec_time": o.get("submitted_at") or record.get("mirrored_at"),
            })
        if trade_rows and not ibkr_store.sync_trades(trade_rows):
            print("  sync: trades upsert FAILED", flush=True)
            ok = False

    print(f"  sync: Supabase {'updated' if ok else 'PARTIAL/FAILED (see above)'} "
          f"@ {now}", flush=True)
    return ok


def run_mirror(state_path: Path, mirror_dir: Path, executor,
               timeout_s: float) -> tuple[int, dict | None]:
    """Mirror the latest processed bar onto IBKR. Returns (exit_code, record)."""
    state = json.loads(state_path.read_text(encoding="utf-8"))
    plan = plan_for_day(state)
    day = plan["date"]
    if day is None:
        print("state has no last_processed_date — nothing to mirror", flush=True)
        return 0, None

    record_path = mirror_dir / f"{day}.json"
    pointer_path = mirror_dir / POINTER_NAME
    pointer = {}
    if pointer_path.exists():
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if record_path.exists() or pointer.get("last_mirrored_date") == day:
        print(f"bar {day} already mirrored ({record_path.name}) — "
              f"strict no-op (idempotent). State NOT re-traded.", flush=True)
        return 0, None

    print("=" * 72, flush=True)
    print(f"IBKR PAPER MIRROR | book={state.get('book')} | bar {day} "
          f"| entries {len(plan['entries'])} exits {len(plan['exits'])}")
    print(f"state: {state_path}")
    print("=" * 72, flush=True)

    executor.connect()   # hard allowlist: raises unless the account is the paper one
    acct = executor.get_account()
    print(f"account {acct.get('account')} | NetLiq {acct.get('NetLiquidation')} "
          f"| AvailableFunds {acct.get('AvailableFunds')}", flush=True)

    record: dict = {
        "date": day,
        "book": state.get("book"),
        "mirrored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account": executor.account,
        "state_path": str(state_path),
        "orders": [], "skipped": [], "warnings": [],
    }

    positions = {p["engine_symbol"]: p for p in executor.get_positions()}

    def _place(kind: str, item: dict, action: str, volume: float) -> None:
        handle, fill = None, None
        try:
            if kind == "exit":
                handle = executor.close_position(item["instrument"])
            else:
                handle = executor.submit_order(
                    item["instrument"], item["direction"], volume=volume,
                    stop=item.get("stop"), target=item.get("target"),
                )
            if handle is None:      # close_position: nothing held (race-safe re-check)
                record["skipped"].append({**item, "kind": kind,
                                          "reason": "no IBKR position at close time"})
                return
            fill = executor.wait_for_fill(handle, timeout_s=timeout_s)
        except Exception as e:  # noqa: BLE001 - record, continue with other orders
            record["warnings"].append(
                f"{kind} {item['instrument']}: {type(e).__name__}: {e}")
            print(f"  ERROR {kind} {item['instrument']}: {e}", flush=True)
        rec = _order_record(kind, item, action, handle, fill)
        record["orders"].append(rec)
        print(f"  {kind.upper():5s} {rec['action']:4s} {rec['quantity_sent']} "
              f"{item['instrument']} | engine {rec['engine_fill_price']} "
              f"-> ibkr {rec['ibkr_avg_fill_price']} "
              f"({rec['status']}, div {rec['divergence_bps']} bps, "
              f"comm {rec['commission']})", flush=True)

    # 1. exits first (the engine's intra-step order), sized to actual IBKR holding
    for item in plan["exits"]:
        inst = item["instrument"]
        held = positions.get(inst)
        if held is None or held["quantity"] == 0:
            record["skipped"].append({**item, "kind": "exit",
                                      "reason": "no IBKR position — nothing to close"})
            print(f"  SKIP  exit {inst}: no IBKR position", flush=True)
            continue
        action = "SELL" if held["quantity"] > 0 else "BUY"
        _place("exit", item, action, abs(held["quantity"]))
        positions.pop(inst, None)

    # 2. entries, deduped against current IBKR positions
    for item in plan["entries"]:
        inst = item["instrument"]
        spec = contract_spec(inst)
        if spec["asset_class"] == "crypto" and item["direction"] == "short":
            record["skipped"].append({**item, "kind": "entry",
                                      "reason": "venue unsupported: IBKR crypto (Paxos) is long-only"})
            print(f"  SKIP  entry {inst} short: IBKR crypto is long-only", flush=True)
            continue
        held = positions.get(inst)
        want = _signed(item["direction"], 1.0)
        if held is not None and held["quantity"] != 0:
            have = 1.0 if held["quantity"] > 0 else -1.0
            if have == want:
                record["skipped"].append({**item, "kind": "entry",
                                          "reason": "already held on IBKR (reconciliation dedupe)"})
                print(f"  SKIP  entry {inst}: already held {held['quantity']}", flush=True)
            else:
                record["skipped"].append({**item, "kind": "entry",
                                          "reason": "OPPOSITE IBKR position held — refusing to flip"})
                record["warnings"].append(
                    f"entry {inst}: engine {item['direction']} vs IBKR {held['quantity']} — skipped")
                print(f"  SKIP  entry {inst}: opposite IBKR position held", flush=True)
            continue
        qty = round_quantity(spec["asset_class"], item["units"])
        if qty <= 0:
            record["skipped"].append({**item, "kind": "entry",
                                      "reason": f"units {item['units']} round to zero for venue"})
            print(f"  SKIP  entry {inst}: rounds to zero", flush=True)
            continue
        action = "BUY" if item["direction"] == "long" else "SELL"
        _place("entry", item, action, qty)

    # 3. post-run reconciliation report (informational; v1 never trades it)
    check = []
    engine_open = state.get("open_positions") or {}
    try:
        ibkr_now = {p["engine_symbol"]: p for p in executor.get_positions() if p["quantity"] != 0}
    except Exception as e:  # noqa: BLE001
        ibkr_now = {}
        record["warnings"].append(f"post-run position fetch failed: {e}")
    for inst in sorted(set(engine_open) | set(ibkr_now)):
        e = engine_open.get(inst)
        i = ibkr_now.get(inst)
        e_qty = _signed(str(e["direction"]), float(e["units"])) if e else 0.0
        i_qty = float(i["quantity"]) if i else 0.0
        tol = max(1e-6, 0.005 * abs(e_qty))
        if e is None:
            check.append({"instrument": inst, "issue": "IBKR holds, engine flat",
                          "engine_units": 0.0, "ibkr_quantity": i_qty})
        elif i is None:
            check.append({"instrument": inst, "issue": "engine holds, IBKR flat",
                          "engine_units": e_qty, "ibkr_quantity": 0.0})
        elif abs(e_qty - i_qty) > tol:
            check.append({"instrument": inst, "issue": "size drift (engine partial "
                          "exits are not traded by the v1 mirror)",
                          "engine_units": e_qty, "ibkr_quantity": i_qty})
    record["post_run_position_check"] = check

    record["summary"] = _summary(record["orders"])

    # 4. persist: daily record (atomic) + idempotency pointer
    mirror_dir.mkdir(parents=True, exist_ok=True)
    tmp = record_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, record_path)
    pointer_path.write_text(json.dumps({
        "last_mirrored_date": day,
        "updated_at": record["mirrored_at"],
        "account": executor.account,
        "record": record_path.name,
    }, indent=2), encoding="utf-8")

    print(f"\nrecord written: {record_path}", flush=True)
    print("divergence summary (filled orders, |divergence| bps):", flush=True)
    if record["summary"]:
        for cls, s in record["summary"].items():
            print(f"  {cls:8s} n={s['n_filled']} mean {s['mean_abs_divergence_bps']:>8} "
                  f"max {s['max_abs_divergence_bps']:>8} commission {s['total_commission']}",
                  flush=True)
    else:
        print("  (no filled orders with divergence)", flush=True)
    if check:
        print(f"post-run position check: {len(check)} residual mismatch(es) "
              f"(see record; informational only)", flush=True)

    # 5. push account + positions + this run's fills to Supabase for the
    #    website's IBKR Terminal (best-effort: never changes the exit code)
    print("syncing IBKR state to Supabase (website IBKR Terminal)...", flush=True)
    sync_ibkr_state(executor, record)
    return 0, record


def _dry_run(state_path: Path) -> int:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    plan = plan_for_day(state)
    if plan["date"] is None:
        print("state has no last_processed_date — nothing to mirror")
        return 0
    print(f"DRY RUN — bar {plan['date']} (no connection, no orders, no record)")
    for item in plan["exits"]:
        spec = contract_spec(item["instrument"])
        print(f"  EXIT  {item['instrument']:9s} was {item['direction']:5s} "
              f"({spec['asset_class']}) engine exit {item['engine_fill_price']} "
              f"reason {item['exit_reason']}")
    for item in plan["entries"]:
        spec = contract_spec(item["instrument"])
        qty = round_quantity(spec["asset_class"], item["units"])
        venue = ""
        if spec["asset_class"] == "crypto" and item["direction"] == "short":
            venue = " [SKIP: IBKR crypto long-only]"
        print(f"  ENTRY {item['instrument']:9s} {item['direction']:5s} {qty} "
              f"({spec['asset_class']}) engine fill {item['engine_fill_price']} "
              f"stop {item['stop']} target {item['target']}{venue}")
    if not plan["entries"] and not plan["exits"]:
        print("  (no entries or exits on this bar)")
    return 0


def main(argv: list[str] | None = None, executor=None) -> int:
    ap = argparse.ArgumentParser(description="Daily IBKR paper mirror of the frozen trend book.")
    ap.add_argument("--state", default=str(STATE_PATH), help="paper portfolio state.json path")
    ap.add_argument("--mirror-dir", default=str(MIRROR_DIR), help="mirror record directory")
    ap.add_argument("--timeout-s", type=float, default=120.0, help="per-order fill wait budget")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan for the latest bar; connect to nothing, write nothing")
    ap.add_argument("--sync-only", action="store_true",
                    help="no orders: connect, push account+positions to Supabase, exit "
                         "(gateway unreachable -> log + exit 0)")
    args = ap.parse_args(argv)

    state_path = Path(args.state)

    if args.sync_only:
        own = executor is None
        if own:
            executor = IBKRExecutor()   # env-overridable host/port/client/account
        try:
            executor.connect()   # hard allowlist still applies
        except IBKRAccountError as e:
            print(f"ACCOUNT ALLOWLIST REFUSAL: {e}", flush=True)
            return 1
        except (ConnectionError, OSError) as e:
            print(f"IBKR connection failed: {e} — gateway down? Nothing synced; "
                  f"exiting 0 (never crash a pipeline).", flush=True)
            return 0
        try:
            print("sync-only: pushing account + positions to Supabase...", flush=True)
            ok = sync_ibkr_state(executor)
            return 0 if ok else 1
        finally:
            if own:
                executor.disconnect()

    if not state_path.exists():
        print(f"state file not found: {state_path}", flush=True)
        return 1
    if args.dry_run:
        return _dry_run(state_path)

    own_executor = executor is None
    if own_executor:
        executor = IBKRExecutor()   # env-overridable host/port/client/account
    try:
        code, _ = run_mirror(state_path, Path(args.mirror_dir), executor, args.timeout_s)
        return code
    except IBKRAccountError as e:
        print(f"ACCOUNT ALLOWLIST REFUSAL: {e}", flush=True)
        return 1
    except (ConnectionError, OSError) as e:
        print(f"IBKR connection failed: {e} — is TWS/IB Gateway (paper) running? "
              f"No record written; re-run today to retry.", flush=True)
        return 1
    finally:
        if own_executor:
            executor.disconnect()


if __name__ == "__main__":
    sys.exit(main())
