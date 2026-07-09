"""Mock executor for paper trading / integration testing.

Implements the same ``submit_order()`` interface as :class:`MT4Executor`
but writes the order to the structured log rather than to the MT4 signal
file.  Useful for testing the full pipeline end-to-end without a live MT4
terminal.
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Mock executor
# ---------------------------------------------------------------------------
class MockExecutor:
    """Log order signals at INFO level instead of writing to a bridge file.

    Parameters
    ----------
    default_volume : float
        Volume (lot size) used when ``submit_order`` receives ``volume=None``
        or ``volume=0.0``.
    """

    def __init__(self, default_volume: float = 0.10) -> None:
        self._default_volume = default_volume

        logger.info(
            "MockExecutor initialised — default volume: %.2f", self._default_volume
        )

    # -- public API ---------------------------------------------------------

    def submit_order(
        self,
        symbol: str,
        cmd: Literal["buy", "sell"],
        volume: float | None = None,
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> dict:
        """Log an order signal (no file I/O).

        Parameters
        ----------
        symbol :
            Instrument ticker (e.g. ``"EURUSD"``).
        cmd :
            Order direction — ``"buy"`` or ``"sell"``.
        volume :
            Lot size.  Falls back to ``default_volume`` if ``None`` or ``0``.
        sl :
            Stop-loss price (0.0 = none).
        tp :
            Take-profit price (0.0 = none).

        Returns
        -------
        dict
            The payload dict that *would* have been written to the bridge file
            (useful for test assertions).
        """
        effective_volume = self._default_volume if not volume else float(volume)

        payload = {
            "symbol": symbol,
            "cmd": cmd,
            "volume": effective_volume,
            "sl": float(sl),
            "tp": float(tp),
        }

        logger.info(
            "[MOCK ORDER] %s %s %.2f lots (SL=%.5f TP=%.5f) — payload=%s",
            cmd.upper(),
            symbol,
            effective_volume,
            float(sl),
            float(tp),
            payload,
        )
        return payload

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(default_volume={self._default_volume:.2f})"
        )