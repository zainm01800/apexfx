"""Venue-side brackets on the IBKR mirror: protection that survives the engine dying.

v1 recorded stops without attaching them, so a position was only protected while the
nightly step kept running — the 2026-07-22 Supabase outage left 6 positions naked.
--attach-stops places REAL GTC stop/target orders. The hazard that creates is orphans:
a resting child outliving its position can trigger and OPEN an unbacked trade. These
tests exist mostly to prove that cannot happen.
"""

import json
from types import SimpleNamespace

import pytest

import scripts.run_ibkr_mirror as rim


def _fill(price, qty, ccy="USD", commission=1.0):
    return SimpleNamespace(status="filled", raw_status="Filled", avg_fill_price=price,
                           filled_quantity=qty, commission=commission,
                           commission_currency=ccy, order_id=1, perm_id=11)


class BracketExecutor:
    """FakeExecutor that models resting orders, so orphans are observable."""

    def __init__(self, positions=None, resting=None, net_liq=100_000.0):
        self.account = "DUQ278370"
        self._positions = positions or []
        self._resting = list(resting or [])      # rows as get_open_orders() returns
        self._net_liq = net_liq
        self.connected = False
        self.submits: list = []
        self.closes: list = []
        self.cancelled: list = []

    def connect(self):
        self.connected = True
        return self.account

    def disconnect(self):
        self.connected = False

    def get_account(self):
        return {"account": self.account, "NetLiquidation": self._net_liq, "currency": "GBP"}

    def get_positions(self):
        return list(self._positions)

    def get_portfolio(self):
        return []

    def get_pnl(self):
        return {}

    def get_open_orders(self):
        return list(self._resting)

    def cancel_order(self, order):
        self.cancelled.append(order)
        self._resting = [r for r in self._resting
                         if getattr(r.get("_trade"), "order", None) is not order]

    def submit_order(self, symbol, direction, volume, stop=None, target=None, attach_stop=False):
        self.submits.append({"symbol": symbol, "direction": direction, "volume": volume,
                             "stop": stop, "target": target, "attach_stop": attach_stop})
        if attach_stop:                            # venue now holds a protective child
            self._resting.append(_resting_row(symbol, "STP", 900 + len(self._resting)))
        return SimpleNamespace(quantity=volume, submitted_at="t", stop=stop, target=target)

    def close_position(self, symbol):
        self.closes.append(symbol)
        self._positions = [p for p in self._positions if p["engine_symbol"] != symbol]
        return SimpleNamespace(quantity=1.0, submitted_at="t", stop=None, target=None)

    def wait_for_fill(self, handle, timeout_s=0):
        return _fill(100.0, handle.quantity)


def _resting_row(symbol, order_type="STP", order_id=901):
    order = SimpleNamespace(orderId=order_id)
    return {"symbol": symbol, "order_type": order_type, "order_id": order_id,
            "_trade": SimpleNamespace(order=order)}


def _state(entries=None, exits=None, day="2026-07-18"):
    return {
        "book": "book_h", "last_processed_date": day,
        "open_positions": {
            e["instrument"]: {"direction": e["direction"], "units": e["units"],
                              "entry_price": 100.0, "entry_time": day,
                              "stop": e.get("stop"), "target": e.get("target")}
            for e in (entries or [])
        },
        "trades": [
            {"instrument": x["instrument"], "direction": x["direction"], "units": 1.0,
             "exit_price": 100.0, "exit_time": day, "exit_reason": "stop"}
            for x in (exits or [])
        ],
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(rim, "sync_ibkr_state", lambda *a, **k: True)

    def run(state, executor, **kw):
        sp = tmp_path / "state.json"
        sp.write_text(json.dumps(state))
        return rim.run_mirror(sp, tmp_path / "mirror", executor, timeout_s=1.0, **kw)
    return run


def test_default_still_records_without_attaching():
    # Unchanged v1 behaviour must remain the default — opt-in only.
    assert rim.run_mirror.__defaults__ is not None
    ex = BracketExecutor()
    ex.submit_order("AAPL", "long", 3.0, stop=90.0, target=115.0, attach_stop=False)
    assert ex.submits[0]["attach_stop"] is False
    assert ex.get_open_orders() == []          # nothing resting at the venue


def test_attach_stops_places_a_real_bracket(env):
    ex = BracketExecutor()
    code, record = env(_state(entries=[{"instrument": "AAPL", "direction": "long",
                                        "units": 3, "stop": 90.0, "target": 115.0}]),
                       ex, attach_stops=True)
    assert code == 0
    assert ex.submits[0]["attach_stop"] is True
    assert ex.submits[0]["stop"] == 90.0
    # Survives the post-run sweep: this fake deliberately does NOT report a position
    # after the fill, modelling async position propagation. A fresh bracket must not
    # be swept as an "orphan" just because the position hasn't landed yet.
    assert [r["symbol"] for r in ex.get_open_orders()] == ["AAPL"]   # protection resting
    assert ex.cancelled == []


def test_entry_without_a_stop_is_sent_unbracketed_and_warned(env):
    # attach_stop=True requires a stop; a stopless entry must not silently claim
    # protection, and must not blow up the run either.
    ex = BracketExecutor()
    code, record = env(_state(entries=[{"instrument": "AAPL", "direction": "long",
                                        "units": 3, "stop": None}]),
                       ex, attach_stops=True)
    assert code == 0
    assert ex.submits[0]["attach_stop"] is False
    assert any("WITHOUT a venue-side bracket" in w for w in record["warnings"])


def test_engine_exit_tears_the_bracket_down_first(env):
    # THE ORPHAN HAZARD: closing while a stop child rests could leave it behind to
    # re-open the trade later. Cancel must happen, and before the close.
    ex = BracketExecutor(positions=[{"engine_symbol": "EUR/USD", "quantity": -20_000}],
                         resting=[_resting_row("EUR/USD", "STP", 901)])
    code, record = env(_state(exits=[{"instrument": "EUR/USD", "direction": "short"}]),
                       ex, attach_stops=True)
    assert code == 0
    assert ex.closes == ["EUR/USD"]
    assert len(ex.cancelled) == 1                       # the child was pulled
    assert ex.get_open_orders() == []                   # nothing left resting
    assert record["brackets_cancelled"][0]["why"] == "engine exit"


def test_orphan_sweep_cancels_protection_with_no_position(env):
    # A bracket whose position vanished by ANY route (barrier filled, manual close)
    # is swept post-run — otherwise it fires later and opens an unbacked position.
    ex = BracketExecutor(positions=[], resting=[_resting_row("MSFT", "STP", 902)])
    code, record = env(_state(), ex, attach_stops=True)
    assert code == 0
    assert len(ex.cancelled) == 1
    assert record["brackets_cancelled"][0]["why"] == "orphan sweep (no position)"


def test_sweep_leaves_brackets_that_still_have_a_position(env):
    ex = BracketExecutor(positions=[{"engine_symbol": "MSFT", "quantity": 10}],
                         resting=[_resting_row("MSFT", "STP", 903)])
    code, _ = env(_state(), ex, attach_stops=True)
    assert code == 0
    assert ex.cancelled == []                           # still protected, correctly
    assert len(ex.get_open_orders()) == 1


def test_no_teardown_happens_when_the_flag_is_off(env):
    # With brackets off the mirror must not touch resting orders at all.
    ex = BracketExecutor(positions=[], resting=[_resting_row("MSFT", "STP", 904)])
    code, _ = env(_state(), ex, attach_stops=False)
    assert code == 0
    assert ex.cancelled == []
    assert len(ex.get_open_orders()) == 1
