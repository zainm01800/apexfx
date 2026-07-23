"""Residual (idiosyncratic) momentum over a universe.

Total-return momentum ranks instruments on their raw past return, so the ranking is dominated
by whatever the common factor did — in an equity-heavy panel, by market beta. Every "extra"
position is then largely the same bet, which is why widening the book degrades it: the
engine's own breadth sweep measured Sharpe falling 0.922 -> 0.704 -> 0.460 as concurrent slots
went 12 -> 20 -> 30, because the marginal position added beta, not information.

Residual momentum removes the shared factor first. Each instrument's returns are regressed on
the cross-sectional (equal-weight) market return over a rolling window; the ranking is then
done on the accumulated RESIDUAL, standardised by its own volatility. What survives is the
instrument-specific component. Blitz-Huij-Martens and Blitz-Hanauer-Vidojevic report this
roughly doubles the momentum Sharpe on large stock cross-sections — not by earning more, but
by roughly halving strategy volatility.

The measured signature on this engine's data is the one the theory predicts: breadth HELPS
residual momentum (Sharpe 0.757 -> 0.963 -> 0.998 at top 5/10/15) while it HURTS total
momentum (0.876 -> 0.747). See ``data_store/profit_frontier_2026-07-23.md`` §7d.

Conventions kept deliberately standard rather than tuned:
  * 12-1 momentum — a ``lookback`` of 252 bars ending ``skip`` (21) bars ago. Skipping the most
    recent month is the standard control for short-term reversal, not a fitted choice.
  * Long-only top-N. Shorting residual losers is a different (and, for a UK retail account,
    largely unimplementable) strategy.

Leakage safety: every window is backward-looking, so the score at bar ``t`` uses only bars
``<= t``. The market factor is the contemporaneous cross-sectional mean, which is observable
at ``t``. One shared model serves the universe; a thin per-instrument adapter plugs it into
:class:`~apex_quant.backtest.portfolio.PortfolioBacktester`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy


class ResidualMomentum:
    """Shared residual-momentum model over a panel of instruments.

    Parameters
    ----------
    panel :
        ``{instrument: OHLCV DataFrame}`` — full history per instrument.
    lookback :
        Bars over which the residual return is accumulated (252 = 12 months).
    skip :
        Bars skipped at the recent end (21 = 1 month), the standard 12-1 control for
        short-term reversal.
    beta_window :
        Bars for the rolling market-beta regression. Defaults to ``lookback``.
    vol_window :
        Bars for the realised-vol scaling used when sizing conviction.
    top_n :
        Number of instruments held long each bar.
    min_universe :
        Minimum instruments with a valid score before ANY signal is emitted. Residualising
        against a handful of names produces noise, not a factor — this is the guard.
    reward_risk, holding_horizon, timeframe :
        Passed through onto emitted signals / consumed by the backtester.
    """

    def __init__(
        self,
        panel: dict[str, pd.DataFrame],
        *,
        lookback: int = 252,
        skip: int = 21,
        beta_window: int | None = None,
        vol_window: int = 63,
        top_n: int = 15,
        min_universe: int = 40,
        reward_risk: float = 1.5,
        holding_horizon: int = 21,
        timeframe: str = "1d",
    ) -> None:
        if lookback < 2:
            raise ValueError("lookback must be >= 2")
        if skip < 0:
            raise ValueError("skip must be >= 0")
        if top_n < 1:
            raise ValueError("top_n must be >= 1")
        if vol_window < 2:
            raise ValueError("vol_window must be >= 2")

        self.lookback = lookback
        self.skip = skip
        self.beta_window = beta_window or lookback
        self.vol_window = vol_window
        self.top_n = top_n
        self.min_universe = max(2, min_universe)
        self.reward_risk = reward_risk
        self.holding_horizon = holding_horizon
        self.timeframe = timeframe
        self.instruments = list(panel.keys())

        close = pd.DataFrame(
            {inst: df["close"] for inst, df in panel.items()}
        ).sort_index()
        rets = close.pct_change()

        # Equal-weight cross-sectional market factor, observable at t.
        mkt = rets.mean(axis=1)
        var_m = mkt.rolling(self.beta_window).var()

        resid_cum: dict[str, pd.Series] = {}
        resid_vol: dict[str, pd.Series] = {}
        for inst in rets.columns:
            beta = rets[inst].rolling(self.beta_window).cov(mkt) / var_m
            resid = rets[inst] - beta * mkt
            # shift(skip) then accumulate => a 12-1 window ending `skip` bars ago.
            # The vol denominator MUST use the same shifted window: leaving it unshifted
            # let a spike inside the skip window inflate the denominator and move the
            # score, which silently defeats the 12-1 control the numerator implements.
            shifted = resid.shift(skip)
            resid_cum[inst] = shifted.rolling(lookback).sum()
            resid_vol[inst] = shifted.rolling(lookback).std(ddof=1) * np.sqrt(252)

        rv = pd.DataFrame(resid_vol)
        # Standardising by the residual's OWN vol is the step Blitz et al. identify as
        # responsible for the volatility reduction — not the residualisation alone.
        self._scores = (pd.DataFrame(resid_cum) / rv.where(rv > 0)).sort_index()

        ann_vol = np.log(close).diff().rolling(vol_window).std(ddof=1) * np.sqrt(252)
        self._ann_vol = ann_vol
        self._cache: dict[pd.Timestamp, dict] = {}

    @staticmethod
    def _norm_t(t) -> pd.Timestamp:
        ts = pd.Timestamp(t)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    def ranks_at(self, t) -> dict[str, float]:
        """``{instrument: z_score}`` for the top-N residual-momentum names at ``t``.

        Instruments outside the top N are absent. Empty when the live cross-section is
        smaller than ``min_universe``. Cached per timestamp.
        """
        t = self._norm_t(t)
        cached = self._cache.get(t)
        if cached is not None:
            return cached

        result: dict[str, float] = {}
        if t in self._scores.index:
            row = self._scores.loc[t].dropna()
            n = len(row)
            if n >= self.min_universe:
                mu = float(row.mean())
                sd = float(row.std(ddof=1)) or 1.0
                for inst in row.sort_values(ascending=False).index[: self.top_n]:
                    result[inst] = (float(row[inst]) - mu) / sd
        self._cache[t] = result
        return result

    def signal_for(self, instrument: str, t) -> Signal:
        z = self.ranks_at(t).get(instrument)
        if z is None:
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=self.reward_risk, timeframe=self.timeframe,
                rationale="residual momentum: not in top-N",
            )
        # Bounded, deliberately modest probability. Residual momentum's hit rate is a
        # relative-strength edge, not a forecast — the measured monthly win rate on this
        # data is ~63%, so the band stays tight rather than flattering the signal.
        p = float(np.clip(0.52 + 0.05 * abs(z), 0.52, 0.70))
        return Signal(
            instrument=instrument, direction=Direction.LONG, probability=p,
            reward_risk=self.reward_risk, confidence=float(min(1.0, abs(z) / 2.0)),
            timeframe=self.timeframe,
            rationale=f"residual momentum LONG | z={z:+.2f} | p={p:.2f}",
        )

    def strategies(self) -> dict[str, "ResidualMomentumStrategy"]:
        """Per-instrument adapters sharing this model — hand straight to
        ``PortfolioBacktester.run(pits, strategies)``."""
        return {inst: ResidualMomentumStrategy(self, inst) for inst in self.instruments}


class ResidualMomentumStrategy(Strategy):
    """Per-instrument view of a shared :class:`ResidualMomentum` model.

    Stateless and rule-based (no fit): the rank is a deterministic function of the
    point-in-time cross-section, so there is nothing to calibrate.
    """

    name = "residual_momentum"

    def __init__(self, model: ResidualMomentum, instrument: str) -> None:
        self.model = model
        self.instrument = instrument
        self.holding_horizon = model.holding_horizon
        self.reward_risk = model.reward_risk
        self.timeframe = model.timeframe

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        return self.model.signal_for(instrument or self.instrument, t)
