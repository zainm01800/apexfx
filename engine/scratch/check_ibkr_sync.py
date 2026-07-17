"""Offline check of the IBKR->Supabase sync payloads (NO gateway, NO network).

Monkeypatches httpx with a recording fake, drives sync_ibkr_state with a stub
executor, and asserts the rows posted match supabase/apex_ibkr.sql columns:
  - apex_ibkr_account: singleton id=1 upsert
  - apex_ibkr_positions: upsert + delete of stale instruments, equity->stocks
  - apex_ibkr_trades: filled orders only, exec_id from ibkr_perm_id w/ fallback

Run:  cd engine && .venv-mac/bin/python scratch/check_ibkr_sync.py
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

CALLS: list[dict] = []


class _Resp:
    def __init__(self, status_code=201):
        self.status_code = status_code

    def json(self):
        return []


class FakeClient:
    def __init__(self, timeout=None):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        CALLS.append({"method": "POST", "url": url, "json": json})
        return _Resp(201)

    def delete(self, url, headers=None, params=None):
        CALLS.append({"method": "DELETE", "url": url, "params": params})
        return _Resp(204)


sys.modules["httpx"] = types.SimpleNamespace(Client=FakeClient)

import run_ibkr_mirror as mirror  # noqa: E402


class StubExecutor:
    account = "DUQ278370"

    def get_account(self):
        return {
            "account": "DUQ278370",
            "NetLiquidation": 100523.45,
            "TotalCashValue": 99800.12,
            "AvailableFunds": 99000.0,
            "BuyingPower": 400000.0,
            "GrossPositionValue": 723.33,
            "UnrealizedPnL": 523.45,
            "RealizedPnL": 12.34,
            "currency": "USD",
        }

    def get_pnl(self):
        return {"daily_pnl": 45.67, "unrealized_pnl": 523.45, "realized_pnl": 12.34}

    def get_portfolio(self):
        return [
            {"engine_symbol": "AAPL", "asset_class": "equity", "quantity": 44.02,
             "avg_cost": 227.5, "market_price": 228.0, "market_value": 10036.56,
             "unrealized_pnl": 22.01},
            {"engine_symbol": "EUR/USD", "asset_class": "forex", "quantity": 20000.0,
             "avg_cost": 1.085, "market_price": 1.086, "market_value": 21720.0,
             "unrealized_pnl": 20.0},
            {"engine_symbol": "BTC/USD", "asset_class": "crypto", "quantity": -0.5,
             "avg_cost": 65000.0, "market_price": 64000.0, "market_value": -32000.0,
             "unrealized_pnl": 500.0},
        ]


RECORD = {
    "date": "2026-07-17",
    "mirrored_at": "2026-07-17T09:00:00+00:00",
    "orders": [
        {"kind": "entry", "instrument": "AAPL", "asset_class": "equity",
         "direction": "long", "action": "BUY", "quantity_sent": 44.02,
         "filled_quantity": 44.02, "ibkr_avg_fill_price": 227.55,
         "ibkr_order_id": 123, "ibkr_perm_id": 987654, "status": "filled",
         "commission": 1.0, "submitted_at": "2026-07-17T08:59:50+00:00"},
        {"kind": "exit", "instrument": "TSLA", "asset_class": "equity",
         "direction": "short", "action": "BUY", "quantity_sent": 15.0,
         "filled_quantity": 15.0, "ibkr_avg_fill_price": 250.1,
         "ibkr_order_id": None, "ibkr_perm_id": None, "status": "filled",
         "commission": None, "submitted_at": "2026-07-17T08:59:51+00:00"},
        {"kind": "entry", "instrument": "NVDA", "asset_class": "equity",
         "direction": "long", "action": "BUY", "quantity_sent": 3.0,
         "filled_quantity": 0.0, "ibkr_avg_fill_price": None,
         "ibkr_order_id": 125, "ibkr_perm_id": None, "status": "timeout_cancelled",
         "commission": None, "submitted_at": "2026-07-17T08:59:52+00:00"},
    ],
}


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)


ok = mirror.sync_ibkr_state(StubExecutor(), RECORD)
check("sync returns True when transport succeeds", ok)

posts = [c for c in CALLS if c["method"] == "POST"]
deletes = [c for c in CALLS if c["method"] == "DELETE"]
by_table = {}
for c in posts:
    by_table.setdefault(c["url"].rsplit("/", 1)[-1], []).extend(c["json"])

acct = by_table["apex_ibkr_account"][0]
check("account singleton id=1", acct["id"] == 1)
check("account fields", acct["net_liquidation"] == 100523.45 and
      acct["cash"] == 99800.12 and acct["buying_power"] == 400000.0 and
      acct["daily_pnl"] == 45.67 and acct["unrealized_pnl"] == 523.45 and
      acct["realized_pnl"] == 12.34 and acct["currency"] == "USD" and
      "updated_at" in acct, json.dumps(acct))

pos = {p["instrument"]: p for p in by_table["apex_ibkr_positions"]}
check("3 position rows", len(pos) == 3)
check("equity -> stocks", pos["AAPL"]["asset_class"] == "stocks")
check("forex kept", pos["EUR/USD"]["asset_class"] == "forex")
check("short crypto direction + abs units", pos["BTC/USD"]["direction"] == "short"
      and pos["BTC/USD"]["units"] == 0.5, json.dumps(pos["BTC/USD"]))
check("long direction", pos["AAPL"]["direction"] == "long")
check("position columns", set(pos["AAPL"]) == {
    "instrument", "direction", "units", "avg_price", "market_value",
    "unrealized_pnl", "asset_class", "updated_at"})

check("stale-position delete issued", len(deletes) == 1 and
      "apex_ibkr_positions" in deletes[0]["url"] and
      deletes[0]["params"]["instrument"] == 'not.in.("AAPL","EUR/USD","BTC/USD")',
      json.dumps(deletes))

trades = {t["exec_id"]: t for t in by_table["apex_ibkr_trades"]}
check("2 trade rows (unfilled order excluded)", len(trades) == 2, json.dumps(list(trades)))
check("exec_id from perm_id", "987654" in trades)
check("exec_id fallback when no perm_id",
      "2026-07-17-TSLA-BUY" in trades, json.dumps(list(trades)))
t = trades["987654"]
check("trade columns", set(t) == {"exec_id", "instrument", "asset_class", "side",
      "qty", "price", "commission", "exec_time"}, json.dumps(t))
check("trade values", t["side"] == "BUY" and t["qty"] == 44.02 and
      t["price"] == 227.55 and t["commission"] == 1.0 and
      t["asset_class"] == "stocks")

print("ALL SYNC PAYLOAD CHECKS PASSED")
