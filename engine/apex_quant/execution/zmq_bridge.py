"""ZeroMQ TCP execution bridge — two-way, with acknowledgements.

Replaces file-based MT4 signal polling with a sub-millisecond TCP link. The
original bridge was *fire-and-forget*: a one-way PUSH with no idea whether MT4
received the order, filled it, or fell over. That is how you get silent
double-fills and phantom positions. This version is two-way and idempotent:

  * Engine → EA  (PUSH on ``port``):      orders, each stamped with a unique id.
  * EA → Engine  (PULL on ``ack_port``):  acks, fills (with ticket), rejects,
                                          heartbeats, and position reports.

Order lifecycle:  ``sent -> acked -> filled`` (or ``rejected``). Because every
order carries an id, a resend is a safe no-op for the EA (idempotency), acks are
matched back to their order, and Python can **reconcile** its view of open
positions against what MT4 actually holds.

Design
------
The lifecycle logic lives in :class:`ExecutionProtocol`, a pure state machine with
no I/O — trivially unit-testable. :class:`ZMQBridge` is a thin transport shell that
owns the two sockets and delegates all state to the protocol.

Latency: file polling 50–200 ms; loopback TCP < 1 ms. ``pyzmq`` is optional — if
absent, importing :class:`ZMQBridge` raises ImportError and callers should fall
back to the file-based :class:`~apex_quant.execution.mt4_executor.MT4Executor`.

Message JSON
------------
Order (engine → EA)::   {"id": "ab12…", "symbol": "EURUSD", "cmd": "buy",
                         "volume": 0.10, "sl": 0.0, "tp": 0.0}
Reply (EA → engine)::   {"type": "ack|fill|reject|heartbeat|positions",
                         "id": "ab12…", "ticket": 12345, "tickets": [...], "ts": …}
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from apex_quant.config import get_config

logger = logging.getLogger(__name__)

Cmd = Literal["buy", "sell", "close"]


# ---------------------------------------------------------------------------
#  Order lifecycle state
# ---------------------------------------------------------------------------
@dataclass
class OrderState:
    id: str
    symbol: str
    cmd: str
    volume: float
    sl: float = 0.0
    tp: float = 0.0
    status: str = "sent"          # sent -> acked -> filled | rejected
    ticket: int | None = None     # MT4 ticket, set on fill
    ts: float = field(default_factory=time.time)

    def payload(self) -> dict:
        return {"id": self.id, "symbol": self.symbol, "cmd": self.cmd,
                "volume": self.volume, "sl": self.sl, "tp": self.tp}


# ---------------------------------------------------------------------------
#  Pure protocol / state machine (no sockets — fully unit-testable)
# ---------------------------------------------------------------------------
class ExecutionProtocol:
    """Order-lifecycle state machine for the MT4 bridge. No I/O whatsoever."""

    _TERMINAL = {"filled", "rejected"}

    def __init__(self, default_volume: float = 0.10) -> None:
        self.default_volume = default_volume
        self._orders: dict[str, OrderState] = {}
        self._broker_tickets: set[int] = set()
        self._last_heartbeat: float | None = None

    # -- outbound -------------------------------------------------------------
    def new_order(
        self,
        symbol: str,
        cmd: Cmd,
        volume: float | None = None,
        sl: float = 0.0,
        tp: float = 0.0,
        order_id: str | None = None,
    ) -> OrderState:
        """Create (or, for a known id, return) an order. Idempotent by id: a
        resend never spawns a duplicate logical order."""
        if cmd not in ("buy", "sell", "close"):
            raise ValueError(f"cmd must be buy|sell|close, got {cmd!r}")
        if order_id is not None and order_id in self._orders:
            return self._orders[order_id]
        oid = order_id or uuid.uuid4().hex[:12]
        vol = self.default_volume if not volume else float(volume)
        st = OrderState(id=oid, symbol=symbol, cmd=cmd, volume=vol, sl=float(sl), tp=float(tp))
        self._orders[oid] = st
        return st

    # -- inbound --------------------------------------------------------------
    def on_message(self, raw: str | dict) -> dict:
        """Apply one inbound EA message and return a normalised event dict."""
        msg = json.loads(raw) if isinstance(raw, str) else dict(raw)
        typ = msg.get("type", "ack")

        if typ == "heartbeat":
            self._last_heartbeat = float(msg.get("ts", time.time()))
            return {"type": "heartbeat", "ts": self._last_heartbeat}

        if typ == "positions":
            self._broker_tickets = {int(x) for x in msg.get("tickets", [])}
            return {"type": "positions", "tickets": sorted(self._broker_tickets)}

        oid = msg.get("id")
        st = self._orders.get(oid)
        if st is None:
            return {"type": typ, "id": oid, "unknown": True}

        # Idempotent transitions: never regress a terminal order; ack only from sent.
        if typ == "ack":
            if st.status == "sent":
                st.status = "acked"
        elif typ == "fill":
            if st.status not in self._TERMINAL:
                st.status = "filled"
            if msg.get("ticket") is not None:
                st.ticket = int(msg["ticket"])
        elif typ == "reject":
            if st.status not in self._TERMINAL:
                st.status = "rejected"
        return {"type": typ, "id": oid, "status": st.status, "ticket": st.ticket}

    # -- heartbeat / liveness -------------------------------------------------
    def on_heartbeat(self, ts: float | None = None) -> None:
        self._last_heartbeat = time.time() if ts is None else float(ts)

    def seconds_since_heartbeat(self, now: float | None = None) -> float | None:
        if self._last_heartbeat is None:
            return None
        return (time.time() if now is None else now) - self._last_heartbeat

    def is_alive(self, timeout_s: float, now: float | None = None) -> bool:
        s = self.seconds_since_heartbeat(now)
        return s is not None and s <= timeout_s

    # -- introspection / reconciliation --------------------------------------
    def get(self, order_id: str) -> OrderState | None:
        return self._orders.get(order_id)

    def pending(self) -> list[OrderState]:
        return [o for o in self._orders.values() if o.status in ("sent", "acked")]

    def filled(self) -> list[OrderState]:
        return [o for o in self._orders.values() if o.status == "filled"]

    def reconcile(self, broker_tickets: set[int] | None = None) -> dict:
        """Compare our filled tickets against the broker's actual open tickets.

        ``missing_in_broker``  — we think it's open, MT4 doesn't (we should stop
                                  managing it). ``unknown_in_broker`` — MT4 holds a
                                  ticket we never opened (manual trade / desync)."""
        broker = set(broker_tickets) if broker_tickets is not None else set(self._broker_tickets)
        mine = {o.ticket for o in self.filled() if o.ticket is not None}
        return {
            "missing_in_broker": sorted(mine - broker),
            "unknown_in_broker": sorted(broker - mine),
            "in_sync": mine == broker,
        }


# ---------------------------------------------------------------------------
#  ZeroMQ transport
# ---------------------------------------------------------------------------
class ZMQBridge:
    """Two-way ZeroMQ bridge: PUSH orders, PULL acks/fills/heartbeats.

    All order state lives in :class:`ExecutionProtocol`; this class only moves
    bytes. Bind host defaults to loopback — never expose the order channel
    externally.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        ack_port: int | None = None,
        linger_ms: int | None = None,
        send_timeout_ms: int | None = None,
        recv_timeout_ms: int | None = None,
        default_volume: float | None = None,
    ) -> None:
        try:
            import zmq  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "pyzmq is required for ZMQBridge. Install it with: pip install pyzmq>=25"
            ) from exc

        cfg = get_config()
        zc = cfg.execution.zmq
        self._host = host or zc.host
        self._port = port or zc.port
        self._ack_port = ack_port or zc.ack_port
        self._linger_ms = linger_ms if linger_ms is not None else zc.linger_ms
        self._send_timeout_ms = send_timeout_ms if send_timeout_ms is not None else zc.send_timeout_ms
        self._recv_timeout_ms = recv_timeout_ms if recv_timeout_ms is not None else zc.recv_timeout_ms
        self._heartbeat_timeout_s = zc.heartbeat_timeout_s
        default_volume = default_volume or cfg.execution.mt4.default_volume

        self.protocol = ExecutionProtocol(default_volume=default_volume)
        self._lock = threading.Lock()
        self._zmq = zmq

        self._ctx = zmq.Context.instance()
        # Orders out (PUSH, bind).
        self._push = self._ctx.socket(zmq.PUSH)
        self._push.setsockopt(zmq.LINGER, self._linger_ms)
        self._push.setsockopt(zmq.SNDTIMEO, self._send_timeout_ms)
        self._push.bind(f"tcp://{self._host}:{self._port}")
        # Acks in (PULL, bind).
        self._pull = self._ctx.socket(zmq.PULL)
        self._pull.setsockopt(zmq.LINGER, self._linger_ms)
        self._pull.setsockopt(zmq.RCVTIMEO, self._recv_timeout_ms)
        self._pull.bind(f"tcp://{self._host}:{self._ack_port}")

        logger.info("ZMQBridge push=tcp://%s:%d ack=tcp://%s:%d",
                    self._host, self._port, self._host, self._ack_port)

    # -- outbound -------------------------------------------------------------
    def push_order(
        self,
        symbol: str,
        cmd: Cmd,
        volume: float | None = None,
        sl: float = 0.0,
        tp: float = 0.0,
        order_id: str | None = None,
    ) -> str:
        """Send an order to MT4. Returns the order id (use it to track acks).

        Raises ``RuntimeError`` if the send times out (EA not connected).
        """
        st = self.protocol.new_order(symbol, cmd, volume, sl, tp, order_id=order_id)
        raw = json.dumps(st.payload(), separators=(",", ":"))
        with self._lock:
            try:
                self._push.send_string(raw)
            except self._zmq.Again:
                raise RuntimeError(
                    f"ZMQ send timed out after {self._send_timeout_ms}ms — "
                    "is the MT4 ZMQ bridge EA running?"
                )
        logger.info("ZMQ order %s — %s %s %.2f (SL=%.5f TP=%.5f)",
                    st.id, cmd.upper(), symbol, st.volume, st.sl, st.tp)
        return st.id

    def submit_order(self, symbol: str, cmd: Cmd, volume: float | None = None,
                     sl: float = 0.0, tp: float = 0.0) -> str:
        """Standard executor interface. Returns the order id."""
        return self.push_order(symbol, cmd, volume, sl, tp)

    def request_positions(self) -> None:
        """Ask the EA to report its open tickets (reply arrives via :meth:`poll`)."""
        with self._lock:
            try:
                self._push.send_string(json.dumps({"cmd": "query_positions"}, separators=(",", ":")))
            except self._zmq.Again:
                raise RuntimeError("ZMQ send timed out requesting positions — EA not connected?")

    # -- inbound --------------------------------------------------------------
    def poll(self, max_messages: int = 100) -> list[dict]:
        """Drain pending EA messages (acks/fills/heartbeats/positions) into the
        protocol and return the normalised events. Non-blocking beyond the
        socket's recv timeout."""
        events: list[dict] = []
        for _ in range(max_messages):
            try:
                raw = self._pull.recv_string(flags=self._zmq.NOBLOCK)
            except self._zmq.Again:
                break
            try:
                events.append(self.protocol.on_message(raw))
            except (ValueError, TypeError):
                logger.warning("ZMQBridge: dropped malformed EA message: %r", raw)
        return events

    # -- convenience passthroughs --------------------------------------------
    def reconcile(self, broker_tickets: set[int] | None = None) -> dict:
        return self.protocol.reconcile(broker_tickets)

    def is_alive(self, timeout_s: float | None = None) -> bool:
        return self.protocol.is_alive(self._heartbeat_timeout_s if timeout_s is None else timeout_s)

    # -- lifecycle ------------------------------------------------------------
    def close(self) -> None:
        try:
            self._push.close(linger=self._linger_ms)
            self._pull.close(linger=self._linger_ms)
            logger.info("ZMQBridge closed.")
        except Exception:
            logger.exception("Error closing ZMQBridge")

    def __enter__(self) -> "ZMQBridge":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def __repr__(self) -> str:
        return (f"ZMQBridge(push={self._host}:{self._port}, ack={self._host}:{self._ack_port}, "
                f"orders={len(self.protocol._orders)})")
