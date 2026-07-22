"""COT Speculator Crowding Reversal Strategy & Book.

Implements data_store/cot_reversal_sleeve_prereg.md:
Generates contrarian trading signals on FX majors and Gold based on 156-week rolling
z-scores of CFTC net non-commercial speculator positioning, point-in-time shifted.
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from typing import Any, Dict

from apex_quant.data.cot import load_cached, net_positioning, as_of_release, positioning_zscore
from apex_quant.risk.types import Direction, Signal

logger = logging.getLogger(__name__)


class COTReversalBook:
    """Multi-asset portfolio book wrapper for COT Speculator Crowding Reversal strategy."""

    def __init__(
        self,
        panel: Dict[str, pd.DataFrame],
        z_threshold: float = 2.0,
        horizon: int = 10,
        z_window: int = 156,
        cot_years: range | list[int] | None = None,
        reward_risk: float = 1.5,
        **kwargs: Any,
    ) -> None:
        self.panel = panel
        self.z_threshold = float(z_threshold)
        self.horizon = int(horizon)
        self.z_window = int(z_window)
        self.reward_risk = float(reward_risk)
        if cot_years is None:
            cot_years = range(2015, 2026)
        self.cot_years = cot_years
        self._cot_df = load_cached(self.cot_years)
        self._signals: Dict[str, pd.Series] = {}
        self._build_signals()

    def _build_signals(self) -> None:
        cot_map = {
            "EUR/USD": "EUR/USD",
            "GBP/USD": "GBP/USD",
            "AUD/USD": "AUD/USD",
            "NZD/USD": "NZD/USD",
            "USD/JPY": "USD/JPY",
            "USD/CHF": "USD/CHF",
            "USD/CAD": "USD/CAD",
            "SGLD.L": "GOLD",
        }

        for inst, df in self.panel.items():
            mapped_cot_sym = cot_map.get(inst)
            if not mapped_cot_sym:
                continue

            try:
                net = net_positioning(self._cot_df, mapped_cot_sym)
                if net.empty:
                    continue
                net_pit = as_of_release(net, lag_days=3)
                z = positioning_zscore(net_pit, window=self.z_window, min_periods=52)

                df_idx_utc = pd.to_datetime(df.index)
                if df_idx_utc.tzinfo is None:
                    df_idx_utc = df_idx_utc.tz_localize("UTC")
                else:
                    df_idx_utc = df_idx_utc.tz_convert("UTC")

                z_idx_utc = pd.to_datetime(z.index)
                if z_idx_utc.tzinfo is None:
                    z_idx_utc = z_idx_utc.tz_localize("UTC")
                else:
                    z_idx_utc = z_idx_utc.tz_convert("UTC")

                z_series = pd.Series(z.values, index=z_idx_utc)
                z_daily = z_series.reindex(df_idx_utc, method="ffill")

                raw_sig = pd.Series(0.0, index=df.index)
                raw_sig.loc[z_daily.values >= self.z_threshold] = -1.0
                raw_sig.loc[z_daily.values <= -self.z_threshold] = 1.0

                sig = pd.Series(0.0, index=df.index)
                curr_sig = 0.0
                bars_left = 0
                for i in range(len(df)):
                    r_val = raw_sig.iloc[i]
                    if r_val != 0.0:
                        curr_sig = r_val
                        bars_left = self.horizon
                    elif bars_left > 0:
                        bars_left -= 1
                        if bars_left == 0:
                            curr_sig = 0.0
                    sig.iloc[i] = curr_sig

                self._signals[inst] = sig
            except Exception as e:
                logger.warning("Failed to generate COT reversal signal for %s: %s", inst, e)

    def signal_for(self, instrument: str, t) -> Signal:
        sig_val = 0.0
        sig_series = self._signals.get(instrument)
        if sig_series is not None and t in sig_series.index:
            sig_val = float(sig_series.loc[t])

        if sig_val > 0.0:
            direction = Direction.LONG
            prob = 0.58
        elif sig_val < 0.0:
            direction = Direction.SHORT
            prob = 0.58
        else:
            direction = Direction.FLAT
            prob = 0.50

        return Signal(
            instrument=instrument,
            direction=direction,
            probability=prob,
            reward_risk=self.reward_risk,
            confidence=0.6,
            timeframe="1d",
            rationale=f"cot_reversal signal {sig_val:+.1f}",
        )

    def strategies(self) -> Dict[str, "COTReversalStrategy"]:
        return {inst: COTReversalStrategy(self, inst) for inst in self.panel}


class COTReversalStrategy:
    """Per-instrument strategy adapter for PortfolioBacktester."""

    def __init__(self, book: COTReversalBook, instrument: str) -> None:
        self.book = book
        self.instrument = instrument

    def generate(self, pit: Any, t: Any, instrument: str = "") -> Signal:
        inst = instrument or self.instrument
        return self.book.signal_for(inst, t)
