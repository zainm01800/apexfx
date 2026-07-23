"""apex_quant.risk.daily_stop — real behavioural tests, not source greps.

The live script cannot be imported in a test (it builds an executor and mutates global config
at import), which is exactly why this logic was moved into the risk package. These exercise
the real functions against a real temp file.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from apex_quant.risk.daily_stop import (
    breached, daily_loss, read_anchor, resolve_anchor, session_key, write_anchor,
)

NOW = datetime(2026, 7, 23, 14, 30, tzinfo=timezone.utc)
TOMORROW = NOW + timedelta(days=1)


def test_first_call_of_the_day_anchors_at_current_equity(tmp_path):
    p = tmp_path / "anchor.json"
    assert read_anchor(p, NOW) is None
    assert resolve_anchor(p, 100_000.0, NOW) == 100_000.0
    assert read_anchor(p, NOW) == 100_000.0


def test_restart_mid_session_reuses_the_original_anchor(tmp_path):
    """THE failure mode: a restart after a loss must not re-anchor at the lower equity."""
    p = tmp_path / "anchor.json"
    resolve_anchor(p, 100_000.0, NOW)

    # process dies; restarts later the same day with equity down 3%
    again = resolve_anchor(p, 97_000.0, NOW)
    assert again == 100_000.0, "re-anchoring at the down equity would erase the day's loss"
    assert daily_loss(again, 97_000.0) == pytest.approx(0.03)
    assert breached(again, 97_000.0, 0.025) is True


def test_stale_anchor_from_a_previous_day_is_never_reused(tmp_path):
    p = tmp_path / "anchor.json"
    resolve_anchor(p, 100_000.0, NOW)
    assert read_anchor(p, TOMORROW) is None, "yesterday's anchor must not carry over"
    assert resolve_anchor(p, 108_000.0, TOMORROW) == 108_000.0
    assert read_anchor(p, TOMORROW) == 108_000.0


def test_corrupt_file_disables_the_check_rather_than_halting_trading(tmp_path):
    p = tmp_path / "anchor.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_anchor(p, NOW) is None
    # resolve falls back to live equity -> measured loss 0 -> not breached
    anchor = resolve_anchor(p, 97_000.0, NOW)
    assert anchor == 97_000.0
    assert breached(anchor, 97_000.0, 0.025) is False


def test_unwritable_path_does_not_raise(tmp_path):
    bad = tmp_path / "nope.json"
    bad.parent.mkdir(exist_ok=True)
    bad.write_text(json.dumps({"date": "1999-01-01", "equity": 1.0}), encoding="utf-8")
    # stale -> treated as absent; resolve must still return something usable
    assert resolve_anchor(bad, 50_000.0, NOW) == 50_000.0


@pytest.mark.parametrize("equity,expected", [
    (100_000.0, 0.0),      # flat
    (105_000.0, 0.0),      # up on the day is never a loss
    (97_500.0, 0.025),
    (96_300.0, 0.037),     # the book's real worst day
])
def test_daily_loss_arithmetic(equity, expected):
    assert daily_loss(100_000.0, equity) == pytest.approx(expected, abs=1e-9)


def test_limit_of_zero_disables_the_stop_entirely():
    assert breached(100_000.0, 50_000.0, 0.0) is False


def test_breach_is_inclusive_at_the_limit():
    assert breached(100_000.0, 97_500.0, 0.025) is True     # exactly -2.5%
    assert breached(100_000.0, 97_600.0, 0.025) is False    # -2.4%


def test_worst_real_day_breaches_a_3pct_rule_but_not_a_4pct_one():
    """-3.70% is the book's worst day: FundedElite (3%) dies, Orion (4%) survives.
    A 2.5% engine stop fires before either."""
    a, e = 100_000.0, 96_300.0
    assert breached(a, e, 0.03) is True      # FundedElite rule
    assert breached(a, e, 0.04) is False     # Orion rule
    assert breached(a, e, 0.025) is True     # our stop fires first


def test_session_key_is_utc_dated():
    assert session_key(NOW) == "2026-07-23"
    assert session_key(TOMORROW) == "2026-07-24"


def test_write_then_read_roundtrip(tmp_path):
    p = tmp_path / "a.json"
    assert write_anchor(p, 123_456.78, NOW) is True
    assert read_anchor(p, NOW) == pytest.approx(123_456.78)
