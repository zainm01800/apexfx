"""SPY -> crypto/FX momentum-spillover entry gate (2026-08-08).

Pre-registered in engine/data_store/momentum_spillover_gate_prereg.md and gated
by scripts/run_momentum_spillover_gate.py (verdict CONFIRMED; best challenger
spill50). Forward paper-traded as the challenger book (Book B) by
scripts/run_paper_portfolio_challenger.py — prereg
engine/data_store/pre_registration_paper_challenger_2026-08-11.md.

Mechanism: on crypto/FX instruments, LONG entries are permitted only when
SPY's trailing L-day return is positive (risk-on), SHORT entries only when it
is negative. Equity/ETF/metals sleeves are untouched. This module is the
single source of truth for the wrapper — both the gate script and the paper
stepper import it, so the certified backtest config and the forward book
cannot drift apart.
"""

from __future__ import annotations

import pandas as pd

from apex_quant.risk.types import Direction, Signal


class SpilloverGate:
    """Wrapper: on crypto/FX, LONG only when SPY trailing L-day return > 0 (risk-on),
    SHORT only when < 0. risk_on is the set of the instrument's own bar timestamps
    mapped through SPY's calendar."""

    def __init__(self, base, risk_on: set, instrument: str) -> None:
        self._base = base
        self._risk_on = risk_on
        self._instrument = instrument

    def __getattr__(self, name):
        if name == "_base":
            raise AttributeError(name)
        return getattr(self._base, name)

    def generate(self, pit, t, instrument: str = "") -> Signal:
        sig = self._base.generate(pit, t, instrument)
        d = sig.direction
        d = d.value if hasattr(d, "value") else str(d)
        on = pd.Timestamp(t) in self._risk_on
        if (d == "long" and not on) or (d == "short" and on):
            return Signal(instrument=instrument or self._instrument, direction=Direction.FLAT,
                          probability=0.50, reward_risk=1.5, timeframe="1d",
                          rationale="spillover regime veto")
        return sig


def risk_on_map(spy_close: pd.Series, panel: dict, gated, L: int) -> dict:
    """Per gated instrument, the set of its own bar timestamps that are risk-on.

    The exact mapping logic from scripts/run_momentum_spillover_gate.py: the
    state at instrument bar t is the sign of SPY's trailing L-day return at the
    SPY bar at-or-before t (``searchsorted(side="right") - 1`` through SPY's
    calendar, so a crypto weekend bar inherits Friday's SPY state). The state
    at bar t uses bar t's SPY close only — point-in-time safe. ``panel`` maps
    instrument -> trimmed daily-bar frame; only its indexes are read.
    """
    ret = spy_close.pct_change(L)
    on = (ret > 0)
    idx = spy_close.index
    out = {}
    for inst in gated:
        inst_idx = panel[inst].index
        pos = idx.searchsorted(inst_idx, side="right") - 1
        pos = pos.clip(min=0)
        out[inst] = set(inst_idx[on.iloc[pos].to_numpy()])
    return out
