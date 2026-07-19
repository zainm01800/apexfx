"""Post-Earnings-Announcement-Drift (PEAD) sleeve - long-only, event-driven.

The academically defensible retail version of the anomaly (Bernard & Thomas 1989,
JAE; Chordia, Goh, Lee & Tan 2014, JAE): after a POSITIVE earnings surprise the
drift continues for days-to-weeks. Chordia et al. put the attenuated premium at
~0.14%/month in the most liquid names vs ~1.60%/month in the least liquid, so the
honest long-only US retail estimate is a net Sharpe of 0.4-0.6 - and only the LONG
side is robust (docs/audits Task B). Hence: long-only, liquid US names, halal
business-activity screen (no banks/financials), no short leg (halal + borrow
constraints), fixed-horizon exits (the academic bet; see below).

Event dates come from the SEC EDGAR 8-K Item 2.02 cache built by
scripts/build_earnings_calendar.py (FMP's historical earnings calendar was
unavailable engine-side - the key lives only in the Vercel deployment; Yahoo and
Finnhub are forward-looking only). EDGAR gives the announcement DATE but no
BMO/AMC flag and no analyst estimates, so the surprise proxy is price-based, not
SUE: the 2-day announcement-window return

    ann_ret = close(T1) / close(T0-1) - 1

where T0 = first trading day >= the filing date and T1 = the next trading day. The
2-day window spans both BMO reactions (on T0) and AMC reactions (gap into T1)
without needing the flag; the cost is that ~1-1.5 days of drift are systematically
sacrificed before entry. Positive surprise := ann_ret >= +2% (a ~1.5-2 sigma 2-day
move for mega-caps - a real surprise, not noise). The signal is emitted at T1's
close and filled at the next bar's open - entry ~1 bar after the surprise, all
inputs known at decision time (no look-ahead).

Design choices (all pre-registered in engine/data_store/pead_prereg.md):

  * Fixed-horizon exits, NOT the managed TMS stack: the PEAD premium is the
    N-day drift, so the position is held exactly ``holding_horizon`` bars
    (grid 5/10/20) and time-stopped. Implemented as exit_mode="barrier" with a
    catastrophic-only barrier pair on the signal (-30% / +200%): stop/target
    that should essentially never bind within 20 trading days for these names,
    leaving the time-stop as the exit. Verified in the gate's exit-reason tally.
  * reward_risk on the signal is the honest expected-payoff ratio of a drift
    trade (~1.5), used by the RiskManager's fractional-Kelly sizing - NOT the
    barrier geometry. Position risk is capped by the standard config caps
    (max_risk_per_trade 2%, max_portfolio_risk 6.5%, gross 3x), which bind.
  * Optional drift-quality filter (grid variant): keep only events whose gap-day
    volume is above the trailing 63-day median - max(vol(T0), vol(T1)) /
    median(vol, 63 bars ending the day before T0) > 1. High-volume surprises
    drift harder (the reaction is real, not a thin-tape artefact).
  * Optional market-adjustment (grid variant): ann_ret minus SPY's matched 2-day
    window return, so a bull-week beta surge cannot masquerade as an earnings
    surprise.
  * probability is a bounded, honest conviction map from surprise size:
    clip(0.53 + 0.4 * (ann_ret - threshold), 0.53, 0.60) - conditional positive-
    drift hit rates in the literature are ~55-60% for large surprises (same
    mapping philosophy as cross_sectional.py / crypto_xs_momentum.py).

Leakage safety: identical discipline to crypto_xs_momentum.py - every quantity is
a backward-only function of the panel and the static filing-date cache, so the
signal at bar t depends only on data <= t. Rule-based and stateless: nothing to
fit, CPCV's train split is a no-op, and the overfitting risk lives entirely in
config selection - which is what DSR/PBO gate. One shared model serves the whole
universe; thin per-instrument adapters plug it into PortfolioBacktester.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy


class PeadBook:
    """Shared event-driven PEAD model over an equity panel.

    Parameters
    ----------
    panel :
        ``{instrument: OHLCV DataFrame}`` - full history per instrument.
    events :
        ``{instrument: [announcement dates]}`` - SEC EDGAR 8-K/2.02 filing dates
        (``datetime.date``/``str``/``Timestamp``); the earnings-release dates.
    gap_threshold :
        Minimum 2-day announcement-window return counting as a positive surprise
        (0.02 = +2%).
    holding_horizon :
        Fixed holding period in trading days (the academic PEAD bet; 5/10/20 grid).
    volume_filter :
        If True, keep only events whose gap-day volume exceeds the trailing
        63-day median (drift-quality filter).
    market_adjust :
        If True, subtract ``market``'s matched 2-day window return from ann_ret.
    market :
        Market-proxy close series (SPY) aligned by timestamp; required iff
        ``market_adjust``.
    vol_median_window :
        Trailing window for the volume median (63 bars).
    min_history :
        Bars an instrument needs before an event is tradable (vol-median sanity).
    stop_pct, target_pct :
        Catastrophic-only barrier pair on the emitted signal (-30% / +200%) -
        the real exit is the holding_horizon time-stop (barrier mode).
    reward_risk, timeframe :
        Honest expected-payoff ratio for Kelly sizing / passed through to signals.
    """

    def __init__(
        self,
        panel: dict[str, pd.DataFrame],
        *,
        events: dict[str, list],
        gap_threshold: float = 0.02,
        holding_horizon: int = 10,
        volume_filter: bool = False,
        market_adjust: bool = False,
        market: pd.Series | None = None,
        vol_median_window: int = 63,
        min_history: int = 70,
        stop_pct: float = 0.30,
        target_pct: float = 2.00,
        reward_risk: float = 1.5,
        timeframe: str = "1d",
    ) -> None:
        if gap_threshold <= 0:
            raise ValueError("gap_threshold must be > 0")
        if holding_horizon < 1:
            raise ValueError("holding_horizon must be >= 1")
        if market_adjust and market is None:
            raise ValueError("market_adjust requires a market close series")
        self.gap_threshold = gap_threshold
        self.holding_horizon = holding_horizon
        self.volume_filter = volume_filter
        self.market_adjust = market_adjust
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.reward_risk = reward_risk
        self.timeframe = timeframe
        self.instruments = list(panel.keys())

        mkt = None
        if market_adjust:
            mkt = market.copy()
            mkt.index = pd.DatetimeIndex(mkt.index)

        # Precompute the signal bar (T1) per event per instrument. Every input is
        # backward-only relative to T1, so the signal map is leak-free by row.
        self._signals: dict[str, dict[pd.Timestamp, float]] = {}
        self.n_events = 0          # events with a computable window
        self.n_qualifying = 0      # events passing all filters
        for inst, df in panel.items():
            inst_events = events.get(inst, [])
            sigs: dict[pd.Timestamp, float] = {}
            if inst_events and len(df) >= min_history + 2:
                close = df["close"]
                vol = df["volume"] if "volume" in df else pd.Series(0.0, index=df.index)
                med = vol.rolling(vol_median_window).median()
                idx = df.index
                # bar position of the first bar >= each candidate date, vectorised
                for d in inst_events:
                    ts_d = pd.Timestamp(d)
                    ts_d = (ts_d.tz_localize("UTC") if ts_d.tzinfo is None
                            else ts_d.tz_convert("UTC"))
                    i0 = idx.searchsorted(ts_d)          # T0 = first bar >= filing date
                    if i0 < max(1, min_history) or i0 + 1 >= len(idx):
                        continue                         # need T0-1 close and a T1 bar
                    t0, t1 = idx[i0], idx[i0 + 1]
                    ann = float(close.iloc[i0 + 1] / close.iloc[i0 - 1] - 1.0)
                    if mkt is not None:
                        # SPY's matched 2-day window (its own bars bracketing T0/T1)
                        j1 = mkt.index.searchsorted(t1)
                        j0 = mkt.index.searchsorted(t0)
                        if j1 >= len(mkt) or j0 < 1:
                            continue
                        ann -= float(mkt.iloc[j1] / mkt.iloc[j0 - 1] - 1.0)
                    self.n_events += 1
                    if ann < self.gap_threshold:
                        continue
                    if volume_filter:
                        med_ref = float(med.iloc[i0 - 1]) if np.isfinite(med.iloc[i0 - 1]) else 0.0
                        gap_vol = float(max(vol.iloc[i0], vol.iloc[i0 + 1]))
                        if med_ref <= 0 or gap_vol <= med_ref:
                            continue
                    sigs[t1] = ann
                    self.n_qualifying += 1
            self._signals[inst] = sigs

    @staticmethod
    def _norm_t(t) -> pd.Timestamp:
        ts = pd.Timestamp(t)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    def signal_for(self, instrument: str, t, decision_price: float | None = None) -> Signal:
        """LONG at the close of the announcement window's second bar (filled at the
        next bar's open). FLAT everywhere else - PEAD is sparse by construction."""
        t = self._norm_t(t)
        ann = self._signals.get(instrument, {}).get(t)
        if ann is None:
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=self.reward_risk, timeframe=self.timeframe,
                rationale="pead: no qualifying earnings event",
            )
        px = float(decision_price) if decision_price else np.nan
        p = float(np.clip(0.53 + 0.4 * (ann - self.gap_threshold), 0.53, 0.60))
        return Signal(
            instrument=instrument, direction=Direction.LONG, probability=p,
            reward_risk=self.reward_risk,
            confidence=float(np.clip(ann / 0.08, 0.0, 1.0)),
            timeframe=self.timeframe,
            stop_price=(px * (1.0 - self.stop_pct)) if np.isfinite(px) else None,
            target_price=(px * (1.0 + self.target_pct)) if np.isfinite(px) else None,
            rationale=f"pead LONG | ann_ret={ann * 100:+.1f}% | p={p:.2f}",
        )

    def strategies(self) -> dict[str, "PeadBookStrategy"]:
        """One per-instrument adapter for every instrument, all sharing this model -
        ready to hand straight to ``PortfolioBacktester.run(pits, strategies)``."""
        return {inst: PeadBookStrategy(self, inst) for inst in self.instruments}


class PeadBookStrategy(Strategy):
    """Per-instrument view of a shared :class:`PeadBook` model.

    Stateless and rule-based (no fit): the signal map is a deterministic,
    backward-only function of the panel and the static filing-date cache.
    """

    name = "pead"

    def __init__(self, model: PeadBook, instrument: str) -> None:
        self.model = model
        self.instrument = instrument
        self.holding_horizon = model.holding_horizon
        self.reward_risk = model.reward_risk
        self.timeframe = model.timeframe

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        inst = instrument or self.instrument
        # Decision price = close at t (PIT-safe); anchors the catastrophic barrier.
        px = None
        try:
            px = float(pit.as_of(t)["close"].iloc[-1])
        except (IndexError, KeyError, TypeError):
            px = None
        return self.model.signal_for(inst, t, decision_price=px)
