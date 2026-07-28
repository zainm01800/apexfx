"""UCITS mapping (PRIIPs replatforming) — the map itself, executor contract
construction for the LSE lines, and the mirror's mapped order path.

Deployed from data_store/ucits_mapping_2026-07-24.md: US-domiciled ETFs are
KID-ineligible for a UK retail IBKR account (venue rejects with error 201), so
the mirror trades the verified UCITS equivalent instead of skipping. These
tests pin the mapping, the contract metadata (USD lines, never the GBp pence
lines), and the mirror wiring. Everything runs offline — no gateway.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

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


# ── mirror wiring: the blocked US ETF is mirrored AS its UCITS line ───────────
def _fill(price, qty, ccy="USD", commission=1.0):
    return SimpleNamespace(status="filled", raw_status="Filled", avg_fill_price=price,
                           filled_quantity=qty, commission=commission,
                           commission_currency=ccy, order_id=1, perm_id=11)


class FakeExecutor:
    """Same seam as test_ibkr_mirror.FakeExecutor — no gateway, no orders."""

    def __init__(self, positions=None, net_liq=100_000.0):
        self.account = "DUQ278370"
        self._positions = positions or []
        self._net_liq = net_liq
        self.connected = False
        self.submits: list = []
        self.closes: list = []

    def connect(self):
        self.connected = True
        return self.account

    def disconnect(self):
        self.connected = False

    def get_account(self):
        return {"account": self.account, "NetLiquidation": self._net_liq,
                "AvailableFunds": self._net_liq, "currency": "GBP"}

    def get_positions(self):
        return list(self._positions)

    def get_portfolio(self):
        return []

    def get_pnl(self):
        return {}

    def submit_order(self, symbol, direction, volume, stop=None, target=None):
        self.submits.append((symbol, direction, volume))
        return SimpleNamespace(quantity=volume, submitted_at="t", stop=stop, target=target)

    def close_position(self, symbol):
        self.closes.append(symbol)
        return SimpleNamespace(quantity=1.0, submitted_at="t", stop=None, target=None)

    def wait_for_fill(self, handle, timeout_s=0):
        return _fill(100.0, handle.quantity)


def _state(entries=None, exits=None, day="2026-07-28", open_positions=None):
    st = {
        "book": "book_d_multiasset_252", "last_processed_date": day,
        "open_positions": {
            e["instrument"]: {"direction": e["direction"], "units": e["units"],
                              "entry_price": e.get("price", 100.0),
                              "entry_time": e.get("entry_time", day),
                              "stop": e.get("stop"), "target": e.get("target")}
            for e in (entries or [])
        },
        "trades": [
            {"instrument": x["instrument"], "direction": x["direction"],
             "units": x.get("units", 1.0), "exit_price": x.get("price", 100.0),
             "exit_time": day, "exit_reason": x.get("reason", "stop")}
            for x in (exits or [])
        ],
    }
    st["open_positions"].update(open_positions or {})
    return st


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(rim, "sync_ibkr_state", lambda *a, **k: True)  # no Supabase

    def run(state, executor, **kw):
        sp = tmp_path / "state.json"
        sp.write_text(json.dumps(state))
        return rim.run_mirror(sp, tmp_path / "mirror", executor, timeout_s=1.0, **kw)
    return run, tmp_path


def test_venue_resolution_pure():
    assert rim._venue_instrument("AAPL") == ("AAPL", None)          # trades directly
    assert rim._venue_instrument("EUR/USD") == ("EUR/USD", None)
    venue, line = rim._venue_instrument("QQQ")
    assert venue == "CNDX" and line["ucits_ticker"] == "CNDX.L"
    assert rim._venue_instrument("SMH") == (None, None)             # engine-only


def test_mapped_us_etf_entry_mirrors_the_ucits_line(env):
    run, _ = env
    ex = FakeExecutor()
    code, record = run(_state(entries=[
        {"instrument": "QQQ", "direction": "long", "units": 9},    # -> CNDX
        {"instrument": "SMH", "direction": "long", "units": 4},    # no equivalent
    ]), ex)
    assert code == 0
    assert ex.submits == [("CNDX", "long", 9.0)]          # the UCITS line is what trades
    skipped = record["skipped"]
    assert len(skipped) == 1 and skipped[0]["instrument"] == "SMH"
    assert "no UCITS equivalent" in skipped[0]["reason"]
    order = record["orders"][0]
    assert order["instrument"] == "QQQ"                   # engine instrument kept for the record
    assert order["venue_instrument"] == "CNDX"
    assert order["priips_mapping"] == "US-ETF QQQ → UCITS CNDX.L"
    assert order["status"] == "filled"
    assert order["divergence_bps"] is None                # different price level: not comparable
    assert "divergence_note" in order
    assert record["summary"] == {}                        # not folded into the cost average


def test_mapped_exit_closes_the_ucits_position(env):
    run, _ = env
    ex = FakeExecutor(positions=[{"engine_symbol": "XRSU", "quantity": 12}])
    code, record = run(_state(exits=[{"instrument": "IWM", "direction": "long"}]), ex)
    assert code == 0
    assert ex.closes == ["XRSU"]                          # the venue line is closed, not IWM
    order = record["orders"][0]
    assert order["instrument"] == "IWM" and order["venue_instrument"] == "XRSU"


def test_mapped_exit_with_no_ucits_position_is_a_clean_skip(env):
    run, _ = env
    ex = FakeExecutor(positions=[])
    code, record = run(_state(exits=[
        {"instrument": "XLK", "direction": "long"},       # mapped, but nothing held
        {"instrument": "SOXX", "direction": "long"},      # no equivalent at all
    ]), ex)
    assert code == 0 and ex.closes == []
    reasons = {s["instrument"]: s["reason"] for s in record["skipped"]}
    assert "no IBKR position" in reasons["XLK"]
    assert "no UCITS equivalent" in reasons["SOXX"]


def test_mapped_entry_dedupes_against_the_ucits_position(env):
    run, _ = env
    ex = FakeExecutor(positions=[{"engine_symbol": "CNDX", "quantity": 9}])
    code, record = run(_state(entries=[{"instrument": "QQQ", "direction": "long",
                                        "units": 9}]), ex)
    assert code == 0 and ex.submits == []                 # no double-buy through the mapping
    assert "already held" in record["skipped"][0]["reason"]


def test_reconciliation_is_mapping_aware(env):
    run, _ = env
    # Engine still holds QQQ (mirrored as CNDX) plus SMH/SPY (engine-only).
    st = _state(day="2026-07-28", open_positions={
        "QQQ": {"direction": "long", "units": 9, "entry_price": 100.0,
                "entry_time": "2026-07-20", "stop": 90.0, "target": None},
        "SMH": {"direction": "long", "units": 4, "entry_price": 100.0,
                "entry_time": "2026-07-20", "stop": 90.0, "target": None},
        "SPY": {"direction": "long", "units": 2, "entry_price": 100.0,
                "entry_time": "2026-07-20", "stop": 90.0, "target": None},
    })
    ex = FakeExecutor(positions=[{"engine_symbol": "CNDX", "quantity": 9}])
    code, record = run(st, ex)
    assert code == 0
    issues = {c["instrument"]: c["issue"] for c in record["post_run_position_check"]}
    # QQQ/CNDX sizes agree 1:1 -> NO false "engine holds, IBKR flat" mismatch
    assert "QQQ" not in issues and "CNDX" not in issues
    # no-equivalent legs are reported as engine-only BY DESIGN, not as drift
    assert issues["SMH"].startswith("engine-only by design")
    assert issues["SPY"].startswith("engine-only by design")


def test_reconciliation_flags_real_drift_through_the_mapping(env):
    run, _ = env
    st = _state(day="2026-07-28", open_positions={
        "QQQ": {"direction": "long", "units": 9, "entry_price": 100.0,
                "entry_time": "2026-07-20", "stop": 90.0, "target": None},
    })
    ex = FakeExecutor(positions=[{"engine_symbol": "CNDX", "quantity": 4}])  # partial-out drift
    code, record = run(st, ex)
    assert code == 0
    check = record["post_run_position_check"]
    assert len(check) == 1
    assert check[0]["instrument"] == "QQQ"
    assert check[0]["venue_instrument"] == "CNDX"
    assert check[0]["issue"].startswith("size drift")
