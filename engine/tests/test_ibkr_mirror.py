"""IBKR paper mirror: the order-decision rules, divergence math, and safety guards.

These are the first tests over the IBKR path (~2,300 lines of order-adjacent code
previously untested). Everything runs against a FakeExecutor through run_mirror's
injectable seam — no gateway, no network, no orders.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.run_ibkr_mirror as rim
from apex_quant.execution.ibkr_executor import contract_spec, round_quantity


# ── fakes ─────────────────────────────────────────────────────────────────────
def _fill(price, qty, ccy="USD", commission=1.0):
    return SimpleNamespace(status="filled", raw_status="Filled", avg_fill_price=price,
                           filled_quantity=qty, commission=commission,
                           commission_currency=ccy, order_id=1, perm_id=11)


class FakeExecutor:
    def __init__(self, positions=None, net_liq=100_000.0):
        self.account = "DUQ278370"
        self._positions = positions or []
        self._net_liq = net_liq
        self.connected = False
        self.submits: list = []
        self.closes: list = []

    def connect(self):
        self.connected = True
        return self.account

    def disconnect(self):
        self.connected = False

    def get_account(self):
        return {"account": self.account, "NetLiquidation": self._net_liq,
                "AvailableFunds": self._net_liq, "currency": "GBP"}

    def get_positions(self):
        return list(self._positions)

    def get_portfolio(self):
        return []

    def get_pnl(self):
        return {}

    def submit_order(self, symbol, direction, volume, stop=None, target=None):
        self.submits.append((symbol, direction, volume))
        return SimpleNamespace(quantity=volume, submitted_at="t", stop=stop, target=target)

    def close_position(self, symbol):
        self.closes.append(symbol)
        return SimpleNamespace(quantity=1.0, submitted_at="t", stop=None, target=None)

    def wait_for_fill(self, handle, timeout_s=0):
        return _fill(100.0, handle.quantity)


def _state(entries=None, exits=None, day="2026-07-18"):
    return {
        "book": "book_h", "last_processed_date": day,
        "open_positions": {
            e["instrument"]: {"direction": e["direction"], "units": e["units"],
                              "entry_price": e.get("price", 100.0),
                              "entry_time": e.get("entry_time", day),
                              "stop": e.get("stop"), "target": e.get("target")}
            for e in (entries or [])
        },
        "trades": [
            {"instrument": x["instrument"], "direction": x["direction"],
             "units": x.get("units", 1.0), "exit_price": x.get("price", 100.0),
             "exit_time": day, "exit_reason": x.get("reason", "stop")}
            for x in (exits or [])
        ],
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(rim, "sync_ibkr_state", lambda *a, **k: True)  # no Supabase
    def run(state, executor, **kw):
        sp = tmp_path / "state.json"
        sp.write_text(json.dumps(state))
        return rim.run_mirror(sp, tmp_path / "mirror", executor, timeout_s=1.0, **kw)
    return run, tmp_path


# ── pure units ────────────────────────────────────────────────────────────────
def test_plan_extracts_only_the_processed_bar():
    st = _state(entries=[{"instrument": "AAPL", "direction": "long", "units": 3}],
                exits=[{"instrument": "EUR/USD", "direction": "short"}])
    st["open_positions"]["MSFT"] = {"direction": "long", "units": 1,
                                    "entry_price": 1.0, "entry_time": "2026-07-10"}
    plan = rim.plan_for_day(st)
    assert plan["date"] == "2026-07-18"
    assert [e["instrument"] for e in plan["entries"]] == ["AAPL"]   # MSFT filtered
    assert [x["instrument"] for x in plan["exits"]] == ["EUR/USD"]
    assert rim.plan_for_day({}) == {"date": None, "entries": [], "exits": []}


def test_divergence_cost_sign_is_direction_adjusted():
    assert rim._divergence_bps(100.0, 100.1, "BUY")["cost_bps"] > 0    # bought higher: worse
    assert rim._divergence_bps(100.0, 99.9, "BUY")["cost_bps"] < 0
    assert rim._divergence_bps(100.0, 99.9, "SELL")["cost_bps"] > 0    # sold lower: worse


def test_summary_never_sums_commissions_across_currencies():
    rows = [{"asset_class": "equity", "divergence_bps": 1.0, "size_delta_pct": 15.0,
             "commission": 1.0, "commission_currency": "USD"},
            {"asset_class": "equity", "divergence_bps": -3.0, "size_delta_pct": None,
             "commission": 2.0, "commission_currency": "GBP"}]
    s = rim._summary(rows)["equity"]
    assert s["total_commission"] is None                       # mixed ccy: no naive sum
    assert s["commission_by_currency"] == {"USD": 1.0, "GBP": 2.0}
    assert s["mean_abs_divergence_bps"] == 2.0
    assert s["mean_abs_size_delta_pct"] == 15.0                # price/size kept separate

    single = rim._summary([rows[0]])["equity"]
    assert single["total_commission"] == 1.0                   # unambiguous: kept


def test_runaway_guard_thresholds():
    plan = {"date": "d", "entries": [{}] * 20, "exits": [{}] * 5}
    assert rim.runaway_guard(plan, 25) is None
    assert "RUNAWAY" in rim.runaway_guard(plan, 24)


def test_equity_floor_fails_closed():
    assert rim.equity_floor_breached(99_000, 100_000) is True
    assert rim.equity_floor_breached(100_000, 100_000) is False
    assert rim.equity_floor_breached(None, None) is False      # unset floor: paper default
    assert rim.equity_floor_breached(None, 50_000) is True     # floor set, NetLiq unknown
    assert rim.equity_floor_breached("junk", 50_000) is True


def test_contract_spec_and_rounding():
    assert contract_spec("AAPL")["secType"] == "STK"
    assert contract_spec("BTC/USD")["exchange"] == "PAXOS"
    assert contract_spec("EUR/USD")["exchange"] == "IDEALPRO"
    assert round_quantity("equity", 2.6) == 3.0                # whole shares (error 10243)
    assert float(round_quantity("equity", 2.4)).is_integer()
    assert round_quantity("forex", 12345.678) > 0              # fractional allowed


# ── integration through the injectable seam ───────────────────────────────────
def test_happy_path_exits_first_then_entries(env):
    run, tmp = env
    ex = FakeExecutor(positions=[{"engine_symbol": "EUR/USD", "quantity": -20_000}])
    code, record = run(_state(
        entries=[{"instrument": "AAPL", "direction": "long", "units": 2.6}],
        exits=[{"instrument": "EUR/USD", "direction": "short"}]), ex)
    assert code == 0
    assert ex.closes == ["EUR/USD"]                            # exit placed first
    assert ex.submits == [("AAPL", "long", 3.0)]               # whole-share rounded
    entry = [o for o in record["orders"] if o["kind"] == "entry"][0]
    assert entry["size_delta_pct"] == pytest.approx(15.385, abs=0.01)
    assert (tmp / "mirror" / "2026-07-18.json").exists()       # record persisted
    ptr = json.loads((tmp / "mirror" / rim.POINTER_NAME).read_text())
    assert ptr["last_mirrored_date"] == "2026-07-18"


def test_rerun_is_a_strict_noop(env):
    run, _ = env
    st = _state(entries=[{"instrument": "AAPL", "direction": "long", "units": 3}])
    ex = FakeExecutor()
    assert run(st, ex)[0] == 0
    code, record = run(st, ex)
    assert (code, record) == (0, None)
    assert len(ex.submits) == 1                                # nothing re-traded


def test_never_flips_and_dedupes(env):
    run, _ = env
    ex = FakeExecutor(positions=[{"engine_symbol": "MSFT", "quantity": -10},
                                 {"engine_symbol": "AAPL", "quantity": 5}])
    code, record = run(_state(entries=[
        {"instrument": "MSFT", "direction": "long", "units": 4},   # opposite held
        {"instrument": "AAPL", "direction": "long", "units": 4},   # same held
    ]), ex)
    assert code == 0 and ex.submits == []
    reasons = " | ".join(s["reason"] for s in record["skipped"])
    assert "refusing to flip" in reasons and "already held" in reasons


def test_crypto_short_and_zero_qty_are_skipped(env):
    run, _ = env
    ex = FakeExecutor()
    code, record = run(_state(entries=[
        {"instrument": "BTC/USD", "direction": "short", "units": 1.0},
        {"instrument": "AAPL", "direction": "long", "units": 0.4},   # rounds to 0
    ]), ex)
    assert code == 0 and ex.submits == []
    assert len(record["skipped"]) == 2


def test_exit_without_position_is_skipped_not_errored(env):
    run, _ = env
    ex = FakeExecutor(positions=[])
    code, record = run(_state(exits=[{"instrument": "EUR/USD", "direction": "long"}]), ex)
    assert code == 0 and ex.closes == []
    assert record["skipped"][0]["reason"].startswith("no IBKR position")


def test_runaway_guard_refuses_before_connecting(env):
    run, tmp = env
    big = _state(entries=[{"instrument": f"SYM{i}", "direction": "long", "units": 1}
                          for i in range(30)])
    ex = FakeExecutor()
    code, record = run(big, ex)
    assert code == 1 and record is None
    assert ex.connected is False                               # refused BEFORE connect
    assert not (tmp / "mirror" / "2026-07-18.json").exists()   # nothing written


def test_equity_floor_refuses_after_connect_before_orders(env):
    run, tmp = env
    ex = FakeExecutor(net_liq=95_000)
    code, record = run(_state(entries=[{"instrument": "AAPL", "direction": "long",
                                        "units": 3}]), ex, min_net_liq=100_000)
    assert code == 1 and record is None
    assert ex.submits == [] and ex.closes == []                # no orders placed
    assert not (tmp / "mirror" / "2026-07-18.json").exists()
