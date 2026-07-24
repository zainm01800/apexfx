"""Earlier-first-partial experiment (pre-registered: early_partial_prereg.md): the 50%
partial + breakeven move trigger at p1_r = 0.75 / 0.50 instead of the certified 1.0R.

Proves: the certified default is unchanged (p1_r = 1.0), the earlier trigger banks 50% and
moves the stop to breakeven at the earlier moment, the rest of the ladder is untouched
(hard stop still protects the downside before the trigger, P2 still trims at 1.5R, the
fixed target still caps), and the win-rate mechanism works: a trade that reaches +0.6R and
reverses is a small WIN under p1_r=0.5 but a full -1R loser under the certified ladder.
"""

from apex_quant.risk.trade_manager import TradeManager


def _pos(entry=100.0, stop=90.0, target=115.0, units=10.0, direction="long"):
    # entry 100, stop 90 -> risk 10 -> 0.5R=105, 0.75R=107.5, 1R=110, 1.5R target=115
    return {
        "symbol": "TST", "direction": direction, "units": units, "initial_units": units,
        "entry_price": entry, "entry_time": "t", "stop": stop, "initial_stop": stop,
        "target": target, "tms_p1": False, "tms_p2": False, "tms_be": False,
        "bars_open": 0, "tms_log": [],
    }


def _bars(hi, lo, n=30):
    return {"high": hi, "low": lo, "len": n}


NOCOST = lambda price, buying: price   # noqa: E731 — isolate exit logic from fills
BE = 100.0 + 3.0 * 0.01                # breakeven stop = entry + be_buffer_pips x pip_size


def test_default_p1_r_is_the_certified_1R():
    tm = TradeManager()
    assert tm.p1_r == 1.0                                # frozen book unchanged
    pos = _pos()
    # +0.9R high: NO partial under the certified ladder, no breakeven move.
    pnl, reason = tm.update_position(
        pos, high=109, low=101, close=108, atr=5.0, is_squeeze=False,
        bars_history=_bars(109, 101), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == "" and pnl == 0.0
    assert pos["tms_p1"] is False and pos["tms_be"] is False
    assert pos["units"] == 10.0 and pos["stop"] == 90.0


def test_p1_075_banks_half_and_moves_to_breakeven_at_075R():
    tm = TradeManager(p1_r=0.75)
    pos = _pos()
    pnl, reason = tm.update_position(
        pos, high=108, low=101, close=107, atr=5.0, is_squeeze=False,
        bars_history=_bars(108, 101), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == ""
    assert pnl == (107.5 - 100) * 5.0                    # 50% banked at +0.75R
    assert pos["units"] == 5.0
    assert pos["tms_p1"] is True and pos["tms_be"] is True
    assert pos["stop"] == BE                             # breakeven moved at the same moment


def test_p1_050_banks_half_and_moves_to_breakeven_at_050R():
    tm = TradeManager(p1_r=0.50)
    pos = _pos()
    pnl, reason = tm.update_position(
        pos, high=106, low=101, close=105, atr=5.0, is_squeeze=False,
        bars_history=_bars(106, 101), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == ""
    assert pnl == (105.0 - 100) * 5.0                    # 50% banked at +0.50R
    assert pos["units"] == 5.0
    assert pos["stop"] == BE


def test_early_partial_converts_a_reversal_into_a_small_win():
    """The mechanism the experiment prices: +0.6R then reversal through entry is a
    full -1R under the certified ladder, but banks +0.5R on half and scratches the rest
    at breakeven under p1_r=0.5."""
    bars = [(106, 100, 105), (104, 89, 95)]              # reach +0.6R, then collapse
    tm_new = TradeManager(p1_r=0.50)
    pos_new = _pos()
    tm_new.update_position(pos_new, high=bars[0][0], low=bars[0][1], close=bars[0][2],
                           atr=5.0, is_squeeze=False, bars_history=_bars(106, 100),
                           timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    pnl2, r2 = tm_new.update_position(pos_new, high=bars[1][0], low=bars[1][1], close=bars[1][2],
                                      atr=5.0, is_squeeze=False, bars_history=_bars(104, 89),
                                      timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert r2 == "stop"
    total_new = (105 - 100) * 5.0 + pnl2
    assert pnl2 == (BE - 100) * 5.0                      # remainder scratched at breakeven
    assert total_new > 0                                 # small WIN, not a loss

    tm_base = TradeManager()                             # certified ladder, same bars
    pos_base = _pos()
    tm_base.update_position(pos_base, high=bars[0][0], low=bars[0][1], close=bars[0][2],
                            atr=5.0, is_squeeze=False, bars_history=_bars(106, 100),
                            timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    pnl2b, r2b = tm_base.update_position(pos_base, high=bars[1][0], low=bars[1][1],
                                         close=bars[1][2], atr=5.0, is_squeeze=False,
                                         bars_history=_bars(104, 89), timeframe="1d",
                                         pip_size=0.01, fill_fn=NOCOST)
    assert r2b == "stop"
    assert pnl2b == (90 - 100) * 10.0                    # full -1R loser
    assert total_new > pnl2b


def test_early_partial_leaves_the_hard_stop_untouched_before_the_trigger():
    """Below the trigger nothing changes: a trade that never sees +0.5R loses the full -1R,
    exactly like the certified book."""
    tm = TradeManager(p1_r=0.50)
    pos = _pos()
    pnl, reason = tm.update_position(
        pos, high=103, low=89, close=92, atr=5.0, is_squeeze=False,
        bars_history=_bars(103, 89), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == "stop"
    assert pnl == (90 - 100) * 10.0                      # full -1R, no partial taken
    assert pos["tms_p1"] is False


def test_ladder_after_early_partial_is_unchanged():
    """P2 (25% at 1.5R) and the 0.5R lock still fire after an early P1 — target set beyond
    1.5R here so the fixed-target close doesn't shadow P2 (in the book rr == p2_r == 1.5,
    where the full-target close at 1.5R fires first, as in the certified book)."""
    tm = TradeManager(p1_r=0.50)
    pos = _pos(target=120.0)                             # rr 2.0 target for the test
    tm.update_position(pos, high=106, low=101, close=105, atr=5.0, is_squeeze=False,
                       bars_history=_bars(106, 101), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert pos["tms_p1"] is True and pos["units"] == 5.0
    pnl2, r2 = tm.update_position(pos, high=115, low=108, close=114, atr=5.0, is_squeeze=False,
                                  bars_history=_bars(115, 108), timeframe="1d", pip_size=0.01,
                                  fill_fn=NOCOST)
    assert r2 == ""
    assert pnl2 == (115 - 100) * 2.5                     # P2: 25% of initial banked at 1.5R
    assert pos["tms_p2"] is True and pos["units"] == 2.5
    assert pos["stop"] == 105.0                          # 0.5R locked, above breakeven


def test_fixed_target_still_caps_a_rocket_bar():
    """The full-target close precedes partials (certified order): a bar that reaches the
    1.5R target closes everything there, even under p1_r=0.5."""
    tm = TradeManager(p1_r=0.50)
    pos = _pos()                                         # target 115
    pnl, reason = tm.update_position(
        pos, high=116, low=104, close=115, atr=5.0, is_squeeze=False,
        bars_history=_bars(116, 104), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == "target"
    assert pos["units"] == 0.0
    assert pnl == (115 - 100) * 10.0                     # whole position at the 1.5R cap
