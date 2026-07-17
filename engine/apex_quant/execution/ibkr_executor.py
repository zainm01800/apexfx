"""IBKR paper execution bridge (ib_async / TWS or IB Gateway).

A real-venue executor in the style of :class:`MT4Executor`, used by
``scripts/run_ibkr_mirror.py`` to mirror the frozen multi-asset trend book
(``book_d_multiasset_252``) onto an IBKR PAPER account so real-vs-model fill
divergence can be measured. It is strictly additive: the engine-simulated
paper portfolio remains the experiment of record (see
``engine/data_store/pre_registration_paper_trend_2026-07-17.md``, change log #2).

Account allowlist (fail-closed)
-------------------------------
The executor refuses to trade unless the account actually reported by the
gateway after connect is exactly the allowlisted account — the IBKR *paper*
account ``DUQ278370`` by default, overridable via the ``IBKR_ACCOUNT``
environment variable (the default keeps the hardcoded value pointed at paper;
the env var exists so a *different paper account* can be used in testing).
The check runs on every :meth:`connect`; a mismatch raises
:class:`IBKRAccountError` and no order can ever be sent.

Connection parameters (all env-overridable)
-------------------------------------------
* ``IBKR_HOST``       — default ``127.0.0.1``
* ``IBKR_PORT``       — default ``4002`` (IB Gateway paper; TWS paper is 7497)
* ``IBKR_CLIENT_ID``  — default ``17``
* ``IBKR_ACCOUNT``    — default ``DUQ278370``

Contract mapping (engine symbol -> IB contract)
-----------------------------------------------
* equities/ETFs  ``"AAPL"``    -> ``STK  AAPL  SMART     USD``
* crypto         ``"BTC/USD"`` -> ``CRYPTO BTC  PAXOS     USD``
* forex          ``"EUR/USD"`` -> ``CASH  EUR.USD IDEALPRO``

Forex vs crypto disambiguation: a ``BASE/QUOTE`` pair is forex iff BOTH legs
are G10 major currencies (:data:`FX_MAJOR_BASES`), crypto otherwise. This
classifies the whole frozen universe without hardcoding it.

Order style: MARKET, DAY tif — and why that is honest for a paper mirror
------------------------------------------------------------------------
The book's own execution convention is "decisions on bar t's close fill at bar
t+1's open" — a marketable order at the session open, with no limit price and
no non-fill risk. A MKT order is the closest real-venue analogue: it takes
the venue's available price when the mirror runs, which is exactly the
quantity the mirror exists to measure against the model's assumed fill. A
limit order would invent a price the model never had and introduce non-fill
risk that would silently desynchronise the mirror's positions from the book's.
DAY tif bounds every order's life to the session: an equity order placed
before the open queues for that session's open (the same event the model
fills at); crypto/FX trade around the clock and fill immediately. Anything
still unfilled at session end dies with the day and is reported in the mirror
record — the mirror never leaves stray GTC orders working unattended.

Stops and targets: recorded, NOT attached
-----------------------------------------
The book's exits are *managed* daily by the engine's TradeManager (50% off at
1R + breakeven, 25% at 1.5R + lock, ATR-chandelier trail, squeeze tighten,
time stop). A static venue-side bracket cannot follow those daily amendments,
and partial exits would desynchronise sizes — attaching one would REDUCE
mirror fidelity, not increase it. So for v1 stops/targets are accepted by
:meth:`submit_order`, stored on the order handle and written to the mirror
record for reference, and exits are mirrored as plain MKT closes when the
engine exits. No fake-complex GTC brackets.

Known venue constraints (recorded, not hidden)
----------------------------------------------
* IBKR crypto (Paxos) is LONG-ONLY: short crypto orders are rejected by the
  venue. The mirror script skips them explicitly and records the divergence.
* IDEALPRO sizes are whole units of base currency; sizes below ~25k USD-equiv
  trade as odd lots. Fine on paper; noted because spreads differ.
* Fractional US shares are sent rounded to 2dp (IBKR supports fractional
  shares for most liquid US names; a non-eligible symbol errors loudly and is
  recorded as a failed order, never silently resized).
* The engine book is GBP-denominated paper; the IBKR paper account is USD.
  Units are passed through 1:1 (1 share / 1 coin / 1 base unit) with no FX
  conversion — the mirror measures FILL divergence, not currency effects.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Connection defaults (env-overridable; see module docstring)
# ---------------------------------------------------------------------------
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4002            # IB Gateway paper (user's setup, 2026-07-17). TWS paper: 7497.
DEFAULT_CLIENT_ID = 17
#: The IBKR PAPER account this mirror is allowed to trade. Hard allowlist:
#: connect() raises unless the gateway reports exactly this account.
DEFAULT_ACCOUNT = "DUQ278370"

#: G10 major currency legs. A BASE/QUOTE engine symbol whose legs are BOTH in
#: this set maps to CASH/IDEALPRO (forex); any other BASE/QUOTE maps to
#: CRYPTO/PAXOS. Classifies the frozen universe (7 FX majors + 11 crypto)
#: without hardcoding the universe itself.
FX_MAJOR_BASES = frozenset({
    "EUR", "GBP", "USD", "JPY", "CHF", "AUD", "CAD", "NZD",
})

AssetClass = Literal["equity", "crypto", "forex"]
DirectionLike = Literal["long", "short", "buy", "sell"]

#: Decimal places for order quantities per asset class (venue conventions).
_QTY_DECIMALS: dict[str, int] = {"equity": 2, "crypto": 6, "forex": 0}

#: accountSummary tags surfaced by get_account().
_ACCOUNT_TAGS = (
    "NetLiquidation", "TotalCashValue", "AvailableFunds",
    "BuyingPower", "GrossPositionValue",
)


class IBKRAccountError(RuntimeError):
    """Raised when the connected IBKR account is not the allowlisted paper
    account. Fail-closed: no order path exists before this check passes."""


# ---------------------------------------------------------------------------
#  Lazy ib_async import — this module must import cleanly without ib_async
#  installed (it is only needed to actually talk to a gateway).
# ---------------------------------------------------------------------------
_ib_async = None


def _load_ib_async():
    global _ib_async
    if _ib_async is None:
        try:
            import ib_async as iba
        except ImportError as e:  # pragma: no cover - depends on env
            raise ImportError(
                "ib_async is required for IBKR execution "
                "(pip install ib_async into engine/.venv-mac). "
                "Offline tests inject a fake client instead."
            ) from e
        _ib_async = iba
    return _ib_async


# ---------------------------------------------------------------------------
#  Contract mapping (pure data; ib_async objects built only at the edge)
# ---------------------------------------------------------------------------
def contract_spec(symbol: str) -> dict:
    """Map an engine symbol to a plain contract spec dict.

    ``"AAPL"``    -> STK AAPL SMART USD          (asset_class "equity")
    ``"BTC/USD"`` -> CRYPTO BTC PAXOS USD        (asset_class "crypto")
    ``"EUR/USD"`` -> CASH EUR.USD IDEALPRO       (asset_class "forex")
    """
    if "/" in symbol:
        base, quote = symbol.split("/", 1)
        base, quote = base.strip().upper(), quote.strip().upper()
        if base in FX_MAJOR_BASES and quote in FX_MAJOR_BASES:
            return {
                "asset_class": "forex", "secType": "CASH",
                "symbol": base, "currency": quote, "exchange": "IDEALPRO",
            }
        return {
            "asset_class": "crypto", "secType": "CRYPTO",
            "symbol": base, "currency": quote, "exchange": "PAXOS",
        }
    return {
        "asset_class": "equity", "secType": "STK",
        "symbol": symbol.strip().upper(), "currency": "USD", "exchange": "SMART",
    }


def make_contract(spec: dict):
    """Build the ib_async Contract for a :func:`contract_spec` dict."""
    iba = _load_ib_async()
    sec = spec["secType"]
    if sec == "STK":
        return iba.Stock(spec["symbol"], spec["exchange"], spec["currency"])
    if sec == "CRYPTO":
        return iba.Crypto(spec["symbol"], spec["exchange"], spec["currency"])
    if sec == "CASH":
        return iba.Forex(spec["symbol"] + spec["currency"])
    raise ValueError(f"unknown secType in spec: {spec!r}")


def engine_symbol_for_contract(contract) -> str:
    """Reverse mapping: ib_async contract -> engine symbol (for positions)."""
    sec = getattr(contract, "secType", "")
    sym = getattr(contract, "symbol", "")
    ccy = getattr(contract, "currency", "")
    if sec == "STK":
        return sym
    if sec in ("CRYPTO", "CASH"):
        return f"{sym}/{ccy}"
    return f"{sec}:{sym}/{ccy}"


def round_quantity(asset_class: AssetClass, units: float) -> float:
    """Round engine units to the venue's quantity convention."""
    return round(float(units), _QTY_DECIMALS[asset_class])


# ---------------------------------------------------------------------------
#  Order handle / fill result
# ---------------------------------------------------------------------------
@dataclass
class OrderHandle:
    """A submitted order plus the mirror context (stop/target are recorded,
    not attached — see module docstring)."""

    symbol: str
    direction: str              # "long" | "short" (engine side)
    action: str                 # "BUY" | "SELL" (venue side)
    quantity: float
    asset_class: str
    stop: float | None = None
    target: float | None = None
    contract: Any = None
    trade: Any = None           # ib_async Trade
    submitted_at: str = ""

    @property
    def order_id(self) -> int | None:
        order = getattr(self.trade, "order", None)
        return getattr(order, "orderId", None) if order is not None else None


@dataclass
class FillResult:
    """Outcome of waiting on an order (cf. MT4Executor.wait_for_ack)."""

    status: str                 # "filled" | "cancelled" | "timeout_cancelled" | raw status
    avg_fill_price: float | None = None
    filled_quantity: float = 0.0
    commission: float | None = None
    commission_currency: str | None = None
    order_id: int | None = None
    perm_id: int | None = None
    raw_status: str = ""

    @property
    def filled(self) -> bool:
        return self.avg_fill_price is not None and self.filled_quantity > 0


# ---------------------------------------------------------------------------
#  Executor
# ---------------------------------------------------------------------------
class IBKRExecutor:
    """Submit orders to IBKR paper (TWS / IB Gateway) via ib_async.

    Parameters
    ----------
    host, port, client_id, account :
        Connection parameters. Each defaults to its ``IBKR_*`` environment
        variable, then to the module defaults (paper TWS on 7497, account
        ``DUQ278370``).
    connect_timeout_s :
        Budget for the initial gateway handshake.
    ib :
        Optional pre-built client (dependency injection for offline tests —
        the production path constructs ``ib_async.IB()`` inside
        :meth:`connect`). Must quack like ``ib_async.IB``.

    Raises
    ------
    IBKRAccountError
        On :meth:`connect` if the gateway's account list does not contain
        exactly the allowlisted account.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
        account: str | None = None,
        connect_timeout_s: float = 10.0,
        ib: Any = None,
    ) -> None:
        self._host = host or os.environ.get("IBKR_HOST") or DEFAULT_HOST
        self._port = int(port or os.environ.get("IBKR_PORT") or DEFAULT_PORT)
        self._client_id = int(
            client_id or os.environ.get("IBKR_CLIENT_ID") or DEFAULT_CLIENT_ID
        )
        self._account = account or os.environ.get("IBKR_ACCOUNT") or DEFAULT_ACCOUNT
        if not self._account:
            raise IBKRAccountError("empty IBKR account is not tradeable")
        self._connect_timeout_s = float(connect_timeout_s)
        self._ib = ib
        self._connected = False
        logger.info(
            "IBKRExecutor initialised — %s:%s clientId=%s account=%s (allowlisted)",
            self._host, self._port, self._client_id, self._account,
        )

    # -- connection ---------------------------------------------------------
    @property
    def account(self) -> str:
        return self._account

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> str:
        """Connect to the gateway and enforce the account allowlist.

        Returns the verified account id. Raises :class:`IBKRAccountError`
        (and disconnects) if the gateway does not report exactly the
        allowlisted account — a live account must fail loudly here, never
        receive a "paper" order.
        """
        if self._connected:
            return self._account
        if self._ib is None:
            self._ib = _load_ib_async().IB()
        self._ib.connect(
            self._host, self._port,
            clientId=self._client_id, timeout=self._connect_timeout_s,
        )
        accounts = list(self._ib.managedAccounts() or [])
        if accounts != [self._account]:
            logger.error(
                "IBKR account allowlist VIOLATION: gateway reports %s, "
                "allowlist permits only %r. Disconnecting.", accounts, self._account,
            )
            try:
                self._ib.disconnect()
            finally:
                self._connected = False
            raise IBKRAccountError(
                f"refusing to trade: connected accounts {accounts} != "
                f"allowlisted paper account {self._account!r} "
                f"(override via IBKR_ACCOUNT only for another paper account)"
            )
        self._connected = True
        logger.info("IBKR connected — account %s verified against allowlist", self._account)
        return self._account

    def disconnect(self) -> None:
        if self._ib is not None and self._connected:
            try:
                self._ib.disconnect()
            except Exception:  # noqa: BLE001 - teardown must not raise
                logger.exception("error during IBKR disconnect")
        self._connected = False

    def __enter__(self) -> "IBKRExecutor":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def _require_connection(self) -> None:
        if not self._connected or self._ib is None:
            raise RuntimeError("IBKRExecutor is not connected — call connect() first")

    # -- introspection --------------------------------------------------------
    def get_positions(self) -> list[dict]:
        """Current positions on the allowlisted account.

        Returns a list of dicts: ``engine_symbol``, ``asset_class``,
        ``quantity`` (signed: >0 long, <0 short), ``avg_cost``.
        """
        self._require_connection()
        out = []
        for p in self._ib.positions(self._account):
            spec = contract_spec(engine_symbol_for_contract(p.contract)) \
                if getattr(p.contract, "secType", "") in ("STK", "CRYPTO", "CASH") else {}
            out.append({
                "engine_symbol": engine_symbol_for_contract(p.contract),
                "asset_class": spec.get("asset_class", "unknown"),
                "quantity": float(p.position),
                "avg_cost": float(p.avgCost),
            })
        return out

    def get_account(self) -> dict:
        """Key accountSummary tags for the allowlisted account."""
        self._require_connection()
        summary: dict[str, Any] = {"account": self._account}
        for av in self._ib.accountSummary(self._account):
            if av.tag in _ACCOUNT_TAGS and av.tag not in summary:
                try:
                    summary[av.tag] = float(av.value)
                except (TypeError, ValueError):
                    summary[av.tag] = av.value
        return summary

    # -- orders ---------------------------------------------------------------
    def _qualify(self, contract) -> None:
        qualified = self._ib.qualifyContracts(contract)
        if not qualified or not getattr(contract, "conId", 0):
            raise RuntimeError(f"could not qualify contract {contract!r} — venue reject")

    def submit_order(
        self,
        symbol: str,
        direction: DirectionLike,
        volume: float | None = None,
        notional: float | None = None,
        stop: float | None = None,
        target: float | None = None,
    ) -> OrderHandle:
        """Submit a MARKET DAY order mirroring one engine decision.

        Parameters
        ----------
        symbol :
            Engine symbol (``"AAPL"``, ``"BTC/USD"``, ``"EUR/USD"``).
        direction :
            ``"long"``/``"short"`` (engine convention) or ``"buy"``/``"sell"``.
        volume :
            Engine units (shares / coins / base-currency units). Rounded to
            the venue convention (2dp equities, 6dp crypto, whole FX units).
        notional :
            Accepted for interface parity with the other executors. v1 does
            NOT convert notional to quantity (that needs a market-data
            subscription); pass ``volume``. Raises ValueError if only
            notional is given.
        stop, target :
            Recorded on the handle and in the mirror record; deliberately NOT
            attached as bracket children (see module docstring).

        Returns
        -------
        OrderHandle
            The submitted order. Confirm the fill with :meth:`wait_for_fill` —
            callers MUST NOT treat a submitted order as filled without it
            (same rule as the MT4 bridge's fills handshake, audit L10).
        """
        self._require_connection()
        d = str(direction).lower()
        if d not in ("long", "short", "buy", "sell"):
            raise ValueError(f"bad direction {direction!r} — expected long/short/buy/sell")
        action = "BUY" if d in ("long", "buy") else "SELL"
        engine_dir = "long" if d in ("long", "buy") else "short"
        if not volume or float(volume) <= 0:
            if notional:
                raise ValueError(
                    "notional-only sizing is out of scope for v1 (needs market "
                    "data); pass volume= (engine units) instead"
                )
            raise ValueError("submit_order requires volume > 0")

        spec = contract_spec(symbol)
        qty = round_quantity(spec["asset_class"], float(volume))
        if qty <= 0:
            raise ValueError(
                f"volume {volume} rounds to zero for {spec['asset_class']} — refusing"
            )

        iba = _load_ib_async()
        contract = make_contract(spec)
        self._qualify(contract)
        order = iba.MarketOrder(action, qty)
        order.tif = "DAY"
        trade = self._ib.placeOrder(contract, order)
        handle = OrderHandle(
            symbol=symbol, direction=engine_dir, action=action, quantity=qty,
            asset_class=spec["asset_class"], stop=stop, target=target,
            contract=contract, trade=trade,
            submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        logger.info(
            "IBKR order: %s %s %s %s (stop=%s target=%s, recorded not attached)",
            action, qty, symbol, spec["secType"], stop, target,
        )
        return handle

    def close_position(self, symbol: str) -> OrderHandle | None:
        """Market-close the ENTIRE IBKR position in *symbol* (DAY tif).

        Sizes to the position the gateway actually reports (reconciliation-safe:
        a close can never overshoot the real holding). Returns ``None`` when
        the account holds nothing in *symbol*.
        """
        self._require_connection()
        held = None
        for p in self.get_positions():
            if p["engine_symbol"] == symbol and p["quantity"] != 0:
                held = p
                break
        if held is None:
            logger.info("IBKR close: no position in %s — nothing to do", symbol)
            return None
        action: DirectionLike = "sell" if held["quantity"] > 0 else "buy"
        return self.submit_order(
            symbol=symbol, direction=action, volume=abs(held["quantity"]),
        )

    def wait_for_fill(
        self,
        handle: OrderHandle,
        timeout_s: float = 120.0,
        poll_interval_s: float = 0.25,
    ) -> FillResult:
        """Wait for an order to reach a done state (cf. wait_for_ack).

        On timeout the order is CANCELLED (the mirror never leaves working
        orders unattended) and the result records ``timeout_cancelled`` plus
        any partial fill. Commissions are summed from the fills' commission
        reports (``None`` when the gateway has not reported them yet).
        """
        self._require_connection()
        trade = handle.trade
        deadline = time.monotonic() + max(0.0, timeout_s)
        while not trade.isDone() and time.monotonic() < deadline:
            self._ib.sleep(poll_interval_s)

        raw = str(getattr(trade.orderStatus, "status", "") or "")
        if not trade.isDone():
            logger.warning(
                "IBKR fill TIMEOUT for %s %s after %.1fs (status=%s) — cancelling",
                handle.action, handle.symbol, timeout_s, raw,
            )
            self._ib.cancelOrder(trade.order)
            self._ib.sleep(1.0)
            raw = str(getattr(trade.orderStatus, "status", "") or raw)
            status = "timeout_cancelled"
        elif raw == "Filled":
            status = "filled"
        else:
            status = raw.lower() or "done"

        os_ = trade.orderStatus
        commission = None
        comm_ccy = None
        for fill in getattr(trade, "fills", []) or []:
            rep = getattr(fill, "commissionReport", None)
            c = getattr(rep, "commission", None)
            if c is not None and abs(c) < 1e300:  # ib_async UNSET_DOUBLE guard
                commission = (commission or 0.0) + float(c)
                comm_ccy = getattr(rep, "currency", None) or comm_ccy
        avg = getattr(os_, "avgFillPrice", None)
        filled_qty = float(getattr(os_, "filled", 0.0) or 0.0)
        if avg is not None and avg >= 1e300:
            avg = None
        result = FillResult(
            status=status,
            avg_fill_price=float(avg) if avg else None,
            filled_quantity=filled_qty,
            commission=commission,
            commission_currency=comm_ccy,
            order_id=handle.order_id,
            perm_id=getattr(os_, "permId", None),
            raw_status=raw,
        )
        logger.info("IBKR fill: %s %s -> %s", handle.action, handle.symbol, result)
        return result

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(host={self._host!r}, port={self._port}, "
            f"client_id={self._client_id}, account={self._account!r}, "
            f"connected={self._connected})"
        )
