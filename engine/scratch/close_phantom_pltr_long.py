"""Close the PHANTOM PLTR venue long on the IBKR paper account (2026-08-04).

Context (audit trail)
---------------------
* 2026-07-17: the v1 mirror placed SELL 66 PLTR @ 131.61 (order id 30,
  permId 1211816801) mirroring the frozen book's PLTR short (engine fill
  131.778631). Record: data_store/ibkr_mirror/2026-07-17.json.
* 2026-07-30 ~18:36 UTC: ad-hoc rebalance script
  ``place_soxx_and_rebalance_ibkr_now.py`` (clientId=95) submitted BUY 66 PLTR
  to flatten the venue short (fill ~121.97 per
  ``populate_ibkr_trades.py``). The ENGINE book was NOT exited — it still
  holds the PLTR short (stop 149.899) — so the venue went flat while the
  experiment of record stayed short.
* 2026-08-03 16:50 UTC -> 2026-08-04 13:40 UTC: IB Gateway outage
  (ibkr_mirror_sync.log: connection refused on 4002/4001/7497/7496).
* 2026-08-04 13:50:09 UTC: BUY 66 PLTR MKT filled @ 150.41 (orderId 14,
  permId 571420331, **clientId=30**) — ten minutes after gateway recovery.
  No repo process uses clientId 30 (mirror cron is 18 and sync-only; the
  live daemon was down since 2026-08-03 13:24; ad-hoc scripts used
  94-99/17/19/40). This long corresponds to NO engine position: it is a
  phantom / external order, NOT a missed mirror exit.

User authorized closing this phantom venue long on 2026-08-04. The engine
paper book (PLTR SHORT @ 131.78) is the experiment of record and is NOT
touched by this script — venue only.

Usage:
    cd engine && IBKR_CLIENT_ID=19 .venv-mac/bin/python scratch/close_phantom_pltr_long.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.execution.ibkr_executor import IBKRExecutor  # noqa: E402

RECORD_PATH = ENGINE_DIR / "data_store" / "ibkr_mirror" / "2026-08-04_phantom_close.json"


def main() -> int:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print("=" * 72)
    print(f"  PHANTOM PLTR VENUE-LONG CLOSE — {ts}")
    print("  account allowlist: DUQ278370 (enforced by IBKRExecutor.connect)")
    print("=" * 72)

    ex = IBKRExecutor()  # IBKR_CLIENT_ID/IBKR_ACCOUNT from env; defaults 17/DUQ278370
    ex.connect()
    try:
        before = {p["engine_symbol"]: p for p in ex.get_positions()}
        print(f"positions before: {json.dumps({k: v['quantity'] for k, v in before.items()})}")
        held = before.get("PLTR", {}).get("quantity", 0.0)
        avg = before.get("PLTR", {}).get("avg_cost")
        if held <= 0:
            print(f"no phantom PLTR long present (qty={held}) — nothing to do")
            return 0

        handle = ex.close_position("PLTR")  # venue-sized MKT DAY sell; cannot overshoot
        assert handle is not None
        res = ex.wait_for_fill(handle, timeout_s=60.0)
        print(f"close order: {handle.action} {handle.quantity} PLTR "
              f"order_id={res.order_id} perm_id={res.perm_id} status={res.status} "
              f"avg_fill={res.avg_fill_price} commission={res.commission} {res.commission_currency}")

        after = {p["engine_symbol"]: p for p in ex.get_positions()}
        print(f"positions after:  {json.dumps({k: v['quantity'] for k, v in after.items()})}")

        rec = {
            "date": "2026-08-04",
            "kind": "manual_phantom_close",
            "instrument": "PLTR",
            "account": ex.account,
            "reason": (
                "phantom venue long (BUY 66 @ 150.41, 2026-08-04T13:50:09Z, "
                "permId 571420331, external clientId=30) matched NO engine "
                "position; engine book still holds PLTR SHORT @ 131.778631 "
                "(experiment of record, untouched). User authorized venue close."
            ),
            "phantom_entry": {
                "time_utc": "2026-08-04T13:50:09+00:00", "side": "BOT",
                "shares": 66.0, "price": 150.41, "perm_id": 571420331,
                "client_id": 30,
            },
            "close": {
                "closed_at": ts,
                "quantity": held,
                "avg_cost_before": avg,
                "order_id": res.order_id,
                "perm_id": res.perm_id,
                "status": res.status,
                "avg_fill_price": res.avg_fill_price,
                "commission": res.commission,
                "commission_currency": res.commission_currency,
            },
            "positions_after": {k: v["quantity"] for k, v in after.items()},
        }
        RECORD_PATH.write_text(json.dumps(rec, indent=2) + "\n")
        print(f"record written: {RECORD_PATH}")

        if after.get("PLTR", 0.0) != 0.0:
            print("ERROR: PLTR position still non-zero after close!", flush=True)
            return 1
        print("VERIFIED: venue PLTR flat.")
        return 0
    finally:
        ex.disconnect()


if __name__ == "__main__":
    sys.exit(main())
