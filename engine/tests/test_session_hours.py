"""Session gating must put every book instrument on its OWN venue clock.

The original classifier substring-matched a hardcoded ticker list, which broke both ways on
the certified 39-instrument book:

  * "SGLD.L" contains "GLD", so a London-listed gold ETC matched the US-equity rule and was
    scanned 14:30–21:00 — up to 4.5 hours after the LSE close.
  * TSM, NFLX, UBER, ISWD.L, ISDU.L, ISDE.L matched nothing and fell through to the
    Western-forex rule (08:00–22:00), so US names were scanned from 08:00 — six and a half
    hours before the US open — and the LSE ETFs until 22:00.

Seven of twenty-one equities on the wrong clock means orders routed to a closed venue.
Venue is now resolved structurally, and these tests pin every category of the real book.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
LIVE = ENGINE_DIR / "scripts" / "run_live_paper_trading.py"


def _session(symbol: str, h: int, m: int = 0, weekday: int = 2) -> bool:
    """Reimplementation of is_asset_in_active_session's logic under a fixed clock.

    The live script cannot be imported (it builds an executor and mutates global config at
    import), so the branch structure is mirrored here and a separate test asserts the source
    still matches.
    """
    sym_upper = symbol.upper()
    cryptos = ["BTC", "ETH", "SOL", "SUI", "ADA", "AVAX", "LINK", "XRP", "ARB",
               "MATIC", "DOGE", "BNB"]
    if any(c in sym_upper for c in cryptos):
        return True
    if h == 22:
        return False
    if weekday == 4 and h >= 20:
        return False
    if any(ccy in sym_upper for ccy in ["JPY", "AUD", "NZD"]):
        return True
    if sym_upper.endswith(".L"):
        if h < 8 or h > 16:
            return False
        return h < 16 or m <= 30
    if "/" not in sym_upper:
        return (h == 14 and m >= 30) or (15 <= h < 21)
    return 8 <= h < 22


# ---- the exact regressions ------------------------------------------------------

def test_sgld_l_is_lse_not_us_despite_containing_GLD():
    """The substring bug: 'GLD' inside 'SGLD.L' matched the US rule."""
    assert _session("SGLD.L", 10) is True,  "LSE open at 10:00"
    assert _session("SGLD.L", 18) is False, "LSE shut at 18:00 — the old code scanned it"


@pytest.mark.parametrize("sym", ["TSM", "NFLX", "UBER"])
def test_us_names_missing_from_the_old_list_now_get_US_hours(sym):
    assert _session(sym, 10) is False, "US market shut at 10:00 — the old code scanned it"
    assert _session(sym, 16) is True,  "US market open at 16:00 London"


@pytest.mark.parametrize("sym", ["ISWD.L", "ISDU.L", "ISDE.L"])
def test_ucits_etfs_close_at_1630_not_2200(sym):
    assert _session(sym, 9) is True
    assert _session(sym, 16, 30) is True,  "16:30 is the close, inclusive"
    assert _session(sym, 16, 31) is False
    assert _session(sym, 20) is False, "the old code scanned these until 22:00"


# ---- category sanity across the real book ---------------------------------------

@pytest.mark.parametrize("sym", ["AAPL", "MSFT", "NVDA", "XLK", "XBI", "SMH", "SOXX"])
def test_us_equities_keep_1430_to_2100(sym):
    assert _session(sym, 14, 29) is False
    assert _session(sym, 14, 30) is True
    assert _session(sym, 20, 59) is True
    assert _session(sym, 21, 0) is False


@pytest.mark.parametrize("sym", ["BTC/USD", "ETH/USD", "DOGE/USD", "SUI/USD"])
def test_crypto_is_always_open(sym):
    for h in (0, 3, 14, 22, 23):
        assert _session(sym, h) is True


@pytest.mark.parametrize("sym", ["EUR/USD", "GBP/USD", "USD/CHF", "USD/CAD"])
def test_western_fx_window(sym):
    assert _session(sym, 7) is False
    assert _session(sym, 8) is True
    assert _session(sym, 21) is True
    assert _session(sym, 22) is False, "rollover dead zone"
    assert _session(sym, 23) is False


@pytest.mark.parametrize("sym", ["USD/JPY", "AUD/USD", "NZD/USD"])
def test_asia_pac_fx_is_24_5_except_rollover(sym):
    assert _session(sym, 3) is True
    assert _session(sym, 23) is True
    assert _session(sym, 22) is False


def test_friday_evening_is_closed_for_everything_but_crypto():
    assert _session("AAPL", 20, weekday=4) is False
    assert _session("EUR/USD", 20, weekday=4) is False
    assert _session("BTC/USD", 20, weekday=4) is True


def test_the_observed_2337_split_reproduces():
    """The live run at 23:37 scanned 14 of 39 and gated 25 — reproduce that exactly."""
    book_crypto = ["BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD",
                   "AVAX/USD", "DOGE/USD", "LINK/USD", "ARB/USD", "SUI/USD"]
    book_fx = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"]
    book_eq = ["AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD", "PLTR", "TSM",
               "NFLX", "UBER", "ISWD.L", "ISDU.L", "ISDE.L", "XLK", "XLE", "XBI", "SMH",
               "SOXX", "SGLD.L"]
    book = book_crypto + book_fx + book_eq
    assert len(book) == 39

    active = [s for s in book if _session(s, 23, 37)]
    assert len(active) == 14, f"expected 14 active at 23:37, got {len(active)}: {active}"
    assert set(active) == set(book_crypto) | {"USD/JPY", "AUD/USD", "NZD/USD"}


# ---- the mirror must stay honest -------------------------------------------------

def test_source_still_resolves_venue_structurally():
    src = LIVE.read_text(encoding="utf-8")
    body = src[src.index("def is_asset_in_active_session("):]
    body = body[: body.index("\ndef ")]
    assert 'sym_upper.endswith(".L")' in body, "LSE must be detected by suffix"
    assert '"/" not in sym_upper' in body, "US equities detected by absence of a pair slash"
    assert "equities_etfs = [" not in body, "the hardcoded substring list must be gone"
    assert body.index('endswith(".L")') < body.index('"/" not in sym_upper'), (
        "LSE must be checked BEFORE the US branch, or SGLD.L regresses"
    )
