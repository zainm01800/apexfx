"""Short-term reversal sleeve on screened US large caps (long-only, weekly).

The academic short-term reversal (Lehmann 1990; Jegadeesh 1990) is NOT an
overreaction fairytale — it is the return to providing liquidity. Nagel (2012,
RFS, "Evaporating Liquidity") shows reversal profits are liquidity-provision
returns: they spike exactly when aggregate volatility is high and liquidity
demanders pay most for immediacy (2008-09, 2011, COVID). That makes the sleeve
a candidate CRISIS-ALPHA complement to a trend book: trend makes its money in
persistent moves, reversal in the weeks when trends break. de Groot, Huij &
Zhou (2012, JBF, "Another look at trading costs and short-term reversal
profits") deliver the honesty: raw weekly reversal is a paper edge that costs
kill, but a cost-aware construction — fewer, more liquid names, trading only on
statistically significant moves — keeps 30-50bps/week net on the large-liquid
US universe. Long-only retail expectation is therefore modest (honest range
0.3-0.5 net Sharpe, crisis-concentrated) but with expected NEGATIVE correlation
to trend — the diversification, not the standalone Sharpe, is the claim.

Design choices (all pre-registered in engine/data_store/st_reversal_prereg.md):

  * Weekly rebalance: signals emitted only on the last bar of each ISO week on
    the union index (detected from the index, gap-safe — same convention as
    strategies/crypto_xs_momentum.py), filled at the next bar's open.
    holding_horizon=5 is the weekly time-stop; under managed exits winners can
    run past it (the deployable sleeve, not the academic fixed-horizon bet).
  * Rank by the RAW trailing ``formation``-bar return (5 or 10 daily bars) and
    BUY the bottom bucket — the biggest losers, long-only. No short leg: the
    documented liquidity-provision premium is roughly symmetric but the retail
    short leg is inaccessible/expensive, and the long side is the halal-
    compliant one.
  * ``filter_mode="cost"`` (de Groot et al. construction): bottom-2 instead of
    bottom-3, and a name is only eligible when (a) its |formation return|
    exceeds ``sig_mult`` x its 20d realised daily vol scaled to the formation
    horizon (a 1.5-sigma move filter — trade only on significant moves), and
    (b) it sits in the liquid half of the universe (20d median dollar volume
    >= cross-sectional median at t). Turnover reduction is the whole point.
  * ``filter_mode="vol_state"`` (Nagel construction): stand down entirely when
    the market's own realised vol is LOW — SPY 21d realised vol below its 126d
    rolling median. If the edge is vol-state-conditional liquidity provision,
    most of the P&L should live in the high-vol half.
  * Universe discipline: ``min_history=300`` bars before a name is rankable
    (late listings like PLTR/UBER never enter on hype alone), ``min_universe``
    eligible names required or the sleeve is flat. The ``regime_instrument``
    (SPY) feeds the vol-state filter only and is never traded by the sleeve.

Leakage safety: identical discipline to cross_sectional.py — scores, vol and
liquidity estimates, the SPY vol-state flag and rebalance-bar detection are all
backward-only rolling functions of the panel, so the rank at bar ``t`` depends
only on data ``<= t``. One shared model serves the whole universe; thin per-
instrument adapters plug it into
:class:`~apex_quant.backtest.portfolio.PortfolioBacktester`. Rule-based and
stateless: nothing to fit, so CPCV's train split is a no-op and the overfitting
risk lives entirely in config selection — which is what DSR/PBO gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy


class ShortTermReversal:
    """Shared weekly-rebalanced bottom-N reversal model over an equity panel.

    Parameters
    ----------
    panel :
        ``{instrument: OHLCV DataFrame}`` — the full history for each instrument.
        Must include ``regime_instrument`` when ``filter_mode="vol_state"``.
    formation :
        Bars over which the reversal return is measured (5 = one trading week —
        the classic weekly sort; 10 = two weeks).
    vol_window :
        Bars for the realised-vol estimate used by the cost-mode significance
        filter (20 ≈ one month).
    bottom_n :
        Number of instruments to hold long (the biggest losers).
    min_universe :
        Minimum instruments with a valid score before any signal is emitted —
        bottom-3 out of 4 is not a cross-section. Below it the sleeve is flat.
    min_history :
        Bars of history an instrument needs before it is rankable.
    filter_mode :
        ``"plain"`` (bottom-N, no extra gates), ``"cost"`` (de Groot et al.:
        significance + liquidity filters), or ``"vol_state"`` (Nagel: trade
        only when SPY realised vol is above its rolling median).
    sig_mult :
        Cost mode: a name is eligible only if |formation return| >
        ``sig_mult`` x 20d daily vol x sqrt(formation) — a 1.5-sigma move.
    regime_instrument :
        The market proxy whose realised vol defines the vol state (SPY). It is
        excluded from the tradable cross-section.
    mkt_vol_window, mkt_median_window :
        Vol-state mode: SPY realised-vol window (21) and the rolling-median
        window that defines "high vol" (126 ≈ 6 months).
    reward_risk, holding_horizon, timeframe :
        Passed through onto the emitted signals / consumed by the backtester
        (holding_horizon=5 is the weekly time-stop).
    """

    def __init__(
        self,
        panel: dict[str, pd.DataFrame],
        *,
        formation: int = 5,
        vol_window: int = 20,
        bottom_n: int = 3,
        min_universe: int = 10,
        min_history: int = 300,
        filter_mode: str = "plain",
        sig_mult: float = 1.5,
        regime_instrument: str = "SPY",
        mkt_vol_window: int = 21,
        mkt_median_window: int = 126,
        reward_risk: float = 1.5,
        holding_horizon: int = 5,
        timeframe: str = "1d",
    ) -> None:
        if filter_mode not in ("plain", "cost", "vol_state"):
            raise ValueError(f"filter_mode must be plain|cost|vol_state, got {filter_mode!r}")
        if bottom_n < 1:
            raise ValueError("bottom_n must be >= 1")
        if formation < 1 or vol_window < 2:
            raise ValueError("need formation >= 1 and vol_window >= 2")
        if filter_mode == "vol_state" and regime_instrument not in panel:
            raise ValueError(f"vol_state mode needs {regime_instrument} in the panel")
        self.formation = formation
        self.vol_window = vol_window
        self.bottom_n = bottom_n
        self.min_universe = max(bottom_n + 1, min_universe)
        self.min_history = max(1, min_history)
        self.filter_mode = filter_mode
        self.sig_mult = sig_mult
        self.regime_instrument = regime_instrument
        self.mkt_vol_window = mkt_vol_window
        self.mkt_median_window = mkt_median_window
        self.reward_risk = reward_risk
        self.holding_horizon = holding_horizon
        self.timeframe = timeframe
        # The regime instrument feeds the vol-state filter only — never traded.
        self.instruments = [i for i in panel.keys() if i != regime_instrument]

        # Raw formation-bar reversal score per instrument, aligned on the union
        # index. Rolling windows are backward-looking, so row t uses only bars
        # <= t. An instrument is rankable only once it carries min_history bars.
        scores: dict[str, pd.Series] = {}
        self._sig_thresh: dict[str, pd.Series] = {}
        self._dollar_vol: dict[str, pd.Series] = {}
        for inst in self.instruments:
            df = panel[inst]
            c = df["close"]
            score = c / c.shift(formation) - 1.0
            score[c.notna().cumsum() < self.min_history] = np.nan
            scores[inst] = score
            if self.filter_mode == "cost":
                vol = np.log(c).diff().rolling(vol_window).std(ddof=1)
                self._sig_thresh[inst] = self.sig_mult * vol * np.sqrt(formation)
                self._dollar_vol[inst] = (c * df["volume"]).rolling(vol_window).median()
        self._scores = pd.DataFrame(scores).sort_index()
        idx = self._scores.index
        self._sig_thresh = {k: v.reindex(idx) for k, v in self._sig_thresh.items()}
        self._dollar_vol = pd.DataFrame(
            {k: v.reindex(idx) for k, v in self._dollar_vol.items()}
        ).sort_index() if self._dollar_vol else pd.DataFrame(index=idx)

        # Rebalance bars: the last bar of each ISO week ON THE UNION INDEX
        # (detected from the index rather than a weekday constant — gap-safe).
        iso = idx.isocalendar()
        week_key = (iso["year"] * 100 + iso["week"]).to_numpy()
        self._rebalance: set[pd.Timestamp] = set(idx[np.append(week_key[:-1] != week_key[1:], True)])

        # Vol state: SPY realised vol vs its own rolling median, backward-only.
        if self.filter_mode == "vol_state":
            sc = panel[self.regime_instrument]["close"]
            mkt_vol = np.log(sc).diff().rolling(self.mkt_vol_window).std(ddof=1)
            med = mkt_vol.rolling(self.mkt_median_window).median()
            self._vol_high = (mkt_vol >= med).reindex(idx).fillna(False)
        else:
            self._vol_high = pd.Series(True, index=idx)

        self._cache: dict[pd.Timestamp, dict] = {}

    @staticmethod
    def _norm_t(t) -> pd.Timestamp:
        ts = pd.Timestamp(t)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    def ranks_at(self, t) -> dict[str, tuple[int, float]]:
        """Return ``{instrument: (direction, z_score)}`` for the bottom bucket at
        ``t`` (+1 long; there is no short leg). Empty on non-rebalance bars, when
        the vol-state filter is off, or when the eligible cross-section is too
        thin. Instruments not selected are absent. Cached."""
        t = self._norm_t(t)
        cached = self._cache.get(t)
        if cached is not None:
            return cached

        result: dict[str, tuple[int, float]] = {}
        if (
            t in self._scores.index
            and t in self._rebalance
            and bool(self._vol_high.get(t, False))
        ):
            row = self._scores.loc[t].dropna()
            n = len(row)
            if n >= self.min_universe:
                eligible = row.index
                if self.filter_mode == "cost":
                    thr = self._sig_thresh_at(t, row.index)
                    liq = self._liquid_at(t, row.index)
                    eligible = [i for i in row.index
                                if abs(float(row[i])) > float(thr.get(i, np.nan))
                                and i in liq]
                if len(eligible) > 0:
                    mu = float(row.mean())
                    sd = float(row.std(ddof=1)) or 1.0
                    ordered = row[eligible].sort_values(ascending=True)  # losers first
                    for inst in ordered.index[: self.bottom_n]:
                        result[inst] = (1, (float(row[inst]) - mu) / sd)
        self._cache[t] = result
        return result

    def _sig_thresh_at(self, t: pd.Timestamp, instruments) -> dict[str, float]:
        return {i: float(self._sig_thresh[i].get(t, np.nan)) for i in instruments}

    def _liquid_at(self, t: pd.Timestamp, instruments) -> set[str]:
        """The liquid half of the cross-section at ``t``: 20d median dollar
        volume >= the cross-sectional median (backward-only)."""
        dv = self._dollar_vol.loc[t, list(instruments)].dropna()
        if dv.empty:
            return set()
        med = float(dv.median())
        return set(dv.index[dv >= med])

    def signal_for(self, instrument: str, t) -> Signal:
        entry = self.ranks_at(t).get(instrument)
        if entry is None:
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=self.reward_risk, timeframe=self.timeframe,
                rationale="st-reversal: not a weekly-rebalance bottom-bucket bar",
            )
        _d, z = entry
        # Loser-bucket conviction -> a bounded, honest probability. Short-term
        # reversal's real hit-rate is modest, so the band is tight (same mapping
        # as cross_sectional.py / crypto_xs_momentum.py).
        p = float(np.clip(0.52 + 0.05 * abs(z), 0.52, 0.70))
        return Signal(
            instrument=instrument, direction=Direction.LONG, probability=p,
            reward_risk=self.reward_risk, confidence=float(min(1.0, abs(z) / 2.0)),
            timeframe=self.timeframe,
            rationale=f"st-reversal LONG | z={z:+.2f} | p={p:.2f}",
        )

    def strategies(self) -> dict[str, "ShortTermReversalStrategy"]:
        """One per-instrument adapter for every tradable instrument, all sharing
        this model — ready to hand straight to
        ``PortfolioBacktester.run(pits, strategies)``."""
        return {inst: ShortTermReversalStrategy(self, inst) for inst in self.instruments}


class ShortTermReversalStrategy(Strategy):
    """Per-instrument view of a shared :class:`ShortTermReversal` model.

    Stateless and rule-based (no fit): the rank is a deterministic function of
    the point-in-time cross-section, so there are no parameters to calibrate.
    """

    name = "st_reversal"

    def __init__(self, model: ShortTermReversal, instrument: str) -> None:
        self.model = model
        self.instrument = instrument
        self.holding_horizon = model.holding_horizon
        self.reward_risk = model.reward_risk
        self.timeframe = model.timeframe

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        return self.model.signal_for(instrument or self.instrument, t)
