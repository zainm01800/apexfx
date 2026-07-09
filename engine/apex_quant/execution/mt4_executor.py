"""MT4 file-based execution bridge.

Writes JSON order signals to the MetaTrader 4 shared common folder where the
companion ``apex_mt4_bridge.mq4`` Expert Advisor polls and executes them.

JSON format expected by the MQ4 script:

.. code-block:: json

    {"symbol": "EURUSD", "cmd": "buy", "volume": 0.1, "sl": 0.0, "tp": 0.0}

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

#: Filename the MQ4 bridge polls for.
SIGNAL_FILENAME = "mt4_signals.json"


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


# ---------------------------------------------------------------------------
#  Executor
# ---------------------------------------------------------------------------
class MT4Executor:
    """Write order signals to the MT4 bridge file with atomic, thread-safe I/O.

    Parameters
    ----------
    common_dir : str | Path | None
        Override the MT4 common directory. If ``None`` (the default), the
        directory is resolved via :func:`_resolve_common_dir`.
    default_volume : float
        Volume (lot size) used when ``submit_order`` receives ``volume=None``
        or ``volume=0.0``.
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
        self._signal_path = self._signal_dir / SIGNAL_FILENAME

        # Ensure the target directory exists.
        self._signal_dir.mkdir(parents=True, exist_ok=True)

        # Default volume fallback.
        if default_volume is not None and default_volume > 0:
            self._default_volume = default_volume
        else:
            cfg = get_config()
            self._default_volume = cfg.execution.mt4.default_volume

        logger.info(
            "MT4Executor initialised — signal path: %s, default volume: %.2f",
            self._signal_path,
            self._default_volume,
        )

    # -- public API ---------------------------------------------------------

    def submit_order(
        self,
        symbol: str,
        cmd: Literal["buy", "sell"],
        volume: float | None = None,
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> Path:
        """Write an order signal to the MT4 bridge file.

        The write is **atomic**: the JSON payload is first written to a
        ``.tmp`` file inside the same directory, then renamed to
        ``mt4_signals.json``.  This guarantees the MQ4 timer callback
        either sees the complete file or no file at all.

        Parameters
        ----------
        symbol :
            Instrument ticker (e.g. ``"EURUSD"``).
        cmd :
            Order direction — ``"buy"`` or ``"sell"``.
        volume :
            Lot size.  Falls back to the configured ``default_volume`` if
            ``None`` or ``0``.
        sl :
            Stop-loss price (0.0 = none).
        tp :
            Take-profit price (0.0 = none).

        Returns
        -------
        Path
            The absolute path that was written.

        Raises
        ------
        OSError
            If the file cannot be written (permissions, disk full, etc.).
        """
        # Resolve effective volume.
        effective_volume = self._default_volume if not volume else float(volume)

        payload = {
            "symbol": symbol,
            "cmd": cmd,
            "volume": effective_volume,
            "sl": float(sl),
            "tp": float(tp),
        }
        raw = json.dumps(payload, separators=(",", ":"))

        # Atomic write: write to temp sibling, then rename.
        tmp_path = self._signal_path.with_suffix(".tmp")
        with self._lock:
            try:
                tmp_path.write_text(raw, encoding="utf-8")
                tmp_path.rename(self._signal_path)
            except OSError:
                logger.exception(
                    "Failed to write MT4 signal to %s", self._signal_path
                )
                # Clean up temp file on failure.
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                raise

        logger.info(
            "MT4 signal written — %s %s %.2f lots (SL=%.5f TP=%.5f) → %s",
            cmd.upper(),
            symbol,
            effective_volume,
            float(sl),
            float(tp),
            self._signal_path,
        )
        return self._signal_path

    # -- convenience / introspection ----------------------------------------

    @property
    def signal_path(self) -> Path:
        """Absolute path to the bridge signal file."""
        return self._signal_path

    @property
    def signal_dir(self) -> Path:
        """Absolute path to the directory containing the signal file."""
        return self._signal_dir

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(signal_path={self._signal_path!r}, "
            f"default_volume={self._default_volume:.2f})"
        )