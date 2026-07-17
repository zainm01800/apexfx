"""MT4-shaped live facade over :class:`IBKRExecutor` (provider ``ibkr``).

The live daemon (``scripts/run_live_paper_trading.py``) was built around the
MT4 file bridge: it speaks MT4 *lots*, MT4 *tickets*, an ack dict, and reads a
positions list shaped like ``mt4_positions.json``. This module presents exactly
that surface so the daemon's order lifecycle — entries with SL/TP, the fills
handshake, ticket-scoped TradeManager exits (partials, breakeven, chandelier
trail, time stops) — works unchanged against the IBKR paper account, and
``provider=mt4`` keeps working exactly as today (this module is only imported
for ``provider=ibkr``).

Virtual tickets over a netting venue
------------------------------------
MT4 (hedging mode) gives every order its own ticket; sibling timeframes on the
same pair are independent positions with independent stops. IBKR FX NETS: one
net position per contract. The bridge therefore keeps a ledger of *virtual
tickets* — one per filled entry, keyed by the entry order's IBKR permId
(orderId fallback, the same id the daemon stores in
``setup_features.mt4_ticket``) — each with its own OCA bracket (GTC STP + GTC
LMT children, the venue-side equivalent of MT4's order-level SL/TP). Per-ticket
TMS commands map onto net orders:

* ``partial_close(ticket, lots)`` — MKT reduction, then the ticket's bracket
  is amended down to its remaining size (a bracket can never over-close).
* ``modify_sl(ticket, new_sl)`` — amends that ticket's STP child only.
* ``close(ticket=...)`` — cancels the ticket's bracket and MKT-closes its
  remaining units.

Netting safety: a new entry in the OPPOSITE direction of an open virtual
ticket on the same pair is REFUSED (a venue-side stop sized for one ticket
would otherwise close into a flip on the netted book). The daemon's reversal
logic closes before flipping, so this guard should never fire — it exists so
a race can never produce it silently.

Ledger persistence + restart reconciliation
-------------------------------------------
The ledger is written atomically to ``data_store/ibkr_live_book.json`` after
every mutation, so a daemon restart does not strand the book. On
:meth:`connect` the bridge reconciles against the gateway: tickets whose stop
child is still working are re-bound to the live order (amendments keep
working), a ticket whose stop died while its position lives gets a FRESH
protective bracket (repair, logged loudly), and tickets whose net position
vanished are marked closed-by-external-fill (resolution then falls back to
the price path — see below).

Trade resolution on IBKR (documented choice)
--------------------------------------------
SL/TP exits execute VENUE-SIDE (the OCA children). The daemon resolves the
Supabase setup two ways, exactly one new on IBKR:

1. Price-based (unchanged, as today): ``check_open_trades`` resolves
   ``sl_hit``/``tp_hit`` when OHLC bars touch the ORIGINAL barriers.
2. Fill-based (new, ``resolve_closed_ibkr_setups``): the bridge watches its
   child-order fills (the executions the venue reports on the orders it
   placed) and the daemon resolves from the actual exit fill, classifying
   against the original SL/TP with the same tolerance the MT4 resolver uses.
   This is what catches trailed-stop/breakeven exits, which never retouch
   the original barriers. The executions-API report (reqExecutions) was the
   alternative; child-order fills were chosen as the simpler correct source
   because the bridge already holds those Trade objects — no extra
   subscription, and they survive restarts via the open-orders rebind +
   net-position reconciliation above.

Lots vs base units
------------------
Daemon volumes are MT4 lots: FX 1 lot = 100,000 base units (all pairs incl.
JPY), crypto/equity 1 lot = 1 unit. Conversion happens HERE, at the boundary
(:func:`lots_to_units`), so the daemon never changes its sizing math.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apex_quant.execution.ibkr_executor import (
    IBKRExecutor,
    OrderHandle,
    contract_spec,
    round_quantity,
)

logger = logging.getLogger(__name__)

#: Default ledger location (engine/data_store/ibkr_live_book.json).
_DEFAULT_LEDGER_PATH = Path(__file__).resolve().parents[2] / "data_store" / "ibkr_live_book.json"

#: Broker symbol decorations the daemon's _normalise_symbol may append.
_SUFFIXES = ("-g", ".m", ".ecn", ".pro", ".raw")


# ---------------------------------------------------------------------------
#  Symbol / size conversion (the only lots<->units boundary in the system)
# ---------------------------------------------------------------------------
def engine_symbol(symbol: str) -> str:
    """Map an MT4-style ticker back to the engine symbol.

    ``"EURUSD-g"`` / ``"EURUSD.m"`` / ``"EURUSD"`` -> ``"EUR/USD"``;
    already-engine symbols (``"EUR/USD"``, ``"AAPL"``) pass through.
    """
    s = str(symbol).strip().upper()
    if "/" in s:
        return s
    for suf in _SUFFIXES:
        if s.endswith(suf.upper()):
            s = s[: -len(suf)]
            break
    s = s.replace(".", "").replace("-", "")
    if len(s) == 6 and s.isalpha():
        return f"{s[:3]}/{s[3:]}"
    return s


def mt4_symbol(engine_sym: str) -> str:
    """Engine symbol -> compact venue ticket shape (``"EUR/USD"`` -> ``"EURUSD"``)."""
    return str(engine_sym).replace("/", "").upper()


def lots_to_units(symbol: str, lots: float) -> float:
    """Daemon lots -> venue base units. FX: 1 lot = 100k base (all pairs,
    JPY included); crypto/equity: 1 lot = 1 unit (matches units_to_lots)."""
    spec = contract_spec(engine_symbol(symbol))
    mult = 100000.0 if spec["asset_class"] == "forex" else 1.0
    return round_quantity(spec["asset_class"], float(lots) * mult)


def units_to_lots(symbol: str, units: float) -> float:
    """Venue base units -> daemon lots (inverse of :func:`lots_to_units`)."""
    spec = contract_spec(engine_symbol(symbol))
    mult = 100000.0 if spec["asset_class"] == "forex" else 1.0
    return round(float(units) / mult, 2)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
#  Bridge
# ---------------------------------------------------------------------------
class IBKRLiveBridge:
    """MT4Executor-compatible facade over IBKRExecutor for the live daemon.

    Parameters
    ----------
    executor :
        Optional pre-built :class:`IBKRExecutor` (dependency injection for
        offline tests — the production path constructs one here).
    ledger_path :
        Virtual-ticket ledger JSON path (restart recovery). Default
        ``engine/data_store/ibkr_live_book.json``.
    fill_timeout_s :
        Fill budget for the MKT orders TMS issues (partials / closes).
        Entries use the daemon's ack budget instead (``mt4_ack_timeout_s``).
    """

    def __init__(
        self,
        executor: IBKRExecutor | None = None,
        ledger_path: str | Path | None = None,
        fill_timeout_s: float = 20.0,
    ) -> None:
        self._executor = executor or IBKRExecutor()
        self._ledger_path = Path(ledger_path) if ledger_path else _DEFAULT_LEDGER_PATH
        self._fill_timeout_s = float(fill_timeout_s)
        self._lock = threading.RLock()
        # int ticket -> virtual position dict (see _bind_entry for the shape)
        self._book: dict[int, dict] = {}
        # int ticket -> live OrderHandle (entry handle with child trades)
        self._handles: dict[int, OrderHandle] = {}
        # id(handle) -> pending-entry context (until the ack binds a ticket)
        self._pending: dict[int, dict] = {}
        self._peak_equity: float | None = None
        self._load_ledger()
        logger.info(
            "IBKRLiveBridge initialised — ledger %s (%d known tickets)",
            self._ledger_path, len(self._book),
        )

    # ------------------------------------------------------------------
    #  Connection / reconciliation
    # ------------------------------------------------------------------
    def connect(self) -> str:
        """Connect the executor (account allowlist enforced there), then
        reconcile the persisted virtual-ticket ledger against gateway truth."""
        acct = self._executor.connect()
        try:
            self._reconcile()
        except Exception:  # noqa: BLE001 - never let reconciliation block trading
            logger.exception("IBKR ledger reconciliation failed — continuing with in-memory book")
        return acct

    @property
    def executor(self) -> IBKRExecutor:
        return self._executor

    @property
    def is_connected(self) -> bool:
        return self._executor.is_connected

    def disconnect(self) -> None:
        self._executor.disconnect()

    def _reconcile(self) -> None:
        """Re-bind the ledger to gateway reality after a (re)start.

        1. Open tickets whose net position vanished -> closed externally
           (exit price unknown; resolution falls back to the price path).
        2. Stop children still working -> re-bound for future amendments.
        3. Stop child gone but position alive -> fresh protective bracket.
        4. Net position smaller than the tickets' total -> proportional trim.
        """
        with self._lock:
            open_vps = [vp for vp in self._book.values() if vp["status"] == "open"]
            if not open_vps:
                return
            try:
                open_orders = self._executor.get_open_orders()
            except Exception:  # noqa: BLE001
                logger.exception("reconcile: get_open_orders failed — keeping ledger as-is")
                return
            by_order_id = {o["order_id"]: o for o in open_orders}
            try:
                net = {p["engine_symbol"]: p["quantity"] for p in self._executor.get_positions()}
            except Exception:  # noqa: BLE001
                logger.exception("reconcile: get_positions failed — keeping ledger as-is")
                return

            changed = False
            for vp in open_vps:
                sym = vp["symbol"]
                venue_net = float(net.get(sym, 0.0))
                if venue_net == 0.0:
                    vp["status"] = "closed"
                    vp["exit_reason"] = "external"
                    vp["exit_price"] = None
                    self._append_fill(vp, "external", 0.0, None)
                    logger.warning(
                        "reconcile: ticket %s (%s) flat on venue — marked closed externally",
                        vp["ticket"], sym,
                    )
                    changed = True
                    continue
                stop_row = by_order_id.get(vp.get("stop_order_id"))
                lmt_row = by_order_id.get(vp.get("lmt_order_id"))
                self._rebind_handle(vp, stop_row, lmt_row)
                if stop_row is None:
                    logger.warning(
                        "reconcile: ticket %s (%s) has a live position but NO working stop "
                        "— placing a fresh protective bracket", vp["ticket"], sym,
                    )
                    self._repair_bracket(vp)
                    changed = True

            # Net-position trim: venue holds less than the tickets claim.
            by_sym: dict[str, list[dict]] = {}
            for vp in self._book.values():
                if vp["status"] == "open":
                    by_sym.setdefault(vp["symbol"], []).append(vp)
            for sym, vps in by_sym.items():
                total = sum(vp["remaining_units"] for vp in vps)
                venue_abs = abs(float(net.get(sym, 0.0)))
                if total > 0 and 0.0 < venue_abs < total - 1e-9:
                    factor = venue_abs / total
                    logger.warning(
                        "reconcile: %s venue net %.0f < tickets total %.0f — trimming %.1f%%",
                        sym, venue_abs, total, (1 - factor) * 100,
                    )
                    for vp in vps:
                        spec = contract_spec(sym)
                        vp["remaining_units"] = round_quantity(
                            spec["asset_class"], vp["remaining_units"] * factor)
                        self._amend_children_qty(vp)
                    changed = True
            if changed:
                self._persist()

    def _rebind_handle(self, vp: dict, stop_row: dict | None, lmt_row: dict | None) -> None:
        """Rebuild an OrderHandle around the venue's live child orders so
        modify_stop keeps working across daemon restarts (order ids are stable
        for the life of a GTC order)."""
        if stop_row is None and lmt_row is None:
            return
        spec = contract_spec(vp["symbol"])
        handle = OrderHandle(
            symbol=vp["symbol"], direction=vp["direction"],
            action="BUY" if vp["direction"] == "long" else "SELL",
            quantity=vp["remaining_units"], asset_class=spec["asset_class"],
            stop=vp["stop"], target=vp["target"],
            contract=(stop_row or lmt_row)["_trade"].contract,
            trade=None,
            stop_trade=stop_row["_trade"] if stop_row else None,
            target_trade=lmt_row["_trade"] if lmt_row else None,
        )
        self._handles[int(vp["ticket"])] = handle

    def _repair_bracket(self, vp: dict) -> None:
        """Re-place a missing protective STP (+LMT) for a live virtual ticket."""
        spec = contract_spec(vp["symbol"])
        child_action = "SELL" if vp["direction"] == "long" else "BUY"
        from apex_quant.execution.ibkr_executor import _load_ib_async, make_contract
        iba = _load_ib_async()
        contract = make_contract(spec)
        self._executor._qualify(contract)
        qty = round_quantity(spec["asset_class"], vp["remaining_units"])
        oca = vp.get("oca_group") or f"apex-repair-{int(vp['ticket'])}"
        stop_order = iba.StopOrder(child_action, qty, float(vp["stop"]))
        stop_order.tif = "GTC"
        stop_order.ocaGroup = oca
        stop_order.ocaType = 1
        lmt_trade = None
        if vp.get("target"):
            stop_order.transmit = False
            lmt_order = iba.LimitOrder(child_action, qty, float(vp["target"]))
            lmt_order.tif = "GTC"
            lmt_order.ocaGroup = oca
            lmt_order.ocaType = 1
            lmt_order.transmit = True
            lmt_trade = self._executor._ib.placeOrder(contract, lmt_order)
        else:
            stop_order.transmit = True
        stop_trade = self._executor._ib.placeOrder(contract, stop_order)
        vp["stop_order_id"] = getattr(stop_trade.order, "orderId", None)
        vp["lmt_order_id"] = getattr(lmt_trade.order, "orderId", None) if lmt_trade else None
        vp["oca_group"] = oca
        handle = self._handles.get(int(vp["ticket"]))
        if handle is not None:
            handle.stop_trade = stop_trade
            handle.target_trade = lmt_trade
            handle.contract = contract
        else:
            self._handles[int(vp["ticket"])] = OrderHandle(
                symbol=vp["symbol"], direction=vp["direction"],
                action="BUY" if vp["direction"] == "long" else "SELL",
                quantity=qty, asset_class=spec["asset_class"],
                stop=vp["stop"], target=vp["target"], contract=contract,
                trade=None, stop_trade=stop_trade, target_trade=lmt_trade,
            )

    # ------------------------------------------------------------------
    #  Ledger persistence (atomic; survives daemon restarts)
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        try:
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "updated_at": _utcnow_iso(),
                "peak_equity": self._peak_equity,
                "tickets": {str(t): vp for t, vp in sorted(self._book.items())},
            }
            tmp = self._ledger_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
            tmp.replace(self._ledger_path)
        except Exception:  # noqa: BLE001 - persistence must never kill trading
            logger.exception("IBKR ledger persist failed (%s)", self._ledger_path)

    def _load_ledger(self) -> None:
        try:
            raw = json.loads(self._ledger_path.read_text(encoding="utf-8"))
            self._peak_equity = raw.get("peak_equity")
            for tk, vp in (raw.get("tickets") or {}).items():
                vp["ticket"] = int(vp.get("ticket") or tk)
                self._book[int(tk)] = vp
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001 - corrupt ledger: start empty, loud
            logger.exception("IBKR ledger unreadable (%s) — starting with an empty book",
                             self._ledger_path)

    # ------------------------------------------------------------------
    #  Order entry (MT4Executor-shaped)
    # ------------------------------------------------------------------
    def submit_order(
        self,
        symbol: str,
        cmd: str,
        volume: float | None = None,
        sl: float = 0.0,
        tp: float = 0.0,
        tp1: float = 0.0,
        tp1_volume: float = 0.0,
        be_buffer: float = 0.0003,
        trail_atr_mult: float = 2.0,
        trail_lookback: int = 22,
        ticket: int | None = None,
    ) -> OrderHandle | None:
        """MT4Executor-compatible entry / ticket-scoped close.

        ``cmd="buy"/"sell"``: converts lots to base units and places the entry
        as a MKT DAY parent with an attached GTC STP child (+ GTC LMT child
        when *tp* > 0) in one OCA group. ``tp1``/``be_buffer``/``trail_*`` are
        accepted for interface parity and deliberately ignored — on IBKR the
        partials and stop moves are driven by the daemon's Python TMS (the
        EA's native 200 ms TMS does not exist here).

        ``cmd="close"`` with *ticket*: cancels that ticket's bracket and
        MKT-closes its remaining units.
        """
        cmd_l = str(cmd).lower()
        sym = engine_symbol(symbol)
        if cmd_l in ("buy", "sell"):
            direction = "long" if cmd_l == "buy" else "short"
            units = lots_to_units(sym, volume if volume else 0.10)
            if units <= 0:
                raise ValueError(f"{volume} lots -> {units} units on {sym} — refusing entry")
            with self._lock:
                # Netting guard: never open opposite a live ticket on this pair.
                for vp in self._book.values():
                    if (vp["status"] == "open" and vp["symbol"] == sym
                            and vp["direction"] != direction):
                        raise RuntimeError(
                            f"IBKR netting guard: refusing {direction} entry on {sym} while "
                            f"{vp['direction']} ticket {vp['ticket']} is open — close it first"
                        )
                handle = self._executor.submit_order(
                    sym, direction, volume=units,
                    stop=float(sl) if sl else None,
                    target=float(tp) if tp else None,
                    attach_stop=True,
                )
                self._pending[id(handle)] = {
                    "symbol": sym, "direction": direction, "units": handle.quantity,
                    "stop": float(sl) if sl else None,
                    "target": float(tp) if tp else None,
                }
                return handle
        if cmd_l == "close":
            return self._close_ticket(sym, ticket, reason="close")
        raise ValueError(f"unknown cmd {cmd!r} — expected buy/sell/close")

    def wait_for_ack(
        self,
        handle: OrderHandle | None = None,
        timeout_s: float | None = None,
        poll_interval_s: float = 0.25,
    ) -> dict | None:
        """Fills handshake: same ack shape as ``MT4Executor.wait_for_ack``.

        On a fill the virtual ticket (= IBKR permId, orderId fallback) is
        bound in the ledger; the daemon stores that id in
        ``setup_features.mt4_ticket`` exactly like an MT4 ticket. On a venue
        rejection (e.g. FX desk closed) ``ok`` is False and NOTHING is bound —
        the setup stays pending and the IBKR resolver expires it after the
        usual grace, so a rejected order is recorded, never retried forever.
        """
        handle = handle or self._executor._last_handle
        if handle is None:
            return None
        ack = self._executor.wait_for_ack(
            handle=handle, timeout_s=timeout_s, poll_interval_s=poll_interval_s)
        if ack and ack.get("ok") and ack.get("ticket") is not None:
            with self._lock:
                self._bind_entry(handle, int(ack["ticket"]), ack)
        elif ack:
            logger.warning(
                "IBKR entry NOT filled for %s (status=%s raw=%s) — recorded, no ticket bound",
                handle.symbol, ack.get("status"), ack.get("raw_status"),
            )
        return ack

    def _bind_entry(self, handle: OrderHandle, ticket: int, ack: dict) -> None:
        ctx = self._pending.pop(id(handle), None) or {
            "symbol": handle.symbol, "direction": handle.direction,
            "units": handle.quantity, "stop": handle.stop, "target": handle.target,
        }
        vp = {
            "ticket": int(ticket),
            "symbol": ctx["symbol"],
            "direction": ctx["direction"],
            "entry_price": ack.get("fill_price"),
            "initial_units": float(ack.get("filled_qty") or ctx["units"]),
            "remaining_units": float(ack.get("filled_qty") or ctx["units"]),
            "stop": ctx["stop"],
            "target": ctx["target"],
            "parent_order_id": handle.order_id,
            "stop_order_id": getattr(getattr(handle.stop_trade, "order", None), "orderId", None),
            "lmt_order_id": getattr(getattr(handle.target_trade, "order", None), "orderId", None),
            "oca_group": getattr(getattr(handle.stop_trade, "order", None), "ocaGroup", ""),
            "opened_at": _utcnow_iso(),
            "status": "open",
            "exit_price": None,
            "exit_reason": None,
            "fills": [{
                "kind": "entry", "side": handle.action,
                "qty": float(ack.get("filled_qty") or ctx["units"]),
                "price": ack.get("fill_price"), "ts": _utcnow_iso(),
            }],
        }
        self._book[int(ticket)] = vp
        self._handles[int(ticket)] = handle
        self._persist()
        logger.info(
            "IBKR ticket %s bound: %s %s %.0f units @ %s (stop %s, target %s)",
            ticket, vp["direction"], vp["symbol"], vp["remaining_units"],
            vp["entry_price"], vp["stop"], vp["target"],
        )

    # ------------------------------------------------------------------
    #  TMS commands (MT4Executor-shaped)
    # ------------------------------------------------------------------
    def partial_close(self, symbol: str, ticket: int, volume: float) -> None:
        """Close *volume* lots of virtual *ticket* and shrink its bracket.

        Raises on non-fill so the daemon does NOT set its partial-done flags —
        the action is retried on a later cycle, exactly like an MT4 signal
        write failure.
        """
        sym = engine_symbol(symbol)
        with self._lock:
            vp = self._require_open_ticket(ticket)
            units = lots_to_units(sym, volume)
            spec = contract_spec(sym)
            units = round_quantity(spec["asset_class"], min(units, vp["remaining_units"]))
            if units <= 0:
                raise ValueError(f"{volume} lots -> nothing to close on ticket {ticket}")
            action = "sell" if vp["direction"] == "long" else "buy"
            handle = self._executor.submit_order(sym, action, volume=units)
            res = self._executor.wait_for_fill(handle, timeout_s=self._fill_timeout_s)
            if not res.filled:
                raise RuntimeError(
                    f"IBKR partial close of ticket {ticket} did not fill (status={res.status})"
                )
            vp["remaining_units"] = round_quantity(
                spec["asset_class"], vp["remaining_units"] - res.filled_quantity)
            self._append_fill(vp, "partial", res.filled_quantity, res.avg_fill_price)
            self._amend_children_qty(vp)
            self._persist()
            logger.info(
                "IBKR partial close: ticket %s %s -%.0f units @ %s (remaining %.0f)",
                ticket, sym, res.filled_quantity, res.avg_fill_price, vp["remaining_units"],
            )

    def modify_sl(self, symbol: str, ticket: int, new_sl: float) -> None:
        """Amend virtual *ticket*'s STP child to *new_sl* (and persist it)."""
        with self._lock:
            vp = self._require_open_ticket(ticket)
            handle = self._handles.get(int(ticket))
            if handle is None or handle.stop_trade is None:
                # Lost the live order reference (e.g. mid-session restart
                # without reconcile) — rebind from open orders, else repair.
                self._rebind_from_venue(vp)
                handle = self._handles.get(int(ticket))
            if handle is None or handle.stop_trade is None:
                raise RuntimeError(f"no working stop order found for ticket {ticket}")
            self._executor.modify_stop(handle, float(new_sl))
            vp["stop"] = float(new_sl)
            vp["stop_order_id"] = getattr(handle.stop_trade.order, "orderId", vp.get("stop_order_id"))
            self._persist()
            logger.info("IBKR modify_sl: ticket %s %s -> %.5f", ticket, vp["symbol"], float(new_sl))

    def close_position(self, symbol: str, ticket: int | None = None) -> OrderHandle | None:
        """MT4Executor-compatible close: ticket-scoped when given, else the
        whole net position in *symbol* (legacy symbol-scoped semantics)."""
        sym = engine_symbol(symbol)
        if ticket:
            return self._close_ticket(sym, ticket, reason="close")
        with self._lock:
            vps = [vp for vp in self._book.values()
                   if vp["status"] == "open" and vp["symbol"] == sym]
            handle = None
            for vp in vps:
                handle = self._close_vp(vp, reason="close")
            if not vps:
                handle = self._executor.close_position(sym)
            return handle

    # ------------------------------------------------------------------
    #  Position introspection for the daemon (mt4_positions.json shape)
    # ------------------------------------------------------------------
    def get_positions_mt4(self) -> list[dict]:
        """Open virtual tickets shaped like rows of ``mt4_positions.json``:
        ``ticket``, ``symbol`` (compact, e.g. ``EURUSD``), ``volume`` (lots),
        ``open_price``, ``sl``, ``tp``, ``cmd`` (0=buy/1=sell), ``profit``,
        ``magic`` (88888 = engine-owned)."""
        with self._lock:
            self.refresh()
            pnl_by_sym = self._venue_unrealized_by_symbol()
            totals: dict[str, float] = {}
            for vp in self._book.values():
                if vp["status"] == "open":
                    totals[vp["symbol"]] = totals.get(vp["symbol"], 0.0) + vp["remaining_units"]
            out = []
            for vp in sorted(self._book.values(), key=lambda v: v["ticket"]):
                if vp["status"] != "open" or vp["remaining_units"] <= 0:
                    continue
                sym_pnl = pnl_by_sym.get(vp["symbol"], 0.0)
                total = totals.get(vp["symbol"]) or 0.0
                share = (vp["remaining_units"] / total) if total > 0 else 0.0
                out.append({
                    "ticket": int(vp["ticket"]),
                    "symbol": mt4_symbol(vp["symbol"]),
                    "volume": units_to_lots(vp["symbol"], vp["remaining_units"]),
                    "open_price": vp.get("entry_price") or 0.0,
                    "sl": vp.get("stop") or 0.0,
                    "tp": vp.get("target") or 0.0,
                    "cmd": 0 if vp["direction"] == "long" else 1,
                    "profit": round(sym_pnl * share, 2),
                    "magic": 88888,
                })
            return out

    def get_open_tickets(self) -> set[int]:
        """Tickets whose virtual position is still open (resolver gate)."""
        with self._lock:
            self.refresh()
            return {int(t) for t, vp in self._book.items()
                    if vp["status"] == "open" and vp["remaining_units"] > 0}

    def ticket_closed_info(self, ticket: int) -> dict | None:
        """``{exit_price, exit_reason}`` when virtual *ticket* is fully closed,
        else None (open or unknown). ``exit_price`` is None for external closes
        (the bridge did not see the fill — resolution falls back to price)."""
        with self._lock:
            self.refresh()
            vp = self._book.get(int(ticket)) if ticket else None
            if vp is None or vp["status"] != "closed":
                return None
            return {"exit_price": vp.get("exit_price"), "exit_reason": vp.get("exit_reason")}

    def refresh(self) -> None:
        """Fold venue-side child fills into the ledger (lock held by callers).

        A filled STP child marks its ticket closed at the stop fill; a filled
        LMT child at the target fill. The OCA sibling is cancelled venue-side;
        we cancel defensively in case it is still listed working.
        """
        for vp in self._book.values():
            if vp["status"] != "open":
                continue
            handle = self._handles.get(int(vp["ticket"]))
            if handle is None:
                continue
            for trade, reason in ((getattr(handle, "stop_trade", None), "stop"),
                                  (getattr(handle, "target_trade", None), "target")):
                if trade is None:
                    continue
                try:
                    done = trade.isDone()
                    status = str(getattr(trade.orderStatus, "status", "") or "")
                except Exception:  # noqa: BLE001
                    continue
                if done and status == "Filled":
                    px = getattr(trade.orderStatus, "avgFillPrice", None)
                    qty = float(getattr(trade.orderStatus, "filled", 0.0) or 0.0)
                    self._mark_closed(vp, px, reason, qty)
                    self._cancel_sibling(vp, keep=trade)
                    break

    # ------------------------------------------------------------------
    #  Account state + Supabase sync (website IBKR Terminal)
    # ------------------------------------------------------------------
    def get_account_state(self) -> tuple[float, float, float]:
        """(equity, balance, peak_equity) from the venue; the peak is the
        running max persisted in the ledger (replaces mt4_account.json's
        start_balance for the drawdown breaker)."""
        acct = self._executor.get_account()
        eq = float(acct.get("NetLiquidation") or 0.0)
        bal = float(acct.get("TotalCashValue") or 0.0)
        peak = max(x for x in (self._peak_equity or 0.0, eq, bal) if x is not None)
        if peak != self._peak_equity:
            self._peak_equity = peak
            self._persist()
        return eq, bal, peak

    def sync_to_supabase(self) -> bool:
        """Push account + positions + ledger fills to the ``apex_ibkr_*``
        tables via :mod:`apex_quant.storage.ibkr_store`. Best-effort: NEVER
        raises — a Supabase outage must not kill the live loop."""
        from apex_quant.storage import ibkr_store

        ok = True
        now = _utcnow_iso()
        try:
            acct = self._executor.get_account()
        except Exception as e:  # noqa: BLE001
            logger.info("ibkr sync: account fetch failed: %s", e)
            acct = {}
        try:
            pnl = self._executor.get_pnl() or {}
        except Exception:  # noqa: BLE001
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
            ok = ibkr_store.sync_account(account_row) and ok
        else:
            ok = False

        try:
            portfolio = self._executor.get_portfolio()
            pos_rows = [{
                "instrument": p["engine_symbol"],
                "direction": "long" if p["quantity"] > 0 else "short",
                "units": abs(p["quantity"]),
                "avg_price": p.get("avg_cost"),
                "market_value": p.get("market_value"),
                "unrealized_pnl": p.get("unrealized_pnl"),
                "asset_class": "stocks" if p.get("asset_class") == "equity" else str(p.get("asset_class", "")),
                "updated_at": now,
            } for p in portfolio if p.get("quantity")]
            ok = ibkr_store.sync_positions(pos_rows) and ok
        except Exception as e:  # noqa: BLE001
            logger.info("ibkr sync: positions failed: %s", e)
            ok = False

        with self._lock:
            self.refresh()
            trade_rows = []
            for vp in self._book.values():
                spec = contract_spec(vp["symbol"])
                for i, f in enumerate(vp.get("fills") or []):
                    if f.get("price") is None:
                        continue  # external close — no venue fill to report
                    trade_rows.append({
                        "exec_id": f"{int(vp['ticket'])}.{i}",
                        "instrument": vp["symbol"],
                        "asset_class": "stocks" if spec["asset_class"] == "equity" else spec["asset_class"],
                        "side": f.get("side") or ("SELL" if vp["direction"] == "long" else "BUY"),
                        "qty": f.get("qty"),
                        "price": f.get("price"),
                        "commission": None,
                        "exec_time": f.get("ts") or vp.get("opened_at") or now,
                    })
        try:
            ok = ibkr_store.sync_trades(trade_rows) and ok
        except Exception as e:  # noqa: BLE001
            logger.info("ibkr sync: trades failed: %s", e)
            ok = False
        return ok

    # ------------------------------------------------------------------
    #  Internals
    # ------------------------------------------------------------------
    def _require_open_ticket(self, ticket: int) -> dict:
        vp = self._book.get(int(ticket)) if ticket else None
        if vp is None:
            raise KeyError(f"unknown IBKR virtual ticket {ticket}")
        if vp["status"] != "open" or vp["remaining_units"] <= 0:
            raise RuntimeError(f"IBKR virtual ticket {ticket} is not open")
        return vp

    def _close_ticket(self, sym: str, ticket: int | None, reason: str) -> OrderHandle | None:
        with self._lock:
            vp = self._book.get(int(ticket)) if ticket else None
            if vp is None:
                # Unknown/legacy ticket: flatten the symbol's net position.
                return self._executor.close_position(sym)
            return self._close_vp(vp, reason=reason)

    def _close_vp(self, vp: dict, reason: str) -> OrderHandle | None:
        """Cancel the ticket's bracket, then MKT-close its remaining units.

        Failure-tolerant: a non-fill is logged and the ticket stays open (the
        next TMS cycle retries), mirroring the MT4 path where a close signal
        that the EA could not execute also leaves the position open.
        """
        if vp["status"] != "open" or vp["remaining_units"] <= 0:
            return None
        self._cancel_children(vp)
        action = "sell" if vp["direction"] == "long" else "buy"
        handle = self._executor.submit_order(vp["symbol"], action, volume=vp["remaining_units"])
        res = self._executor.wait_for_fill(handle, timeout_s=self._fill_timeout_s)
        if res.filled:
            spec = contract_spec(vp["symbol"])
            remaining = round_quantity(
                spec["asset_class"], vp["remaining_units"] - res.filled_quantity)
            self._append_fill(vp, reason, res.filled_quantity, res.avg_fill_price)
            if remaining <= 0:
                vp["remaining_units"] = 0.0
                # qty=0: _mark_closed must not decrement again or re-record
                # the fill (already appended above).
                self._mark_closed(vp, res.avg_fill_price, reason, qty=0.0)
            else:
                vp["remaining_units"] = remaining
                self._persist()
        else:
            logger.warning(
                "IBKR close of ticket %s did not fill (status=%s) — ticket stays open",
                vp["ticket"], res.status,
            )
        return handle

    def _mark_closed(self, vp: dict, price: float | None, reason: str, qty: float = 0.0) -> None:
        vp["status"] = "closed"
        vp["exit_price"] = price
        vp["exit_reason"] = reason
        if qty > 0:
            spec = contract_spec(vp["symbol"])
            vp["remaining_units"] = round_quantity(
                spec["asset_class"], max(0.0, vp["remaining_units"] - qty))
        if reason in ("stop", "target"):
            side = "SELL" if vp["direction"] == "long" else "BUY"
            self._append_fill(vp, reason, qty or vp["initial_units"], price, side=side)
        self._persist()
        logger.info(
            "IBKR ticket %s (%s %s) CLOSED — reason=%s exit=%s",
            vp["ticket"], vp["direction"], vp["symbol"], reason, price,
        )

    def _append_fill(self, vp: dict, kind: str, qty: float, price: float | None,
                     side: str | None = None) -> None:
        vp.setdefault("fills", []).append({
            "kind": kind,
            "side": side or ("SELL" if vp["direction"] == "long" else "BUY"),
            "qty": float(qty), "price": price, "ts": _utcnow_iso(),
        })

    def _cancel_children(self, vp: dict) -> None:
        handle = self._handles.get(int(vp["ticket"]))
        if handle is None:
            return
        for trade in (getattr(handle, "stop_trade", None), getattr(handle, "target_trade", None)):
            try:
                if trade is not None and not trade.isDone():
                    self._executor.cancel_order(trade.order)
            except Exception:  # noqa: BLE001 - cancel is best-effort
                logger.debug("cancel child failed for ticket %s", vp["ticket"], exc_info=True)

    def _cancel_sibling(self, vp: dict, keep) -> None:
        handle = self._handles.get(int(vp["ticket"]))
        if handle is None:
            return
        for trade in (getattr(handle, "stop_trade", None), getattr(handle, "target_trade", None)):
            if trade is None or trade is keep:
                continue
            try:
                if not trade.isDone():
                    self._executor.cancel_order(trade.order)
            except Exception:  # noqa: BLE001
                logger.debug("OCA sibling cancel failed", exc_info=True)

    def _amend_children_qty(self, vp: dict) -> None:
        """After a partial, re-size the ticket's bracket to the remainder."""
        handle = self._handles.get(int(vp["ticket"]))
        if handle is None or handle.stop_trade is None or vp["remaining_units"] <= 0:
            return
        try:
            self._executor.modify_stop(
                handle, vp["stop"], quantity=vp["remaining_units"])
            vp["stop_order_id"] = getattr(handle.stop_trade.order, "orderId", vp.get("stop_order_id"))
        except Exception:  # noqa: BLE001 - logged; next TMS cycle retries
            logger.exception("bracket resize failed for ticket %s", vp["ticket"])

    def _rebind_from_venue(self, vp: dict) -> None:
        """Find the ticket's working children among the venue's open orders
        (by stored order id) and rebuild the live handle."""
        try:
            open_orders = self._executor.get_open_orders()
        except Exception:  # noqa: BLE001
            return
        by_id = {o["order_id"]: o for o in open_orders}
        self._rebind_handle(vp, by_id.get(vp.get("stop_order_id")),
                            by_id.get(vp.get("lmt_order_id")))

    def _venue_unrealized_by_symbol(self) -> dict[str, float]:
        try:
            return {p["engine_symbol"]: float(p.get("unrealized_pnl") or 0.0)
                    for p in self._executor.get_portfolio()}
        except Exception:  # noqa: BLE001 - profit display is best-effort
            return {}

    def __repr__(self) -> str:
        open_n = sum(1 for vp in self._book.values() if vp["status"] == "open")
        return (f"{self.__class__.__name__}(executor={self._executor!r}, "
                f"open_tickets={open_n}, ledger={self._ledger_path})")
