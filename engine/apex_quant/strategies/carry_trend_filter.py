"""Carry-filtered trend: the baseline regime-gated momentum signal, vetoed when
the trade's direction *pays* negative carry.

Rationale (docs/research/2026-07-17_fx_edges_evidence.md, sec.1): naive carry
harvesting does not survive retail swap markups, but carry as a directional
*filter* on trend is documented to raise Sharpe and cut skew (Clare et al.,
York DP 15/07). So this wrapper never hunts swap income — it only refuses
trend trades that fight the rate differential:

  * LONG  base/quote is vetoed when base_rate  < quote_rate (negative carry)
  * SHORT base/quote is vetoed when quote_rate < base_rate  (negative carry)

Rates come from the point-in-time ``CSVRateProvider`` (central-bank policy
rates, effective-dated — no lookahead). When no rate row exists for the pair
at time ``t`` the signal passes through unvetoed: absence of data is not
evidence against the trade, and silently fabricating a veto would be worse.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.data.rates import CSVRateProvider
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy
from apex_quant.strategies.baseline import RegimeGatedMomentum


class CarryTrendFilter(Strategy):
    """Wrap ``RegimeGatedMomentum`` and zero trades whose direction earns negative carry.

    Pass ``instrument`` whenever the pair is known (always, in a multi-instrument
    book): without it the inner momentum strategy falls back to the equity asset
    class (wrong regime slope eps for forex) and its class-level Bollinger cache
    key collapses to "" — every pair sharing one process would read the first
    pair's band midline as its mean-reversion target.
    """

    name = "carry_trend_filter"

    def __init__(
        self,
        momentum_lookback: int = 126,
        vol_window: int = 63,
        holding_horizon: int = 21,
        reward_risk: float = 1.5,
        regime_method: str = "rule_based",
        timeframe: str = "1d",
        rate_provider=None,
        instrument: str | None = None,
    ):
        self.base = RegimeGatedMomentum(
            momentum_lookback=momentum_lookback,
            vol_window=vol_window,
            holding_horizon=holding_horizon,
            reward_risk=reward_risk,
            regime_method=regime_method,
            timeframe=timeframe,
            instrument=instrument,
        )
        self.holding_horizon = holding_horizon
        self.reward_risk = reward_risk
        self.timeframe = timeframe
        self._rates = rate_provider  # lazy: CSVRateProvider() on first use
        self.n_vetoes = 0
        self.n_signals = 0

    def _provider(self):
        if self._rates is None:
            self._rates = CSVRateProvider()  # default data_store/central_bank_rates.csv
        return self._rates

    # -- training: delegate to the primary -------------------------------------
    def fit(self, pit: PointInTimeAccessor, train_timestamps: Iterable[pd.Timestamp]) -> None:
        self.base.fit(pit, train_timestamps)

    def is_fitted(self) -> bool:
        return self.base.is_fitted()

    # -- inference --------------------------------------------------------------
    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        sig = self.base.generate(pit, t, instrument)
        if sig.direction == Direction.FLAT:
            return sig

        self.n_signals += 1
        try:
            rates = self._provider()(instrument, pd.Timestamp(t))
        except Exception:  # noqa: BLE001 - missing rates file must not kill a backtest
            rates = None
        if rates is None:
            return sig

        base_rate, quote_rate = rates
        carry = (base_rate - quote_rate) if sig.direction == Direction.LONG else (quote_rate - base_rate)
        if carry < 0:
            self.n_vetoes += 1
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=sig.reward_risk, confidence=0.0, timeframe=sig.timeframe,
                rationale=(
                    f"carry veto: {sig.direction.value} {instrument} earns "
                    f"{carry * 100:.2f}%/yr (base {base_rate * 100:.2f} vs quote {quote_rate * 100:.2f})"
                ),
            )
        sig.rationale = f"{sig.rationale} | carry +{carry * 100:.2f}%/yr"
        return sig
