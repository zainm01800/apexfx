"""Currency-leg cross-sectional momentum strategy.

Decomposes currency pairs into individual currency strength scores,
ranks the currencies, and trades top-k vs bottom-k currencies.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy


def parse_base_quote(symbol: str) -> tuple[str, str]:
    """Parse base and quote currencies from pair symbols."""
    cleaned = symbol.replace("/", "").replace("-", "").upper()
    for suffix in [".ECN", ".M", "-G", ".X"]:
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)]
    if len(cleaned) >= 6:
        return cleaned[:3], cleaned[3:6]
    half = len(cleaned) // 2
    return cleaned[:half], cleaned[half:]


class CurrencyCrossSectionalMomentum:
    """Shared model that decomposes pairs into currency legs and ranks currencies.

    Parameters
    ----------
    panel :
        ``{instrument: OHLCV DataFrame}`` — the full history for each instrument.
    lookback :
        Bars over which the momentum return is measured.
    vol_window :
        Bars for the realised-vol scaling.
    k :
        Number of strong/weak currencies to select.
    min_universe :
        Minimum instruments with a valid score before any signal is emitted.
    allow_short :
        If False, only long the top fraction.
    reward_risk, holding_horizon, timeframe :
        Passed through onto the emitted signals.
    """

    def __init__(
        self,
        panel: dict[str, pd.DataFrame],
        *,
        lookback: int = 63,
        vol_window: int = 63,
        k: int = 2,
        min_universe: int = 4,
        allow_short: bool = True,
        reward_risk: float = 1.5,
        holding_horizon: int = 21,
        timeframe: str = "1d",
    ) -> None:
        if k < 1:
            raise ValueError("k must be >= 1")
        if lookback < 1 or vol_window < 2:
            raise ValueError("need lookback >= 1 and vol_window >= 2")
        self.lookback = lookback
        self.vol_window = vol_window
        self.k = k
        self.min_universe = max(2, min_universe)
        self.allow_short = allow_short
        self.reward_risk = reward_risk
        self.holding_horizon = holding_horizon
        self.timeframe = timeframe
        self.instruments = list(panel.keys())

        # Parse base and quote for each instrument
        self.pair_legs = {}
        self.all_currencies = set()
        for inst in self.instruments:
            base, quote = parse_base_quote(inst)
            self.pair_legs[inst] = (base, quote)
            self.all_currencies.add(base)
            self.all_currencies.add(quote)
        self.all_currencies = sorted(list(self.all_currencies))

        # Compute pair scores
        pair_scores: dict[str, pd.Series] = {}
        for inst, df in panel.items():
            c = df["close"]
            ret = c / c.shift(lookback) - 1.0
            vol = np.log(c).diff().rolling(vol_window).std(ddof=1)
            pair_scores[inst] = ret / vol.where(vol > 0)
        self._pair_scores = pd.DataFrame(pair_scores).sort_index()
        self._cache: dict[pd.Timestamp, dict] = {}

    @staticmethod
    def _norm_t(t) -> pd.Timestamp:
        ts = pd.Timestamp(t)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    def ranks_at(self, t) -> dict[str, tuple[int, float]]:
        """Return ``{instrument: (direction, strength_diff)}`` for active pairs at t."""
        t = self._norm_t(t)
        cached = self._cache.get(t)
        if cached is not None:
            return cached

        result: dict[str, tuple[int, float]] = {}
        if t in self._pair_scores.index:
            row = self._pair_scores.loc[t].dropna()
            n_pairs = len(row)
            if n_pairs >= self.min_universe:
                curr_sums = {c: 0.0 for c in self.all_currencies}
                curr_counts = {c: 0 for c in self.all_currencies}
                for inst, score in row.items():
                    base, quote = self.pair_legs[inst]
                    curr_sums[base] += score
                    curr_counts[base] += 1
                    curr_sums[quote] -= score
                    curr_counts[quote] += 1

                curr_strengths = {}
                for c in self.all_currencies:
                    if curr_counts[c] > 0:
                        curr_strengths[c] = curr_sums[c] / curr_counts[c]

                if len(curr_strengths) >= 3:
                    strengths_series = pd.Series(curr_strengths)
                    ordered = strengths_series.sort_values(ascending=False)
                    
                    real_k = min(self.k, len(curr_strengths) // 2)
                    if real_k >= 1:
                        top_k = set(ordered.index[:real_k])
                        bottom_k = set(ordered.index[-real_k:]) if self.allow_short else set()

                        mu = float(strengths_series.mean())
                        sd = float(strengths_series.std(ddof=1)) or 1.0
                        z_scores = {c: (val - mu) / sd for c, val in strengths_series.items()}

                        for inst in row.index:
                            base, quote = self.pair_legs[inst]
                            if base in z_scores and quote in z_scores:
                                if base in top_k and quote in bottom_k:
                                    z_diff = z_scores[base] - z_scores[quote]
                                    result[inst] = (1, z_diff)
                                elif base in bottom_k and quote in top_k:
                                    z_diff = z_scores[base] - z_scores[quote]
                                    result[inst] = (-1, z_diff)

        self._cache[t] = result
        return result

    def signal_for(self, instrument: str, t) -> Signal:
        entry = self.ranks_at(t).get(instrument)
        if entry is None:
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=self.reward_risk, timeframe=self.timeframe,
                rationale="currency-cross-sectional: not top/bottom match",
            )
        d, z_diff = entry
        direction = Direction.LONG if d > 0 else Direction.SHORT
        p = float(np.clip(0.52 + 0.05 * abs(z_diff), 0.52, 0.70))
        return Signal(
            instrument=instrument, direction=direction, probability=p,
            reward_risk=self.reward_risk, confidence=float(np.clip(abs(z_diff) / 2.0, 0.0, 1.0)),
            timeframe=self.timeframe,
            rationale=f"currency-cross-sectional {direction.value.upper()} | z_diff={z_diff:+.2f} | p={p:.2f}",
        )

    def strategies(self) -> dict[str, CurrencyCrossSectionalMomentumStrategy]:
        """One per-instrument adapter for every instrument."""
        return {inst: CurrencyCrossSectionalMomentumStrategy(self, inst) for inst in self.instruments}


class CurrencyCrossSectionalMomentumStrategy(Strategy):
    """Adapter for CurrencyCrossSectionalMomentum strategy."""
    name = "currency_cross_sectional_momentum"

    def __init__(self, model: CurrencyCrossSectionalMomentum, instrument: str) -> None:
        self.model = model
        self.instrument = instrument
        self.holding_horizon = model.holding_horizon
        self.reward_risk = model.reward_risk
        self.timeframe = model.timeframe

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        return self.model.signal_for(instrument or self.instrument, t)
