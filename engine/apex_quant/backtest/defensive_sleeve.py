"""Defensive cash-substitute sleeve (2026-07-27; prereg
engine/data_store/defensive_sleeve_prereg.md).

The certified book's regime filter parks a large share of equity in zero-yield GBP
cash. This module describes a *cash-substitute* overlay: while the flag is set, the
book's idle capital — `max(0, equity - gross open notional)` at each daily mark —
accrues the sleeve's daily returns instead of 0%, less one-way rebalance costs.

The spec is a pure data object (leg close series + mix rule + costs). The
:meth:`DefensiveSleeveSpec.align` precompute is a deterministic, point-in-time
function of the leg closes and the book's union timeline — leg prices are
forward-filled across non-trading days (0 return), and before a leg's first bar its
return accrues 0% (cash). ``None`` passed to ``PortfolioBacktester`` keeps the
certified zero-yield-cash behaviour byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class DefensiveSleeveSpec:
    """A defensive sleeve: named legs, a daily mix rule, and one-way costs.

    mode:
      * ``"static"`` — ``static_weights`` every bar (a leg without data accrues 0%,
        i.e. its share sits in cash; weights are NOT renormalised).
      * ``"inverse_vol"`` — ``x_leg(t) ∝ 1/σ_leg(t)`` with σ the trailing
        ``vol_window``-day std (ddof=1) of the leg's aligned daily returns. A leg
        with fewer than ``vol_window`` valid returns gets weight 0 and the other
        legs renormalise to 1; if no leg is valid the sleeve is all cash.
    """

    closes: dict[str, pd.Series]
    mode: str = "static"
    static_weights: dict[str, float] = field(default_factory=dict)
    vol_window: int = 63
    oneway_cost: dict[str, float] = field(default_factory=dict)

    def align(self, timeline: pd.DatetimeIndex) -> dict:
        """Per-bar leg returns and sleeve mix on the book's union timeline.

        Returns ``{"ret": {leg: np.ndarray}, "mix": {leg: np.ndarray}}`` where
        ``ret`` is NaN-free (no data => 0.0 => cash) and ``mix`` rows sum to the
        invested sleeve fraction (1 for static with full weights; possibly 0 for
        inverse-vol when no leg has a valid vol — all cash).
        """
        legs = list(self.closes)
        rets: dict[str, pd.Series] = {}
        ret: dict[str, np.ndarray] = {}
        for leg in legs:
            px = self.closes[leg].reindex(timeline, method="ffill")
            r = px.pct_change()
            rets[leg] = r
            ret[leg] = r.fillna(0.0).to_numpy(dtype=float)

        mix: dict[str, np.ndarray] = {}
        if self.mode == "static":
            for leg in legs:
                mix[leg] = np.full(len(timeline), float(self.static_weights.get(leg, 0.0)))
        elif self.mode == "inverse_vol":
            df = pd.DataFrame(rets)
            vol = df.rolling(self.vol_window, min_periods=self.vol_window).std(ddof=1)
            inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan).fillna(0.0)
            inv_sum = inv.sum(axis=1)
            w = inv.div(inv_sum.where(inv_sum > 0.0), axis=0).fillna(0.0)
            for leg in legs:
                mix[leg] = w[leg].to_numpy(dtype=float)
        else:
            raise ValueError(f"unknown defensive sleeve mode {self.mode!r}")
        return {"ret": ret, "mix": mix}
