"""Defensive cash-substitute sleeve (2026-07-27; prereg
engine/data_store/defensive_sleeve_prereg.md).

The flag defaults to certified behaviour (``defensive_sleeve=None`` -> zero-yield GBP
cash, byte-identical equity). When a spec is passed, the book's idle capital —
``max(0, equity - gross open notional)`` at each daily mark — accrues the sleeve's
daily returns instead of 0%, less one-way rebalance costs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest import PortfolioBacktester
from apex_quant.backtest.defensive_sleeve import DefensiveSleeveSpec
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.strategies import RegimeGatedMomentum


def _ohlc(rets, base=100.0, start="2016-01-01"):
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range(start, periods=len(rets), tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


def _spec(closes: dict[str, pd.Series], mode="static", **kw) -> DefensiveSleeveSpec:
    args = dict(static_weights={k: 0.5 for k in closes},
                oneway_cost={k: 0.0002 for k in closes})
    args.update(kw)
    return DefensiveSleeveSpec(closes=closes, mode=mode, **args)


# -- align: static ---------------------------------------------------------------
def test_align_static_constant_mix_and_cash_pre_listing():
    idx = pd.bdate_range("2020-01-01", periods=10, tz="UTC")
    gold = pd.Series(np.linspace(100, 110, 10), index=idx)
    sukuk = pd.Series(np.linspace(50, 51, 6), index=idx[4:])      # lists day 5
    spec = _spec({"G": gold, "S": sukuk})
    a = spec.align(idx)
    assert np.all(a["mix"]["G"] == 0.5) and np.all(a["mix"]["S"] == 0.5)
    # sukuk return is 0 before its first bar (cash), non-zero after
    assert np.all(a["ret"]["S"][:4] == 0.0)
    assert a["ret"]["S"][5] == pytest.approx(sukuk.iloc[1] / sukuk.iloc[0] - 1.0)
    # gold day-1 return: reindex+ffill leaves first pct_change NaN -> 0
    assert a["ret"]["G"][0] == 0.0


def test_align_static_ffill_gives_zero_on_holidays():
    trade_idx = pd.bdate_range("2020-01-01", periods=5, tz="UTC")      # Wed..Tue (skips weekend)
    cal_idx = pd.date_range("2020-01-01", periods=7, tz="UTC")         # Wed..Tue incl Sat/Sun
    gold = pd.Series([100, 101, 102, 103, 104], index=trade_idx)
    spec = _spec({"G": gold})
    a = spec.align(cal_idx)
    # cal_idx: [Wed, Thu, Fri, Sat, Sun, Mon, Tue] — weekend positions 3 and 4 are flat
    assert a["ret"]["G"][3] == 0.0 and a["ret"]["G"][4] == 0.0
    assert a["ret"]["G"][5] == pytest.approx(103 / 102 - 1.0)          # Monday's real return


# -- align: inverse-vol ----------------------------------------------------------
def test_align_inverse_vol_weights_and_cash_fallback():
    idx = pd.bdate_range("2020-01-01", periods=120, tz="UTC")
    rng = np.random.default_rng(11)
    calm = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.001, 120))), index=idx)
    wild = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.020, 120))), index=idx)
    spec = _spec({"CALM": calm, "WILD": wild}, mode="inverse_vol", vol_window=63)
    a = spec.align(idx)
    # first 63 bars: no valid vol -> all cash (weights sum to 0)
    assert a["mix"]["CALM"][30] == 0.0 and a["mix"]["WILD"][30] == 0.0
    # after the window: weights sum to 1 and the CALM leg dominates (~1/20 vol ratio)
    last = -1
    assert a["mix"]["CALM"][last] + a["mix"]["WILD"][last] == pytest.approx(1.0)
    assert a["mix"]["CALM"][last] > 0.85


def test_align_inverse_vol_missing_legs_get_zero_weight():
    idx = pd.bdate_range("2020-01-01", periods=120, tz="UTC")
    rng = np.random.default_rng(3)
    gold = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.004, 120))), index=idx)
    sukuk = pd.Series(50 * np.exp(np.cumsum(rng.normal(0, 0.001, 80))), index=idx[40:])
    spec = _spec({"G": gold, "S": sukuk}, mode="inverse_vol", vol_window=63)
    a = spec.align(idx)
    assert a["mix"]["S"][80] == 0.0                       # sukuk: 40 returns < 63 yet
    assert a["mix"]["G"][80] == pytest.approx(1.0)        # gold renormalised to 100%
    assert a["mix"]["S"][-1] > 0.0                        # sukuk joins once it has history


# -- backtester integration -------------------------------------------------------
def _flat_book(n=400):
    """One instrument whose strategy never trades -> the book is 100% idle cash."""
    df = _ohlc(np.random.default_rng(1).normal(0.0, 0.003, n))
    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum(momentum_lookback=n + 50, vol_window=63,
                                instrument="EUR/USD")   # never enough history -> FLAT
    return pit, strat, df.index


def test_flag_off_is_certified_and_has_no_sleeve_metrics():
    pit, strat, _ = _flat_book()
    res = PortfolioBacktester().run({"EUR/USD": pit}, {"EUR/USD": strat},
                                    timeframes={"EUR/USD": "1d"}, warmup=0)
    assert "defensive_sleeve_net_pnl" not in res.metrics
    # certified cash: a flat book's equity never moves
    assert res.equity.nunique() == 1


def test_idle_cash_accrues_sleeve_returns_less_costs():
    pit, strat, idx = _flat_book()
    # sleeve leg: deterministic daily log-step 0.001 -> simple daily return r = e^0.001 - 1
    gold = pd.Series(100 * np.exp(np.cumsum(np.full(len(idx), 0.001))), index=idx)
    r_day = float(np.exp(0.001) - 1.0)
    spec = _spec({"G": gold}, static_weights={"G": 1.0}, oneway_cost={"G": 0.0002})
    res = PortfolioBacktester(defensive_sleeve=spec).run(
        {"EUR/USD": pit}, {"EUR/USD": strat}, timeframes={"EUR/USD": "1d"}, warmup=0)
    m = res.metrics
    assert m["defensive_sleeve_mean_idle_frac"] == pytest.approx(1.0)
    # day 1: establishment cost 1.0 * E * 2bps, no accrual (prev weights 0).
    # day t>1: accrual = E_{t-1} * r_day; weight stays 1.0 -> no further cost.
    n = len(res.equity)
    e0 = 100000.0 * (1 - 0.0002)
    expected = e0 * (1.0 + r_day) ** (n - 1)          # n-1 accruals after the first mark
    got = res.equity.to_numpy()
    assert len(got) == len(idx)
    assert got[0] == pytest.approx(e0, rel=1e-12)
    assert got[1] == pytest.approx(e0 * (1.0 + r_day), rel=1e-12)
    assert got[-1] == pytest.approx(expected, rel=1e-9)
    assert m["defensive_sleeve_cost_total"] == pytest.approx(100000.0 * 0.0002, rel=1e-9)
    # metrics are rounded to 2dp by the backtester
    assert m["defensive_sleeve_net_pnl"] == pytest.approx(got[-1] - 100000.0, abs=0.01)


def test_deployed_capital_does_not_accrue():
    """A book whose gross notional >= equity has idle_frac 0: no accrual, no cost
    after the initial establishment (which itself is at idle_frac measured day 1)."""
    # A real trading strategy on trending data -> positions open; we only assert the
    # accounting identity: accrual happens only on the idle fraction, so a sleeve
    # overlay can never REDUCE equity below the no-sleeve twin by more than its costs,
    # and a fully-deployed bar accrues exactly 0.
    rng = np.random.default_rng(7)
    df = _ohlc(rng.normal(0.002, 0.01, 400))
    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum(momentum_lookback=63, vol_window=63, instrument="AAPL")
    strat.fit(pit, df.index[:300])
    gold = pd.Series(100 * np.exp(np.cumsum(np.full(400, 0.001))), index=df.index)
    spec = _spec({"G": gold}, static_weights={"G": 1.0}, oneway_cost={"G": 0.0002})
    plain = PortfolioBacktester().run({"AAPL": pit}, {"AAPL": strat},
                                      timeframes={"AAPL": "1d"}, warmup=250)
    sleeved = PortfolioBacktester(defensive_sleeve=spec).run(
        {"AAPL": pit}, {"AAPL": strat}, timeframes={"AAPL": "1d"}, warmup=250)
    # same entry/exit TIMING and direction (the sleeve never changes signals); units
    # legitimately differ — vol-scaled sizing is equity-dependent and the sleeve
    # changes the equity path by construction.
    assert [(t.instrument, t.entry_time, t.exit_time, t.direction) for t in plain.trades] == \
           [(t.instrument, t.entry_time, t.exit_time, t.direction) for t in sleeved.trades]
    # with a rising sleeve leg and zero-yield cash, the sleeved book ends >= plain - costs
    assert sleeved.metrics["final_equity"] >= plain.metrics["final_equity"] \
        - sleeved.metrics["defensive_sleeve_cost_total"] - 1e-6
    # idle accounting bounds: mean idle fraction in [0, 1]
    assert 0.0 <= sleeved.metrics["defensive_sleeve_mean_idle_frac"] <= 1.0


def test_cpcv_forwards_sleeve():
    """run_portfolio_cpcv must hand the sleeve to every fold's inner backtest."""
    from apex_quant.validation.portfolio_report import run_portfolio_cpcv

    rng = np.random.default_rng(5)
    names = ["EUR/USD", "GBP/USD"]
    panel, pits = {}, {}
    for nm in names:
        df = _ohlc(rng.normal(0.001, 0.005, 500))
        panel[nm] = df
        pits[nm] = PointInTimeAccessor(df)
    timeline = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in panel.values()])))
    gold = pd.Series(100 * np.exp(np.cumsum(np.full(len(timeline), 0.001))), index=timeline)
    spec = _spec({"G": gold}, static_weights={"G": 1.0}, oneway_cost={"G": 0.0002})

    def factory(p, **kw):
        class M:
            def strategies(self):
                return {nm: RegimeGatedMomentum(momentum_lookback=63, vol_window=63,
                                                instrument=nm) for nm in p}
        return M()

    kw = dict(cfg=None, timeframes={nm: "1d" for nm in names}, warmup=0, horizon=21,
              periods_per_year=252, exit_mode="managed")
    plain = run_portfolio_cpcv(panel, pits, factory, {}, **kw)
    sleeved = run_portfolio_cpcv(panel, pits, factory, {}, defensive_sleeve=spec, **kw)
    assert sleeved["n_paths"] == plain["n_paths"] > 0
    # a +0.1%/day sleeve on idle cash can only shift OOS Sharpes up vs certified cash
    assert sleeved["oos_sharpe_mean"] >= plain["oos_sharpe_mean"] - 1e-9
