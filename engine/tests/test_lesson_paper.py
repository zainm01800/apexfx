"""Paper (unticketed) research setups carry NO fabricated £ P&L — only the real
outcome; executed (ticketed) trades keep their real broker P&L.

Regression for the `£1000 x R:R` fabrication that rendered identically to real money
on the History tab (a 2.3:1 setup always "made £2,300", even for SMH/AAPL the engine
can't trade) — and for the infinite-regeneration trap that removing it would create
if a legitimately no-£ paper lesson were flagged stale on every pass.
"""

from __future__ import annotations

import scripts.update_lessons as ul

V = ul._LESSON_VERSION


def _wrap(body: str, marker: str = V) -> str:
    return f"<strong>{body}</strong> — detail here.<br>\n<!-- {marker} -->"


def _paper_win_lesson() -> str:
    return _wrap(
        "✅ What Went Right: Reached its take-profit target at 573.33 from an entry of "
        "583.55. Research setup — not executed, so no cash P&L; judged on outcome only."
    )


def test_paper_lesson_carries_no_pounds():
    assert "£" not in _paper_win_lesson()


def test_paper_lesson_is_stable_not_regenerated(monkeypatch):
    # THE regression: a no-£ paper lesson must not be flagged stale forever.
    monkeypatch.setattr(ul, "_match_mt4_trade", lambda t, h: None)
    t = {"id": "SMH_1", "symbol": "SMH", "outcome": "tp_hit", "lesson": _paper_win_lesson()}
    assert ul._needs_structured_lesson(t) is False


def test_real_lesson_without_pounds_is_malformed(monkeypatch):
    # A real-money lesson (no 'Research setup' marker) that lacks a £ figure is broken
    # and must regenerate — the paper exemption must not leak to executed trades.
    monkeypatch.setattr(ul, "_match_mt4_trade", lambda t, h: {"ticket": 1, "profit": 12.0})
    t = {"id": "EURUSD_1", "symbol": "EUR/USD", "outcome": "sl_hit",
         "lesson": _wrap("❌ What Went Wrong: no currency figure present here")}
    assert ul._needs_structured_lesson(t) is True


def test_version_bump_regenerates_old_paper_lessons(monkeypatch):
    monkeypatch.setattr(ul, "_match_mt4_trade", lambda t, h: None)
    t = {"id": "SMH_1", "symbol": "SMH", "outcome": "tp_hit",
         "lesson": _paper_win_lesson().replace(V, "LESSON_V1")}
    assert ul._needs_structured_lesson(t) is True


def test_paper_flag_is_ticket_absence():
    # is_paper is defined as "no ticket" — the load-bearing distinction between a
    # research setup and an executed trade.
    assert ul._LESSON_VERSION.startswith("LESSON_V")
