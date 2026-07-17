"""MT4 file-based execution bridge.

Writes JSON order signals to the MetaTrader 4 shared common folder where the
companion ``apex_mt4_bridge.mq4`` Expert Advisor polls and executes them.

Signal protocol (EA v1.10)
--------------------------
Every signal is written to its own unique ``signal_<id>.json`` file (one
client order id per signal) and the EA batch-processes all pending
``signal_*.json`` files each poll — the old single-slot ``mt4_signals.json``
was last-write-wins and silently dropped orders written inside the same EA
poll window (audit L2). After executing, the EA writes a fill receipt to
``ack_<id>.json`` containing the client order id, the MT4 ticket and the
fill price; :meth:`MT4Executor.wait_for_ack` polls for it.

Payloads::

    {"id": "...", "symbol": "EURUSD", "cmd": "buy",           "volume": 0.10, "sl": 1.08000, "tp": 1.09500}
    {"id": "...", "symbol": "EURUSD", "cmd": "sell",          "volume": 0.10, "sl": 1.09000, "tp": 1.07500}
    {"id": "...", "symbol": "EURUSD", "cmd": "close",         "volume": 0.10, "ticket": 12345}

Ticket-scoped TMS commands (audit L3/L4 — when ``ticket`` is present the EA
operates ONLY on that ticket; without it, ``close`` keeps the legacy
symbol-scoped semantics for pre-handshake trades)::

    {"id": "...", "symbol": "EURUSD", "cmd": "partial_close", "ticket": 12345, "volume": 0.05}
    {"id": "...", "symbol": "EURUSD", "cmd": "modify_sl",     "ticket": 12345, "new_sl": 1.08200}

Thread safety
-------------
File writes use an atomic write pattern (write to a ``.tmp`` sibling, then
``os.rename()``) so the MQ4 timer callback never reads a partially-written file.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from apex_quant.config import get_config

logger = logging.getLogger(__name__)

#: Fallback path when neither env var nor config provides one.
#:
#: The precedence is:
#: 1. ``MT4_COMMON_DIR`` environment variable.
#: 2. ``config.execution.mt4.common_dir`` from the YAML config.
#: 3. This default (macOS Wine path).
_DEFAULT_MT4_COMMON_DIR = (
    "/Applications/MetaTrader 4.app/Contents/SharedSupport/"
    "wine/drive_c/Program Files (x86)/MetaTrader 4/MQL4/Files"
)

#: Legacy single-slot filename the EA (< v1.10) polls. No longer written by
#: this executor — kept for reference/back-compat with old EA versions.
SIGNAL_FILENAME = "mt4_signals.json"

#: v1.10 protocol: one unique file per signal / per ack.
SIGNAL_GLOB = "signal_*.json"
ACK_PREFIX = "ack_"


# ---------------------------------------------------------------------------
#  Path resolution
# ---------------------------------------------------------------------------
def _resolve_common_dir() -> Path:
    """Resolve the MT4 shared common directory by priority:

    1. ``MT4_COMMON_DIR`` environment variable.
    2. ``config.execution.mt4.common_dir`` from the YAML config.
    3. A hard-coded sensible default for macOS Wine.
    """
    env_path = os.environ.get("MT4_COMMON_DIR")
    if env_path:
        logger.info("Using MT4_COMMON_DIR from env: %s", env_path)
        return Path(env_path).resolve()

    cfg = get_config()
    if cfg.execution.mt4.common_dir:
        logger.info("Using MT4 common_dir from config: %s", cfg.execution.mt4.common_dir)
        return Path(cfg.execution.mt4.common_dir).resolve()

    logger.info("Falling back to default MT4 common dir: %s", _DEFAULT_MT4_COMMON_DIR)
    return Path(_DEFAULT_MT4_COMMON_DIR).resolve()


def resolve_mt4_common_dir() -> Path:
    """Public resolver for the MT4 shared common directory (audit L11).

    THE single resolution path — order writes (this executor) and engine
    reads (``mt4_positions.json`` / ``mt4_account.json`` in the live script)
    must both go through here so the ``MT4_COMMON_DIR`` env override cannot
    desync writes from reads.
    """
    return _resolve_common_dir()


# ---------------------------------------------------------------------------
#  Executor
# ---------------------------------------------------------------------------
class MT4Executor:
    """Write order signals to the MT4 bridge directory with atomic, thread-safe I/O.

    Parameters
    ----------
    common_dir : str | Path | None
        Override the MT4 common directory. If ``None`` (the default), the
        directory is resolved via :func:`_resolve_common_dir`.
    default_volume : float
        Volume (lot size) used when ``submit_order`` receives ``volume=None``
        or ``volume=0.0``.

    Raises
    ------
    FileNotFoundError
        If the resolved common directory does not exist. Fail-closed
        (audit L10): a wrong ``common_dir`` must be a loud startup error,
        never a silently ``mkdir``-ed black hole that swallows orders.
    """

    def __init__(
        self,
        common_dir: str | Path | None = None,
        default_volume: float | None = None,
    ) -> None:
        self._lock = threading.Lock()

        # Resolve target directory.
        base = Path(common_dir).resolve() if common_dir else _resolve_common_dir()
        self._signal_dir = base

        # Fail closed when the directory is missing — do NOT create it.
        if not self._signal_dir.is_dir():
            raise FileNotFoundError(
                f"MT4 common_dir does not exist: {self._signal_dir}. "
                "Refusing to create it (audit L10): a wrong path must fail "
                "loudly, not become an order black hole. Fix "
                "execution.mt4.common_dir or MT4_COMMON_DIR."
            )

        self._last_signal_id: str | None = None

        # Default volume fallback.
        if default_volume is not None and default_volume > 0:
            self._default_volume = default_volume
        else:
            cfg = get_config()
            self._default_volume = cfg.execution.mt4.default_volume

        logger.info(
            "MT4Executor initialised — signal dir: %s, default volume: %.2f",
            self._signal_dir,
            self._default_volume,
        )

    # -- private helpers ----------------------------------------------------

    def _write_signal(self, payload: dict) -> Path:
        """Atomically write *payload* as JSON to a unique signal file.

        Each signal gets a client order id (``id`` field, uuid4 hex) and its
        own ``signal_<id>.json`` file so the EA can batch-process every
        pending signal without last-write-wins losses (audit L2).

        Uses a ``.tmp`` → ``rename`` pattern so the MQ4 timer never reads a
        partially-written file.

        Returns
        -------
        Path
            The absolute path of the written signal file.

        Raises
        ------
        OSError
            If the file cannot be written.
        """
        signal_id = payload.setdefault("id", uuid.uuid4().hex)
        signal_path = self._signal_dir / f"signal_{signal_id}.json"
        raw = json.dumps(payload, separators=(",", ":"))
        tmp_path = signal_path.with_suffix(".tmp")
        with self._lock:
            try:
                tmp_path.write_text(raw, encoding="utf-8")
                tmp_path.rename(signal_path)
            except OSError:
                logger.exception("Failed to write MT4 signal to %s", signal_path)
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                raise
            self._last_signal_id = signal_id
        logger.debug("MT4 signal written: %s → %s", payload, signal_path)
        return signal_path

    # -- public API ---------------------------------------------------------

    def submit_order(
        self,
        symbol: str,
        cmd: Literal["buy", "sell", "close"],
        volume: float | None = None,
        sl: float = 0.0,
        tp: float = 0.0,
        tp1: float = 0.0,
        tp1_volume: float = 0.0,
        be_buffer: float = 0.0003,
        trail_atr_mult: float = 2.0,
        trail_lookback: int = 22,
        ticket: int | None = None,
    ) -> Path:
        """Write a standard entry or full-close order signal.

        For entry orders (``buy`` / ``sell``) you can optionally include TMS
        parameters that the EA will use to natively manage the position at
        200 ms resolution without any Python latency:

        Parameters
        ----------
        symbol :
            Instrument ticker (e.g. ``"EURUSD"``).
        cmd :
            ``"buy"``, ``"sell"``, or ``"close"``.
        volume :
            Lot size.  Falls back to the configured ``default_volume`` when
            ``None`` or ``0``.
        sl :
            Stop-loss price (``0.0`` = none).
        tp :
            Full take-profit price (``0.0`` = none).
        tp1 :
            First partial TP price.  When price reaches this level the EA
            closes *tp1_volume* lots and sets the SL to breakeven.
            ``0.0`` disables the native partial close.
        tp1_volume :
            Lots to close at *tp1* (e.g. half the position size).
        be_buffer :
            Breakeven SL buffer added to the entry price after TP1 fires
            (in price units, e.g. ``0.0003`` ≈ 3 pips).
        trail_atr_mult :
            ATR multiplier for the Chandelier trailing stop (default ``2.0``).
        trail_lookback :
            Swing high/low lookback bars for the Chandelier exit (default ``22``).
        ticket :
            MT4 order ticket for ``cmd="close"`` (audit L3). When set, the EA
            closes ONLY that ticket; when omitted the close keeps the legacy
            symbol-scoped semantics (all engine positions on the pair) for
            pre-handshake trades.

        Returns
        -------
        Path
            The absolute path that was written.
        """
        effective_volume = self._default_volume if not volume else float(volume)
        payload: dict = {
            "symbol": symbol,
            "cmd": cmd,
            "volume": effective_volume,
            "sl": float(sl),
            "tp": float(tp),
        }
        if ticket:
            payload["ticket"] = int(ticket)
        # Only attach TMS fields for entry orders to keep close signals clean
        if cmd in ("buy", "sell"):
            payload["tp1"]            = round(float(tp1), 5)
            payload["tp1_volume"]     = round(float(tp1_volume), 2)
            payload["be_buffer"]      = round(float(be_buffer), 5)
            payload["trail_atr_mult"] = round(float(trail_atr_mult), 2)
            payload["trail_lookback"] = int(trail_lookback)
        logger.info(
            "MT4 signal: %s %s %.2f lots (SL=%.5f TP=%.5f TP1=%.5f ticket=%s)",
            cmd.upper(), symbol, effective_volume, float(sl), float(tp), float(tp1),
            ticket if ticket else "-",
        )
        return self._write_signal(payload)

    def close_position(self, symbol: str, ticket: int | None = None) -> Path:
        """Write a full-close signal for *symbol*.

        With *ticket* the EA closes exactly that ticket (audit L3); without
        it the legacy symbol-scoped close (all engine positions on the pair)
        applies — kept only for trades opened before the fills handshake.
        """
        return self.submit_order(symbol=symbol, cmd="close", volume=0.1, ticket=ticket)

    def partial_close(self, symbol: str, ticket: int, volume: float) -> Path:
        """Write a *partial close* TMS command for a specific MT4 ticket.

        The EA will close exactly *volume* lots on the specified ticket,
        leaving the remainder of the position open.

        Parameters
        ----------
        symbol :
            Instrument ticker — used for logging only (EA routes by ticket).
        ticket :
            MT4 order ticket number (from the fill ack or
            ``mt4_positions.json``).
        volume :
            Number of lots to close (must be ≤ the open lot size).

        Returns
        -------
        Path
            The absolute path that was written.
        """
        payload = {
            "symbol": symbol,
            "cmd": "partial_close",
            "ticket": int(ticket),
            "volume": round(float(volume), 2),
        }
        logger.info(
            "MT4 TMS: partial_close ticket=#%d %.2f lots (%s)",
            ticket, volume, symbol,
        )
        return self._write_signal(payload)

    def modify_sl(
        self,
        symbol: str,
        ticket: int,
        new_sl: float,
    ) -> Path:
        """Write a *stop-loss modification* TMS command for a specific MT4 ticket.

        The EA will call ``OrderModify()`` on the ticket, moving the SL to
        *new_sl*.  The EA validates that the new SL does not immediately
        trigger before applying the modification.

        Parameters
        ----------
        symbol :
            Instrument ticker — used for logging only.
        ticket :
            MT4 order ticket number.
        new_sl :
            New stop-loss price in broker quote units.

        Returns
        -------
        Path
            The absolute path that was written.
        """
        payload = {
            "symbol": symbol,
            "cmd": "modify_sl",
            "ticket": int(ticket),
            "new_sl": round(float(new_sl), 5),
        }
        logger.info(
            "MT4 TMS: modify_sl ticket=#%d new_sl=%.5f (%s)",
            ticket, new_sl, symbol,
        )
        return self._write_signal(payload)

    def wait_for_ack(
        self,
        signal_id: str | None = None,
        timeout_s: float | None = None,
        poll_interval_s: float = 0.5,
    ) -> dict | None:
        """Poll for the EA's fill receipt ``ack_<id>.json`` (fills handshake).

        The EA v1.10 writes an ack after executing any order, containing the
        client order id, the MT4 ticket and the fill price. Callers MUST NOT
        treat an order as filled without this ack (audit L10).

        Parameters
        ----------
        signal_id :
            Client order id to wait for. Defaults to the id of the most
            recently written signal on this executor.
        timeout_s :
            Poll budget in seconds. Defaults to
            ``config.execution.mt4_ack_timeout_s`` (10 s when unset).
        poll_interval_s :
            Delay between polls (default 0.5 s — the EA polls every 500 ms).

        Returns
        -------
        dict | None
            The parsed ack payload, or ``None`` on timeout / missing id.
        """
        sid = signal_id or self._last_signal_id
        if not sid:
            return None
        if timeout_s is None:
            try:
                timeout_s = float(getattr(get_config().execution, "mt4_ack_timeout_s", 10.0))
            except Exception:
                timeout_s = 10.0
        ack_path = self._signal_dir / f"{ACK_PREFIX}{sid}.json"
        deadline = time.monotonic() + max(0.0, timeout_s)
        while time.monotonic() < deadline:
            if ack_path.exists():
                try:
                    ack = json.loads(ack_path.read_text(encoding="utf-8"))
                    logger.info("MT4 ack received for signal %s: %s", sid, ack)
                    return ack
                except (OSError, json.JSONDecodeError):
                    pass  # mid-write by the EA — retry until the deadline
            time.sleep(poll_interval_s)
        logger.warning(
            "MT4 ack TIMEOUT for signal %s after %.1fs — no fill receipt from the EA",
            sid, timeout_s,
        )
        return None

    # -- convenience / introspection ----------------------------------------

    @property
    def signal_dir(self) -> Path:
        """Absolute path to the directory signals are written to."""
        return self._signal_dir

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(signal_dir={self._signal_dir!r}, "
            f"default_volume={self._default_volume:.2f})"
        )
