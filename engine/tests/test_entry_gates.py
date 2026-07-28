"""Signal-conditioning entry gates (pre-registered: fip_prereg.md,
factor_confirmation_prereg.md, comomentum_prereg.md, 2026-07-28).

Proves: the FIP sign convention (continuous trend -> LOW ID, jumpy -> HIGH ID) and the
median-split keep rule; the factor-confirmation block/pass semantics including the
undefined-factor pass-through; the comomentum z-score blocking an in-lockstep cohort;
the DirectionalEntryGate wrapper (blocked -> FLAT, mean-reversion bypass, FLAT
pass-through); and the certified TrendBook default (entry_gate=None wraps NOTHING).
"""

import numpy as np
import pandas as pd
import pytest

from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy
from apex_quant.strategies.entry_gates import (
    DirectionalEntryGate,
    _cohort_comomentum,
    build_gate_masks,
    comomentum_blocked,
    comomentum_series,
    factor_blocked,
    fip_blocked,
    fip_id_series,
    trend_sign_series,
)

UTC = "UTC"


def _df(closes, start="2020-01-01", freq="B"):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz=UTC)
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": 1.0}, index=idx)


# ── FIP (E1) ───────────────────────────────────────────────────────────────────

def test_fip_id_sign_convention_continuous_vs_jumpy():
    """An uptrend of many small up days is CONTINUOUS -> LOW (negative) ID; an uptrend
    of the same size built from one big jump among many small down days is DISCRETE ->
    HIGH (positive) ID."""
    n = 200
    cont = 100.0 * np.cumprod(np.full(n, 1.001))                  # +0.1% every day
    # one +25% jump at day 150 (INSIDE the last-126 formation), small down days else
    jumpy = 100.0 * np.cumprod(np.where(np.arange(n) == 150, 1.25, 0.9995))
    assert jumpy[-1] > jumpy[0]                                   # still an UPtrend overall
    id_cont = fip_id_series(_df(cont), formation=126).iloc[-1]
    id_jumpy = fip_id_series(_df(jumpy), formation=126).iloc[-1]
    assert id_cont < 0 < id_jumpy
    # sign flips with the trend direction: continuous DOWNTREND is also LOW ID.
    cont_dn = 200.0 * np.cumprod(np.full(n, 0.999))
    assert fip_id_series(_df(cont_dn), formation=126).iloc[-1] < 0


def test_fip_id_undefined_until_formation_filled():
    closes = 100.0 * np.cumprod(np.full(200, 1.0005))
    s = fip_id_series(_df(closes), formation=126)
    assert s.iloc[:126].isna().all()
    assert np.isfinite(s.iloc[126:]).all()


def test_fip_blocked_median_split_keeps_continuous():
    """3 instruments: one continuous (ID << median), one jumpy (ID >> median), one
    mid. The jumpy one must be blocked at the final bar; the continuous one kept."""
    n = 200
    a = 100.0 * np.cumprod(np.full(n, 1.001))                    # continuous up (ID very neg)
    b = 100.0 * np.cumprod(np.where(np.arange(n) % 3 == 0, 1.004, 0.999))  # mixed
    c = 100.0 * np.cumprod(np.where(np.arange(n) == 150, 1.30, 0.9995))    # jumpy up (ID pos)
    panel = {k: _df(v) for k, v in {"A": a, "B": b, "C": c}.items()}
    blocked = fip_blocked(panel, formation=126)
    last = panel["A"].index[-1]
    assert last not in blocked["A"]
    assert last in blocked["C"]


def test_fip_blocked_is_deterministic():
    rng = np.random.default_rng(42)
    panel = {f"I{k}": _df(100 * np.cumprod(1 + rng.normal(0, 0.01, 200))) for k in range(6)}
    b1, b2 = fip_blocked(panel, 126), fip_blocked(panel, 126)
    assert all(b1[k] == b2[k] for k in b1)


# ── Factor confirmation (E2) ───────────────────────────────────────────────────

def test_trend_sign_and_factor_blocked():
    up = 100.0 * np.cumprod(np.full(100, 1.001))
    dn = 100.0 * np.cumprod(np.full(100, 0.999))
    panel = {"F": _df(up), "X": _df(dn)}
    masks = factor_blocked(panel, "F", ["X"], lookback=63)
    bl, bs = masks["X"]
    last = panel["X"].index[-1]
    # factor trend is UP: longs allowed, shorts blocked.
    assert last not in bl and last in bs
    # undefined factor (lookback longer than history) blocks nothing.
    masks2 = factor_blocked(panel, "F", ["X"], lookback=500)
    assert masks2["X"] == (set(), set())


def test_build_gate_masks_factor_sleeves():
    panel = {"AAPL": _df(100 * np.cumprod(np.full(150, 1.001))),
             "SGLD.L": _df(100 * np.cumprod(np.full(150, 1.0005))),
             "ISWD.L": _df(100 * np.cumprod(np.full(150, 1.0008))),
             "BTC/USD": _df(100 * np.cumprod(np.full(150, 1.002))),
             "ETH/USD": _df(100 * np.cumprod(np.full(150, 0.998))),
             "EUR/USD": _df(100 * np.cumprod(np.full(150, 1.0001)))}
    cls = {"AAPL": "equity", "SGLD.L": "equity", "ISWD.L": "equity",
           "BTC/USD": "crypto", "ETH/USD": "crypto", "EUR/USD": "forex"}
    eq = build_gate_masks(panel, {"kind": "factor", "sleeve": "equity"}, cls.get)
    assert set(eq) == {"AAPL", "ISWD.L"}            # SGLD.L (gold) is NOT gated
    cr = build_gate_masks(panel, {"kind": "factor", "sleeve": "crypto"}, cls.get)
    assert set(cr) == {"ETH/USD"}                   # BTC is the factor — NOT gated


# ── Comomentum (E3) ────────────────────────────────────────────────────────────

def test_cohort_comomentum_high_when_in_lockstep():
    rng = np.random.default_rng(7)
    dates = pd.date_range("2020-01-01", periods=400, freq="B", tz=UTC)
    common = rng.normal(0, 0.01, 400)
    rets = pd.DataFrame({f"I{k}": common + rng.normal(0, 1e-6, 400) for k in range(6)},
                        index=dates)
    mask = pd.DataFrame(True, index=dates, columns=rets.columns)
    c = _cohort_comomentum(rets, mask, corr_window=60, min_cohort=5, min_pairs=10)
    assert c.iloc[-1] > 0.99                       # lockstep cohort -> mean corr ~ 1
    assert c.iloc[:59].isna().all()                # undefined before the window fills


def test_comomentum_blocks_only_when_abnormal():
    """A cohort that suddenly moves in lockstep after independent history must produce
    a z > 1.5 block; the calm history itself must stay unblocked."""
    rng = np.random.default_rng(11)
    n, n_calm = 700, 500
    # Calm era: independent small-idio uptrends (long cohort full, mean corr ~ 0).
    # Lockstep era: a shared common component dominates (mean corr ~ 1, steady up
    # drift so the long cohort stays full).
    closes = {}
    shared = rng.normal(0.001, 0.002, n - n_calm)
    for k in range(8):
        idio_calm = rng.normal(0.001, 0.005, n_calm)
        rets_k = np.concatenate([idio_calm, shared + rng.normal(0, 0.0005, n - n_calm)])
        closes[f"I{k}"] = 100 * np.exp(np.cumsum(rets_k))
    panel = {k: _df(v) for k, v in closes.items()}
    dates = panel["I0"].index
    z = comomentum_series(panel, lookback=60, corr_window=60, ref_window=252)
    bl, bs = comomentum_blocked(panel, lookback=60, corr_window=60, ref_window=252)
    k_onset = n_calm + 61          # 61 days into lockstep: reference window still calm
    assert z["long"].iloc[k_onset] > 1.5
    assert dates[k_onset] in bl                        # blocked at crowding onset
    calm_blocked = [d for d in bl if d < dates[n_calm]]
    assert len(calm_blocked) < 0.05 * n_calm           # ~never blocked while calm
    assert not bs                                      # no short cohort -> never blocked


# ── Wrapper ────────────────────────────────────────────────────────────────────

class _Stub(Strategy):
    def __init__(self, direction, rationale="mom"):
        self._d, self._r = direction, rationale

    def generate(self, pit, t, instrument=""):
        return Signal(instrument=instrument, direction=self._d, probability=0.6,
                      reward_risk=1.5, confidence=0.5, rationale=self._r)


def test_wrapper_vetoes_blocked_and_passes_allowed():
    t0 = pd.Timestamp("2024-01-02", tz=UTC)
    t1 = pd.Timestamp("2024-01-03", tz=UTC)
    g = DirectionalEntryGate(_Stub(Direction.LONG), blocked_long={t0}, label="fip")
    assert g.generate(None, t0).direction == Direction.FLAT and g.n_vetoes == 1
    assert g.generate(None, t1).direction == Direction.LONG and g.n_vetoes == 1
    assert g.n_signals == 2


def test_wrapper_bypasses_mean_reversion_and_flat():
    t0 = pd.Timestamp("2024-01-02", tz=UTC)
    g = DirectionalEntryGate(_Stub(Direction.LONG, "MR: BB signal mode=mean_reversion"),
                             blocked_long={t0})
    assert g.generate(None, t0).direction == Direction.LONG and g.n_vetoes == 0
    flat = DirectionalEntryGate(_Stub(Direction.FLAT), blocked_long={t0})
    assert flat.generate(None, t0).direction == Direction.FLAT and flat.n_signals == 0


# ── Certified TrendBook default ────────────────────────────────────────────────

def test_trendbook_certified_default_wraps_nothing():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from run_portfolio_gate import COMMON_PARAMS, TrendBook
    from apex_quant.strategies.multi_timeframe import MultiTimeframeMomentum

    panel = {"AAPL": _df(100 * np.cumprod(np.full(300, 1.001)))}
    base = TrendBook(panel, **{"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252})
    assert all(isinstance(s, MultiTimeframeMomentum) for s in base.strategies().values())
    gated = TrendBook(panel, **{"carry_filter": False, **COMMON_PARAMS,
                                "momentum_lookback": 252,
                                "entry_gate": {"kind": "fip", "formation": 126}})
    assert all(isinstance(s, DirectionalEntryGate) for s in gated.strategies().values())
