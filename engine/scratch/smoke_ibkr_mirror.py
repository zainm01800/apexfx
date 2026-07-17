"""Offline smoke test for the IBKR paper mirror — NO gateway needed.

Stubs the ib_async CLIENT (a FakeIB that quacks like ib_async.IB) and drives
the real executor + mirror code against it. Contract/order dataclasses are
the REAL ib_async ones (installed in engine/.venv-mac), so the mapping is
verified against the actual library surface.

Proves:
  1. account allowlist: a wrong account is REFUSED (IBKRAccountError), the
     paper account DUQ278370 connects, and IBKR_ACCOUNT env override works;
  2. contract mapping: AAPL -> STK/SMART/USD, BTC/USD -> CRYPTO/PAXOS,
     EUR/USD (and USD/JPY) -> CASH/IDEALPRO, incl. quantity rounding and the
     position reverse-mapping;
  3. reconciliation/idempotency: a synthetic state.json with a bar-D entry,
     an already-held entry, a crypto short (venue long-only), an old position
     and a bar-D exit produces exactly TWO orders (exit cover + entry), and a
     second run is a strict no-op (no duplicate orders);
  4. divergence math: signed bps and direction-adjusted cost_bps are exact,
     per-order and in the by-asset-class summary;
  5. fill timeout -> order cancelled, status recorded (no stray orders).

Run:
    cd engine
    .venv-mac/bin/python scratch/smoke_ibkr_mirror.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

from ib_async import AccountValue, Crypto, Forex, Position, Stock  # noqa: E402

from apex_quant.execution import ibkr_executor as ibkr  # noqa: E402
import run_ibkr_mirror as mirror  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
#  Fake ib_async client layer
# ---------------------------------------------------------------------------
def _key(contract) -> str:
    return f"{contract.secType}:{contract.symbol}/{contract.currency}"


class FakeTrade:
    def __init__(self, contract, order, filled: bool, fill_price: float | None):
        self.contract = contract
        self.order = order
        self.log = []
        self._done = filled
        self.orderStatus = SimpleNamespace(
            status="Filled" if filled else "Submitted",
            filled=float(order.totalQuantity) if filled else 0.0,
            remaining=0.0 if filled else float(order.totalQuantity),
            avgFillPrice=fill_price if filled else None,
            permId=999,
        )
        self.fills = (
            [SimpleNamespace(commissionReport=SimpleNamespace(commission=1.0, currency="USD"))]
            if filled else []
        )

    def isDone(self) -> bool:
        return self._done


class FakeIB:
    """Minimal ib_async.IB stand-in. Fills MKT orders immediately at
    ``fill_prices[key(contract)]`` (default 100.0) unless auto_fill=False."""

    def __init__(self, accounts, fill_prices=None, seed_positions=(), auto_fill=True):
        self._accounts = list(accounts)
        self.fill_prices = dict(fill_prices or {})
        self.auto_fill = auto_fill
        self.connected = False
        self.disconnect_calls = 0
        self.place_count = 0
        self.cancelled = []
        self._next_id = 5000
        # seed_positions: iterable of (contract, signed_qty, avg_cost)
        self._positions = { _key(c): [c, float(q), float(a)] for c, q, a in seed_positions }

    # -- connection --
    def connect(self, host, port, clientId=1, timeout=4.0, **kw):
        self.connected = True

    def disconnect(self):
        self.connected = False
        self.disconnect_calls += 1

    def managedAccounts(self):
        return list(self._accounts)

    # -- contracts / orders --
    def qualifyContracts(self, *contracts):
        for c in contracts:
            c.conId = 4242
        return list(contracts)

    def placeOrder(self, contract, order):
        self.place_count += 1
        self._next_id += 1
        order.orderId = self._next_id
        px = self.fill_prices.get(_key(contract), 100.0)
        if self.auto_fill:
            qty = float(order.totalQuantity) * (1 if order.action == "BUY" else -1)
            row = self._positions.setdefault(_key(contract), [contract, 0.0, 0.0])
            row[1] += qty
            row[2] = px
        return FakeTrade(contract, order, self.auto_fill, px if self.auto_fill else None)

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)

    # -- state --
    def positions(self, account=""):
        return [
            Position(account, c, q, a)
            for c, q, a in self._positions.values() if q != 0.0
        ]

    def accountSummary(self, account=""):
        return [
            AccountValue(account, "NetLiquidation", "1000000.00", "USD", ""),
            AccountValue(account, "AvailableFunds", "990000.00", "USD", ""),
        ]

    def sleep(self, seconds=0.0):
        return None


# ---------------------------------------------------------------------------
#  1. Account allowlist
# ---------------------------------------------------------------------------
def test_allowlist() -> None:
    print("\n[1] account allowlist")
    os.environ.pop("IBKR_ACCOUNT", None)
    wrong = ibkr.IBKRExecutor(ib=FakeIB(accounts=["DU999999"]))
    try:
        wrong.connect()
        check("wrong account refused", False, "connect() did not raise")
    except ibkr.IBKRAccountError as e:
        check("wrong account refused", "DU999999" in str(e), str(e)[:70])
    check("refusal disconnected the gateway", wrong._ib.disconnect_calls == 1)
    check("executor not left connected", not wrong.is_connected)

    good = ibkr.IBKRExecutor(ib=FakeIB(accounts=["DUQ278370"]))
    acct = good.connect()
    check("paper account accepted", acct == "DUQ278370" and good.is_connected)
    check("default account is the paper one", ibkr.DEFAULT_ACCOUNT == "DUQ278370")

    os.environ["IBKR_ACCOUNT"] = "DU777777"
    try:
        env_exec = ibkr.IBKRExecutor(ib=FakeIB(accounts=["DU777777"]))
        check("IBKR_ACCOUNT env override respected",
              env_exec.account == "DU777777" and env_exec.connect() == "DU777777")
    finally:
        os.environ.pop("IBKR_ACCOUNT", None)


# ---------------------------------------------------------------------------
#  2. Contract mapping
# ---------------------------------------------------------------------------
def test_contract_mapping() -> None:
    print("\n[2] contract mapping (real ib_async dataclasses)")
    s = ibkr.contract_spec("AAPL")
    check("AAPL spec", s == {"asset_class": "equity", "secType": "STK", "symbol": "AAPL",
                             "currency": "USD", "exchange": "SMART"}, str(s))
    c = ibkr.contract_spec("BTC/USD")
    check("BTC/USD spec", c == {"asset_class": "crypto", "secType": "CRYPTO", "symbol": "BTC",
                                "currency": "USD", "exchange": "PAXOS"}, str(c))
    f = ibkr.contract_spec("EUR/USD")
    check("EUR/USD spec", f == {"asset_class": "forex", "secType": "CASH", "symbol": "EUR",
                                "currency": "USD", "exchange": "IDEALPRO"}, str(f))
    j = ibkr.contract_spec("USD/JPY")
    check("USD/JPY spec forex (not crypto)", j["asset_class"] == "forex"
          and j["symbol"] == "USD" and j["currency"] == "JPY", str(j))

    con = ibkr.make_contract(s)
    check("AAPL -> Stock SMART USD", isinstance(con, Stock) and con.secType == "STK"
          and con.exchange == "SMART" and con.currency == "USD")
    con = ibkr.make_contract(c)
    check("BTC/USD -> Crypto PAXOS USD", isinstance(con, Crypto) and con.secType == "CRYPTO"
          and con.symbol == "BTC" and con.exchange == "PAXOS" and con.currency == "USD")
    con = ibkr.make_contract(f)
    check("EUR/USD -> CASH EUR.USD IDEALPRO", isinstance(con, Forex) and con.secType == "CASH"
          and con.symbol == "EUR" and con.currency == "USD" and con.exchange == "IDEALPRO")

    check("position reverse-map STK", ibkr.engine_symbol_for_contract(Stock("AAPL", "SMART", "USD")) == "AAPL")
    check("position reverse-map CRYPTO", ibkr.engine_symbol_for_contract(Crypto("BTC", "PAXOS", "USD")) == "BTC/USD")
    check("position reverse-map CASH", ibkr.engine_symbol_for_contract(Forex("EURUSD")) == "EUR/USD")

    check("qty rounding equity 2dp", ibkr.round_quantity("equity", 44.018286) == 44.02)
    check("qty rounding crypto 6dp", ibkr.round_quantity("crypto", 0.123456789) == 0.123457)
    check("qty rounding forex whole", ibkr.round_quantity("forex", 12345.6) == 12346.0)


# ---------------------------------------------------------------------------
#  3 + 4. Mirror run: reconciliation, idempotency, divergence math
# ---------------------------------------------------------------------------
DAY = "2026-07-16"
SYNTH_STATE = {
    "schema_version": 1,
    "book": "book_d_multiasset_252",
    "last_processed_date": DAY,
    "open_positions": {
        # filled this bar -> should become ONE buy order (44.02 rounded)
        "AAPL": {"symbol": "AAPL", "direction": "long", "units": 44.018286,
                 "entry_price": 100.0, "entry_time": f"{DAY}T00:00:00+00:00",
                 "stop": 95.0, "target": 107.5},
        # filled this bar but already held on IBKR -> dedupe skip
        "MSFT": {"symbol": "MSFT", "direction": "short", "units": 24.7314,
                 "entry_price": 200.0, "entry_time": f"{DAY}T00:00:00+00:00",
                 "stop": 210.0, "target": 185.0},
        # crypto short this bar -> venue long-only skip
        "SOL/USD": {"symbol": "SOL/USD", "direction": "short", "units": 100.0,
                    "entry_price": 150.0, "entry_time": f"{DAY}T00:00:00+00:00",
                    "stop": 160.0, "target": 135.0},
        # opened on an OLDER bar -> untouched by the mirror plan
        "NVDA": {"symbol": "NVDA", "direction": "long", "units": 10.0,
                 "entry_price": 50.0, "entry_time": "2026-07-15T00:00:00+00:00",
                 "stop": 48.0, "target": 53.0},
    },
    "trades": [
        # closed this bar -> one cover order on the held short
        {"instrument": "TSLA", "direction": "short", "entry_time": "2026-07-14",
         "entry_price": 210.0, "exit_time": DAY, "exit_price": 200.0,
         "units": 15.0, "pnl": 150.0, "return_pct": 0.0476, "exit_reason": "target"},
        # closed on an OLDER bar -> untouched
        {"instrument": "XLE", "direction": "long", "entry_time": "2026-07-10",
         "entry_price": 90.0, "exit_time": "2026-07-15", "exit_price": 91.0,
         "units": 100.0, "pnl": 100.0, "return_pct": 0.0111, "exit_reason": "stop"},
    ],
    "pending": {},
}


def test_mirror_run(tmp: Path) -> None:
    print("\n[3+4] mirror run: reconciliation, idempotency, divergence")
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps(SYNTH_STATE), encoding="utf-8")
    mdir = tmp / "ibkr_mirror"

    fake = FakeIB(
        accounts=["DUQ278370"],
        fill_prices={"STK:AAPL/USD": 100.05, "STK:TSLA/USD": 200.10},
        seed_positions=[
            (Stock("MSFT", "SMART", "USD"), -24.7314, 200.0),   # matches engine short
            (Stock("TSLA", "SMART", "USD"), -15.0, 210.0),      # engine exits today
        ],
    )
    executor = ibkr.IBKRExecutor(ib=fake)

    plan = mirror.plan_for_day(SYNTH_STATE)
    check("plan: only bar-D entries",
          sorted(e["instrument"] for e in plan["entries"]) == ["AAPL", "MSFT", "SOL/USD"])
    check("plan: only bar-D exits",
          [x["instrument"] for x in plan["exits"]] == ["TSLA"])

    code = mirror.main(["--state", str(state_path), "--mirror-dir", str(mdir)], executor=executor)
    check("mirror run exits 0", code == 0)
    check("exactly two orders placed", fake.place_count == 2,
          f"place_count={fake.place_count}")

    rec_path = mdir / f"{DAY}.json"
    check("daily record written", rec_path.exists())
    rec = json.loads(rec_path.read_text(encoding="utf-8"))

    exit_rec = next(o for o in rec["orders"] if o["kind"] == "exit")
    entry_rec = next(o for o in rec["orders"] if o["kind"] == "entry")
    check("exit first, covers the short (BUY 15 TSLA)",
          rec["orders"][0]["kind"] == "exit" and exit_rec["instrument"] == "TSLA"
          and exit_rec["action"] == "BUY" and exit_rec["quantity_sent"] == 15.0)
    check("entry is a rounded BUY 44.02 AAPL",
          entry_rec["instrument"] == "AAPL" and entry_rec["action"] == "BUY"
          and entry_rec["quantity_sent"] == 44.02 and entry_rec["status"] == "filled")
    check("entry carries recorded (unattached) stop/target",
          entry_rec["stop_recorded"] == 95.0 and entry_rec["target_recorded"] == 107.5
          and entry_rec["brackets_attached"] is False)

    # divergence math: AAPL 100.0 -> 100.05 = +5 bps buy (cost +5);
    # TSLA 200.0 -> 200.10 cover buy = +5 bps (cost +5, bought back higher)
    check("entry divergence +5bps / cost +5bps",
          entry_rec["divergence_bps"] == 5.0 and entry_rec["cost_bps"] == 5.0,
          f"{entry_rec['divergence_bps']}/{entry_rec['cost_bps']}")
    check("exit divergence +5bps / cost +5bps",
          exit_rec["divergence_bps"] == 5.0 and exit_rec["cost_bps"] == 5.0,
          f"{exit_rec['divergence_bps']}/{exit_rec['cost_bps']}")
    d = mirror._divergence_bps(200.0, 199.90, "SELL")
    check("SELL side: sold lower = positive cost",
          d["divergence_bps"] == -5.0 and d["cost_bps"] == 5.0, str(d))

    check("commissions recorded per order",
          entry_rec["commission"] == 1.0 and exit_rec["commission"] == 1.0
          and entry_rec["commission_currency"] == "USD")
    summ = rec["summary"].get("equity", {})
    check("summary by asset class (equity: mean 5 / max 5 / comm 2.0)",
          summ.get("n_filled") == 2 and summ.get("mean_abs_divergence_bps") == 5.0
          and summ.get("max_abs_divergence_bps") == 5.0 and summ.get("total_commission") == 2.0,
          str(summ))

    reasons = {s["instrument"]: s["reason"] for s in rec["skipped"]}
    check("already-held entry deduped", "MSFT" in reasons and "already held" in reasons["MSFT"])
    check("crypto short skipped (venue long-only)",
          "SOL/USD" in reasons and "long-only" in reasons["SOL/USD"])
    check("no skip for the untouched old position", "NVDA" not in reasons)

    chk = {c["instrument"]: c["issue"] for c in rec["post_run_position_check"]}
    check("post-run check flags engine-held/IBKR-flat (NVDA)",
          chk.get("NVDA", "").startswith("engine holds"))
    check("post-run check clean for mirrored+deduped names",
          "AAPL" not in chk and "MSFT" not in chk and "TSLA" not in chk, str(chk))

    pointer = json.loads((mdir / "mirror_state.json").read_text(encoding="utf-8"))
    check("idempotency pointer advanced", pointer["last_mirrored_date"] == DAY)

    # re-run: strict no-op, no duplicate orders, record untouched
    before = rec_path.read_text(encoding="utf-8")
    code2 = mirror.main(["--state", str(state_path), "--mirror-dir", str(mdir)], executor=executor)
    check("re-run exits 0 as no-op", code2 == 0)
    check("re-run placed NO further orders", fake.place_count == 2,
          f"place_count={fake.place_count}")
    check("record unchanged after re-run", rec_path.read_text(encoding="utf-8") == before)


# ---------------------------------------------------------------------------
#  5. Fill timeout -> cancel, recorded
# ---------------------------------------------------------------------------
def test_fill_timeout() -> None:
    print("\n[5] fill timeout cancels the order")
    fake = FakeIB(accounts=["DUQ278370"], auto_fill=False)
    executor = ibkr.IBKRExecutor(ib=fake)
    executor.connect()
    handle = executor.submit_order("AAPL", "long", volume=10.0, stop=95.0, target=107.5)
    res = executor.wait_for_fill(handle, timeout_s=0.3, poll_interval_s=0.05)
    check("timeout reported", res.status == "timeout_cancelled", res.status)
    check("order cancelled on timeout", fake.cancelled == [handle.order_id], str(fake.cancelled))
    check("no fill price on timeout", res.avg_fill_price is None and not res.filled)


def main() -> int:
    print("IBKR paper mirror — offline smoke test (fake ib_async client)")
    test_allowlist()
    test_contract_mapping()
    with tempfile.TemporaryDirectory() as td:
        test_mirror_run(Path(td))
    test_fill_timeout()
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
