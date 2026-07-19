"""Crypto cross-sectional momentum sleeve (Sleeve E of the 2026-07-18 research stack).

The one documented crypto-universe edge a spot-only retail book can actually reach
(docs/research/2026-07-18_beating_sharpe_1_2.md, section 1): weekly-rebalanced
cross-sectional momentum over the top ~10-30 liquid coins, LONG-ONLY top bucket vs
cash/stablecoin. Short-horizon (1-4 week) momentum is statistically significant on
large/liquid coins (Liu et al. 2022 J. Finance; Jia et al. 2022; Dobrynskaya 2023);
long-horizon (6-12m) is not (Grobys & Sapkota 2019); the small-coin "reversal"
literature is a bid-ask-bounce/illiquidity artefact that does not apply to a
top-11 liquid universe. Post-2021 honesty (Springer, RQFA 2025): crypto momentum is
EPISODIC — it has moments — so the honest net estimate is 0.4-0.8, regime-dependent,
and only viable with vol management. Hence the optional regime filter: hold only
when the asset class itself (BTC) is trending up — the documented momentum-crash
protection (Daniel & Moskowitz 2016 logic: momentum crashes in post-panic rebounds;
in crypto the common factor IS the crash).

Design choices (all pre-registered in engine/data_store/crypto_xs_prereg.md):

  * Weekly rebalance, not 21-bar: the literature edge is specifically the WEEKLY
    rebalanced sort. Crypto trades 365 days/yr, so a week is a clean ISO week on
    the daily union index: signals are emitted only on the LAST bar of each ISO
    week (detected from the index itself, not a hardcoded weekday — gap-safe),
    filled at the next bar's open. holding_horizon=7 is the TradeManager
    time-stop, so positions roll roughly weekly under the engine's managed exits
    (winners can run past 7 bars; that is the deployable sleeve, not the academic
    fixed-horizon bet — same convention as every prior book gate).
  * Rank by vol-scaled momentum (lookback return / 63d realised vol), the same
    score shape as strategies/cross_sectional.py — vol-equalised ranks so a 100%-vol
    alt does not win the sort on noise alone.
  * Top-3 long-only, NO short leg: spot-only retail access (Coinbase/Kraken), and
    the documented edge is the top bucket vs stablecoin, not the long-short.
  * min_history=300 bars before an instrument is rankable (same floor as
    run_backtests.py): late listings (ARB, SUI) enter the cross-section only once
    they have a real history, never on their first hype month.
  * Sizing: the standard RiskManager vol-scaled path (equal-conviction signals in
    the top bucket -> risk-parity-ish across the 3 names), config risk caps
    binding — identical to every prior portfolio gate.

Leakage safety: identical discipline to cross_sectional.py — scores, eligibility
counts, the regime flag and rebalance-bar detection are all backward-only functions
of the panel, so the rank at bar ``t`` depends only on data ``<= t``. One shared
model serves the whole universe; thin per-instrument adapters plug it into
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


class CryptoXsMomentum:
    """Shared weekly-rebalanced top-N momentum model over a crypto panel.

    Parameters
    ----------
    panel :
        ``{instrument: OHLCV DataFrame}`` — the full history for each instrument.
    lookback :
        Bars over which the momentum return is measured (21 ≈ 3 weeks of dailies —
        the centre of the documented 1-4-week effect).
    vol_window :
        Bars for the realised-vol scaling of the momentum score.
    top_n :
        Number of instruments to hold long (the top bucket).
    min_universe :
        Minimum eligible instruments with a valid score before any signal is
        emitted — top-3 out of 3 is not a cross-section. Below it the sleeve is
        flat (e.g. the BTC-only 2016-2019 era produces no trades).
    min_history :
        Bars of history an instrument needs before it is rankable.
    regime_filter :
        If True, hold only while the asset-class trend is up:
        ``regime_instrument``'s ``regime_lookback``-bar return > 0. If the regime
        instrument has no valid reading at ``t``, the filter fails closed (flat).
    regime_instrument, regime_lookback :
        The common-factor proxy (BTC/USD) and its trend window (63 ≈ 3 months).
    reward_risk, holding_horizon, timeframe :
        Passed through onto the emitted signals / consumed by the backtester
        (holding_horizon=7 is the weekly time-stop).
    """

    def __init__(
        self,
        panel: dict[str, pd.DataFrame],
        *,
        lookback: int = 21,
        vol_window: int = 63,
        top_n: int = 3,
        min_universe: int = 4,
        min_history: int = 300,
        regime_filter: bool = True,
        regime_instrument: str = "BTC/USD",
        regime_lookback: int = 63,
        reward_risk: float = 1.5,
        holding_horizon: int = 7,
        timeframe: str = "1d",
    ) -> None:
        if top_n < 1:
            raise ValueError("top_n must be >= 1")
        if lookback < 1 or vol_window < 2:
            raise ValueError("need lookback >= 1 and vol_window >= 2")
        self.lookback = lookback
        self.vol_window = vol_window
        self.top_n = top_n
        self.min_universe = max(top_n + 1, min_universe)
        self.min_history = max(1, min_history)
        self.regime_filter = regime_filter
        self.regime_instrument = regime_instrument
        self.regime_lookback = regime_lookback
        self.reward_risk = reward_risk
        self.holding_horizon = holding_horizon
        self.timeframe = timeframe
        self.instruments = list(panel.keys())

        # Vol-scaled momentum score per instrument, aligned on the union index.
        # Rolling windows are backward-looking, so row t uses only bars <= t.
        # An instrument is rankable only once it carries min_history bars.
        scores: dict[str, pd.Series] = {}
        for inst, df in panel.items():
            c = df["close"]
            ret = c / c.shift(lookback) - 1.0
            vol = np.log(c).diff().rolling(vol_window).std(ddof=1)
            score = ret / vol.where(vol > 0)
            score[c.notna().cumsum() < self.min_history] = np.nan
            scores[inst] = score
        self._scores = pd.DataFrame(scores).sort_index()

        # Rebalance bars: the last bar of each ISO week ON THE UNION INDEX (crypto
        # trades 365/yr, so weeks are complete 7-day spans; detecting from the
        # index rather than a weekday constant is gap-safe).
        idx = self._scores.index
        iso = idx.isocalendar()
        week_key = (iso["year"] * 100 + iso["week"]).to_numpy()
        self._rebalance: set[pd.Timestamp] = set(idx[np.append(week_key[:-1] != week_key[1:], True)])

        # Asset-class regime: the common factor's own trend, backward-only.
        if regime_filter and self.regime_instrument in panel:
            rc = panel[self.regime_instrument]["close"]
            up = (rc / rc.shift(self.regime_lookback) - 1.0) > 0.0
            self._regime_up = up.reindex(idx).fillna(False)
        else:
            self._regime_up = pd.Series(True, index=idx)

        self._cache: dict[pd.Timestamp, dict] = {}

    @staticmethod
    def _norm_t(t) -> pd.Timestamp:
        ts = pd.Timestamp(t)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    def ranks_at(self, t) -> dict[str, tuple[int, float]]:
        """Return ``{instrument: (direction, z_score)}`` for the top bucket at ``t``
        (+1 long; there is no short leg). Empty on non-rebalance bars, when the
        regime filter is down, or when the eligible cross-section is too thin.
        Instruments not selected are absent. Cached."""
        t = self._norm_t(t)
        cached = self._cache.get(t)
        if cached is not None:
            return cached

        result: dict[str, tuple[int, float]] = {}
        if (
            t in self._scores.index
            and t in self._rebalance
            and bool(self._regime_up.get(t, False))
        ):
            row = self._scores.loc[t].dropna()
            n = len(row)
            if n >= self.min_universe:
                ordered = row.sort_values(ascending=False)
                mu = float(row.mean())
                sd = float(row.std(ddof=1)) or 1.0
                for inst in ordered.index[: self.top_n]:
                    result[inst] = (1, (float(row[inst]) - mu) / sd)
        self._cache[t] = result
        return result

    def signal_for(self, instrument: str, t) -> Signal:
        entry = self.ranks_at(t).get(instrument)
        if entry is None:
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=self.reward_risk, timeframe=self.timeframe,
                rationale="crypto-xs: not a weekly-rebalance top-bucket bar",
            )
        _d, z = entry
        # Relative-strength conviction -> a bounded, honest probability. Cross-
        # sectional momentum's real hit-rate is modest, so the band is tight
        # (same mapping as cross_sectional.py).
        p = float(np.clip(0.52 + 0.05 * abs(z), 0.52, 0.70))
        return Signal(
            instrument=instrument, direction=Direction.LONG, probability=p,
            reward_risk=self.reward_risk, confidence=float(min(1.0, abs(z) / 2.0)),
            timeframe=self.timeframe,
            rationale=f"crypto-xs LONG | z={z:+.2f} | p={p:.2f}",
        )

    def strategies(self) -> dict[str, "CryptoXsMomentumStrategy"]:
        """One per-instrument adapter for every instrument, all sharing this model —
        ready to hand straight to ``PortfolioBacktester.run(pits, strategies)``."""
        return {inst: CryptoXsMomentumStrategy(self, inst) for inst in self.instruments}


class CryptoXsMomentumStrategy(Strategy):
    """Per-instrument view of a shared :class:`CryptoXsMomentum` model.

    Stateless and rule-based (no fit): the rank is a deterministic function of the
    point-in-time cross-section, so there are no parameters to calibrate.
    """

    name = "crypto_xs_momentum"

    def __init__(self, model: CryptoXsMomentum, instrument: str) -> None:
        self.model = model
        self.instrument = instrument
        self.holding_horizon = model.holding_horizon
        self.reward_risk = model.reward_risk
        self.timeframe = model.timeframe

    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        return self.model.signal_for(instrument or self.instrument, t)
