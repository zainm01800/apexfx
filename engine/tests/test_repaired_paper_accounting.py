import copy
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from apex_quant.models.paper_accounting import conversion_rate, lot_pnl, close_fraction
from apex_quant.models.book_f_forward import new_book_f_state, advance_book_f_forward, display_position_rows
from apex_quant.models.book_s_session_smc import new_book_s_state, validate_book_s_state, _date_str
from apex_quant.models.book_s_execution import advance_hours
from apex_quant.backtest.paper import PaperPortfolio
from apex_quant.risk.types import Direction, Position
from apex_quant.models.paper_readiness import require_daily_panel, require_hourly_panel, require_restored_state
from apex_quant.risk.trade_manager import TradeManager


def bars(values, start="2026-09-01", freq="D"):
    return pd.DataFrame(values, columns=["open", "high", "low", "close"],
                        index=pd.date_range(start, periods=len(values), freq=freq, tz="UTC")).assign(volume=1000.)


def test_conversion_is_not_one_to_one_and_cannot_look_ahead():
    fx = bars([[1.25]*4, [2.0]*4])
    panel = {"GBP/USD": fx}
    assert conversion_rate("AAPL", "GBP", panel, fx.index[0]) == .8
    with pytest.raises(ValueError):
        conversion_rate("AAPL", "GBP", panel, "2026-08-31")
    with pytest.raises(ValueError):
        conversion_rate("AAPL", "GBP", panel, "2026-09-10")
    with pytest.raises(ValueError):
        conversion_rate("UNKNOWN.L", "USD", panel, fx.index[0])
    assert conversion_rate("ISWD.L", "GBP", panel, fx.index[0]) == .01


def test_cross_conversion():
    panel = {"GBP/USD": bars([[1.25]*4]), "USD/JPY": bars([[150.]*4])}
    assert conversion_rate("USD/JPY", "GBP", panel, "2026-09-01") == pytest.approx(.8/150)


def f_state():
    st = new_book_f_state("2026-08-31")
    st["positions"]["AAPL"] = dict(direction="long", entry_price=100., entry_time="2026-08-31",
        units=10., initial_units=10., lots=[dict(entry_price=100., units=10.)], stop_loss=90.,
        initial_risk=10., partial_taken=False, pyramided=False, realized_pnl=0., last_close=100., bars_open=0)
    return st


def test_added_lot_does_not_earn_pre_entry_profit():
    pos = {"direction": "long", "lots": [{"entry_price": 100., "units": 5.}, {"entry_price": 115., "units": 5.}]}
    assert lot_pnl(pos, 115.) == 75.
    assert close_fraction(pos, .5, 115.) == 37.5
    assert pos["units"] == 5.


def test_f_gap_stop_precedes_partial_and_pyramid():
    panel = {"AAPL": bars([[80, 120, 79, 100]])}
    original = f_state()
    st, _ = advance_book_f_forward(original, panel, panel["AAPL"].index[-1])
    assert st["trades"][0]["exit_price"] < 80
    assert st["trades"][0]["pnl"] < -200
    assert not st["trades"][0]["pyramided"]
    assert original == f_state()  # no caller mutation


def test_f_pyramid_lots_partial_cash_and_restart_invariance():
    panel = {"AAPL": bars([[109,116,108,115], [115,117,114,116]])}
    original = f_state()
    batch, _ = advance_book_f_forward(original, panel, panel["AAPL"].index[-1])
    split, _ = advance_book_f_forward(original, panel, panel["AAPL"].index[0])
    assert split["cash"] > 100049
    assert split["positions"]["AAPL"]["lots"][1]["entry_price"] > 115
    split = json.loads(json.dumps(split))
    split, _ = advance_book_f_forward(split, panel, panel["AAPL"].index[-1])
    assert split == batch
    rows = display_position_rows(batch)
    assert batch["equity"] == pytest.approx(batch["cash"] + rows[0]["unrealized_pnl"])
    assert batch["equity_curve"][-1]["day_pnl"] == pytest.approx(batch["equity_curve"][-1]["equity"] - batch["equity_curve"][-2]["equity"])
    again, rows = advance_book_f_forward(batch, panel, panel["AAPL"].index[-1])
    assert again == batch and rows == []


def s_run(st, frames, times):
    return advance_hours(st, frames, times, universe=list(frames), risk=500., rr=1.8,
                         max_positions=4, daily_limit=1800., max_hours=16,
                         pip_sizes={s:.01 if s=="USD/JPY" else .0001 for s in frames},
                         spreads={s:1. for s in frames}, stamp=_date_str)


def s_frames(symbol="GBP/USD"):
    return {symbol:bars([[1.2,1.21,1.19,1.2], [1.2,1.205,1.195,1.202], [1.202,1.21,1.2,1.205]],
                        start="2026-09-01 08:00", freq="h").assign(asian_high=1.19,asian_low=1.18,atr=.01,htf_bull=True)}


def test_s_signal_waits_for_next_open_and_restart_is_invariant():
    frames = s_frames(); times=frames["GBP/USD"].index
    original = new_book_s_state("2026-09-01")
    batch, _ = s_run(copy.deepcopy(original), frames, times)
    split = copy.deepcopy(original)
    for t in times:
        split, _ = s_run(json.loads(json.dumps(split)), frames, [t])
    assert split == batch
    one, _ = s_run(copy.deepcopy(original), frames, times[:1])
    assert not one["positions"] and "GBP/USD" in one["pending"]
    assert batch["positions"]["GBP/USD"]["entry_time"] == "2026-09-01 09:00:00"
    assert batch["equity"] == pytest.approx(batch["cash"] + sum(p["unrealized_pnl"] for p in batch["positions"].values()))
    assert len(batch["equity_curve"]) == 1


def test_s_usd_base_fx_units_and_entry_bar_gap_stop():
    frame = bars([[150,150.1,149.5,150]], start="2026-09-01 09:00",freq="h")
    st = new_book_s_state("2026-09-01")
    st["pending"]={"USD/JPY":{"symbol":"USD/JPY","direction":"long","decision_time":"2026-09-01 08:00:00","stop_dist":.2}}
    out,_=s_run(st,{"USD/JPY":frame},frame.index)
    trade=out["trades"][0]
    assert trade["units"] == pytest.approx(500*149.8/.205)
    assert trade["exit_reason"] == "stop_loss"
    assert trade["pnl"] == pytest.approx(-500)
    assert "stop_loss" in trade and "take_profit" in trade


def test_s_missing_trend_does_not_default_long():
    frames=s_frames(); frames["GBP/USD"]["htf_bull"]=np.nan
    st,_=s_run(new_book_s_state("2026-09-01"),frames,frames["GBP/USD"].index)
    assert st["pending"] == {} and st["positions"] == {}


def test_old_s_ledger_rejected_not_reseeded():
    st=new_book_s_state("2026-09-01"); st["schema_version"]=1
    with pytest.raises(ValueError): validate_book_s_state(st)


def test_abc_cash_mark_and_gross_are_converted_and_old_state_rejected():
    frame=bars([[100,101,99,100]])
    fx=bars([[1.25]*4])
    book=PaperPortfolio({"AAPL":frame},{"AAPL":SimpleNamespace(holding_horizon=20)},warmup=999,
                        account_currency="GBP",fx_panel={"GBP/USD":fx})
    book._account_timestamp=frame.index[0]
    position=dict(symbol="AAPL",direction=Direction.LONG,entry_price=90.,units=10.,last_px=100.,stop=80.,tf="1d")
    assert book._unrealized(position,100.) == 80.
    assert book._open_record("AAPL",position).notional == 800.
    assert book._open_record("AAPL",position).risk == 160.
    state=book.to_state(); state.pop("accounting_version")
    with pytest.raises(ValueError):
        PaperPortfolio({"AAPL":frame},{"AAPL":SimpleNamespace(holding_horizon=20)},account_currency="GBP",state=state)


def test_abc_repaired_pending_has_entry_bar_stop_and_roundtrips_metadata():
    frame=bars([[100,105,80,101]])
    fx=bars([[1.25]*4])
    book=PaperPortfolio({"AAPL":frame},{"AAPL":SimpleNamespace(holding_horizon=20)},warmup=999,
                        account_currency="GBP",fx_panel={"GBP/USD":fx})
    book._pending["AAPL"]={"pos":Position(instrument="AAPL",direction=Direction.LONG,units=10.,notional=800.,
                             risk_fraction=.0008,stop_price=90.,target_price=115.,permitted=True),
                            "dec":100.,"risk_abs":80.,"tf":"1d"}
    recs=book.advance(frame.index[-1])
    assert len(recs[0]["entries"])==1 and len(recs[0]["exits"])==1
    assert not book.open_positions
    assert recs[0]["equity"] < 100000
    st=book.to_state()
    clone=PaperPortfolio({"AAPL":frame},{"AAPL":SimpleNamespace(holding_horizon=20)},warmup=999,
                         account_currency="GBP",fx_panel={"GBP/USD":fx},state=json.loads(json.dumps(st)))
    assert clone.to_state()==st
    assert clone.advance(frame.index[-1])==[]


def test_repaired_target_preserves_lower_partial_fill():
    pos=dict(symbol="AAPL",direction=Direction.LONG,entry_price=100.,units=10.,initial_units=10.,
             stop=90.,initial_stop=90.,target=115.,tms_p1=False,tms_p2=False,tms_be=False,
             bars_open=0,tms_log=[])
    tm=TradeManager(causal_partials=True)
    pnl,reason=tm.update_position(pos,high=116,low=101,close=115,atr=2,is_squeeze=False,
        bars_history={"high":116,"low":101,"len":2},timeframe="1d",pip_size=.01,
        fill_fn=lambda price,buying:price,open_=102,max_bars=20)
    assert pnl==125.  # half at +1R, remainder at +1.5R; not full +1.5R
    assert reason=="target" and pos["tms_p1"]


def test_daily_freshness_handles_holidays_and_never_accepts_stale_success():
    friday=bars([[100]*4],start="2026-09-04")
    require_daily_panel({"SPY":friday},["SPY"],"2026-09-08")  # Monday Labor Day
    with pytest.raises(ValueError,match="stale"):
        require_daily_panel({"SPY":friday},["SPY"],"2026-09-09")
    with pytest.raises(ValueError):
        require_daily_panel({},["SPY"],"2026-09-05")
    with pytest.raises(ValueError):
        require_daily_panel({"BTC/USD":friday},["BTC/USD"],"2026-09-07")


def test_hourly_weekend_freshness():
    last=bars([[1.2]*4],start="2026-09-04 20:00",freq="h")
    assert require_hourly_panel({"GBP/USD":last},["GBP/USD"],"2026-09-05 12:00")==last.index[-1]


def test_missing_state_and_overwrite_fail_closed(tmp_path):
    original=tmp_path/'original.json';new=tmp_path/'repaired.json'
    with pytest.raises(ValueError): require_restored_state(None)
    with pytest.raises(ValueError): require_restored_state({},initialize=True)
    with pytest.raises(ValueError): require_restored_state(None,initialize=True,no_remote=True,state_path=original,original_path=original)
    require_restored_state(None,initialize=True,no_remote=True,state_path=new,original_path=original)


def test_s_runtime_contains_restorable_full_state():
    from apex_quant.models.book_s_session_smc import runtime_payload
    st=new_book_s_state("2026-09-01")
    payload=runtime_payload(st)
    assert payload["state"]==st and payload["state"] is not st
    assert payload["profit_factor"] is None


def test_s_daily_lock_survives_restart_and_recovery():
    frames=s_frames();times=frames["GBP/USD"].index
    st=new_book_s_state("2026-09-01")
    st["daily_guard"]={"date":"2026-09-01","start_equity":100000.,"minimum_equity":97000.,"locked":True}
    st,_=s_run(st,frames,times[:1])
    st,_=s_run(json.loads(json.dumps(st)),frames,times[1:])
    assert st["daily_guard"]["locked"] and not st["pending"] and not st["positions"]
