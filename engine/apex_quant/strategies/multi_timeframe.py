"""Multi-timeframe trend confluence strategy.

Wraps a base strategy (e.g., RegimeGatedMomentum) and filters its signals
based on whether they align with the trend on a higher timeframe (HTF).
"""

from __future__ import annotations

import weakref

import logging
import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy

logger = logging.getLogger("apex_quant.strategies.multi_timeframe")


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregates a lower-timeframe OHLCV DataFrame into a higher timeframe."""
    if df.empty:
        return df
    
    # We specify the aggregation mapping for OHLCV
    agg_dict = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    }
    
    # Filter columns to only those that exist
    actual_agg = {k: v for k, v in agg_dict.items() if k in df.columns}
    
    # Resample and drop NaNs
    resampled = df.resample(rule).agg(actual_agg)
    return resampled.dropna()


class MultiTimeframeMomentum(Strategy):
    """Wraps any base strategy and gates its signals with an HTF trend filter."""
    name = "multi_timeframe_momentum"

    def __init__(
        self,
        base_strategy: Strategy,
        htf_rule: str | None = None,
        htf_ma_window: int = 200,
        instrument: str | None = None,
    ) -> None:
        self.base_strategy = base_strategy
        self.htf_rule = htf_rule
        self.htf_ma_window = htf_ma_window
        self.instrument = instrument or getattr(base_strategy, "instrument", "")
        
        # Mirror base strategy's metadata
        self.holding_horizon = getattr(base_strategy, "holding_horizon", 10)
        self.reward_risk = getattr(base_strategy, "reward_risk", 1.5)
        self.timeframe = getattr(base_strategy, "timeframe", "1d")

    def is_fitted(self) -> bool:
        return getattr(self.base_strategy, "is_fitted", lambda: True)()

    def fit(self, pit: PointInTimeAccessor, train_timestamps) -> None:
        if hasattr(self.base_strategy, "fit"):
            self.base_strategy.fit(pit, train_timestamps)

    # Class-level cache sharing HTF trend calculations across strategy instances,
    # scoped PER DATA OBJECT. Keying by (instrument, rule, window, t) alone served
    # one dataset's trend to any other dataset sharing the instrument name and
    # timestamp — the same cross-contamination bug as the regime cache (an uptrend
    # fixture's result answered for a downtrend fixture; live, a trend computed on
    # a half-formed current bar would be frozen for that timestamp). A
    # WeakKeyDictionary ties entries to the pit's lifetime, which also prevents
    # unbounded growth in the long-running live loop.
    _global_htf_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()

    def _determine_htf_trend(self, pit: PointInTimeAccessor, t) -> int:
        """Returns +1 for UP trend, -1 for DOWN trend, or 0 if indeterminate."""
        if not self.htf_rule:
            return 0  # No HTF rule -> no-op (always allow)

        per_pit = self._global_htf_cache.get(pit)
        if per_pit is None:
            per_pit = {}
            self._global_htf_cache[pit] = per_pit
        cache_key = (self.instrument, self.htf_rule, self.htf_ma_window, t)
        if cache_key in per_pit:
            return per_pit[cache_key]

        # Retrieve a large window of history to ensure we have enough bars for the HTF MA.
        # e.g., 200 daily bars require at least 4800 hourly bars, but let's request up to 3000
        # or use a smaller window if we don't have enough.
        # We fetch up to 4000 bars.
        df_ltf = pit.window(t, 4000)
        if df_ltf.empty:
            per_pit[cache_key] = 0
            return 0

        # Resample to HTF
        df_htf = resample_ohlcv(df_ltf, self.htf_rule)
        if len(df_htf) < self.htf_ma_window + 5:
            # If not enough history on HTF, return 0 (no-op/neutral) or fallback
            per_pit[cache_key] = 0
            return 0

        # Compute Simple Moving Average on close
        close = df_htf["close"]
        ma = close.rolling(self.htf_ma_window).mean()

        latest_close = float(close.iloc[-1])
        latest_ma = float(ma.iloc[-1])

        if not np.isfinite(latest_close) or not np.isfinite(latest_ma):
            per_pit[cache_key] = 0
            return 0

        res = 1 if latest_close > latest_ma else -1
        per_pit[cache_key] = res
        return res

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        # Get base strategy signal first
        sig = self.base_strategy.generate(pit, t, instrument or self.instrument)
        if sig.direction == Direction.FLAT:
            return sig

        # Bypass HTF trend filter for counter-trend mean-reversion signals
        if "mode=mean_reversion" in sig.rationale:
            return sig

        # Determine HTF trend direction
        htf_dir = self._determine_htf_trend(pit, t)
        if htf_dir == 0:
            # If indeterminate, allow the base signal to pass through
            return sig

        # Filter the signal
        if sig.direction == Direction.LONG and htf_dir < 0:
            return Signal(
                instrument=sig.instrument,
                direction=Direction.FLAT,
                probability=0.5,
                reward_risk=sig.reward_risk,
                confidence=0.0,
                timeframe=self.timeframe,
                rationale=(
                    f"Blocked by HTF trend filter: base signal is LONG but "
                    f"HTF {self.htf_rule} trend is DOWN (Close < {self.htf_ma_window}MA)."
                )
            )
        elif sig.direction == Direction.SHORT and htf_dir > 0:
            return Signal(
                instrument=sig.instrument,
                direction=Direction.FLAT,
                probability=0.5,
                reward_risk=sig.reward_risk,
                confidence=0.0,
                timeframe=self.timeframe,
                rationale=(
                    f"Blocked by HTF trend filter: base signal is SHORT but "
                    f"HTF {self.htf_rule} trend is UP (Close > {self.htf_ma_window}MA)."
                )
            )

        # Aligning trend -> allow signal to pass through
        # Append HTF confluence information to rationale
        confluence_info = f" | HTF {self.htf_rule} trend aligned ({'UP' if htf_dir > 0 else 'DOWN'})"
        sig.rationale += confluence_info
        return sig
