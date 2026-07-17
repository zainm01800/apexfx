"""BTC/ETH US-close momentum (Shen, Urquhart & Wang 2022, Financial Review 57(2)).

Documented effect (docs/research/2026-07-17_subdaily_edges_post_cost.md, sec.1):
the rest-of-day return predicts the move into the 17:00 ET CME break; the effect
is concentrated on high-volume / high-volatility days; gross edge ~3-10 bps per
trade - real but fee-fragile (breakeven 3-10 bps; dead at 25bps taker fees).

Implementation (deliberately thin - no ML, no extra indicators):
  * Data convention: 1h bars labeled by bar OPEN time, UTC (the store's de-facto
    1h convention; Binance klines are open-time natively). A bar labeled H covers
    [H, H+1h); its close is the (H+1) price.
  * "Crypto day" = 00:00 UTC. SIMPLIFICATION vs the paper: Shen et al. anchor the
    day at the volume-spike open and close at 17:00 EST; we use a fixed UTC
    clock (documented in the candidate report). Signal bar = the bar CLOSING at
    ``signal_close_hour`` UTC (default 20:00; on open-time labels that is the bar
    timestamped 19:00). 20:00 UTC ~ 15:00-16:00 ET - the last liquid hours into
    the CME break on 1h granularity.
  * Signal: rest-of-day return R = close(signal bar) / open(00:00 bar) - 1.
    If |R| > dead_zone, enter in sign(R) at the NEXT bar's open (the engine's
    pending-fill mechanics; the fill price is the signal bar's close instant).
    Long/short symmetric.
  * Optional vol/volume filter (paper: effect lives on high-vol/high-volume
    days): trade only if today's 00:00->signal close volume AND |R| both exceed
    their trailing medians over complete prior days, measured over the SAME
    partial-day window (00:00 -> signal close) so the comparison is like-for-like.
  * Exit: time barrier only. ``holding_horizon`` (=hold_bars) 1 -> exit at the
    close of the 1st bar after entry (~2h exposure), 2 -> 2nd bar (~3h). The
    ATR stop/target are set 8x ATR(14) away so they essentially never bind -
    the academic trade is a pure timing bet, not a stop-managed one.

Look-ahead: the signal at bar t reads only bars <= t (point-in-time accessor);
the engine fills at the next bar's open. Stateless: fit() is a no-op, so CPCV
folds cannot leak through calibration.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy


class IntradayCloseMomentum(Strategy):
    name = "intraday_close_momentum"

    def __init__(
        self,
        signal_close_hour: int = 20,      # UTC close hour of the signal bar
        day_open_hour: int = 0,           # UTC hour the crypto "day" opens
        dead_zone: float = 0.0,           # min |rest-of-day return| to trade
        vol_filter: bool = True,          # require high-vol + high-volume day
        vol_lookback_days: int = 20,      # trailing median window (complete days)
        holding_horizon: int = 1,         # bars to hold AFTER the entry bar
        stop_atr_mult: float = 8.0,       # wide: time exit must bind, not the stop
        timeframe: str = "1h",
        instrument: str | None = None,
    ):
        self.signal_close_hour = signal_close_hour
        self.day_open_hour = day_open_hour
        self.dead_zone = dead_zone
        self.vol_filter = vol_filter
        self.vol_lookback_days = vol_lookback_days
        self.holding_horizon = holding_horizon   # engine time-barrier reads this
        self.stop_atr_mult = stop_atr_mult
        self.timeframe = timeframe
        self.instrument = instrument or ""

    # -- helpers -----------------------------------------------------------------
    def _flat(self, instrument: str, reason: str) -> Signal:
        return Signal(
            instrument=instrument, direction=Direction.FLAT, probability=0.5,
            reward_risk=1.0, confidence=0.0, timeframe=self.timeframe, rationale=reason,
        )

    # -- inference ---------------------------------------------------------------
    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        t = pd.Timestamp(t)
        # On open-time labels the bar CLOSING at signal_close_hour is labeled one
        # hour earlier (19:00 bar closes at 20:00).
        if t.minute != 0 or t.hour != (self.signal_close_hour - 1) % 24:
            return self._flat(instrument, "not the signal bar")

        # Enough bars for today's session + trailing complete days for the filter.
        n_bars = 24 * (self.vol_lookback_days + 1) + 2
        df = pit.window(t, n_bars)
        if df.empty or df.index[-1] != t:
            return self._flat(instrument, "signal bar not in window")

        day_start = t.normalize() + pd.Timedelta(hours=self.day_open_hour)
        if day_start not in df.index:
            return self._flat(instrument, "day-open bar missing (data gap)")
        day_open = float(df.loc[day_start, "open"])
        close_t = float(df["close"].iloc[-1])
        if day_open <= 0:
            return self._flat(instrument, "bad day-open price")
        rod_ret = close_t / day_open - 1.0
        if abs(rod_ret) < self.dead_zone:
            return self._flat(instrument, f"|RoD ret| {abs(rod_ret):.4f} < dead_zone")

        if self.vol_filter:
            ok, detail = self._passes_vol_filter(df, day_start, rod_ret)
            if not ok:
                return self._flat(instrument, detail)
        else:
            detail = "vol_filter off"

        # Wide stop/target: the TIME exit is the trade; stops exist only because
        # the risk layer needs a stop distance for sizing. 8x ATR(14,1h) is ~never
        # touched inside a 2-3h hold.
        atr = self._atr(df, 14)
        if not (np.isfinite(atr) and atr > 0):
            return self._flat(instrument, "ATR unavailable")
        direction = Direction.LONG if rod_ret > 0 else Direction.SHORT
        if direction == Direction.LONG:
            stop_price, target_price = close_t - self.stop_atr_mult * atr, close_t + self.stop_atr_mult * atr
        else:
            stop_price, target_price = close_t + self.stop_atr_mult * atr, close_t - self.stop_atr_mult * atr

        return Signal(
            instrument=instrument,
            direction=direction,
            probability=0.55,          # thin honest prior; sizing only, no Kelly gate (kelly_fraction=0)
            reward_risk=1.0,
            confidence=0.5,
            timeframe=self.timeframe,
            stop_price=stop_price,
            target_price=target_price,
            rationale=(
                f"close-mom: RoD {rod_ret * 1e4:.0f}bps ({self.day_open_hour:02d}00->"
                f"{self.signal_close_hour:02d}00 UTC) | {detail} | hold {self.holding_horizon}b"
            ),
        )

    def _passes_vol_filter(self, df: pd.DataFrame, day_start: pd.Timestamp, rod_ret: float):
        """Today's partial-day volume and |RoD return| must both exceed their
        trailing medians (Shen et al.: the effect concentrates on high-volume /
        high-vol days). Partial-day windows are aligned (00:00 -> signal close)
        across days so the comparison is not biased by time-of-day."""
        sig_label_hour = (self.signal_close_hour - 1) % 24
        w = df  # window already ends at the signal bar
        days = w.index.normalize()
        stats = {}
        for d in pd.unique(days):
            bars = w[days == d]
            open_bars = bars[bars.index.hour == self.day_open_hour]
            sig_bars = bars[bars.index.hour == sig_label_hour]
            vol_sum = float(bars["volume"].sum())
            if len(open_bars) and len(sig_bars) and float(open_bars["open"].iloc[0]) > 0:
                ret = abs(float(sig_bars["close"].iloc[-1]) / float(open_bars["open"].iloc[0]) - 1.0)
            else:
                ret = np.nan  # incomplete day (gap) - excluded from the trailing set
            stats[pd.Timestamp(d)] = (vol_sum, ret)

        today = day_start.normalize()
        trailing = [
            v for d, v in stats.items()
            if d < today and np.isfinite(v[1])
        ][-self.vol_lookback_days:]
        if len(trailing) < 10:
            return False, f"only {len(trailing)} complete trailing days for filter"
        med_vol = float(np.median([v[0] for v in trailing]))
        med_ret = float(np.median([v[1] for v in trailing]))
        today_vol, _ = stats[today]
        passed = today_vol > med_vol and abs(rod_ret) > med_ret
        return passed, (f"vol {today_vol:.0f} vs med {med_vol:.0f}; |R| {abs(rod_ret):.4f} "
                        f"vs med {med_ret:.4f} -> {'PASS' if passed else 'FILTERED'}")

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
