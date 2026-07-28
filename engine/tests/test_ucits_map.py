"""UCITS mapping (PRIIPs replatforming) — the map itself, executor contract
construction for the LSE lines, and the mirror's mapped order path.

Deployed from data_store/ucits_mapping_2026-07-24.md: US-domiciled ETFs are
KID-ineligible for a UK retail IBKR account (venue rejects with error 201), so
the mirror trades the verified UCITS equivalent instead of skipping. These
tests pin the mapping, the contract metadata (USD lines, never the GBp pence
lines), and the mirror wiring. Everything runs offline — no gateway.
"""

from __future__ import annotations

import pytest

import scripts.run_ibkr_mirror as rim
from apex_quant.execution import ucits_map
from apex_quant.execution.ibkr_executor import contract_spec, make_contract


# ── the map ───────────────────────────────────────────────────────────────────
def test_verified_lines_resolve_to_the_documented_usd_lines():
    expected = {
        "QQQ": ("CNDX", "CNDX.L"),
        "IWM": ("XRSU", "XRSU.L"),
        "XLK": ("IUIT", "IUIT.L"),
        "XLE": ("SXLE", "SXLE.L"),
        "XBI": ("BTEC", "BTEC.L"),
    }
    for us, (ib_sym, yahoo) in expected.items():
        line = ucits_map.resolve_for_venue(us)
        assert line is not None, us
        assert line["ibkr_symbol"] == ib_sym
        assert line["ucits_ticker"] == yahoo
        assert line["currency"] == "USD"                    # USD LSE line, never the GBp pence line
        assert line["exchange"] in ("SMART", "LSE")         # what ib_insync/ib_async routes
        assert line["isin"].startswith("IE")                # Irish-domiciled UCITS -> a KID exists
        assert line["fund"] and line["primary_exchange"] == "LSE"


def test_xlk_maps_to_iuit_not_the_dirty_gbp_iitu_line():
    # IITU.L is the GBp pence line of the SAME FUND, SAME ISIN (IE00B3WJKG14) —
    # the flagged candidate the doc rejects. The USD line is IUIT.L.
    line = ucits_map.resolve_for_venue("XLK")
    assert line["ucits_ticker"] == "IUIT.L"
    assert line["isin"] == "IE00B3WJKG14"
    assert "IITU" in line["caveat"]


def test_no_equivalent_cases_return_none_and_document_why():
    for us in ("SPY", "SMH", "SOXX"):
        assert ucits_map.resolve_for_venue(us) is None
        assert ucits_map.NO_EQUIVALENT_REASON[us]           # documented, not silent
    # instruments that are not blocked US ETFs are none of the map's business —
    # they trade directly and the mirror never consults the map for them
    assert ucits_map.resolve_for_venue("AAPL") is None
    assert ucits_map.resolve_for_venue("EUR/USD") is None
    assert ucits_map.resolve_for_venue("NOPE") is None


def test_xbi_partial_history_caveat_is_carried():
    line = ucits_map.resolve_for_venue("XBI")
    assert "PARTIAL" in line["caveat"]
    assert "2017-10" in line["caveat"]                      # late start
    assert "equal-weight" in line["caveat"]                 # different index vs XBI


def test_every_mapped_us_ticker_is_kid_blocked():
    # The mirror only consults the map for KID_BLOCKED instruments — every map
    # key must be in that set or the mapping could never fire.
    for us in ucits_map.UCITS_MAP:
        assert us in rim.KID_BLOCKED, us


def test_reverse_lookup_accepts_ibkr_and_yahoo_forms():
    for form in ("CNDX", "CNDX.L", "cndx.l", " xrsu "):
        line = ucits_map.line_for_symbol(form)
        assert line is not None, form
        assert line["ibkr_symbol"] in ("CNDX", "XRSU")
    assert ucits_map.line_for_symbol("AAPL") is None        # plain share: not a UCITS line
    assert ucits_map.line_for_symbol("QQQ") is None         # the US ETF itself is NOT a UCITS line


# ── executor contract construction for an LSE UCITS line ─────────────────────
def test_contract_spec_uses_the_map_for_ucits_lines():
    spec = contract_spec("CNDX")
    assert spec == {"asset_class": "equity", "secType": "STK", "symbol": "CNDX",
                    "currency": "USD", "exchange": "SMART"}
    assert contract_spec("CNDX.L")["symbol"] == "CNDX"      # Yahoo form resolves too
    assert contract_spec("xrsu")["symbol"] == "XRSU"        # case-insensitive


def test_make_contract_builds_lse_usd_stock_objects():
    iba = pytest.importorskip("ib_async")
    for sym in ("CNDX", "XRSU", "IUIT", "SXLE"):
        contract = make_contract(contract_spec(sym))
        assert isinstance(contract, iba.Stock)
        assert contract.symbol == sym
        assert contract.currency == "USD"                   # USD line, not the GBp pence line
        assert contract.exchange in ("SMART", "LSE")


def test_engine_symbols_are_never_rewritten_by_the_executor():
    # The PRIIPs mapping lives in the mirror's venue resolution; contract_spec
    # must not silently turn a US ETF into something else.
    assert contract_spec("QQQ")["symbol"] == "QQQ"
    assert contract_spec("IWM")["exchange"] == "SMART"
    # plain shares / pairs: byte-identical to before the map existed
    assert contract_spec("AAPL") == {"asset_class": "equity", "secType": "STK",
                                     "symbol": "AAPL", "currency": "USD",
                                     "exchange": "SMART"}
    assert contract_spec("EUR/USD")["secType"] == "CASH"
    assert contract_spec("BTC/USD")["secType"] == "CRYPTO"


def test_venue_contract_spec_helper():
    assert ucits_map.venue_contract_spec("QQQ") == {
        "asset_class": "equity", "secType": "STK", "symbol": "CNDX",
        "currency": "USD", "exchange": "SMART"}
    assert ucits_map.venue_contract_spec("SPY") is None     # no equivalent
    assert ucits_map.venue_contract_spec("AAPL") is None    # not a mapped US ETF
