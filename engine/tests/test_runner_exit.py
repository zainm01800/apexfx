"""Runner-mode exit (pre-registered experiment): the post-P1 remainder rides the
Chandelier trail uncapped instead of capping at the fixed 1.5R target.

Proves three things: the default book is unchanged (runner OFF), runner removes ONLY
the upside cap (the hard stop still protects the downside), and a winner genuinely
exits ABOVE the old 1.5R target when it runs.
"""

from apex_quant.risk.trade_manager import TradeManager


def _pos(entry=100.0, stop=90.0, target=115.0, units=10.0, direction="long"):
    # entry 100, stop 90 -> risk 10 -> 1R=110, 1.5R target=115
    return {
        "symbol": "TST", "direction": direction, "units": units, "initial_units": units,
        "entry_price": entry, "entry_time": "t", "stop": stop, "initial_stop": stop,
        "target": target, "tms_p1": False, "tms_p2": False, "tms_be": False,
        "bars_open": 0, "tms_log": [],
    }


def _bars(hi, lo, n=30):
    return {"high": hi, "low": lo, "len": n}


NOCOST = lambda price, buying: price   # noqa: E731 — isolate exit logic from fills


def test_default_is_off_and_caps_at_target():
    tm = TradeManager()
    assert tm.runner_mode is False                       # frozen book keeps its cap
    pos = _pos()
    pnl, reason = tm.update_position(
        pos, high=116, low=108, close=115, atr=5.0, is_squeeze=False,
        bars_history=_bars(116, 90), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == "target"                            # closed at the fixed 1.5R target
    assert pos["units"] == 0.0
    assert pnl == (115 - 100) * 10                       # +150, capped


def test_runner_rides_past_the_old_target():
    tm = TradeManager(runner_mode=True)
    pos = _pos()
    # Bar 1: rockets to +3R. Baseline would have closed the lot at 115; the runner
    # takes 50% at 1R, keeps the rest, and trails the stop UP via Chandelier.
    _, r1 = tm.update_position(
        pos, high=130, low=108, close=128, atr=5.0, is_squeeze=False,
        bars_history=_bars(130, 90), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert r1 == ""                                      # NOT capped at target
    assert pos["units"] == 5.0                           # 50% off at P1, remainder runs
    assert pos["tms_p1"] is True
    assert pos["stop"] == 130 - 2.0 * 5.0                # chandelier = swing_high - 2*ATR = 120
    assert pos["stop"] > 115                             # trailed ABOVE the old 1.5R cap

    # Bar 2: pulls back into the trail -> exits at 120, well beyond the old 115 target.
    pnl2, r2 = tm.update_position(
        pos, high=131, low=119, close=119, atr=5.0, is_squeeze=False,
        bars_history=_bars(131, 118), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert r2 == "stop"
    assert pnl2 == (120 - 100) * 5.0                     # remainder banked at 120 > 115


def test_runner_still_respects_the_hard_stop():
    tm = TradeManager(runner_mode=True)
    pos = _pos()
    pnl, reason = tm.update_position(
        pos, high=101, low=89, close=92, atr=5.0, is_squeeze=False,
        bars_history=_bars(101, 89), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert reason == "stop"                              # runner removes the CAP, not the STOP
    assert pnl == (90 - 100) * 10                        # full -1R loss protected as before


def test_runner_skips_partial_two():
    # In runner mode the whole post-P1 remainder rides; P2 must not trim it to 25%.
    tm = TradeManager(runner_mode=True)
    pos = _pos()
    tm.update_position(pos, high=110, low=100, close=110, atr=5.0, is_squeeze=False,
                       bars_history=_bars(110, 100), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert pos["tms_p1"] is True and pos["units"] == 5.0
    # Push to 1.5R: baseline P2 would fire (-> 2.5 units). Runner keeps all 5.
    tm.update_position(pos, high=115, low=110, close=115, atr=5.0, is_squeeze=False,
                       bars_history=_bars(115, 108), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert pos["tms_p2"] is False
    assert pos["units"] == 5.0                           # NOT trimmed to 25%


# ── gap-aware stop fills (2026-07-22) ────────────────────────────────────────
def test_stop_gapped_through_fills_at_the_open_not_the_stop():
    """A stop does not guarantee the stop PRICE — the tail-loss honesty fix."""
    tm = TradeManager()
    pos = _pos()                                   # long, entry 100, stop 90
    # Bar gaps DOWN to 80 and never trades at 90: the real fill is 80, not 90.
    pnl, reason = tm.update_position(
        pos, high=82, low=78, close=81, atr=5.0, is_squeeze=False,
        bars_history=_bars(82, 78), timeframe="1d", pip_size=0.01,
        fill_fn=NOCOST, open_=80.0)
    assert reason == "stop"
    assert pnl == (80 - 100) * 10                  # -200, NOT the optimistic -100
    assert pos["tms_log"][-1]["action"] == "gap_through_stop"


def test_short_gap_up_through_stop_is_also_worse():
    tm = TradeManager()
    pos = _pos(entry=100.0, stop=110.0, target=85.0, direction="short")
    pnl, reason = tm.update_position(
        pos, high=122, low=118, close=120, atr=5.0, is_squeeze=False,
        bars_history=_bars(122, 118), timeframe="1d", pip_size=0.01,
        fill_fn=NOCOST, open_=120.0)
    assert reason == "stop"
    assert pnl == (100 - 120) * 10                 # -200, filled at the open


def test_no_gap_still_fills_at_the_stop():
    tm = TradeManager()
    pos = _pos()
    pnl, reason = tm.update_position(
        pos, high=99, low=88, close=95, atr=5.0, is_squeeze=False,
        bars_history=_bars(99, 88), timeframe="1d", pip_size=0.01,
        fill_fn=NOCOST, open_=98.0)                # opened above the stop
    assert reason == "stop"
    assert pnl == (90 - 100) * 10                  # normal -1R


def test_omitting_open_keeps_the_old_behaviour():
    tm = TradeManager()
    pos = _pos()
    pnl, _ = tm.update_position(
        pos, high=82, low=78, close=81, atr=5.0, is_squeeze=False,
        bars_history=_bars(82, 78), timeframe="1d", pip_size=0.01, fill_fn=NOCOST)
    assert pnl == (90 - 100) * 10                  # callers without open_ unchanged
