"""Signal-conditioning entry gates (2026-07-28) — veto wrappers driven by precomputed,
point-in-time gate series.

Three pre-registered experiments (engine/data_store/fip_prereg.md,
factor_confirmation_prereg.md, comomentum_prereg.md):

  * FIP / information discreteness (Da, Gurun & Warachka RFS 2014): keep only entries
    whose 126-day formation was built from many small same-sign days (continuous
    information, low ID) — discrete, jumpy formations reverse.
  * Cross-asset factor confirmation (Ehsani & Linnainmaa JF 2022): equity entries only
    when the halal equity index's 63d trend agrees in sign; alt-crypto entries only when
    BTC's 63d trend agrees.
  * Comomentum crowding (Lou & Polk RFS 2022): block new entries in a direction while the
    same-direction momentum cohort's mean pairwise correlation is abnormally high
    (z > +1.5 vs its trailing 252d distribution).

Every series is a deterministic function of closes at or before the decision bar — the
same causality as the certified momentum score, nothing to fit, CPCV's purged train split
intentionally unused (same argument as the certified TrendBook). Certified default:
``entry_gate=None`` in ``TrendBook`` — no wrapper, byte-identical certified behaviour.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy


class DirectionalEntryGate(Strategy):
    """Veto momentum-mode signals whose (timestamp, direction) is blocked.

    ``blocked_long`` / ``blocked_short`` are sets of tz-aware timestamps. Bollinger
    mean-reversion signals pass through — the conditioning mechanisms are about trend
    persistence, not counter-trend bounces (same bypass rule as the HTF gate).
    """

    name = "directional_entry_gate"

    def __init__(self, base_strategy: Strategy, *,
                 blocked_long=frozenset(), blocked_short=frozenset(), label: str = "gate") -> None:
        self.base_strategy = base_strategy
        self.blocked_long = blocked_long
        self.blocked_short = blocked_short
        self.label = label
        self.holding_horizon = getattr(base_strategy, "holding_horizon", 10)
        self.reward_risk = getattr(base_strategy, "reward_risk", 1.5)
        self.timeframe = getattr(base_strategy, "timeframe", "1d")
        self.instrument = getattr(base_strategy, "instrument", "")
        self.n_signals = 0
        self.n_vetoes = 0

    # -- training: delegate to the wrapped strategy -----------------------------
    def fit(self, pit: PointInTimeAccessor, train_timestamps) -> None:
        if hasattr(self.base_strategy, "fit"):
            self.base_strategy.fit(pit, train_timestamps)

    def is_fitted(self) -> bool:
        return getattr(self.base_strategy, "is_fitted", lambda: True)()

    # -- inference ----------------------------------------------------------------
    def generate(self, pit: PointInTimeAccessor, t, instrument: str = "") -> Signal:
        sig = self.base_strategy.generate(pit, t, instrument or self.instrument)
        if sig.direction == Direction.FLAT:
            return sig
        if "mode=mean_reversion" in sig.rationale:
            return sig
        self.n_signals += 1
        blocked = (t in self.blocked_long) if sig.direction == Direction.LONG else (t in self.blocked_short)
        if blocked:
            self.n_vetoes += 1
            return Signal(
                instrument=sig.instrument, direction=Direction.FLAT, probability=0.5,
                reward_risk=sig.reward_risk, confidence=0.0, timeframe=sig.timeframe,
                rationale=f"{self.label} veto: {sig.direction.value} blocked at {pd.Timestamp(t).date()}",
            )
        sig.rationale = f"{sig.rationale} | {self.label} pass"
        return sig


# ── E1: FIP / information discreteness ─────────────────────────────────────────

def fip_id_series(df: pd.DataFrame, formation: int = 126) -> pd.Series:
    """ID(t) = sign(R) x (down_frac - up_frac) over the trailing ``formation`` bars.

    R = close_t / close_{t-F} - 1; up/down fractions are the share of up/down days among
    the F daily returns ending at t (flat days count in neither). A continuous trend
    (many small same-sign days) yields a LOW (more negative) ID; a jumpy one a HIGH ID.
    NaN until F returns exist.
    """
    close = df["close"].astype(float)
    past = close / close.shift(formation) - 1.0
    r = close.pct_change()
    up = (r > 0).rolling(formation).mean()
    down = (r < 0).rolling(formation).mean()
    return np.sign(past) * (down - up)


def fip_blocked(panel: dict[str, pd.DataFrame], formation: int = 126) -> dict[str, set]:
    """Per-instrument timestamps whose ID sits in the discrete half (> cross-sectional
    median) or is undefined. Median = per-date cross-section over defined IDs on the
    union timeline, per-instrument IDs forward-filled (last known value, causal)."""
    frame = pd.DataFrame({inst: fip_id_series(df, formation) for inst, df in panel.items()}).sort_index().ffill()
    median = frame.median(axis=1, skipna=True)
    blocked: dict[str, set] = {}
    for inst in frame.columns:
        col = frame[inst]
        blocked[inst] = set(col.index[col.isna() | (col > median)])
    return blocked


# ── E2: cross-asset factor confirmation ────────────────────────────────────────

def trend_sign_series(df: pd.DataFrame, lookback: int = 63) -> pd.Series:
    """sign(close_t / close_{t-L} - 1): +1 up, -1 down, 0 flat; NaN until L returns."""
    close = df["close"].astype(float)
    return np.sign(close / close.shift(lookback) - 1.0)


def factor_blocked(panel: dict[str, pd.DataFrame], factor_inst: str,
                   sleeve_insts: list[str], lookback: int = 63) -> dict[str, tuple[set, set]]:
    """For each sleeve instrument: block LONGs when the factor's 63d trend is not up,
    block SHORTs when it is not down. An undefined factor trend (NaN — insufficient
    factor history) blocks nothing: absence of data is not evidence against the trade.
    The factor series is forward-filled onto the union timeline (last known value)."""
    if factor_inst not in panel:
        raise ValueError(f"factor instrument {factor_inst} not in panel")
    idx = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in panel.values()])))
    sign = trend_sign_series(panel[factor_inst], lookback).reindex(idx).ffill()
    blocked_long = set(sign.index[sign.notna() & (sign != 1.0)])
    blocked_short = set(sign.index[sign.notna() & (sign != -1.0)])
    return {inst: (blocked_long, blocked_short) for inst in sleeve_insts}


# ── E3: comomentum crowding ────────────────────────────────────────────────────

def _cohort_comomentum(rets: pd.DataFrame, cohort_mask: pd.DataFrame,
                       corr_window: int, min_cohort: int, min_pairs: int) -> pd.Series:
    """Per-date mean pairwise Pearson correlation of daily returns within the cohort
    (columns True in ``cohort_mask``), over the trailing ``corr_window`` rows of
    ``rets``, pairwise-complete. NaN when the cohort is smaller than ``min_cohort``
    or fewer than ``min_pairs`` pairwise values are defined."""
    idx = rets.index
    out = pd.Series(np.nan, index=idx, dtype=float)
    cols = list(rets.columns)
    mask_vals = cohort_mask.to_numpy()
    ret_vals = rets.to_numpy()
    for k in range(corr_window - 1, len(idx)):
        members = [j for j in range(len(cols)) if mask_vals[k, j]]
        if len(members) < min_cohort:
            continue
        window = ret_vals[k - corr_window + 1:k + 1][:, members]
        sub = pd.DataFrame(window)
        cm = sub.corr().to_numpy()
        iu = np.triu_indices_from(cm, k=1)
        vals = cm[iu]
        vals = vals[np.isfinite(vals)]
        if len(vals) < min_pairs:
            continue
        out.iloc[k] = float(vals.mean())
    return out


def comomentum_series(panel: dict[str, pd.DataFrame], lookback: int = 252,
                      corr_window: int = 60, ref_window: int = 252,
                      min_cohort: int = 5, min_pairs: int = 10) -> pd.DataFrame:
    """Abnormal-comomentum z-scores per direction.

    Cohorts = instruments whose ``lookback``-day momentum sign is in the direction at t
    (per-instrument momentum forward-filled onto the union timeline). Comomentum =
    60-row mean pairwise daily-return correlation within the cohort (pairwise-complete,
    mirroring the certified correlation-cap machinery — no ffill inside the returns
    frame). Abnormal = (c_t - trailing ``ref_window``-row median) / trailing std
    (population, ddof=0, window inclusive of t). NaN until both the correlation and the
    full reference window exist — an undefined state blocks nothing.
    """
    closes = pd.DataFrame({inst: df["close"].astype(float) for inst, df in panel.items()}).sort_index().ffill()
    mom = closes / closes.shift(lookback) - 1.0
    # Returns on each instrument's OWN calendar (outer-joined; NaN on foreign dates) —
    # the same construction as the certified portfolio correlation frame.
    rets = pd.DataFrame({inst: df["close"].astype(float).pct_change()
                         for inst, df in panel.items()}).sort_index()
    out = {}
    for label, mask in (("long", mom > 0), ("short", mom < 0)):
        c = _cohort_comomentum(rets, mask.astype(bool), corr_window, min_cohort, min_pairs)
        med = c.rolling(ref_window, min_periods=ref_window).median()
        std = c.rolling(ref_window, min_periods=ref_window).std(ddof=0)
        out[label] = (c - med) / std.where(std > 0)
    return pd.DataFrame(out)


def comomentum_blocked(panel: dict[str, pd.DataFrame], lookback: int = 252,
                       corr_window: int = 60, ref_window: int = 252,
                       z_thresh: float = 1.5, min_cohort: int = 5,
                       min_pairs: int = 10) -> tuple[set, set]:
    """Timestamps where abnormal comomentum exceeds +z_thresh, per direction."""
    z = comomentum_series(panel, lookback, corr_window, ref_window, min_cohort, min_pairs)
    return (set(z.index[z["long"] > z_thresh]), set(z.index[z["short"] > z_thresh]))


# ── dispatch (TrendBook seam) ──────────────────────────────────────────────────

def _panel_fingerprint(panel: dict[str, pd.DataFrame]) -> tuple:
    return tuple(sorted((inst, len(df), str(df.index[0]), str(df.index[-1]))
                        for inst, df in panel.items()))


_GATE_CACHE: dict[tuple, dict[str, tuple[set, set]]] = {}


def build_gate_masks(panel: dict[str, pd.DataFrame], spec: dict,
                     asset_class_of) -> dict[str, tuple[set, set]]:
    """Dispatch a gate spec to per-instrument (blocked_long, blocked_short) sets.

    ``spec`` is the pre-registered, ledger-recorded gate description:
      {"kind": "fip", "formation": 126}
      {"kind": "factor", "sleeve": "equity"|"crypto", "lookback": 63}
      {"kind": "comomentum", "lookback": 252, "corr_window": 60, "ref_window": 252,
       "z_thresh": 1.5}
    Results are memoised per process on the panel fingerprint — CPCV rebuilds the model
    per fold over the SAME panel, and the masks are a pure function of it.
    """
    kind = spec["kind"]
    key = (kind, tuple(sorted((k, str(v)) for k, v in spec.items())), _panel_fingerprint(panel))
    if key in _GATE_CACHE:
        return _GATE_CACHE[key]

    if kind == "fip":
        blocked = fip_blocked(panel, int(spec.get("formation", 126)))
        masks = {inst: (s, s) for inst, s in blocked.items()}
    elif kind == "factor":
        lookback = int(spec.get("lookback", 63))
        sleeve = spec["sleeve"]
        if sleeve == "equity":
            # the book's genuine equity exposure: stocks + UCITS index/sector ETFs;
            # gold (SGLD.L) and sukuk (SPSK) are not stocks — ungated (prereg §2).
            sleeve_insts = [inst for inst in panel
                            if asset_class_of(inst) == "equity" and inst not in ("SGLD.L", "SPSK")]
            factor_inst = spec.get("factor", "ISWD.L")
        elif sleeve == "crypto":
            # alt crypto only; BTC is the factor itself and stays ungated (prereg §2).
            sleeve_insts = [inst for inst in panel
                            if asset_class_of(inst) == "crypto" and inst != "BTC/USD"]
            factor_inst = spec.get("factor", "BTC/USD")
        else:
            raise ValueError(f"unknown factor sleeve: {sleeve}")
        masks = {inst: factor_blocked(panel, factor_inst, [inst], lookback)[inst]
                 for inst in sleeve_insts}
    elif kind == "comomentum":
        bl, bs = comomentum_blocked(panel, int(spec.get("lookback", 252)),
                                    int(spec.get("corr_window", 60)),
                                    int(spec.get("ref_window", 252)),
                                    float(spec.get("z_thresh", 1.5)))
        masks = {inst: (bl, bs) for inst in panel}
    else:
        raise ValueError(f"unknown entry gate kind: {kind}")

    _GATE_CACHE[key] = masks
    return masks
