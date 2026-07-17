"""Backtester: end-to-end run, leakage, cost impact, exit mechanics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.config import get_config
from apex_quant.backtest import Backtester
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import Direction
from apex_quant.strategies import RegimeGatedMomentum


def _series(rets, start="2018-01-01", base=1.10):
    n = len(rets)
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range(start, periods=n, tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


def _trend(n=700, drift=0.0012, noise=0.004, seed=3):
    rng = np.random.default_rng(seed)
    return _series(rng.normal(drift, noise, n))


def _fitted(df, train_n=350):
    strat = RegimeGatedMomentum()
    strat.fit(PointInTimeAccessor(df), df.index[:train_n])
    return strat


# -- end-to-end ----------------------------------------------------------------
def test_backtest_runs_and_reports():
    df = _trend()
    pit = PointInTimeAccessor(df)
    res = Backtester().run(pit, _fitted(df), "EUR/USD", warmup=350)
    assert len(res.equity) > 0
    assert "sharpe" in res.metrics
    assert res.metrics["n_trades"] >= 1
    assert res.metrics["final_equity"] > 0


def test_accepts_tz_aware_start_end():
    """start/end may be tz-aware (as df.index yields) or naive strings."""
    df = _trend()
    pit = PointInTimeAccessor(df)
    strat = _fitted(df, 300)
    split = df.index[400]  # tz-aware UTC Timestamp
    res = Backtester().run(pit, strat, "EUR/USD", start=split, warmup=0)
    assert res.equity.index.min() >= split


def test_no_trades_when_flat_market():
    rng = np.random.default_rng(2)
    df = _series(rng.normal(0.0, 0.0004, 500))  # flat -> ranging -> no momentum trades
    pit = PointInTimeAccessor(df)
    # Pure momentum has no trend to follow on a flat/ranging market, so no trades.
    # Bollinger mean-reversion is a separate feature that DOES trade ranges by design;
    # it is disabled here so this test isolates the momentum invariant it was written for.
    strat = RegimeGatedMomentum(enable_mean_reversion=False)
    strat.fit(pit, df.index[:300])
    res = Backtester().run(pit, strat, "EUR/USD", warmup=300)
    assert res.metrics["n_trades"] == 0
    # equity flat (no positions ever opened)
    assert res.equity.nunique() == 1


# -- leakage: pre-cutoff equity invariant to future poison ---------------------
def test_backtest_equity_is_point_in_time():
    df = _trend()
    strat = _fitted(df, 300)          # fit on clean early slice
    bt = Backtester()
    clean = bt.run(PointInTimeAccessor(df), strat, "EUR/USD", warmup=300)

    cutoff = df.index[500]
    poisoned = df.copy()
    poisoned.loc[poisoned.index > cutoff, ["open", "high", "low", "close"]] *= 1000.0
    poison = bt.run(PointInTimeAccessor(poisoned), strat, "EUR/USD", warmup=300)

    a = clean.equity.loc[clean.equity.index <= cutoff]
    b = poison.equity.loc[poison.equity.index <= cutoff]
    assert np.allclose(a.to_numpy(), b.to_numpy())


# -- costs reduce performance --------------------------------------------------
def test_higher_costs_reduce_equity():
    df = _trend()
    strat = _fitted(df, 300)

    cheap = get_config().model_copy(deep=True)
    cheap.backtest.spread_pips = 0.0
    cheap.backtest.slippage_bps = 0.0

    dear = get_config().model_copy(deep=True)
    dear.backtest.spread_pips = 10.0
    dear.backtest.slippage_bps = 5.0

    r_cheap = Backtester(cfg=cheap).run(PointInTimeAccessor(df), strat, "EUR/USD", warmup=300)
    r_dear = Backtester(cfg=dear).run(PointInTimeAccessor(df), strat, "EUR/USD", warmup=300)
    assert r_cheap.metrics["n_trades"] >= 1
    assert r_dear.metrics["final_equity"] <= r_cheap.metrics["final_equity"]


# -- per-pair cost overrides (config v5, measured live round-trip costs) --------
def test_fill_uses_per_pair_cost_override():
    bt = Backtester()
    # EUR/NZD has a measured 10.1 pips round-trip override -> 5.05 pips per fill,
    # and the override replaces the class slippage entirely.
    px = bt._fill(1.7800, "EUR/NZD", buying=True, timeframe="1h")
    assert px == pytest.approx(1.7800 + 0.5 * 10.1 * 0.0001, abs=1e-8)
    # pair×timeframe beats pair level: CAD/JPY 1d = 4.1 pips, other TFs = 2.6
    px_d = bt._fill(110.00, "CAD/JPY", buying=True, timeframe="1d")
    assert px_d == pytest.approx(110.00 + 0.5 * 4.1 * 0.01, abs=1e-8)
    px_s = bt._fill(110.00, "CAD/JPY", buying=True, timeframe="15m")
    assert px_s == pytest.approx(110.00 + 0.5 * 2.6 * 0.01, abs=1e-8)
    # selling is symmetric (cost subtracted)
    px_sell = bt._fill(1.7800, "EUR/NZD", buying=False, timeframe="1h")
    assert px_sell == pytest.approx(1.7800 - 0.5 * 10.1 * 0.0001, abs=1e-8)


def test_fill_cost_defaults_without_override():
    bt = Backtester()
    # EUR/USD (measured mean <= 0, deliberately unlisted) keeps the class
    # default: 1.0 pip half-spread + 0.5 bps slippage per fill
    px = bt._fill(1.1000, "EUR/USD", buying=True, timeframe="1d")
    assert px == pytest.approx(1.1000 + 0.5 * 1.0 * 0.0001 + 0.5 / 1e4 * 1.1000, abs=1e-8)
    # unlisted cross -> conservative cross default (4.85 pips RT, slippage off)
    px_cross = bt._fill(0.8600, "EUR/GBP", buying=True, timeframe="1d")
    assert px_cross == pytest.approx(0.8600 + 0.5 * 4.85 * 0.0001, abs=1e-8)
    # equity bps path untouched by the forex table
    px_eq = bt._fill(200.0, "AAPL", buying=True)
    assert px_eq == pytest.approx(200.0 + (0.5 * 2.0 + 1.0) / 1e4 * 200.0, abs=1e-8)


# -- exit mechanics ------------------------------------------------------------
def _pos(direction, entry=100.0, stop=98.0, target=103.0):
    return {"direction": direction, "units": 100.0, "entry_price": entry,
            "entry_time": pd.Timestamp("2020-01-01", tz="UTC"), "entry_idx": 0,
            "stop": stop, "target": target}


def test_exit_target_then_stop_priority():
    bt = Backtester()
    # long: target hit, stop not -> target
    px, reason = bt._check_exit(_pos(Direction.LONG), hi=104, lo=99, close_px=103, i=1, max_hold=20, instrument="EUR/USD")
    assert reason == "target" and px < 103  # sold slightly below target after costs

    # long: stop hit (and even if target also in range, stop wins) -> stop
    px, reason = bt._check_exit(_pos(Direction.LONG), hi=104, lo=97, close_px=100, i=1, max_hold=20, instrument="EUR/USD")
    assert reason == "stop"


def test_exit_time_barrier():
    bt = Backtester()
    px, reason = bt._check_exit(_pos(Direction.LONG), hi=101, lo=99, close_px=100, i=25, max_hold=20, instrument="EUR/USD")
    assert reason == "time"


def test_short_pnl_positive_when_price_falls():
    bt = Backtester()
    pos = _pos(Direction.SHORT, entry=100.0)
    assert bt._pnl(pos, 95.0) > 0     # short profits as price falls
    assert bt._pnl(pos, 105.0) < 0
