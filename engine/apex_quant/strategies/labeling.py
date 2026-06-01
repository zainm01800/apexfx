"""Triple-barrier labelling (Lopez de Prado, AFML).

For each decision bar we place three barriers: a profit-target and a stop-loss
(symmetric to the risk layer's ATR stop and reward:risk), plus a vertical time
barrier at ``horizon`` bars. The label is which barrier is touched first:
  * target first -> win (1)
  * stop first   -> loss (0)
  * neither      -> labelled by the sign of the holding return vs the trade
                    direction (a weak win/loss), so we don't silently drop bars.

This makes the calibrated probability mean exactly what the risk layer assumes:
P(target hit before stop), with payoff ratio b = target/stop = reward_risk.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def atr_series(df: pd.DataFrame, window: int) -> np.ndarray:
    """Wilder true-range, simple-moving-average ATR, aligned to df rows (NaN warmup)."""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    prev = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])
    s = pd.Series(tr).rolling(window, min_periods=window).mean()
    return s.to_numpy()


def triple_barrier_label(
    high: np.ndarray,
    low: np.ndarray,
    entry: float,
    direction: int,            # +1 long, -1 short
    stop_dist: float,
    target_dist: float,
    start_idx: int,
    horizon: int,
) -> int | None:
    """Label one decision bar. Returns 1 (win), 0 (loss), or None if not enough
    forward bars exist. Entry is assumed at ``start_idx+1`` open ~ entry price.

    Conservative tie-break: if a single bar's range spans both barriers, count it
    as the stop (loss) - we never optimistically assume the target filled first.
    """
    n = len(high)
    last = start_idx + horizon
    if start_idx + 1 >= n:
        return None
    if last >= n:
        return None

    if direction > 0:
        stop_px = entry - stop_dist
        target_px = entry + target_dist
    else:
        stop_px = entry + stop_dist
        target_px = entry - target_dist

    for i in range(start_idx + 1, last + 1):
        hi, lo = high[i], low[i]
        if direction > 0:
            hit_stop = lo <= stop_px
            hit_target = hi >= target_px
        else:
            hit_stop = hi >= stop_px
            hit_target = lo <= target_px
        if hit_stop:           # conservative: stop checked first
            return 0
        if hit_target:
            return 1
    # time barrier: weak label by holding return
    return None  # signalled by caller as "neutral / no clean label"
