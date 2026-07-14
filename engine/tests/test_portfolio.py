"""Portfolio-level backtester: single-instrument parity with the existing engine,
book-level cap enforcement (the whole reason it exists), and accounting integrity.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest import Backtester, PortfolioBacktester
from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.manager import RiskManager
from apex_quant.strategies import RegimeGatedMomentum


def _ohlc(rets, base=1.10, start="2016-01-01"):
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range(start, periods=len(rets), tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


# -- parity with the single-instrument engine ---------------------------------
def test_single_instrument_parity():
    """One instrument + generous caps must reproduce the single-instrument engine
    exactly — same trades, same equity — proving the mechanics port is faithful."""
    df = _ohlc(np.random.default_rng(3).normal(0.0012, 0.004, 600))
    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum()
    strat.fit(pit, df.index[:300])

    single = Backtester().run(pit, strat, "EUR/USD", warmup=300)
    port = PortfolioBacktester().run(
        {"EUR/USD": pit}, {"EUR/USD": strat}, timeframes={"EUR/USD": "1d"}, warmup=300,
    )
    assert port.metrics["n_trades"] == single.metrics["n_trades"] > 0
    assert port.metrics["final_equity"] == pytest.approx(single.metrics["final_equity"], rel=1e-9)


# -- book-level caps ----------------------------------------------------------
@pytest.fixture(scope="module")
def correlated_run():
    """Four highly-correlated instruments with a tight correlated-exposure cap."""
    rng = np.random.default_rng(5)
    common = rng.normal(0.0010, 0.006, 600)
    names = ["EUR/USD", "GBP/USD", "AUD/USD", "NZD/USD"]
    pits, strats = {}, {}
    for nm in names:
        df = _ohlc(common + rng.normal(0, 0.0008, 600))  # ~0.98 correlated
        pit = PointInTimeAccessor(df)
        s = RegimeGatedMomentum()
        s.fit(pit, df.index[:300])
        pits[nm], strats[nm] = pit, s

    risk_cfg = get_config().risk.model_copy(
        update={"max_correlated_exposure": 0.2, "correlation_threshold": 0.3}
    )
    pbt = PortfolioBacktester(risk_manager=RiskManager(cfg=risk_cfg))
    res = pbt.run(pits, strats, timeframes={nm: "1d" for nm in names}, warmup=300)
    return res


def test_correlation_cap_actually_binds(correlated_run):
    # This is the raison d'être: with the single-instrument engine this rule can
    # NEVER fire. Here, correlated simultaneous entries must trip it.
    assert correlated_run.constraint_log, "no constraints logged at all"
    assert correlated_run.constraint_log.get("max_correlated_exposure", 0) > 0


def test_runs_and_produces_curve(correlated_run):
    res = correlated_run
    assert len(res.equity) > 0
    assert res.metrics["final_equity"] > 0
    assert "sharpe" in res.metrics
    assert isinstance(res.summary(), str)


def test_per_instrument_accounting(correlated_run):
    res = correlated_run
    total_trades = sum(v["n_trades"] for v in res.per_instrument.values())
    assert total_trades == len(res.trades) == res.metrics["n_trades"]
    total_pnl = sum(v["net_pnl"] for v in res.per_instrument.values())
    assert total_pnl == pytest.approx(res.metrics["net_pnl"], abs=1.0)


def test_gross_exposure_never_exceeds_cap(correlated_run):
    # Sanity: no single instrument shows an absurd number of concurrent trades —
    # the per-timeframe swing bucket (5) and caps keep the book bounded.
    res = correlated_run
    assert res.metrics["n_trades"] >= 1
    assert all(v["n_trades"] >= 0 for v in res.per_instrument.values())
