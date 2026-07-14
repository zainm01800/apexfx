"""Two-way ZMQ execution bridge.

The pure ExecutionProtocol (order lifecycle, idempotency, heartbeat, reconciliation)
is tested in isolation; the ZMQBridge transport is tested end-to-end over real
loopback TCP with a fake MT4 EA (a PULL socket for orders + a PUSH socket for acks).
"""

from __future__ import annotations

import json
import socket as _socket
import time

import pytest

from apex_quant.execution.zmq_bridge import ExecutionProtocol, OrderState


# ===========================================================================
#  Pure protocol (no sockets)
# ===========================================================================
def test_new_order_defaults_and_ids():
    p = ExecutionProtocol(default_volume=0.1)
    o = p.new_order("EURUSD", "buy")
    assert isinstance(o, OrderState)
    assert o.volume == 0.1 and o.status == "sent" and o.ticket is None
    assert o.id and o.payload()["symbol"] == "EURUSD"


def test_new_order_custom_volume_and_rejects_bad_cmd():
    p = ExecutionProtocol()
    assert p.new_order("EURUSD", "sell", volume=0.5).volume == 0.5
    with pytest.raises(ValueError):
        p.new_order("EURUSD", "hodl")  # type: ignore[arg-type]


def test_order_id_is_idempotent():
    p = ExecutionProtocol()
    a = p.new_order("EURUSD", "buy", order_id="fixed1")
    b = p.new_order("EURUSD", "buy", order_id="fixed1")  # resend -> same object
    assert a is b
    assert len(p._orders) == 1


def test_ack_fill_lifecycle():
    p = ExecutionProtocol()
    oid = p.new_order("EURUSD", "buy", order_id="o1").id
    assert p.on_message({"type": "ack", "id": oid})["status"] == "acked"
    ev = p.on_message({"type": "fill", "id": oid, "ticket": 42})
    assert ev["status"] == "filled" and ev["ticket"] == 42
    assert p.get(oid).status == "filled" and p.get(oid).ticket == 42


def test_terminal_state_is_not_regressed():
    p = ExecutionProtocol()
    oid = p.new_order("EURUSD", "buy", order_id="o1").id
    p.on_message({"type": "fill", "id": oid, "ticket": 7})
    # A late/duplicate ack must not knock a filled order back to 'acked'.
    p.on_message({"type": "ack", "id": oid})
    assert p.get(oid).status == "filled"
    # Duplicate fill is harmless and keeps the ticket.
    p.on_message({"type": "fill", "id": oid, "ticket": 7})
    assert p.get(oid).ticket == 7


def test_reject_and_unknown_order():
    p = ExecutionProtocol()
    oid = p.new_order("EURUSD", "buy", order_id="o1").id
    assert p.on_message({"type": "reject", "id": oid})["status"] == "rejected"
    assert p.on_message({"type": "ack", "id": "nope"})["unknown"] is True


def test_pending_and_filled_partitions():
    p = ExecutionProtocol()
    a = p.new_order("EURUSD", "buy", order_id="a").id
    b = p.new_order("GBPUSD", "sell", order_id="b").id
    p.on_message({"type": "fill", "id": a, "ticket": 1})
    assert [o.id for o in p.filled()] == [a]
    assert [o.id for o in p.pending()] == [b]


def test_heartbeat_liveness():
    p = ExecutionProtocol()
    assert p.seconds_since_heartbeat() is None
    assert p.is_alive(30) is False
    now = 1000.0
    p.on_message({"type": "heartbeat", "ts": now})
    assert p.is_alive(30, now=now + 10) is True
    assert p.is_alive(30, now=now + 40) is False  # stale


def test_reconcile_detects_drift():
    p = ExecutionProtocol()
    a = p.new_order("EURUSD", "buy", order_id="a").id
    p.on_message({"type": "fill", "id": a, "ticket": 100})
    assert p.reconcile({100})["in_sync"] is True
    r = p.reconcile({999})           # broker has a trade we don't; we think 100 is open
    assert r["missing_in_broker"] == [100]
    assert r["unknown_in_broker"] == [999]
    assert r["in_sync"] is False


def test_positions_message_updates_broker_view():
    p = ExecutionProtocol()
    a = p.new_order("EURUSD", "buy", order_id="a").id
    p.on_message({"type": "fill", "id": a, "ticket": 100})
    p.on_message({"type": "positions", "tickets": [100, 200]})
    assert p.reconcile()["unknown_in_broker"] == [200]  # 200 is a broker-side surprise


# ===========================================================================
#  Transport over real loopback TCP (fake EA)
# ===========================================================================
def _free_port() -> int:
    s = _socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _poll_until(bridge, predicate, tries=60, delay=0.03):
    for _ in range(tries):
        bridge.poll()
        if predicate():
            return True
        time.sleep(delay)
    return False


def test_zmq_bridge_two_way_loopback():
    zmq = pytest.importorskip("zmq")
    from apex_quant.execution.zmq_bridge import ZMQBridge

    port, ack_port = _free_port(), _free_port()
    bridge = ZMQBridge(host="127.0.0.1", port=port, ack_port=ack_port, default_volume=0.1)

    ctx = zmq.Context()
    ea_pull = ctx.socket(zmq.PULL)          # EA receives orders
    ea_pull.setsockopt(zmq.RCVTIMEO, 3000)
    ea_pull.connect(f"tcp://127.0.0.1:{port}")
    ea_push = ctx.socket(zmq.PUSH)          # EA sends acks/fills/heartbeats
    ea_push.connect(f"tcp://127.0.0.1:{ack_port}")
    time.sleep(0.25)                         # let PUSH/PULL connections establish

    try:
        # 1. order reaches the EA intact, stamped with its id
        oid = bridge.push_order("EURUSD", "buy", volume=0.2, sl=1.09, tp=1.12)
        order = json.loads(ea_pull.recv_string())
        assert order["id"] == oid
        assert (order["symbol"], order["cmd"], order["volume"]) == ("EURUSD", "buy", 0.2)

        # 2. ack + fill flow back and drive the lifecycle to filled(ticket)
        ea_push.send_string(json.dumps({"type": "ack", "id": oid}))
        ea_push.send_string(json.dumps({"type": "fill", "id": oid, "ticket": 5555}))
        assert _poll_until(bridge, lambda: bridge.protocol.get(oid).status == "filled")
        assert bridge.protocol.get(oid).ticket == 5555

        # 3. heartbeat makes the bridge report alive
        ea_push.send_string(json.dumps({"type": "heartbeat", "ts": time.time()}))
        assert _poll_until(bridge, lambda: bridge.is_alive(60))

        # 4. reconciliation against the (now known) fill
        assert bridge.reconcile({5555})["in_sync"] is True
        assert bridge.reconcile({5555, 9999})["unknown_in_broker"] == [9999]
    finally:
        ea_pull.close()
        ea_push.close()
        ctx.term()
        bridge.close()
