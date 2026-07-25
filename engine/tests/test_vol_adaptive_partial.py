"""Vol-adaptive-first-partial experiment (pre-registered: vol_adaptive_partial_prereg.md):
the 50% partial + breakeven move trigger at p1_r = 0.75R for HIGH-vol instruments only,
1.0R (certified) for LOW-vol instruments, via TradeManager(p1_r_by_instrument=...).

Proves: the certified default is unchanged (no map -> flat p1_r = 1.0), the map fires the
earlier trigger ONLY for covered symbols (uncovered symbols fall back to the flat p1_r),
the lookup keys on the position's "symbol" for both directions, and the rest of the ladder
is untouched (hard stop still protects the downside before the trigger, P2 still trims at
1.5R with the 0.5R lock, the fixed target still caps).
"""

from apex_quant.risk.trade_manager import TradeManager


def _pos(entry=100.0, stop=90.0, target=115.0, units=10.0, direction="long", symbol="TST"):
    # entry 100, stop 90 -> risk 10 -> 0.75R=107.5, 1R=110, 1.5R target=115
    return {
        "symbol": symbol, "direction": direction, "units": units, "initial_units": units,
        "entry_price": entry, "entry_time": "t", "stop": stop, "initial_stop": stop,
        "target": target, "tms_p1": False, "tms_p2": False, "tms_be": False,
        "bars_open": 0, "tms_log": [],
    }


def _bars(hi, lo, n=30):
    return {"high": hi, "low": lo, "len": n}


NOCOST = lambda price, buying: price   # noqa: E731 — isolate exit logic from fills
BE = 100.0 + 3.0 * 0.01                # breakeven stop = entry + be_buffer_pips x pip_size
MAP = {"HIVOL": 0.75}                  # the challenger rule: HIGH-vol names bank at 0.75R


def test_default_has_no_map_and_is_the_certified_1R():
    tm = TradeManager()
    assert tm.p1_r_by_instrument is None                 # frozen book unchanged
    assert tm.p1_r == 1.0
    pos = _pos(symbol="HIVOL")
    # +0.9R high: NO partial under the certified ladder, no breakeven move.
    pnl, reason = tm.update_position(
        pos, high=109, low=101, close=108, atr=5.0, is_squeeze=False,
        bars_history=_bars(109, 101), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == "" and pnl == 0.0
    assert pos["tms_p1"] is False and pos["tms_be"] is False
    assert pos["units"] == 10.0 and pos["stop"] == 90.0


def test_mapped_symbol_banks_half_and_moves_to_breakeven_at_075R():
    tm = TradeManager(p1_r_by_instrument=MAP)
    pos = _pos(symbol="HIVOL")
    pnl, reason = tm.update_position(
        pos, high=108, low=101, close=107, atr=5.0, is_squeeze=False,
        bars_history=_bars(108, 101), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == ""
    assert pnl == (107.5 - 100) * 5.0                    # 50% banked at +0.75R
    assert pos["units"] == 5.0
    assert pos["tms_p1"] is True and pos["tms_be"] is True
    assert pos["stop"] == BE                             # breakeven moved at the same moment


def test_unmapped_symbol_keeps_the_certified_1R_trigger():
    """LOW-vol names are simply absent from the map: they fall back to the flat p1_r."""
    tm = TradeManager(p1_r_by_instrument=MAP)
    pos = _pos(symbol="LOVOL")
    pnl, reason = tm.update_position(
        pos, high=109, low=101, close=108, atr=5.0, is_squeeze=False,
        bars_history=_bars(109, 101), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == "" and pnl == 0.0                   # +0.9R: nothing yet
    assert pos["tms_p1"] is False and pos["stop"] == 90.0
    pnl2, reason2 = tm.update_position(
        pos, high=110, low=101, close=109.5, atr=5.0, is_squeeze=False,
        bars_history=_bars(110, 101), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason2 == ""
    assert pnl2 == (110 - 100) * 5.0                     # 50% banked at the certified +1R
    assert pos["stop"] == BE


def test_map_lookup_keys_on_symbol_for_shorts_too():
    tm = TradeManager(p1_r_by_instrument=MAP)
    pos = _pos(entry=100.0, stop=110.0, target=85.0, direction="short", symbol="HIVOL")
    # short: risk 10, 0.75R = 92.5
    pnl, reason = tm.update_position(
        pos, high=101, low=92.0, close=93.0, atr=5.0, is_squeeze=False,
        bars_history=_bars(101, 92.0), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == ""
    assert pnl == (100 - 92.5) * 5.0                     # 50% banked at +0.75R short
    assert pos["units"] == 5.0
    assert pos["stop"] == 100.0 - 3.0 * 0.01             # breakeven below entry


def test_adaptive_partial_leaves_the_hard_stop_untouched_before_the_trigger():
    """Below the trigger nothing changes, even for mapped names: never seeing +0.75R
    loses the full -1R, exactly like the certified book."""
    tm = TradeManager(p1_r_by_instrument=MAP)
    pos = _pos(symbol="HIVOL")
    pnl, reason = tm.update_position(
        pos, high=106, low=89, close=92, atr=5.0, is_squeeze=False,
        bars_history=_bars(106, 89), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == "stop"
    assert pnl == (90 - 100) * 10.0                      # full -1R, no partial taken
    assert pos["tms_p1"] is False


def test_ladder_after_adaptive_partial_is_unchanged():
    """P2 (25% at 1.5R) and the 0.5R lock still fire after an adaptive P1 — target set
    beyond 1.5R here so the fixed-target close doesn't shadow P2 (in the book
    rr == p2_r == 1.5, where the full-target close at 1.5R fires first, as certified)."""
    tm = TradeManager(p1_r_by_instrument=MAP)
    pos = _pos(target=120.0, symbol="HIVOL")             # rr 2.0 target for the test
    tm.update_position(pos, high=108, low=101, close=107, atr=5.0, is_squeeze=False,
                       bars_history=_bars(108, 101), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert pos["tms_p1"] is True and pos["units"] == 5.0
    pnl2, r2 = tm.update_position(pos, high=115, low=108, close=114, atr=5.0, is_squeeze=False,
                                  bars_history=_bars(115, 108), timeframe="1d", pip_size=0.01,
                                  fill_fn=NOCOST)
    assert r2 == ""
    assert pnl2 == (115 - 100) * 2.5                     # P2: 25% of initial banked at 1.5R
    assert pos["tms_p2"] is True and pos["units"] == 2.5
    assert pos["stop"] == 105.0                          # 0.5R locked, above breakeven


def test_fixed_target_still_caps_a_rocket_bar_with_map():
    """The full-target close precedes partials (certified order), even for mapped names."""
    tm = TradeManager(p1_r_by_instrument=MAP)
    pos = _pos(symbol="HIVOL")                           # target 115
    pnl, reason = tm.update_position(
        pos, high=116, low=104, close=115, atr=5.0, is_squeeze=False,
        bars_history=_bars(116, 104), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == "target"
    assert pos["units"] == 0.0
    assert pnl == (115 - 100) * 10.0                     # whole position at the 1.5R cap
