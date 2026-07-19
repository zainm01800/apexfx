"""Book-level vol-management wrapper for the diversified trend book (Sleeve A of
the max-Sharpe stack): conditional vol targeting + a Daniel-Moskowitz panic
stand-down, wrapped around ANY per-instrument signal strategy.

Research basis (docs/research/2026-07-18_beating_sharpe_1_2.md):
  * Barroso & Santa-Clara (2015, JFE) — scaling momentum exposure by the
    strategy's OWN recent realised vol roughly doubled Sharpe (0.53 -> 0.97).
  * Daniel & Moskowitz (2016, JFE) — momentum crashes cluster in PANIC states:
    market down + realised vol spiking; standing down there avoids the crash.
  * Bongaerts et al. (2020, FAJ) — unlevered conditional vol targeting (scale
    DOWN only, never lever up) keeps the uplift with LOWER turnover.

Construction (exact; strictly point-in-time — no bar ``t`` close is ever used):

(a) VOL TARGETING (Barroso & Santa-Clara): the wrapped strategy's own signal
    returns are proxied by a SHADOW book, the same construction as
    ``vol_managed_overlay.py``: a non-FLAT base signal at bar ``s`` puts the
    shadow in that direction for bars ``s+1 … s+holding_horizon`` (time-stop
    only — the managed ATR/chandelier exits are path-dependent and are NOT
    replicated; a fresh non-FLAT signal replaces the shadow early, mirroring
    re-entry; FLAT signals let it expire, mirroring live sequencing where the
    book only requests signals while flat). Shadow daily return for bar ``u``
    = shadow direction during ``u`` × the instrument's close-to-close return.
      proxy(t) = annualised (×√252) sample std of the last ``proxy_window``
    (21) shadow daily returns dated STRICTLY BEFORE ``t``.
      scale f(t) = min(1, target_vol / proxy(t))  — exposure is only ever
    DAMPED (the defensible version per the Cederburg 2020 caveat documented in
    the existing overlay: fixed target, no leverage-up).

(b) PANIC STAND-DOWN (Daniel & Moskowitz): force FLAT when BOTH
      * the INSTRUMENT's 21-day realised vol (annualised √252, closes strictly
        before ``t``) exceeds ``stand_mult`` (1.5) × the median of that daily
        vol series over the trailing ``median_window`` (126) days, AND
      * the instrument's trailing 21-day close-to-close return is negative
        (the panic state: high vol + falling market). Vol spiking on a RALLY
        does not trigger the stand-down.

Scaling mechanics: signals carry NO size — the risk layer sizes from
``probability`` via fractional Kelly. The wrapper remaps
``p → p' = (f·full_kelly(p,b)·b + 1) / (b + 1)``, which yields EXACTLY ``f ×``
the Kelly risk fraction at unchanged stop/target geometry (identity:
``full_kelly(p', b) == f · full_kelly(p, b)``; same remap as
``vol_managed_overlay.py``). The risk layer stays supreme — regime, drawdown,
exposure and vol-target caps still bind below the request.

State/warm-up: one-time pre-warm at the first ``generate`` call replays the
shadow and the instrument vol series over the trailing
``median_window + proxy_window + holding_horizon + 15`` bars before ``t0``
(point-in-time by construction — each replayed signal is ``base.generate`` at
``s < t0``), so the overlay is active immediately, including inside CPCV folds
where the run starts mid-history with warmup=0. Each feature is INERT until
its own history exists (scaling needs ``proxy_window`` shadow returns; the
stand-down needs ``median_window`` valid daily instrument-vol values).
Assumes chronological ``generate`` calls per instance — the engine's
PortfolioBacktester always calls chronologically, and validation builds a
fresh instance per backtest/fold.

Annualization caveat: √252 is used uniformly for all instruments (the book's
own metrics convention; crypto's 365 mechanics annualization lives in the risk
layer, not here). The stand-down is a ratio to the instrument's own median, so
the constant cancels; the target comparison is internally consistent (proxy
and target use the same convention).

This class is deliberately NOT the carry-specific ``VolManagedCarryTrend``:
it wraps an arbitrary base (here: the book's
``MultiTimeframeMomentum(RegimeGatedMomentum)`` per-instrument stack), uses a
FIXED vol target rather than the self-calibrating trailing median, and gates
the stand-down on the INSTRUMENT's panic state (vol spike + negative return)
rather than on strategy-vol alone.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.sizing import full_kelly
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy

_ANN = float(np.sqrt(252.0))  # book metrics convention (see module docstring)


class VolTargetOverlay(Strategy):
    """Wrap any per-instrument strategy: vol-target scaling + panic stand-down."""

    name = "vol_target_overlay"

    def __init__(
        self,
        base_strategy: Strategy,
        *,
        holding_horizon: int = 21,
        target_vol: float = 0.10,       # fixed annualised target for the signal-return proxy
        proxy_window: int = 21,         # days of shadow returns in the vol proxy
        median_window: int = 126,       # trailing days for the instrument-vol median
        stand_mult: float = 1.5,        # panic = inst vol > stand_mult x its median ...
        panic_ret_window: int = 21,     # ... AND inst return over this window < 0
        vol_scale: bool = True,         # (a) Barroso & Santa-Clara scaling on/off
        stand_down: bool = True,        # (b) Daniel & Moskowitz stand-down on/off
    ):
        self.base = base_strategy
        self.holding_horizon = int(holding_horizon)
        self.target_vol = float(target_vol)
        self.proxy_window = int(proxy_window)
        self.median_window = int(median_window)
        self.stand_mult = float(stand_mult)
        self.panic_ret_window = int(panic_ret_window)
        self.vol_scale = bool(vol_scale)
        self.stand_down = bool(stand_down)
        self._reset_state()

    # -- internal state ---------------------------------------------------------
    def _reset_state(self) -> None:
        self._prewarmed = False
        self._sh_dir = 0              # current shadow direction (+1/-1/0)
        self._sh_expiry = -1          # last int bar position the shadow is active
        self._real_sigs: dict[int, int] = {}  # int bar position -> +1/-1 (live base signals)
        self._sh_rets: list[float] = []       # daily shadow returns, chronological
        self._proxies: list[float] = []       # valid daily signal-vol proxy values
        self._inst_rets: list[float] = []     # daily instrument close returns
        self._inst_vols: list[float] = []     # valid daily 21d instrument-vol values
        self._last_pos: int | None = None     # last bar position processed
        self.n_signals = 0          # non-FLAT base signals seen (live)
        self.n_scaled = 0           # of those, damped by the vol target (f < 1)
        self.n_standdowns = 0       # of those, forced FLAT by the panic rule

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

    # -- bookkeeping --------------------------------------------------------------
    def _advance_bar(self, inst_ret: float) -> None:
        """Append one bar of history (instrument return + shadow return) strictly
        before ``t`` and refresh the running vol series."""
        self._inst_rets.append(inst_ret)
        if len(self._inst_rets) >= self.proxy_window:
            v = float(np.std(self._inst_rets[-self.proxy_window:], ddof=1)) * _ANN
            if np.isfinite(v):
                self._inst_vols.append(v)
        sh_ret = self._sh_dir * inst_ret
        self._sh_rets.append(sh_ret)
        if len(self._sh_rets) >= self.proxy_window:
            sd = float(np.std(self._sh_rets[-self.proxy_window:], ddof=1)) * _ANN
            if np.isfinite(sd):
                self._proxies.append(sd)

    def _catch_up(self, pit: PointInTimeAccessor, idx: pd.DatetimeIndex,
                  start: int, pos_t: int, instrument: str, replay: bool) -> None:
        """Process bars ``[start, pos_t)`` (strictly before ``t``) into the state.
        ``replay=True`` simulates the strategy alone (signals only when the shadow
        is flat); ``replay=False`` applies the live base signals recorded at their
        bars."""
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
            self._advance_bar(c1 / c0 - 1.0 if c0 else 0.0)
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
            pos_t = len(idx)  # t past the last bar: state from all bars (still strictly < t)

        if not self._prewarmed:
            span = self.median_window + self.proxy_window + self.holding_horizon + 15
            self._catch_up(pit, idx, max(1, pos_t - span), pos_t, instrument, replay=True)
            self._prewarmed = True
        else:
            start = self._last_pos + 1 if self._last_pos is not None else max(1, pos_t - 1)
            self._catch_up(pit, idx, start, pos_t, instrument, replay=False)

        proxy = self._proxies[-1] if self._proxies else float("nan")
        inst_vol = self._inst_vols[-1] if self._inst_vols else float("nan")
        vol_med = (float(np.median(self._inst_vols[-self.median_window:]))
                   if len(self._inst_vols) >= self.median_window else float("nan"))
        ret_panic = (float(np.prod([1.0 + r for r in self._inst_rets[-self.panic_ret_window:]]) - 1.0)
                     if len(self._inst_rets) >= self.panic_ret_window else float("nan"))

        sig = self.base.generate(pit, t, instrument)
        if sig.direction == Direction.FLAT:
            return sig

        self.n_signals += 1
        # The shadow tracks the WRAPPED strategy's own returns (pre-overlay):
        # record the base direction at this bar for the next catch-up.
        self._real_sigs[pos_t] = 1 if sig.direction == Direction.LONG else -1

        # (b) Daniel & Moskowitz panic stand-down: instrument vol spiking AND
        # the instrument falling. Inert until the full median window exists.
        if (self.stand_down and np.isfinite(inst_vol) and np.isfinite(vol_med)
                and vol_med > 0 and np.isfinite(ret_panic)
                and inst_vol > self.stand_mult * vol_med and ret_panic < 0.0):
            self.n_standdowns += 1
            return Signal(
                instrument=instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=sig.reward_risk, confidence=0.0, timeframe=sig.timeframe,
                rationale=(
                    f"panic stand-down: inst vol {inst_vol:.3f} > {self.stand_mult:.1f}x "
                    f"its {self.median_window}d median {vol_med:.3f} and "
                    f"{self.panic_ret_window}d ret {ret_panic:+.2%} < 0 | {sig.rationale}"
                ),
            )

        # (a) Barroso & Santa-Clara vol targeting on the strategy's own signal vol.
        if not (self.vol_scale and np.isfinite(proxy) and proxy > 0):
            return sig
        f = min(1.0, self.target_vol / proxy)
        if f >= 1.0:
            return sig

        # Remap probability so fractional Kelly yields EXACTLY f x the risk fraction
        # at unchanged geometry: full_kelly(p', b) == f * full_kelly(p, b).
        b = float(sig.reward_risk)
        p_scaled = (f * full_kelly(float(sig.probability), b) * b + 1.0) / (b + 1.0)
        sig.probability = min(max(p_scaled, 0.0), 1.0)
        self.n_scaled += 1
        sig.rationale = (
            f"{sig.rationale} | vol-target x{f:.2f} (proxy {proxy:.3f} vs target "
            f"{self.target_vol:.3f})"
        )
        return sig
