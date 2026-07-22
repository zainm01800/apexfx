"""One-off: attach protective stops to IBKR positions that were opened naked.

Why this exists: the v1 mirror RECORDED stop/target on its day-record but never
attached them at the venue, so a position was only protected while the nightly
engine step kept running. On 2026-07-22 a Supabase egress block killed the stepper
and left every open position with no stop anywhere. This retrofits the protection
using the stops the engine already holds in state.json.

Safety properties (deliberate, and the reason this is a separate script):
  * DRY RUN by default — prints the exact orders and exits. --apply to place them.
  * Sized to the ACTUAL IBKR position and direction, never the engine's units, so
    engine/venue drift can't produce an oversized or wrong-way order.
  * Skips any instrument that already has a resting protective order (idempotent —
    safe to re-run; it will never stack duplicate stops).
  * Skips anything the engine has no stop for, loudly, rather than guessing one.
  * Fail-closed account allowlist is inherited from IBKRExecutor.connect().

Usage:
    cd engine
    .venv-mac/bin/python scripts/backfill_ibkr_brackets.py            # dry run
    .venv-mac/bin/python scripts/backfill_ibkr_brackets.py --apply    # place orders
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENGINE_DIR / ".env")

from apex_quant.execution.ibkr_executor import IBKRExecutor  # noqa: E402

STATE_PATH = ENGINE_DIR / "data_store" / "paper_portfolio" / "state.json"


def plan_backfill(open_positions: dict, ibkr_positions: list[dict],
                  resting: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pure planner: (to_protect, skipped). Unit-tested without a gateway."""
    protected = {o.get("symbol") for o in resting if o.get("symbol")}
    to_protect, skipped = [], []
    for p in ibkr_positions:
        sym, qty = p.get("engine_symbol"), float(p.get("quantity") or 0.0)
        if not qty:
            continue
        if sym in protected:
            skipped.append({"instrument": sym, "reason": "already has a resting protective order"})
            continue
        eng = open_positions.get(sym)
        if not eng:
            skipped.append({"instrument": sym, "reason": "held at IBKR but not in engine state — "
                                                         "no stop to apply, needs a human"})
            continue
        stop = eng.get("stop")
        if not stop:
            skipped.append({"instrument": sym, "reason": "engine has no stop for this position"})
            continue
        # Direction sanity: engine and venue must agree before we send an exit-side order.
        eng_long = str(eng.get("direction", "")).lower() != "short"
        if eng_long != (qty > 0):
            skipped.append({"instrument": sym,
                            "reason": f"DIRECTION MISMATCH engine={eng.get('direction')} "
                                      f"ibkr_qty={qty} — refusing to protect"})
            continue
        to_protect.append({"instrument": sym, "qty": abs(qty),
                           "side": "long" if qty > 0 else "short",
                           "stop": float(stop),
                           "target": float(eng["target"]) if eng.get("target") else None})
    return to_protect, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Attach protective stops to unprotected IBKR positions.")
    ap.add_argument("--apply", action="store_true", help="place the orders (default: dry run)")
    ap.add_argument("--state", default=str(STATE_PATH))
    args = ap.parse_args(argv)

    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    open_positions = state.get("open_positions") or {}

    executor = IBKRExecutor()
    executor.connect()          # fail-closed allowlist
    try:
        ibkr_positions = executor.get_positions()
        resting = executor.get_open_orders()
        to_protect, skipped = plan_backfill(open_positions, ibkr_positions, resting)

        print("=" * 72)
        print(f"BRACKET BACKFILL | account {executor.account} | "
              f"{'APPLY' if args.apply else 'DRY RUN (no orders)'}")
        print(f"IBKR positions: {len(ibkr_positions)} | already protected/skipped: {len(skipped)} "
              f"| to protect: {len(to_protect)}")
        print("=" * 72)
        for s in skipped:
            print(f"  SKIP  {s['instrument']:10s} {s['reason']}")
        for t in to_protect:
            side = "SELL" if t["side"] == "long" else "BUY"
            print(f"  {'PLACE' if args.apply else 'WOULD'} {t['instrument']:10s} "
                  f"{side} {t['qty']} @ STP {t['stop']}"
                  + (f" / LMT {t['target']}" if t["target"] else " (no target)"))

        if not args.apply:
            print("\ndry run — nothing placed. Re-run with --apply to protect these positions.")
            return 0

        # Broker rejections arrive asynchronously (error 110 off-tick prices, margin,
        # session rules) — placeOrder still returns a handle, so without listening we
        # print OK for orders the venue is throwing away.
        ib_errors: list[str] = []
        executor._ib.errorEvent += (
            lambda reqId, code, msg, contract=None: ib_errors.append(f"[{code}] {msg}")
        )

        placed, failed = 0, 0
        for t in to_protect:
            try:
                h = executor.protect_position(t["instrument"], t["stop"], t["target"])
                executor._ib.sleep(1.0)          # let a rejection land before judging
                if h is None:
                    print(f"  WARN {t['instrument']}: position vanished before protection")
                    failed += 1
                    continue
                legs = [lg for lg in (h.stop_trade, h.target_trade) if lg is not None]
                bad = [lg for lg in legs if lg.orderStatus.status in ("Cancelled", "Inactive", "ApiCancelled")]
                if bad:
                    failed += 1
                    why = "; ".join(e.message for lg in bad for e in lg.log if e.message) or "rejected"
                    print(f"  FAIL {t['instrument']}: {why}")
                else:
                    placed += 1
                    print(f"  OK   {t['instrument']}: {h.action} {h.quantity} "
                          f"stop {h.stop}" + (f" target {h.target}" if h.target else ""))
            except Exception as e:  # noqa: BLE001 — one failure must not abort the rest
                failed += 1
                print(f"  FAIL {t['instrument']}: {type(e).__name__}: {e}")

        # VERIFY, don't assume. placeOrder() returns a handle even when the order is
        # never transmitted, so an unverified run once reported "protected 6, 0
        # failures" while the account still held zero resting orders. Protection you
        # believe in but do not have is worse than none.
        executor._ib.reqAllOpenOrders()
        executor._ib.sleep(2)
        resting_now = {o.get("symbol") for o in executor.get_open_orders() if o.get("symbol")}
        confirmed = [t["instrument"] for t in to_protect if t["instrument"] in resting_now]
        missing = [t["instrument"] for t in to_protect if t["instrument"] not in resting_now]
        print(f"\nsubmitted {placed}, {failed} failure(s)")
        print(f"VERIFIED resting at IBKR: {len(confirmed)}/{len(to_protect)} "
              f"-> {', '.join(confirmed) or 'none'}")
        if missing:
            print(f"::WARNING:: NOT protected (no resting order found): {', '.join(missing)}")
        return 0 if (failed == 0 and not missing) else 1
    finally:
        executor.disconnect()


if __name__ == "__main__":
    sys.exit(main())
