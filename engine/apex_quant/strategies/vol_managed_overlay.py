"""Vol-managed carry-trend: the carry-filtered trend signal with its exposure
scaled down when the strategy's OWN recent realised volatility runs hot, plus a
hard stand-down in vol spikes.

Research basis (docs/research/2026-07-17_fx_edges_evidence.md): Barroso &
Santa-Clara (2015, JFE) — scaling momentum exposure inversely to its own recent
realised vol ~doubled Sharpe and cut crashes; Daniel & Moskowitz (2016) —
momentum crashes cluster in post-panic high-vol rebounds. Per research report
#3's caveat (aggressive inverse-variance scaling is contested — Cederburg 2020),
this is the defensible version: a FIXED vol target (default: the proxy's own
trailing 252-day median — self-calibrating, no tuned constant), exposure only
ever DAMPED (scale ≤ 1, never levered up), and a hard stand-down when strategy
vol exceeds 1.5× its long-run median.

Proxy construction (exact; strictly point-in-time):
  * Shadow position, unit exposure: a non-FLAT base (carry-trend) signal at bar
    ``s`` puts the shadow in that direction for bars ``s+1 … s+holding_horizon``
    — a time-stop-only approximation of the managed exits (ATR stop / target /
    chandelier trails are path-dependent and are NOT replicated). A fresh
    non-FLAT base signal replaces the shadow early (mirrors re-entry after an
    early real exit); FLAT signals leave the shadow to expire (a non-signal
    never closes a real trade).
  * Shadow daily return for bar ``u`` = shadow direction during ``u`` × the
    close-to-close instrument return of ``u``.
  * proxy(t) = annualised (×√252 — daily bars, the forex class annualization)
    sample std of the last ``proxy_window`` (21) shadow daily returns dated
    STRICTLY BEFORE ``t``. Bar ``t``'s own close is never touched; the overlay
    adds no information beyond what the base signal itself uses at ``t``.
  * One-time pre-warm at the first ``generate`` call: the shadow is replayed
    over the trailing ``median_window + proxy_window + holding_horizon + 15``
    bars before ``t0`` so the overlay is active immediately instead of after a
    dead year. Each replayed signal is ``base.generate(pit, s)`` at ``s < t0``
    (point-in-time by construction) and is accepted only when the replayed
    shadow is flat — mirroring live sequencing (this strategy only signals when
    flat). The replay uses the already-fitted base exactly as the trial-matrix
    backtest does; inside CPCV folds the fit is fold-local, so the replay is
    too. Assumes chronological ``generate`` calls per instance — the engine's
    Backtester always calls chronologically, and validation builds a fresh
    instance per backtest/fold.

Scaling mechanics: signals in this engine carry NO size — the risk layer sizes
from ``probability`` via fractional Kelly. The overlay therefore remaps
``p → p' = (f·full_kelly(p,b)·b + 1) / (b + 1)``, which yields EXACTLY ``f ×``
the Kelly risk fraction at unchanged stop/target geometry (identity:
``full_kelly(p', b) == f · full_kelly(p, b)``). The risk layer stays supreme —
regime, drawdown, exposure and vol-target caps can still bind below the
request. The overlay is INERT (pass-through) until ``median_window`` valid
daily proxy values exist, and while the proxy or its median is ≤ 0.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.sizing import full_kelly
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy
from apex_quant.strategies.carry_trend_filter import CarryTrendFilter

_ANN = float(np.sqrt(252.0))  # daily bars; forex class annualization (config: 252)


class VolManagedCarryTrend(Strategy):
    """Wrap ``CarryTrendFilter``; damp by ``min(1, target/proxy)``, stand down in vol spikes."""

    name = "vol_managed_carry_trend"

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
        target_vol: float | None = None,   # None -> trailing median_window median of the proxy
        proxy_window: int = 21,
        median_window: int = 252,
        stand_mult: float = 1.5,
        stand_down: bool = True,
    ):
        self.base = CarryTrendFilter(
            momentum_lookback=momentum_lookback,
            vol_window=vol_window,
            holding_horizon=holding_horizon,
            reward_risk=reward_risk,
            regime_method=regime_method,
            timeframe=timeframe,
            rate_provider=rate_provider,
            instrument=instrument,
        )
        self.holding_horizon = holding_horizon
        self.reward_risk = reward_risk
        self.timeframe = timeframe
        self.target_vol = target_vol
        self.proxy_window = int(proxy_window)
        self.median_window = int(median_window)
        self.stand_mult = float(stand_mult)
        self.stand_down = bool(stand_down)
        self._reset_state()

    # -- internal state ---------------------------------------------------------
    def _reset_state(self) -> None:
        self._prewarmed = False
        self._sh_dir = 0              # current shadow direction (+1/-1/0)
        self._sh_expiry = -1          # last int bar position the shadow is active
        self._real_sigs: dict[int, int] = {}  # int bar position -> +1/-1 (live base signals)
        self._rets: list[float] = []          # daily shadow returns, chronological
        self._proxies: list[float] = []       # valid daily proxy values, chronological
        self._last_pos: int | None = None     # last bar position processed
        self.n_signals = 0
        self.n_scaled = 0
        self.n_standdowns = 0

    @staticmethod
    def _norm(t) -> pd.Timestamp:
        ts = pd.Timestamp(t)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    # -- training: delegate to the wrapped strategy ------------------------------
    def fit(self, pit: PointInTimeAccessor, train_timestamps: Iterable[pd.Timestamp]) -> None:
        self.base.fit(pit, train_timestamps)
        self._reset_state()  # refit => clean walk-forward state

    def is_fitted(self) -> bool:
        return self.base.is_fitted()

    # -- shadow bookkeeping -------------------------------------------------------
    def _advance_bar(self, ret: float) -> None:
        """Append one shadow daily return and refresh the running proxy."""
        self._rets.append(ret)
        if len(self._rets) >= self.proxy_window:
            sd = float(np.std(self._rets[-self.proxy_window:], ddof=1)) * _ANN
            if np.isfinite(sd):
                self._proxies.append(sd)

    def _catch_up(self, pit: PointInTimeAccessor, idx: pd.DatetimeIndex,
                  start: int, pos_t: int, instrument: str, replay: bool) -> None:
        """Process bars ``[start, pos_t)`` (strictly before ``t``) into the shadow
        series. ``replay=True`` simulates the strategy alone (signals only when the
        shadow is flat); ``replay=False`` applies the live base signals recorded at
        their bars."""
        if start > pos_t - 1:
            return
        # bars [start-1 .. pos_t-1]; close[start-1] is needed for the first return
        w = pit.window(idx[pos_t - 1], pos_t - start + 1, inclusive=True)
        closes = w["close"].to_numpy()
        base_off = start - 1
        for p in range(max(1, start), pos_t):
            if p > self._sh_expiry:
                self._sh_dir = 0  # time-stop: shadow expires holding_horizon bars after entry
            c1, c0 = closes[p - base_off], closes[p - 1 - base_off]
            self._advance_bar(self._sh_dir * (c1 / c0 - 1.0) if c0 else 0.0)
            if replay:
                if self._sh_dir == 0:
                    sig = self.base.generate(pit, idx[p], instrument)
                    if sig.direction != Direction.FLAT:
                        self._sh_dir = 1 if sig.direction == Direction.LONG else -1
                        self._sh_expiry = p + self.holding_horizon
            else:
                d = self._real_sigs.get(p)
                if d is not None:
                    self._sh_dir = d
                    self._sh_expiry = p + self.holding_horizon
        self._last_pos = pos_t - 1

    # -- inference ----------------------------------------------------------------
    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        idx = pit.timestamps()
        ts = self._norm(t)
        pos_t = int(idx.searchsorted(ts))
        if pos_t >= len(idx):
            pos_t = len(idx)  # t past the last bar: proxy from all bars (still strictly < t)

        if not self._prewarmed:
            span = self.median_window + self.proxy_window + self.holding_horizon + 15
            self._catch_up(pit, idx, max(1, pos_t - span), pos_t, instrument, replay=True)
            self._prewarmed = True
        else:
            start = self._last_pos + 1 if self._last_pos is not None else max(1, pos_t - 1)
            self._catch_up(pit, idx, start, pos_t, instrument, replay=False)

        proxy = self._proxies[-1] if self._proxies else float("nan")
        median = (float(np.median(self._proxies[-self.median_window:]))
                  if len(self._proxies) >= self.median_window else float("nan"))

        sig = self.base.generate(pit, t, instrument)
        if sig.direction == Direction.FLAT:
            return sig

        self.n_signals += 1
        # The shadow tracks the WRAPPED strategy's own returns (pre-overlay):
        # record the base direction at this bar for the next catch-up.
        self._real_sigs[pos_t] = 1 if sig.direction == Direction.LONG else -1

        if not (np.isfinite(proxy) and np.isfinite(median) and proxy > 0 and median > 0):
            return sig  # inert: not enough point-in-time history yet

        if self.stand_down and proxy > self.stand_mult * median:
            self.n_standdowns += 1
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=sig.reward_risk, confidence=0.0, timeframe=sig.timeframe,
                rationale=(
                    f"vol stand-down: strategy vol {proxy:.3f} > {self.stand_mult:.1f}x "
                    f"its {self.median_window}d median {median:.3f} | {sig.rationale}"
                ),
            )

        target = self.target_vol if self.target_vol is not None else median
        f = min(1.0, target / proxy)
        if f >= 1.0:
            return sig

        # Remap probability so fractional Kelly yields EXACTLY f x the risk fraction
        # at unchanged geometry: full_kelly(p', b) == f * full_kelly(p, b).
        b = float(sig.reward_risk)
        p_scaled = (f * full_kelly(float(sig.probability), b) * b + 1.0) / (b + 1.0)
        sig.probability = min(max(p_scaled, 0.0), 1.0)
        self.n_scaled += 1
        sig.rationale = (
            f"{sig.rationale} | vol-mgmt x{f:.2f} (proxy {proxy:.3f} vs target {target:.3f})"
        )
        return sig
