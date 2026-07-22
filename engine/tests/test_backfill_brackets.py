"""Bracket backfill planner — the safety rules, without a gateway.

Retrofitting stops onto live positions is the one place a sizing or direction
error would be actively dangerous, so the planner is pure and tested hard.
"""

from scripts.backfill_ibkr_brackets import plan_backfill


def _eng(direction="long", stop=90.0, target=115.0):
    return {"direction": direction, "stop": stop, "target": target, "units": 10.0}


def test_protects_an_unprotected_position_sized_to_the_venue():
    to_protect, skipped = plan_backfill(
        {"AAPL": _eng(stop=310.38, target=364.55)},
        [{"engine_symbol": "AAPL", "quantity": 44}],      # venue holds 44, engine said 10
        resting=[])
    assert skipped == []
    assert to_protect == [{"instrument": "AAPL", "qty": 44.0, "side": "long",
                           "stop": 310.38, "target": 364.55}]   # venue qty wins, not engine


def test_short_position_gets_a_buy_side_stop():
    to_protect, _ = plan_backfill(
        {"MSFT": _eng(direction="short", stop=425.58, target=348.57)},
        [{"engine_symbol": "MSFT", "quantity": -25}], resting=[])
    assert to_protect[0]["side"] == "short" and to_protect[0]["qty"] == 25.0


def test_idempotent_skips_already_protected():
    # Re-running must never stack a second stop on the same position.
    to_protect, skipped = plan_backfill(
        {"AAPL": _eng()},
        [{"engine_symbol": "AAPL", "quantity": 44}],
        resting=[{"symbol": "AAPL", "order_type": "STP"}])
    assert to_protect == []
    assert "already has a resting" in skipped[0]["reason"]


def test_direction_mismatch_refuses_rather_than_guessing():
    # Engine thinks long, venue is short: an exit-side order here could be wrong-way.
    to_protect, skipped = plan_backfill(
        {"AAPL": _eng(direction="long")},
        [{"engine_symbol": "AAPL", "quantity": -44}], resting=[])
    assert to_protect == []
    assert "DIRECTION MISMATCH" in skipped[0]["reason"]


def test_missing_stop_or_unknown_position_is_skipped_loudly():
    to_protect, skipped = plan_backfill(
        {"AAPL": _eng(stop=None)},
        [{"engine_symbol": "AAPL", "quantity": 44},
         {"engine_symbol": "GHOST", "quantity": 5}],
        resting=[])
    assert to_protect == []
    reasons = " | ".join(s["reason"] for s in skipped)
    assert "no stop" in reasons and "not in engine state" in reasons


def test_zero_quantity_rows_are_ignored():
    to_protect, skipped = plan_backfill(
        {"AAPL": _eng()}, [{"engine_symbol": "AAPL", "quantity": 0}], resting=[])
    assert to_protect == [] and skipped == []
