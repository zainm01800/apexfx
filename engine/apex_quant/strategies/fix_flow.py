"""USD fix-flow reversal (Krohn, Mueller & Whelan 2024, J. Finance 79(1)).

Documented effect (docs/research/2026-07-17_subdaily_edges_post_cost.md, sec.3):
the USD appreciates into the major FX fixes and depreciates afterward - a
W-shaped intraday pattern driven by dealer inventory-risk intermediation of fix
demand. The trade mimics the dealer who provides that inventory: go SHORT-USD
at the 16:00 London fix and cover a couple of hours later.

Implementation (deliberately thin - no ML, no extra indicators):
  * Data convention: 1h OANDA bars labeled by bar OPEN time, UTC (verified for
    the store's 1h caches 2026-07-17: the last Friday bar is 21:00, the week
    closes 22:00 UTC). A bar labeled H covers [H, H+1h); its close is the (H+1)
    price.
  * Fix convention: the 16:00 LONDON fix, DST-aware. Bars are converted to
    Europe/London; the signal fires on the bar whose London-local hour is
    ``signal_local_hour`` (default 15:00 - i.e. the bar CLOSING at 16:00 London:
    15:00 UTC in winter / 14:00 UTC under BST). The engine then fills at the
    next bar's open, whose price is the 16:00 London fix instant. Melvin & Prins
    (2015) / Evans (2018) use the same WMR 4pm London anchor.
  * Direction: short-USD basket leg. For "EUR/USD"-shaped pairs (USD quote) go
    LONG the pair; for "USD/JPY"-shaped pairs (USD base) go SHORT. The 4-leg
    basket (long EUR+GBP, short JPY+CHF legs) is validated per instrument -
    correlation between legs is handled, if ever, at the book level, not here.
  * Optional pre-move conditioning (grid variant): only fade the USD if it
    actually appreciated over the ``pre_hours`` into the fix (Krohn et al. is a
    conditional pattern; unconditionally shorting USD at 16:00 London is the
    naive version). USD-move sign is computed per pair orientation.
  * Exit: time barrier only; wide 8x ATR(14,1h) stop/target so the time exit
    binds, not the stop. ``holding_horizon`` 1 -> exit at the close of the 1st
    bar after entry (~2h: 16:00 -> 18:00 London), 2 -> ~3h.

Look-ahead: the signal at bar t reads only bars <= t; the engine fills at the
next bar's open (the fix instant). Stateless: fit() is a no-op.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy

LONDON = "Europe/London"


class FixFlowReversal(Strategy):
    name = "fix_flow_reversal"

    def __init__(
        self,
        signal_local_hour: int = 15,      # London-local hour of the bar closing at the fix
        condition_on_premove: bool = False,  # require USD appreciation into the fix
        pre_hours: int = 6,               # into-fix window for the conditioning move
        dead_zone: float = 0.0,           # min |USD move| for the conditioning variant
        holding_horizon: int = 1,         # bars to hold AFTER the entry bar
        stop_atr_mult: float = 8.0,       # wide: time exit must bind, not the stop
        timeframe: str = "1h",
        instrument: str | None = None,
    ):
        self.signal_local_hour = signal_local_hour
        self.condition_on_premove = condition_on_premove
        self.pre_hours = pre_hours
        self.dead_zone = dead_zone
        self.holding_horizon = holding_horizon   # engine time-barrier reads this
        self.stop_atr_mult = stop_atr_mult
        self.timeframe = timeframe
        self.instrument = instrument or ""

    def _flat(self, instrument: str, reason: str) -> Signal:
        return Signal(
            instrument=instrument, direction=Direction.FLAT, probability=0.5,
            reward_risk=1.0, confidence=0.0, timeframe=self.timeframe, rationale=reason,
        )

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        t = pd.Timestamp(t)
        lt = t.tz_convert(LONDON)
        if lt.minute != 0 or lt.hour != self.signal_local_hour:
            return self._flat(instrument, "not the fix signal bar")

        inst = instrument or self.instrument
        try:
            base, quote = (p.strip().upper() for p in inst.split("/"))
        except ValueError:
            return self._flat(inst, f"cannot parse pair '{inst}'")
        if quote == "USD":
            trade_dir, usd_sign = Direction.LONG, -1.0   # pair down == USD up
        elif base == "USD":
            trade_dir, usd_sign = Direction.SHORT, +1.0  # pair up == USD up
        else:
            return self._flat(inst, "no USD leg - not a fix-flow basket instrument")

        df = pit.window(t, self.pre_hours + 20)  # pre-move window + ATR room
        if df.empty or df.index[-1] != t:
            return self._flat(inst, "signal bar not in window")
        close_t = float(df["close"].iloc[-1])

        detail = "unconditional"
        if self.condition_on_premove:
            pre_bars = df.iloc[-(self.pre_hours + 1):]
            if len(pre_bars) < self.pre_hours + 1:
                return self._flat(inst, "insufficient bars for pre-move")
            pair_move = close_t / float(pre_bars["open"].iloc[0]) - 1.0
            usd_move = usd_sign * pair_move
            if usd_move < self.dead_zone:
                return self._flat(
                    inst, f"USD did not appreciate into fix ({usd_move * 1e4:.0f}bps < {self.dead_zone * 1e4:.0f}bps)"
                )
            detail = f"USD pre-move {usd_move * 1e4:+.0f}bps/{self.pre_hours}h"

        atr = self._atr(df, 14)
        if not (np.isfinite(atr) and atr > 0):
            return self._flat(inst, "ATR unavailable")
        if trade_dir == Direction.LONG:
            stop_price, target_price = close_t - self.stop_atr_mult * atr, close_t + self.stop_atr_mult * atr
        else:
            stop_price, target_price = close_t + self.stop_atr_mult * atr, close_t - self.stop_atr_mult * atr

        return Signal(
            instrument=inst,
            direction=trade_dir,
            probability=0.55,          # thin honest prior; sizing only, no Kelly gate (kelly_fraction=0)
            reward_risk=1.0,
            confidence=0.5,
            timeframe=self.timeframe,
            stop_price=stop_price,
            target_price=target_price,
            rationale=(
                f"fix-flow: short-USD at 16:00 London ({detail}) | "
                f"hold {self.holding_horizon}b"
            ),
        )

    @staticmethod
    def _atr(df: pd.DataFrame, window: int) -> float:
        if len(df) < window + 1:
            return float("nan")
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat(
            [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
            axis=1,
        ).max(axis=1)
        return float(tr.rolling(window).mean().iloc[-1])
