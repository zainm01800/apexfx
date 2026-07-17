"""Regression tests for the 2026-07-17 consolidated audit, engine-core section.

Each test names the finding it locks in:
  E1  portfolio/paper time-stops follow the strategy holding_horizon
  E2  backtests pass bar time into RiskManager.permit (no wall clock)
  E3  baseline Bollinger cache is scoped per data object (no cross-talk)
  E4  engine-level regime uses the same eps scaling as the strategy gate
  E5  per-timeframe annualization for Sharpe / ann_return / Calmar
  E6  book risk after partials/BE is units x |last - stop|, not scaled initial
  E7  breakeven buffer is be_buffer_pips x pip_size (3.0 pips, no x100 hack)
  A-H2 Bayesian sizer vetoes non-positive Kelly after adaptation
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest import Backtester, PortfolioBacktester
from apex_quant.backtest.result import compute_metrics
from apex_quant.config import get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.regime.rule_based import RuleBasedRegime, regime_config_for
from apex_quant.risk.bayesian_sizer import BayesianRiskSizer
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.trade_manager import TradeManager
from apex_quant.risk.types import AccountState, Direction, MarketState, OpenPosition, Signal
from apex_quant.strategies import RegimeGatedMomentum


def _ohlc(rets, base=1.10, start="2016-01-01"):
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    idx = pd.bdate_range(start, periods=len(rets), tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


def _pos(entry=1.1000, stop=1.0900, target=1.1300, units=100_000.0):
    return {
        "symbol": "EUR/USD", "direction": Direction.LONG,
        "units": units, "initial_units": units,
        "entry_price": entry, "entry_time": pd.Timestamp("2024-01-01", tz="UTC"),
        "entry_idx": 0, "stop": stop, "initial_stop": stop, "target": target,
        "tms_p1": False, "tms_p2": False, "tms_be": False, "bars_open": 0,
        "tms_log": [], "realized_pnl_total": 0.0,
    }


def _tm_step(tm, pos, hi, lo, close, pip_size=0.0001, **kw):
    return tm.update_position(
        position=pos, high=hi, low=lo, close=close, atr=0.001,
        is_squeeze=False, bars_history={"high": hi, "low": lo, "len": 5},
        timeframe="1h", pip_size=pip_size, fill_fn=lambda p, b: p, **kw,
    )


# -- E1: per-call max_bars drives the managed time-stop -------------------------
def test_e1_per_call_max_bars_overrides_time_stop_table():
    tm = TradeManager()
    # Stagnant trade (close pinned at entry -> current_r ~ 0): with max_bars=2
    # the third bar must time-stop it, exactly like the barrier engine's max_hold.
    pos = _pos()
    reason = ""
    for _ in range(3):
        _pnl, reason = _tm_step(tm, pos, 1.1005, 1.0995, 1.1001, max_bars=2)
    assert reason == "time"

    # Same trade with the strategy's longer horizon stays open.
    pos = _pos()
    reason = ""
    for _ in range(3):
        _pnl, reason = _tm_step(tm, pos, 1.1005, 1.0995, 1.1001, max_bars=10)
    assert reason == ""

    # No override -> the per-timeframe table still applies (live compatibility).
    tm_tbl = TradeManager(time_stop_bars={"1h": 2})
    pos = _pos()
    reason = ""
    for _ in range(3):
        _pnl, reason = _tm_step(tm_tbl, pos, 1.1005, 1.0995, 1.1001)
    assert reason == "time"


# -- E7: breakeven buffer is pips x pip_size ------------------------------------
def test_e7_breakeven_buffer_is_three_pips():
    tm = TradeManager()  # default be_buffer_pips=3.0
    pos = _pos()
    _tm_step(tm, pos, 1.1105, 1.0995, 1.1100)  # high clears the 1R partial line
    assert pos["tms_p1"] and pos["tms_be"]
    assert pos["stop"] == pytest.approx(1.1000 + 3.0 * 0.0001)  # not the old ~3e-8

    # pip_size is JPY-aware at the call sites, so the same 3 pips works there too
    pos_j = _pos(entry=150.00, stop=149.00, target=153.00)
    _tm_step(tm, pos_j, 151.05, 149.95, 151.00, pip_size=0.01)
    assert pos_j["stop"] == pytest.approx(150.00 + 3.0 * 0.01)


# -- E2: bar time, not wall clock, reaches the news filter ----------------------
class _RecordingNewsFilter:
    def __init__(self):
        self.seen: list[pd.Timestamp] = []

    def check_veto(self, instrument, t):
        self.seen.append(pd.Timestamp(t))
        return False, ""


def test_e2_engine_passes_bar_time_to_permit():
    df = _ohlc(np.random.default_rng(3).normal(0.0012, 0.004, 600))
    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum()
    strat.fit(pit, df.index[:300])
    nf = _RecordingNewsFilter()
    Backtester(risk_manager=RiskManager(news_filter=nf)).run(
        pit, strat, "EUR/USD", warmup=300, timeframe="1d",
    )
    assert nf.seen, "permit() was never reached"
    bar_times = set(df.index)
    assert all(t in bar_times for t in nf.seen)  # every check at a BAR time


def _small_portfolio_run(seed=11):
    rng = np.random.default_rng(seed)
    pits, strats = {}, {}
    for nm in ("EUR/USD", "GBP/USD"):
        df = _ohlc(rng.normal(0.0010, 0.006, 400))
        pit = PointInTimeAccessor(df)
        s = RegimeGatedMomentum()
        s.fit(pit, df.index[:250])
        pits[nm], strats[nm] = pit, s
    return PortfolioBacktester().run(
        pits, strats, timeframes={"EUR/USD": "1d", "GBP/USD": "1d"}, warmup=250,
    )


def test_e2_portfolio_backtest_is_deterministic():
    r1 = _small_portfolio_run()
    r2 = _small_portfolio_run()
    pd.testing.assert_series_equal(r1.equity, r2.equity)  # byte-identical
    assert r1.metrics == r2.metrics


# -- E3: Bollinger cache is scoped per data object ------------------------------
def _mr_frame(last_close, n=260, seed=3):
    """Flat (ranging) series with one engineered band break on the final bar."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n, tz="UTC", name="timestamp")
    close = 1.10 + rng.normal(0.0, 0.0002, n)
    close[-1] = last_close
    op = np.concatenate([[1.10], close[:-1]])
    hi = np.maximum(op, close) + 0.0005
    lo = np.minimum(op, close) - 0.0005
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


def test_e3_bb_cache_no_cross_talk_between_datasets():
    # Same timestamps, same instrument/timeframe key, DIFFERENT data: the dip
    # dataset must answer LONG and the spike dataset SHORT — the old flat
    # (instrument, timeframe, t) cache served the first result to both.
    dip = PointInTimeAccessor(_mr_frame(1.08))    # closes below the lower band
    spike = PointInTimeAccessor(_mr_frame(1.12))  # closes above the upper band
    t = dip.end
    assert t == spike.end

    strat_a = RegimeGatedMomentum(instrument="EUR/USD", timeframe="1d")
    strat_b = RegimeGatedMomentum(instrument="EUR/USD", timeframe="1d")
    ev_a = strat_a._evaluate(dip, t)
    ev_b = strat_b._evaluate(spike, t)

    assert ev_a["mode"] == "mean_reversion" and ev_a["direction"] == Direction.LONG
    assert ev_b["mode"] == "mean_reversion" and ev_b["direction"] == Direction.SHORT


# -- E4: one eps scaling shared by strategy gate and engine regime --------------
def test_e4_regime_eps_scaling_matches_strategy_gate():
    base = get_config().regime.rule_based.ranging_slope_eps
    assert regime_config_for("1d", "forex").ranging_slope_eps == pytest.approx(base)
    assert regime_config_for("1h", "forex").ranging_slope_eps == pytest.approx(base * 0.15)
    assert regime_config_for("15m", "forex").ranging_slope_eps == pytest.approx(base * 0.05)
    assert regime_config_for("15m", "crypto").ranging_slope_eps == pytest.approx(base * 0.05 * 8.0)
    assert regime_config_for("1d", "crypto").ranging_slope_eps == pytest.approx(base * 5.0)
    assert regime_config_for("1d", "equity").ranging_slope_eps == pytest.approx(base * 1.5)

    # The strategy gate resolves the SAME eps through the same helper.
    strat = RegimeGatedMomentum(instrument="EUR/USD", timeframe="1h")
    assert strat._regime.cfg.ranging_slope_eps == pytest.approx(base * 0.15)


def test_e4_engine_regime_matches_strategy_regime():
    df = _ohlc(np.random.default_rng(3).normal(0.0012, 0.004, 600))
    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum(instrument="EUR/USD", timeframe="1h")
    strat.fit(pit, df.index[:300])
    bt = Backtester()
    bt.run(pit, strat, "EUR/USD", warmup=300, timeframe="1h")
    # Engine-level regime (risk scaling) sees the same semantics as the signal.
    assert isinstance(bt._regime, RuleBasedRegime)
    assert bt._regime.cfg.ranging_slope_eps == pytest.approx(
        strat._regime.cfg.ranging_slope_eps
    )


# -- E5: per-timeframe annualization ---------------------------------------------
def test_e5_bars_per_year_session_conventions():
    cfg = get_config()
    assert cfg.bars_per_year("EUR/USD", "1d") == 252.0
    assert cfg.bars_per_year("EUR/USD", "1w") == 52.0
    assert cfg.bars_per_year("EUR/USD", "1h") == pytest.approx(24 * 5 * 52.14)
    assert cfg.bars_per_year("EUR/USD", "15m") == pytest.approx(4 * 24 * 5 * 52.14)
    assert cfg.bars_per_year("BTC/USD", "1d") == 365.0
    assert cfg.bars_per_year("BTC/USD", "1h") == pytest.approx(24 * 365)
    assert cfg.bars_per_year("AAPL", "1d") == 252.0
    assert cfg.bars_per_year("EUR/USD", None) == 252.0  # unknown tf -> daily convention


def test_e5_sharpe_scales_with_sqrt_bars_per_year():
    rng = np.random.default_rng(7)
    eq = pd.Series(100_000 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, 500))))
    daily = compute_metrics(eq, [], 252.0)["sharpe"]
    hourly = compute_metrics(eq, [], get_config().bars_per_year("EUR/USD", "1h"))["sharpe"]
    # ~sqrt(24): the 1h Sharpe was understated by exactly this before E5.
    assert hourly / daily == pytest.approx(np.sqrt((24 * 5 * 52.14) / 252.0), rel=1e-9)


def test_e5_engine_threads_timeframe_into_metrics():
    df = _ohlc(np.random.default_rng(3).normal(0.0012, 0.004, 600))
    pit = PointInTimeAccessor(df)
    strat = RegimeGatedMomentum()
    strat.fit(pit, df.index[:300])
    res = Backtester().run(pit, strat, "EUR/USD", warmup=300, timeframe="1h")
    expect = compute_metrics(res.equity, res.trades, get_config().bars_per_year("EUR/USD", "1h"))
    assert res.metrics["sharpe"] == pytest.approx(expect["sharpe"])
    assert res.metrics["ann_return"] == pytest.approx(expect["ann_return"])
    assert res.metrics["calmar"] == pytest.approx(expect["calmar"])


# -- E6: book risk after partials/BE is the live-stop distance ------------------
def _be_posd():
    # After partial-1 (half closed) + breakeven move: stop at entry + 3 pips.
    return {
        "symbol": "EUR/USD", "direction": Direction.LONG,
        "units": 50_000.0, "initial_units": 100_000.0,
        "entry_price": 1.1000, "stop": 1.1003, "initial_stop": 1.0900,
        "target": 1.1300, "risk_abs": 3_000.0, "tf": "1d", "last_px": 1.1000,
    }


def test_e6_open_risk_tracks_live_stop_not_initial_fraction():
    pbt = PortfolioBacktester()
    rec = pbt._open_record("EUR/USD", _be_posd())
    # Old logic: 3000 x (50k/100k) = 1500. New: 50_000 x |1.1000 - 1.1003| = 15.
    assert rec.risk == pytest.approx(15.0)

    # Untouched position at the initial stop: full risk (units x stop distance).
    fresh = dict(_be_posd(), units=100_000.0, stop=1.0900)
    assert pbt._open_record("EUR/USD", fresh).risk == pytest.approx(1_000.0)


def test_e6_portfolio_cap_no_longer_blocks_after_breakeven():
    pbt = PortfolioBacktester()
    book = [pbt._open_record("EUR/USD", _be_posd())]
    cfg = get_config().risk.model_copy(update={"max_portfolio_risk": 0.01})  # = 1000 on 100k
    rm = RiskManager(cfg=cfg)
    sig = Signal(instrument="GBP/USD", direction=Direction.LONG,
                 probability=0.8, reward_risk=2.0, timeframe="1d")
    mkt = MarketState(instrument="GBP/USD", price=1.25, ann_vol=0.08, atr=0.01)
    acct = AccountState(equity=100_000.0, peak_equity=100_000.0, open_positions=book)
    pos = rm.permit(sig, acct, mkt)
    assert pos.permitted
    assert "max_portfolio_risk_exceeded" not in pos.constraints_applied

    # Same book measured the OLD way (1500 > 1000) tripped the cap — the blocked
    # entry this fix unblocks.
    stale = [OpenPosition(instrument="EUR/USD", direction=Direction.LONG,
                          notional=55_000.0, risk=1_500.0, timeframe="1d")]
    vetoed = rm.permit(sig, AccountState(
        equity=100_000.0, peak_equity=100_000.0, open_positions=stale), mkt)
    assert not vetoed.permitted
    assert "max_portfolio_risk_exceeded" in vetoed.constraints_applied


# -- A-H2: negative Kelly after adaptation vetoes --------------------------------
def _mk_signal(instrument="EUR/USD", b=2.0):
    return Signal(instrument=instrument, direction=Direction.LONG, probability=0.6, reward_risk=b)


def _mk_account(equity=100_000.0, peak=100_000.0):
    return AccountState(equity=equity, peak_equity=peak)


def test_ah2_proven_losing_record_vetoes():
    s = BayesianRiskSizer(min_risk=0.005, min_trades_for_adaptation=5)
    for _ in range(30):
        s.record_outcome("EUR/USD", False, pnl=-1.0)  # demonstrated loser
    assert s.risk_fraction(_mk_signal(), _mk_account()) is None

    # The min_risk floor survives ONLY in the pre-adaptation cold start.
    cold = BayesianRiskSizer(min_risk=0.005, min_trades_for_adaptation=20)
    for _ in range(5):
        cold.record_outcome("EUR/USD", False, pnl=-1.0)
    assert cold.risk_fraction(_mk_signal(), _mk_account()) == pytest.approx(0.005)


def test_ah2_veto_flows_through_risk_manager_like_no_edge():
    sizer = BayesianRiskSizer(min_trades_for_adaptation=5, min_risk=0.005, max_risk=0.02)
    for _ in range(30):
        sizer.record_outcome("EUR/USD", False, pnl=-1.0)
    # Manager breaker disabled so only the sizer's verdict is under test.
    cfg = get_config().risk.model_copy(update={"drawdown_breaker": 0.9, "drawdown_reducing_limit": 0.9})
    rm = RiskManager(cfg=cfg, bayesian_sizer=sizer)
    pos = rm.permit(_mk_signal(), _mk_account(),
                    MarketState(instrument="EUR/USD", price=1.10, ann_vol=0.08, atr=0.01))
    assert not pos.permitted
    assert "bayesian_no_edge" in pos.constraints_applied

    # Positive edge from an informed posterior still trades.
    winner = BayesianRiskSizer(min_trades_for_adaptation=5, min_risk=0.005, max_risk=0.02)
    for _ in range(30):
        winner.record_outcome("EUR/USD", True, pnl=2.0)
    rm_w = RiskManager(cfg=cfg, bayesian_sizer=winner)
    assert rm_w.permit(_mk_signal(), _mk_account(),
                       MarketState(instrument="EUR/USD", price=1.10, ann_vol=0.08, atr=0.01)).permitted
