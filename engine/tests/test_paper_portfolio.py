"""PaperPortfolio (engine-simulated forward paper book): parity with the
batch PortfolioBacktester, state round-trip, idempotency, one-step advance.

The parity test is the guarantee behind the paper program: stepping day-by-day
over persisted state must reproduce run()'s monolithic loop EXACTLY — same
equity curve, same trades, same per-instrument accounting, same constraint log.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from apex_quant.backtest import PortfolioBacktester
from apex_quant.backtest.paper import PaperPortfolio
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.strategies import RegimeGatedMomentum


def _ohlc(rets, base=1.10, start="2016-01-01", calendar="bday"):
    close = base * np.exp(np.cumsum(rets))
    op = np.concatenate([[base], close[:-1]])
    hi = np.maximum(op, close) * 1.003
    lo = np.minimum(op, close) * 0.997
    if calendar == "bday":
        idx = pd.bdate_range(start, periods=len(rets), tz="UTC", name="timestamp")
    else:
        idx = pd.date_range(start, periods=len(rets), tz="UTC", name="timestamp")
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)


# Mixed calendar on purpose: FX/equity-style business-day instruments plus a
# crypto-style 7-day instrument — the union calendar is how the real book runs.
_NAMES = ["EUR/USD", "GBP/USD", "AAPL", "BTC/USD"]
_N_BARS = 600
_WARMUP = 300


def _panel():
    rng = np.random.default_rng(5)
    common = rng.normal(0.0010, 0.006, _N_BARS)
    frames = {
        "EUR/USD": _ohlc(common + rng.normal(0, 0.0008, _N_BARS), base=1.10),
        "GBP/USD": _ohlc(common + rng.normal(0, 0.0008, _N_BARS), base=1.25),
        "AAPL": _ohlc(rng.normal(0.0011, 0.009, _N_BARS), base=150.0),
        "BTC/USD": _ohlc(rng.normal(0.0015, 0.020, _N_BARS), base=20000.0, calendar="daily"),
    }
    return frames


def _fitted_strats(panel):
    strats = {}
    for nm, df in panel.items():
        s = RegimeGatedMomentum()
        s.fit(PointInTimeAccessor(df), df.index[:_WARMUP])
        strats[nm] = s
    return strats


def _trade_key(t):
    return (t.instrument, t.direction, t.entry_time, t.exit_time,
            round(t.entry_price, 6), round(t.exit_price, 6), round(t.pnl, 2), t.exit_reason)


# -- the parity proof -------------------------------------------------------------
def test_stepper_matches_backtester():
    panel = _panel()
    pits = {nm: PointInTimeAccessor(df) for nm, df in panel.items()}
    tfs = {nm: "1d" for nm in panel}
    cutoff = max(df.index[-1] for df in panel.values())

    batch = PortfolioBacktester().run(
        pits, _fitted_strats(panel), timeframes=tfs, warmup=_WARMUP, periods_per_year=252,
    )

    stepper = PaperPortfolio(panel, _fitted_strats(panel), warmup=_WARMUP)
    recs = stepper.advance(cutoff)
    assert len(recs) == len(batch.equity)

    stepped = stepper.equity_series()
    pd.testing.assert_index_equal(stepped.index, batch.equity.index)
    assert stepped.to_numpy() == pytest.approx(batch.equity.to_numpy(), rel=1e-9)

    assert len(stepper._trades) == len(batch.trades) > 0
    assert sorted(_trade_key(t) for t in stepper._trades) == sorted(_trade_key(t) for t in batch.trades)

    assert dict(stepper._constraint_log) == batch.constraint_log
    for inst in panel:
        assert stepper._per_inst[inst]["n_trades"] == batch.per_instrument[inst]["n_trades"]
        assert stepper._per_inst[inst]["net_pnl"] == pytest.approx(
            batch.per_instrument[inst]["net_pnl"], rel=1e-9)


# -- state round-trip ---------------------------------------------------------------
def test_state_roundtrip(tmp_path):
    panel = _panel()
    stepper = PaperPortfolio(panel, _fitted_strats(panel), warmup=_WARMUP)
    all_dates = stepper.union_dates()
    mid, end = all_dates[len(all_dates) // 2], all_dates[-1]
    stepper.advance(mid)

    # JSON round-trip through an actual file (the persistence path the script uses)
    p = tmp_path / "state.json"
    stepper.save_state(p)
    restored = PaperPortfolio(panel, _fitted_strats(panel), warmup=_WARMUP,
                              state=PaperPortfolio.load_state_file(p))
    assert json.dumps(stepper.to_state(), sort_keys=True) == json.dumps(restored.to_state(), sort_keys=True)

    # both advance identically from the restored point
    recs_a = stepper.advance(end)
    recs_b = restored.advance(end)
    assert [r["equity"] for r in recs_a] == pytest.approx([r["equity"] for r in recs_b], rel=1e-12)
    assert json.dumps(stepper.to_state(), sort_keys=True) == json.dumps(restored.to_state(), sort_keys=True)


# -- idempotency ---------------------------------------------------------------------
def test_idempotent_no_new_bars():
    panel = _panel()
    stepper = PaperPortfolio(panel, _fitted_strats(panel), warmup=_WARMUP)
    cutoff = max(df.index[-1] for df in panel.values())
    recs = stepper.advance(cutoff)
    assert recs, "expected at least one processed bar"
    before = json.dumps(stepper.to_state(), sort_keys=True)

    # re-running the same day (no new bars) must be a strict no-op
    assert stepper.advance(cutoff) == []
    assert json.dumps(stepper.to_state(), sort_keys=True) == before


# -- one-step advance from a fresh seed -----------------------------------------------
def test_one_step_advance_from_seed():
    panel = _panel()
    stepper = PaperPortfolio(panel, _fitted_strats(panel), warmup=_WARMUP)
    dates = stepper.union_dates()
    cutoff = dates[-1] + pd.Timedelta(days=1)   # "tomorrow": every panel bar is closed
    wm = stepper.seed_watermark(cutoff)
    assert wm == dates[-2]
    recs = stepper.advance(cutoff)
    assert len(recs) == 1
    assert recs[0]["date"] == str(dates[-1].date())
    assert recs[0]["equity"] == stepper.initial_equity      # flat book marks at cash
    assert stepper.last_processed == dates[-1]
    assert len(stepper.equity_series()) == 1
    # pending entries are plain Position payloads waiting for the next bar
    for inst, d in stepper.pending_entries.items():
        assert d["pos"].permitted and d["tf"] == "1d" and inst in panel


# -- the experiment HALT overlay (paper-only; off in the parity test) -------------------
def test_halt_blocks_new_entries():
    panel = _panel()
    stepper = PaperPortfolio(panel, _fitted_strats(panel), warmup=_WARMUP, halt_drawdown=0.0)
    dates = stepper.union_dates()
    recs = stepper.advance(dates[min(len(dates) - 1, _WARMUP + 10)])
    assert stepper.halted                       # 0.0 threshold trips on day one
    assert any(r["halt_triggered"] for r in recs)
    after = [r for r in recs if r["halted"]]
    assert all(not r["decisions"] and not r["entries"] for r in after)
