"""Offline smoke test for the IBKR live-paper migration — NO gateway needed.

Stubs the ib_async CLIENT (a FakeIB that quacks like ib_async.IB, with bracket
order support) and drives the REAL executor + bridge + daemon wiring against
it. Contract/order dataclasses are the REAL ib_async ones (installed in
engine/.venv-mac), so the mapping is verified against the actual library
surface. provider=mt4 is covered by scratch/smoke_live_hardening.py (40/40).

Proves:
  1. provider selection: APEX_EXECUTION__PROVIDER=ibkr -> _create_executor()
     builds a connected IBKRLiveBridge; "mock" still dispatches; env wins
     over config.yaml through the pydantic Literal;
  2. lots -> base-units math (EUR/USD, USD/JPY, GBP/JPY, crypto) and the
     EURUSD-g -> EUR/USD symbol mapping;
  3. daemon entry: parent MKT (transmit=False) + GTC STP child + GTC LMT
     child in ONE OCA group; ack stamps filled_at + binds the IBKR permId
     into setup_features.mt4_ticket exactly like an MT4 ticket;
  4. positions served in the mt4_positions.json shape for the TMS paths;
  5. get_open_orders() exposes the working bracket (stop state inspectable);
  6. modify_sl amends the STP child in place (same orderId);
  7. partial_close sizes correctly (0.08 lots -> 8000 units) and the bracket
     is re-sized to the remainder (never over-closes);
  8. ticket-scoped close cancels the bracket and flattens the ticket;
  9. ack shape matches what the daemon consumes (ok/ticket/fill_price/id);
 10. netting guard: an opposite-direction entry against an open virtual
     ticket is REFUSED (no silent venue flip);
 11. venue rejection (closed session): ack ok=False, no ticket bound, no
     filled_at; the IBKR resolver then EXPIRES the aged setup — recorded,
     never retried forever;
 12. stop fill -> fill-based resolution: sl_hit from the actual exit price;
     restore-to-pending when a resolved setup's ticket is still open;
 13. sync_to_supabase pushes account/positions/trades rows (apex_ibkr_*);
 14. restart reconciliation: ledger reload + bracket rebind keeps modify_sl
     working; a venue-flat ticket is marked closed-externally.

Run:
    cd engine
    .venv-mac/bin/python scratch/smoke_live_ibkr.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

# Provider flip BEFORE import: config is a cached singleton, and the env var
# must prove it drives provider selection through the pydantic Literal.
os.environ["APEX_EXECUTION__ENABLED"] = "true"
os.environ["APEX_EXECUTION__PROVIDER"] = "ibkr"

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from ib_async import AccountValue, Forex, Position  # noqa: E402

from apex_quant.execution import ibkr_executor as ibkr  # noqa: E402
from apex_quant.execution import ibkr_bridge as bridge_mod  # noqa: E402
from apex_quant.storage import ibkr_store  # noqa: E402
import scripts.run_live_paper_trading as scanner  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
#  Fake ib_async client with bracket support
# ---------------------------------------------------------------------------
def _key(contract) -> str:
    return f"{contract.secType}:{contract.symbol}/{contract.currency}"


class FakeTrade:
    def __init__(self, contract, order, status, fill_price=None, perm_id=None):
        self.contract = contract
        self.order = order
        self.log = []
        self.orderStatus = SimpleNamespace(
            status=status,
            filled=float(order.totalQuantity) if status == "Filled" else 0.0,
            remaining=0.0 if status == "Filled" else float(order.totalQuantity),
            avgFillPrice=fill_price,
            permId=perm_id,
        )
        self.fills = (
            [SimpleNamespace(commissionReport=SimpleNamespace(commission=1.0, currency="USD"))]
            if status == "Filled" else []
        )

    def isDone(self) -> bool:
        return str(self.orderStatus.status) in ("Filled", "Cancelled", "Inactive", "ApiCancelled")


class FakeIB:
    """Minimal ib_async.IB stand-in with parent/child bracket semantics.

    MKT parents fill immediately at ``fill_prices[key]`` (default 100.0) and
    move the net position; STP/LMT children REST until ``trigger`` is called
    (or cancelled). ``reject_next=True`` makes the next parent come back
    Inactive (venue rejection, e.g. FX desk closed).
    """

    def __init__(self, accounts, fill_prices=None, seed_positions=()):
        self._accounts = list(accounts)
        self.fill_prices = dict(fill_prices or {})
        self.connected = False
        self.reject_next = False
        self._next_id = 5000
        self._next_perm = 900001
        self._positions = {_key(c): [c, float(q), float(a)] for c, q, a in seed_positions}
        self.orders: dict[int, object] = {}     # orderId -> order (live object)
        self.trades: dict[int, FakeTrade] = {}  # orderId -> FakeTrade
        self.place_calls: list[int] = []        # orderIds, in place order (incl. amends)

    # -- connection --
    def connect(self, host, port, clientId=1, timeout=4.0, **kw):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def managedAccounts(self):
        return list(self._accounts)

    # -- contracts / orders --
    def qualifyContracts(self, *contracts):
        for c in contracts:
            c.conId = 4242
        return list(contracts)

    def placeOrder(self, contract, order):
        if not getattr(order, "orderId", 0):
            self._next_id += 1
            order.orderId = self._next_id
        oid = order.orderId
        self.place_calls.append(oid)
        self.orders[oid] = order
        if oid in self.trades:
            return self.trades[oid]          # amend: same id, same trade

        self._next_perm += 1
        perm = self._next_perm
        if getattr(order, "parentId", 0):
            tr = FakeTrade(contract, order, "Submitted", perm_id=perm)   # child rests
        elif self.reject_next:
            self.reject_next = False
            tr = FakeTrade(contract, order, "Inactive", perm_id=perm)    # venue reject
        else:
            px = self.fill_prices.get(_key(contract), 100.0)
            qty = float(order.totalQuantity) * (1 if order.action == "BUY" else -1)
            row = self._positions.setdefault(_key(contract), [contract, 0.0, 0.0])
            row[1] += qty
            row[2] = px
            tr = FakeTrade(contract, order, "Filled", fill_price=px, perm_id=perm)
        self.trades[oid] = tr
        return tr

    def cancelOrder(self, order):
        tr = self.trades.get(order.orderId)
        if tr is not None and not tr.isDone():
            tr.orderStatus.status = "Cancelled"

    def trigger(self, order_id: int, price: float):
        """Simulate the venue filling a resting STP/LMT child at *price*."""
        tr = self.trades[order_id]
        o = tr.order
        qty = float(o.totalQuantity) * (1 if o.action == "BUY" else -1)
        row = self._positions.setdefault(_key(tr.contract), [tr.contract, 0.0, 0.0])
        row[1] += qty
        row[2] = price
        tr.orderStatus.status = "Filled"
        tr.orderStatus.filled = float(o.totalQuantity)
        tr.orderStatus.remaining = 0.0
        tr.orderStatus.avgFillPrice = price
        # OCA: venue cancels the sibling(s)
        for oid, other in self.trades.items():
            if oid != order_id and getattr(other.order, "ocaGroup", "") == getattr(o, "ocaGroup", None) \
                    and not other.isDone():
                other.orderStatus.status = "Cancelled"

    def openTrades(self):
        return [t for t in self.trades.values() if not t.isDone()]

    # -- state --
    def positions(self, account=""):
        return [Position(account, c, q, a) for c, q, a in self._positions.values() if q != 0.0]

    def portfolio(self, account=""):
        out = []
        for c, q, a in self._positions.values():
            if q == 0.0:
                continue
            mkt = self.fill_prices.get(_key(c), 100.0)
            out.append(SimpleNamespace(
                account=account, contract=c, position=q, marketPrice=mkt,
                marketValue=mkt * q, averageCost=a, unrealizedPNL=(mkt - a) * q,
                realizedPNL=0.0,
            ))
        return out

    def accountSummary(self, account=""):
        return [
            AccountValue(account, "NetLiquidation", "1000000.00", "USD", ""),
            AccountValue(account, "TotalCashValue", "990000.00", "USD", ""),
            AccountValue(account, "BuyingPower", "4000000.00", "USD", ""),
        ]

    def sleep(self, seconds=0.0):
        return None


def _px(symbol: str) -> str:
    """fill_prices key helper (CASH EUR/USD)."""
    base, quote = symbol.split("/")
    return f"CASH:{base}/{quote}"


# ---------------------------------------------------------------------------
#  Build the world
# ---------------------------------------------------------------------------
print("=" * 70)
print("SMOKE TEST: IBKR live-paper migration (offline, fake ib_async client)")
print("=" * 70)

_tmp = tempfile.TemporaryDirectory()
TMP = Path(_tmp.name)
LEDGER = TMP / "ibkr_live_book.json"
# The bridge under test must write its ledger to the tmp dir, NEVER the real
# engine/data_store/ibkr_live_book.json (also proves the ledger is portable).
bridge_mod._DEFAULT_LEDGER_PATH = LEDGER

fake = FakeIB(
    accounts=["DUQ278370"],
    fill_prices={_px("EUR/USD"): 1.2345, _px("USD/JPY"): 156.0,
                 _px("GBP/USD"): 1.3000, _px("AUD/USD"): 0.6600,
                 _px("NZD/USD"): 0.6000},
)
_real_executor_cls = ibkr.IBKRExecutor
bridge_mod.IBKRExecutor = lambda *a, **kw: _real_executor_cls(ib=fake)  # injected factory

bridge = None  # created in [1]


# ---------------------------------------------------------------------------
#  1. Provider selection
# ---------------------------------------------------------------------------
def test_provider_selection() -> None:
    global bridge
    print("\n[1] provider selection (env -> _create_executor)")
    check("env override reached the validated config (Literal accepts ibkr)",
          scanner.cfg.execution.provider == "ibkr",
          f"cfg.execution.provider={scanner.cfg.execution.provider!r}")
    ex = scanner._create_executor()
    check("_create_executor returns the IBKR bridge", type(ex).__name__ == "IBKRLiveBridge")
    check("bridge connected to the paper account", ex.is_connected and
          ex.executor.account == "DUQ278370")
    bridge = ex
    scanner._EXECUTOR = bridge
    check("_is_ibkr_executor() true", scanner._is_ibkr_executor() is True)

    os.environ["APEX_EXECUTION__PROVIDER"] = "mock"
    try:
        mock_ex = scanner._create_executor()
        check("provider=mock still dispatches (dispatch table intact)",
              type(mock_ex).__name__ == "MockExecutor")
    finally:
        os.environ["APEX_EXECUTION__PROVIDER"] = "ibkr"
    scanner._EXECUTOR = bridge


# ---------------------------------------------------------------------------
#  2. Units / symbol math + mirror path untouched
# ---------------------------------------------------------------------------
def test_units_math() -> None:
    print("\n[2] lots->units math + symbol mapping + mirror default unchanged")
    check("EUR/USD 0.15 lots -> 15000 units", bridge_mod.lots_to_units("EUR/USD", 0.15) == 15000.0)
    check("USD/JPY 1.5 lots -> 150000 units", bridge_mod.lots_to_units("USD/JPY", 1.5) == 150000.0)
    check("GBP/JPY 2.35 lots -> 235000 units", bridge_mod.lots_to_units("GBP/JPY", 2.35) == 235000.0)
    check("BTC/USD 0.5 lots -> 0.5 units (crypto 1:1)",
          bridge_mod.lots_to_units("BTC/USD", 0.5) == 0.5)
    check("units->lots inverse (150000 USD/JPY -> 1.5)",
          bridge_mod.units_to_lots("USD/JPY", 150000.0) == 1.5)
    check("EURUSD-g -> EUR/USD", bridge_mod.engine_symbol("EURUSD-g") == "EUR/USD")
    check("EURUSD.m -> EUR/USD", bridge_mod.engine_symbol("EURUSD.m") == "EUR/USD")
    check("EUR/USD -> EURUSD (mt4 shape)", bridge_mod.mt4_symbol("EUR/USD") == "EURUSD")

    # Mirror default: submit_order WITHOUT attach_stop must create NO children.
    ex = _real_executor_cls(ib=fake)
    ex.connect()
    n_before = len(fake.trades)
    h = ex.submit_order("NZD/USD", "long", volume=10000.0, stop=0.59, target=0.61)
    check("mirror default: no bracket attached", h.stop_trade is None and h.target_trade is None)
    check("mirror default: exactly one order (parent only)", len(fake.trades) == n_before + 1)
    check("mirror default: parent tif DAY, transmit default",
          h.trade.order.tif == "DAY" and bool(getattr(h.trade.order, "transmit", True)) is True)
    # flatten NZD/USD again so later venue state stays tidy
    ex.close_position("NZD/USD")


# ---------------------------------------------------------------------------
#  Shared httpx patch (daemon Supabase I/O)
# ---------------------------------------------------------------------------
class FakeResp:
    def __init__(self, status=200, rows=None):
        self.status_code = status
        self._rows = rows if rows is not None else []
        self.text = ""

    def json(self):
        return self._rows


POSTS: list = []
PATCHES: list = []
GET_ROWS: list = []


def patch_httpx() -> None:
    scanner.httpx.post = lambda url, headers=None, json=None, **kw: POSTS.append(json) or FakeResp(201)
    scanner.httpx.patch = lambda url, headers=None, json=None, **kw: PATCHES.append(json) or FakeResp(204)
    scanner.httpx.get = lambda url, headers=None, **kw: FakeResp(200, list(GET_ROWS))


# ---------------------------------------------------------------------------
#  3. Daemon entry: bracket + ack -> ticket bound (the open_new_trade path)
# ---------------------------------------------------------------------------
def test_daemon_entry() -> dict:
    print("\n[3] daemon entry: parent+STP+LMT OCA bracket; ack binds permId")
    patch_httpx()
    POSTS.clear(); PATCHES.clear()
    orig_ticket_col = scanner._TICKET_COLUMN_OK
    scanner._TICKET_COLUMN_OK = False
    ok = scanner.open_new_trade(
        symbol="EUR/USD", direction="LONG", entry_price=1.2345,
        stop_loss=1.2300, target_price=1.2430, timeframe="1d",
        confidence=60, rr=2.0, volume=0.15, style="swing",
    )
    scanner._TICKET_COLUMN_OK = orig_ticket_col
    check("trade row created (POST)", ok is True and len(POSTS) == 1)

    # identify the bracket: the three most recent orders (parent, LMT, STP)
    recent_ids = fake.place_calls[-3:]
    parent = fake.orders[recent_ids[0]]
    children = [fake.orders[i] for i in recent_ids[1:]]
    stp = next(o for o in children if o.orderType == "STP")
    lmt = next(o for o in children if o.orderType == "LMT")
    check("entry placed exactly 3 orders (parent + 2 children)", len(recent_ids) == 3)
    check("parent is MKT DAY, held (transmit=False)",
          parent.orderType == "MKT" and parent.tif == "DAY" and parent.transmit is False)
    check("STP child: GTC, auxPrice=stop, parentId linked",
          stp.tif == "GTC" and abs(stp.auxPrice - 1.2300) < 1e-9 and stp.parentId == parent.orderId)
    check("LMT child: GTC at target, parentId linked, releases bracket",
          lmt.tif == "GTC" and abs(lmt.lmtPrice - 1.2430) < 1e-9
          and lmt.parentId == parent.orderId and lmt.transmit is True)
    check("children share one OCA group", stp.ocaGroup == lmt.ocaGroup and bool(stp.ocaGroup))
    check("entry qty is base units (0.15 lots -> 15000)", float(parent.totalQuantity) == 15000.0)

    ack_patches = [p for p in PATCHES if p and "filled_at" in p]
    check("filled_at stamped after the ack", len(ack_patches) == 1)
    sf = ack_patches[0].get("setup_features", {}) if ack_patches else {}
    ticket = sf.get("mt4_ticket")
    check("ack ticket bound (IBKR permId, int)", isinstance(ticket, int) and ticket >= 900001,
          f"ticket={ticket!r}")
    check("fill price recorded from ack", sf.get("fill_price") == 1.2345, f"{sf.get('fill_price')}")
    vp = bridge._book.get(int(ticket)) if ticket else None
    check("ledger: virtual ticket open with 15000 units",
          vp is not None and vp["status"] == "open" and vp["remaining_units"] == 15000.0)
    return {"ticket": int(ticket), "parent": parent, "stp": stp, "lmt": lmt}


# ---------------------------------------------------------------------------
#  4 + 5. Positions shape + open-orders inspection
# ---------------------------------------------------------------------------
def test_positions_and_orders(ctx: dict) -> None:
    print("\n[4+5] mt4-shaped positions + get_open_orders inspection")
    rows = bridge.get_positions_mt4()
    row = next((r for r in rows if r["ticket"] == ctx["ticket"]), None)
    check("positions row exists for the ticket", row is not None)
    if row:
        check("row shape: EURUSD, 0.15 lots, BUY, magic 88888, sl/tp carried",
              row["symbol"] == "EURUSD" and row["volume"] == 0.15 and row["cmd"] == 0
              and row["magic"] == 88888 and row["sl"] == 1.2300 and row["tp"] == 1.2430,
              json.dumps(row))
    oo = bridge.executor.get_open_orders()
    stp_row = next((o for o in oo if o["order_id"] == ctx["stp"].orderId), None)
    lmt_row = next((o for o in oo if o["order_id"] == ctx["lmt"].orderId), None)
    check("get_open_orders lists the working STP with its trigger",
          stp_row is not None and stp_row["aux_price"] == 1.2300 and stp_row["order_type"] == "STP")
    check("get_open_orders lists the LMT sibling + parent link",
          lmt_row is not None and lmt_row["lmt_price"] == 1.2430
          and lmt_row["parent_id"] == ctx["parent"].orderId)


# ---------------------------------------------------------------------------
#  6 + 7 + 8. modify_sl / partial_close / close
# ---------------------------------------------------------------------------
def test_lifecycle(ctx: dict) -> None:
    print("\n[6] modify_sl amends the STP child in place")
    ticket = ctx["ticket"]
    bridge.modify_sl("EURUSD", ticket, 1.2315)
    stp_order = fake.orders[ctx["stp"].orderId]
    check("same orderId, new auxPrice", abs(stp_order.auxPrice - 1.2315) < 1e-9)
    check("amend went through placeOrder (TWS modify semantics)",
          fake.place_calls.count(ctx["stp"].orderId) >= 2)
    check("ledger stop updated", bridge._book[ticket]["stop"] == 1.2315)

    print("\n[7] partial_close: 0.08 lots -> 8000 units; bracket re-sized")
    ids_before = set(fake.trades)
    bridge.partial_close("EURUSD", ticket, 0.08)
    new_trades = [fake.trades[oid] for oid in set(fake.trades) - ids_before]
    mkt = next(t for t in new_trades if t.order.orderType == "MKT")
    check("MKT SELL 8000 placed and filled", mkt.order.action == "SELL"
          and float(mkt.order.totalQuantity) == 8000.0 and mkt.orderStatus.status == "Filled")
    check("ledger remaining 7000 units", bridge._book[ticket]["remaining_units"] == 7000.0)
    check("STP child re-sized to 7000 (never over-closes)",
          float(fake.orders[ctx["stp"].orderId].totalQuantity) == 7000.0)
    check("LMT sibling re-sized in sync", float(fake.orders[ctx["lmt"].orderId].totalQuantity) == 7000.0)
    venue = sum(q for c, q, a in fake._positions.values() if _key(c) == _px("EUR/USD"))
    check("venue net position now 7000", venue == 7000.0, f"net={venue}")

    print("\n[8] ticket-scoped close cancels bracket and flattens")
    bridge.submit_order(symbol="EURUSD", cmd="close", volume=0.07, ticket=ticket)
    check("STP child cancelled", fake.trades[ctx["stp"].orderId].orderStatus.status == "Cancelled")
    check("LMT child cancelled", fake.trades[ctx["lmt"].orderId].orderStatus.status == "Cancelled")
    info = bridge.ticket_closed_info(ticket)
    check("ticket closed with exit fill price",
          info is not None and info["exit_price"] == 1.2345 and info["exit_reason"] == "close",
          str(info))
    venue = sum(q for c, q, a in fake._positions.values() if _key(c) == _px("EUR/USD"))
    check("venue flat on EUR/USD", venue == 0.0, f"net={venue}")
    check("ticket no longer open", ticket not in bridge.get_open_tickets())


# ---------------------------------------------------------------------------
#  9 + 10. Ack shape + netting guard
# ---------------------------------------------------------------------------
def test_ack_and_netting() -> dict:
    print("\n[9+10] ack shape + opposite-direction netting guard")
    h = bridge.submit_order(symbol="GBPUSD", cmd="buy", volume=0.10, sl=1.2950, tp=1.3100)
    ack = bridge.wait_for_ack(handle=h, timeout_s=2.0)
    check("ack shape: ok/id/ticket/fill_price/filled_qty present",
          ack is not None and all(k in ack for k in ("ok", "id", "ticket", "fill_price", "filled_qty")))
    check("ack values: ok True, int ticket, fill at 1.30, 10000 units",
          ack["ok"] is True and isinstance(ack["ticket"], int)
          and ack["fill_price"] == 1.3000 and ack["filled_qty"] == 10000.0)
    gbp_ticket = int(ack["ticket"])

    refused = False
    try:
        bridge.submit_order(symbol="GBPUSD", cmd="sell", volume=0.05, sl=1.3050, tp=1.2900)
    except RuntimeError as e:
        refused = "netting guard" in str(e)
    check("opposite-direction entry REFUSED (netting guard)", refused)
    check("long ticket still intact", bridge._book[gbp_ticket]["status"] == "open"
          and bridge._book[gbp_ticket]["remaining_units"] == 10000.0)
    return {"gbp_ticket": gbp_ticket}


# ---------------------------------------------------------------------------
#  11. Venue rejection recorded -> resolver expires (never retried forever)
# ---------------------------------------------------------------------------
def test_reject_and_expiry() -> None:
    print("\n[11] venue rejection: ack ok=False, no ticket; resolver EXPIRES the setup")
    fake.reject_next = True
    h = bridge.submit_order(symbol="NZDUSD", cmd="buy", volume=0.10, sl=0.5950, tp=0.6100)
    ack = bridge.wait_for_ack(handle=h, timeout_s=2.0)
    check("rejected entry: ack ok=False with raw status",
          ack is not None and ack["ok"] is False and ack["raw_status"] == "Inactive", str(ack))
    check("no virtual ticket bound for the reject",
          all(vp["symbol"] != "NZD/USD" for vp in bridge._book.values()))

    # daemon path: no filled_at patch on rejection
    POSTS.clear(); PATCHES.clear()
    orig_ticket_col = scanner._TICKET_COLUMN_OK
    scanner._TICKET_COLUMN_OK = False
    fake.reject_next = True
    scanner.open_new_trade(
        symbol="NZD/USD", direction="LONG", entry_price=0.6000,
        stop_loss=0.5950, target_price=0.6100, timeframe="1h",
        confidence=55, rr=2.0, volume=0.10, style="intraday",
    )
    scanner._TICKET_COLUMN_OK = orig_ticket_col
    check("rejected daemon entry: filled_at NOT stamped",
          not any(p and "filled_at" in p for p in PATCHES))

    # resolver expires the aged, ticket-less setup — recorded, not retried
    PATCHES.clear()
    scanner._resolved_setup_ids.clear()
    GET_ROWS[:] = [{
        "id": "NZDUSD_reject", "symbol": "NZD/USD", "created_at": "2020-01-01T00:00:00+00:00",
        "verdict": "BUY", "setup_features": {"auto": True}, "ticket": None,
        "stop_loss": 0.5950, "target_price": 0.6100, "price": 0.6000, "outcome": "pending",
    }]
    scanner.resolve_closed_ibkr_setups()
    exp = [p for p in PATCHES if p and p.get("outcome") == "expired"]
    check("aged unfilled setup EXPIRED by the IBKR resolver", len(exp) == 1,
          json.dumps(PATCHES)[:120])
    check("expiry lesson records the no-fill reason", exp and "no fill on IBKR" in exp[0].get("lesson", ""))


# ---------------------------------------------------------------------------
#  12. Stop fill -> fill-based resolution (sl_hit from the actual exit)
# ---------------------------------------------------------------------------
def test_stop_fill_resolution() -> None:
    print("\n[12] stop fill -> resolver marks sl_hit from the actual exit price")
    h = bridge.submit_order(symbol="USDJPY", cmd="buy", volume=1.5, sl=155.00, tp=157.00)
    ack = bridge.wait_for_ack(handle=h, timeout_s=2.0)
    ticket = int(ack["ticket"])
    check("JPY entry sized in base units (1.5 lots -> 150000)",
          bridge._book[ticket]["initial_units"] == 150000.0)
    stp_id = bridge._book[ticket]["stop_order_id"]
    fake.trigger(stp_id, 155.00)   # venue stops the position out
    info = bridge.ticket_closed_info(ticket)
    check("bridge saw the stop fill (reason=stop, exit=155.0)",
          info is not None and info["exit_reason"] == "stop" and info["exit_price"] == 155.00,
          str(info))

    PATCHES.clear()
    scanner._resolved_setup_ids.clear()
    GET_ROWS[:] = [{
        "id": "USDJPY_stop", "symbol": "USD/JPY", "created_at": "2020-01-01T00:00:00+00:00",
        "verdict": "BUY", "setup_features": {"auto": True, "mt4_ticket": ticket}, "ticket": None,
        "stop_loss": 155.00, "target_price": 157.00, "price": 156.00, "outcome": "pending",
    }]
    orig_ticket_col = scanner._TICKET_COLUMN_OK
    scanner._TICKET_COLUMN_OK = False
    scanner.resolve_closed_ibkr_setups()
    scanner._TICKET_COLUMN_OK = orig_ticket_col
    res = [p for p in PATCHES if p and p.get("outcome") == "sl_hit"]
    check("setup resolved sl_hit at the stop fill price",
          len(res) == 1 and res[0].get("outcome_price") == 155.00, json.dumps(PATCHES)[:120])
    check("lesson names the IBKR ticket + stop exit", res and f"IBKR ticket {ticket}" in res[0]["lesson"])


# ---------------------------------------------------------------------------
#  13. Restore-to-pending + Supabase sync rows
# ---------------------------------------------------------------------------
def test_restore_and_sync(ctx9: dict) -> None:
    print("\n[13] restore-to-pending + sync_to_supabase row shapes")
    PATCHES.clear()
    GET_ROWS[:] = [{
        "id": "GBPUSD_rest", "symbol": "GBP/USD", "outcome": "tp_hit",
        "setup_features": {"auto": True, "mt4_ticket": ctx9["gbp_ticket"]}, "ticket": None,
    }]
    scanner.ensure_active_ibkr_setups_pending()
    rest = [p for p in PATCHES if p and p.get("outcome") == "pending"]
    check("resolved setup with a STILL-OPEN ticket restored to pending", len(rest) == 1)

    sent = {"account": [], "positions": [], "trades": []}
    orig = (ibkr_store.sync_account, ibkr_store.sync_positions, ibkr_store.sync_trades)
    ibkr_store.sync_account = lambda row: sent["account"].append(row) or True
    ibkr_store.sync_positions = lambda rows: sent["positions"].extend(rows) or True
    ibkr_store.sync_trades = lambda rows: sent["trades"].extend(rows) or True
    try:
        ok = bridge.sync_to_supabase()
    finally:
        (ibkr_store.sync_account, ibkr_store.sync_positions, ibkr_store.sync_trades) = orig
    check("sync_to_supabase succeeded", ok is True)
    check("account row: net_liquidation + cash + currency",
          sent["account"] and sent["account"][0].get("net_liquidation") == 1000000.0
          and sent["account"][0].get("currency") == "USD")
    gbp_row = next((r for r in sent["positions"] if r["instrument"] == "GBP/USD"), None)
    check("positions row: GBP/USD long 10000 forex units",
          gbp_row is not None and gbp_row["direction"] == "long"
          and gbp_row["units"] == 10000.0 and gbp_row["asset_class"] == "forex",
          json.dumps(gbp_row or {}))
    entry_fills = [r for r in sent["trades"] if r["instrument"] == "GBP/USD" and r["side"] == "BUY"]
    check("trades rows: entry fill with ticket-scoped exec_id",
          entry_fills and entry_fills[0]["exec_id"].startswith(f"{ctx9['gbp_ticket']}."),
          json.dumps(entry_fills[:1])[:120])


# ---------------------------------------------------------------------------
#  14. Restart reconciliation: rebind keeps modify_sl; flat venue -> external
# ---------------------------------------------------------------------------
def test_restart_reconcile(ctx9: dict) -> None:
    print("\n[14] restart: ledger reload + bracket rebind + external-close detection")
    # AUD/USD entry on the CURRENT bridge, then the venue stops it out WITHOUT
    # the bridge watching (simulates a fill while the daemon was down).
    h = bridge.submit_order(symbol="AUDUSD", cmd="buy", volume=0.10, sl=0.6550, tp=0.6700)
    ack = bridge.wait_for_ack(handle=h, timeout_s=2.0)
    aud_ticket = int(ack["ticket"])
    aud_stp = bridge._book[aud_ticket]["stop_order_id"]
    fake.trigger(aud_stp, 0.6550)   # venue-side stop while "down"

    bridge2 = bridge_mod.IBKRLiveBridge(executor=_real_executor_cls(ib=fake), ledger_path=LEDGER)
    bridge2.connect()
    info = bridge2.ticket_closed_info(aud_ticket)
    check("venue-flat ticket marked closed externally (exit unknown)",
          info is not None and info["exit_reason"] == "external" and info["exit_price"] is None,
          str(info))
    gbp = bridge2._book.get(ctx9["gbp_ticket"])
    check("GBP/USD ticket reloaded from the persisted ledger", gbp is not None and gbp["status"] == "open")
    bridge2.modify_sl("GBPUSD", ctx9["gbp_ticket"], 1.2960)
    check("modify_sl works after restart (rebound to the live stop order)",
          gbp["stop"] == 1.2960)
    venue = sum(q for c, q, a in fake._positions.values() if _key(c) == _px("GBP/USD"))
    check("GBP/USD venue position untouched by reconciliation", venue == 10000.0, f"net={venue}")


def main() -> int:
    test_provider_selection()
    test_units_math()
    ctx = test_daemon_entry()
    test_positions_and_orders(ctx)
    test_lifecycle(ctx)
    ctx9 = test_ack_and_netting()
    test_reject_and_expiry()
    test_stop_fill_resolution()
    test_restore_and_sync(ctx9)
    test_restart_reconcile(ctx9)
    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{len(RESULTS) - n_fail}/{len(RESULTS)} checks passed"
          + (f" — {n_fail} FAILED" if n_fail else " — ALL PASS"))
    if n_fail:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  FAILED: {name} — {detail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
