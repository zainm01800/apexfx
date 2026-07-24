"""Short-side financing: the borrow-fee cost model (2026-07-24, W2).

The v5 per-class cost model charged spread+slippage on fills but no financing on
shorts. ``AssetClassConfig.short_borrow_bps_annual`` adds a per-bar accrual on the
mark-to-market short notional. Default 0.0 must leave behaviour byte-identical.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest import PortfolioBacktester
from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction, Signal
from apex_quant.strategies.base import Strategy

FEE_BPS = 10_000.0  # 100%/yr so the accrual is unmissable in a short test window


def _wiggly_flat(n=200, base=100.0):
    """Nearly flat series with nonzero ATR and nonzero close-to-close vol (the
    candidate gate requires both > 0). Price drifts ±1 tick so short P&L ~ 0 and
    the borrow accrual is the only material P&L driver."""
    close = base * (1.0 + 0.0002 * np.sin(np.arange(n)))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.002
    lo = np.minimum(op, close) * 0.998
    idx = pd.bdate_range("2020-01-01", periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


class _OneShot(Strategy):
    """Fires one directional signal at the first eligible bar, then stays flat.
    holding_horizon keeps the trade open across the whole window (time exit)."""

    def __init__(self, direction: Direction, horizon: int = 60):
        self.direction = direction
        self.holding_horizon = horizon
        self._fired = False

    def generate(self, pit, t, instrument: str = "") -> Signal:
        if not self._fired:
            self._fired = True
            return Signal(instrument=instrument, direction=self.direction,
                          probability=0.60, reward_risk=1.5, timeframe="1d")
        return Signal(instrument=instrument, direction=Direction.FLAT,
                      probability=0.50, reward_risk=1.5, timeframe="1d")


def _run(direction: Direction, fee_bps: float):
    cfg = get_config().model_copy(deep=True)
    cfg.asset_classes.equity.short_borrow_bps_annual = fee_bps
    df = _wiggly_flat()
    pit = PointInTimeAccessor(df)
    pbt = PortfolioBacktester(cfg, use_regime=False)
    return pbt.run({"AAPL": pit}, {"AAPL": _OneShot(direction)},
                   timeframes={"AAPL": "1d"}, warmup=30, periods_per_year=252)


def test_borrow_fee_off_by_default_is_zero():
    res = _run(Direction.SHORT, 0.0)
    assert res.metrics["n_trades"] == 1
    assert res.metrics["short_borrow_fees_total"] == 0.0


def test_borrow_fee_drags_short_equity():
    free = _run(Direction.SHORT, 0.0)
    charged = _run(Direction.SHORT, FEE_BPS)
    fees = charged.metrics["short_borrow_fees_total"]
    assert fees > 0.0
    # Equity difference equals the accrued fees (trade P&L identical: same fills —
    # the accrual never changes sizing here because the position is already open).
    diff = free.metrics["final_equity"] - charged.metrics["final_equity"]
    assert diff == pytest.approx(fees, rel=1e-6)
    # Sanity on magnitude: per-bar accrual = notional * rate/252, one accrual per
    # bar held (entry bar through the bar before the exit bar).
    notional = charged.trades[0].entry_price * charged.trades[0].units
    n_accruals = len(pd.bdate_range(charged.trades[0].entry_time, charged.trades[0].exit_time)) - 1
    assert n_accruals > 20
    assert fees == pytest.approx(notional * (FEE_BPS / 1e4) / 252 * n_accruals, rel=0.1)


def test_borrow_fee_ignored_for_longs():
    free = _run(Direction.LONG, 0.0)
    charged = _run(Direction.LONG, FEE_BPS)
    assert charged.metrics["short_borrow_fees_total"] == 0.0
    assert charged.metrics["final_equity"] == pytest.approx(free.metrics["final_equity"], rel=1e-12)


def test_borrow_fee_in_trade_pnl():
    """Per-trade and per-instrument accounting include the accrual (honest ledger)."""
    free = _run(Direction.SHORT, 0.0)
    charged = _run(Direction.SHORT, FEE_BPS)
    fees = charged.metrics["short_borrow_fees_total"]
    assert charged.trades[0].pnl == pytest.approx(free.trades[0].pnl - fees, abs=0.02)
    assert charged.per_instrument["AAPL"]["net_pnl"] == pytest.approx(
        free.per_instrument["AAPL"]["net_pnl"] - fees, abs=0.02)
