"""Stops - ATR / volatility based, computed by the risk layer (never the signal).

Stops are derived from volatility, not from price targets a model 'hopes' for.
A wider-vol regime => a wider stop => a smaller position for the same risk budget.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.risk.types import Direction


def atr(df: pd.DataFrame, window: int = 14) -> float:
    """Average True Range over the last ``window`` bars (Wilder's true range)."""
    if len(df) < 2:
        return float("nan")
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    prev_close = close[:-1]
    tr = np.maximum.reduce(
        [
            high[1:] - low[1:],
            np.abs(high[1:] - prev_close),
            np.abs(low[1:] - prev_close),
        ]
    )
    w = min(window, len(tr))
    return float(np.mean(tr[-w:]))


def atr_stop(price: float, atr_value: float, mult: float, direction: Direction) -> tuple[float, float]:
    """Return ``(stop_price, stop_distance)`` for a long/short entry at ``price``.

    Distance = ``mult * ATR``. Long stop sits below price, short stop above."""
    distance = mult * atr_value
    if direction == Direction.LONG:
        return price - distance, distance
    if direction == Direction.SHORT:
        return price + distance, distance
    return price, 0.0
