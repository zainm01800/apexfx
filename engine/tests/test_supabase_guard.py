"""Supabase quota breaker + live-scan pinning.

The 402 storm was self-reinforcing: Supabase restricted the project, the 5-second sync daemon
retried regardless, and every rejected request still counted as egress — keeping the project
in the restricted state it was trying to escape. The breaker exists to make a quota block
decay rather than compound.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from apex_quant.config import get_config
from apex_quant.data import supabase_guard as g

ENGINE_DIR = Path(__file__).resolve().parent.parent
LIVE = ENGINE_DIR / "scripts" / "run_live_paper_trading.py"


@pytest.fixture(autouse=True)
def _clean():
    g.reset()
    yield
    g.reset()


def test_quota_codes_trip_the_breaker_and_others_do_not():
    assert g.note_response(402) is True
    g.reset()
    assert g.note_response(429) is True
    g.reset()
    # a genuine client error is OUR bug, not a quota block — must not gag the engine
    assert g.note_response(400) is False
    assert g.note_response(404) is False
    assert g.is_blocked() is False


def test_blocked_until_cooldown_elapses():
    g.note_response(402, cooldown_s=0.4)
    assert g.is_blocked() is True
    import time
    time.sleep(0.5)
    assert g.is_blocked() is False


def test_cooldown_doubles_per_consecutive_trip():
    """A persistent block must back off, not re-hammer every cooldown expiry."""
    g.note_response(402, cooldown_s=100)
    first = g.status()["seconds_remaining"]
    g.note_response(402, cooldown_s=100)
    second = g.status()["seconds_remaining"]
    assert second >= first * 1.5, f"expected escalation, got {first} -> {second}"


def test_a_healthy_response_clears_the_escalation():
    g.note_response(402, cooldown_s=100)
    g.note_response(402, cooldown_s=100)
    assert g.status()["trips"] == 2
    g.note_response(200)
    assert g.status()["trips"] == 0


def test_suppressed_requests_are_counted_for_visibility():
    g.note_response(402, cooldown_s=60)
    for _ in range(5):
        g.is_blocked()
    assert g.status()["requests_suppressed"] == 5
    assert "suppressed" in g.describe()


# ---- live-loop wiring -----------------------------------------------------------

def _src() -> str:
    return LIVE.read_text(encoding="utf-8")


def test_sync_daemon_is_config_driven_not_a_hardcoded_5s_loop():
    src = _src()
    assert "time.sleep(interval)" in src, "daemon must use the configured interval"
    assert "time.sleep(5)" not in src, "the hardcoded 5s sync loop must be gone"
    assert 'getattr(cfg.execution, "sync_interval_s", 60)' in src


def test_expensive_rebuilds_run_on_their_own_slower_clock():
    """update_lessons + symbol knowledge are full-table reads; they must not track fills."""
    src = _src()
    assert "knowledge_every" in src
    assert "now - last_knowledge >= knowledge_every" in src


def test_daemon_skips_supabase_entirely_while_blocked():
    src = _src()
    assert "supabase_guard.is_blocked()" in src
    assert "pausing Supabase sync" in src


def test_write_failures_are_reported_to_the_breaker():
    src = _src()
    assert src.count("supabase_guard.note_response(r.status_code") >= 3, (
        "each Supabase write guard must feed the breaker, else a 402 never trips it"
    )


def test_defaults_are_sane():
    ex = get_config().execution
    assert ex.sync_interval_s >= 30, "a sub-30s sync loop is what caused the overrun"
    assert ex.knowledge_interval_s >= ex.sync_interval_s
    assert ex.supabase_cooldown_s > 0


# ---- certified-book pinning -----------------------------------------------------

def test_book_universe_matches_the_gate_exactly_and_is_daily_only():
    """Pinned list must equal the gate universe minus MATIC/USD (no 1d data at gate time,
    so every certified figure is a 39-instrument result)."""
    import re
    import sys
    sys.path.insert(0, str(ENGINE_DIR / "scripts"))
    from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC
    from run_portfolio_gate_multiasset import FX_MAJORS_7

    src = _src()
    block = src[src.index("BOOK_H_GOLD_39 = ["):]
    block = block[: block.index("]")]
    pinned = set(re.findall(r'"([^"]+)"', block))

    gate = set(EQUITY_CORE) | {GOLD_ETC} | set(get_config().data.crypto) | set(FX_MAJORS_7)

    assert len(pinned) == 39, f"certified book is 39 instruments, found {len(pinned)}"
    assert pinned - gate == set(), f"pinned names not in the gate universe: {pinned - gate}"
    assert gate - pinned == {"MATIC/USD"}, (
        f"only MATIC/USD may be absent (no gate-time data); missing: {gate - pinned}"
    )
    assert '"style": "swing", "timeframe": "1d"' in src, "the book is DAILY only"


def test_book_flag_exists_and_replaces_the_scan_list_in_place():
    src = _src()
    assert '"--book"' in src
    assert "ROBUST_CORE_PORTFOLIO[:] = build_book_portfolio()" in src, (
        "must mutate in place — consumers hold a reference to the module global"
    )
