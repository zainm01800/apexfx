"""UCITS instrument mapping — UK-retail-tradeable equivalents for the book's US ETFs.

Single source of truth for the PRIIPs replatforming of the execution/mirror
layer, deployed 2026-07-28 from the verified research doc
``engine/data_store/ucits_mapping_2026-07-24.md`` (every line below was probed
through the engine's own YahooAdapter path; TERs are issuer-stated; a PRIIPs
KID exists by construction for every genuine UCITS, which is the whole point).

Why this exists
---------------
US-domiciled ETFs (SPY QQQ IWM XLK XLE XBI SMH SOXX) are PRIIPs/KID-ineligible
for a UK retail IBKR account — the venue rejects them outright (error 201,
confirmed live on IWM/QQQ 2026-07-22), so the mirror could never fill the
book's US-ETF sleeve. This module maps each blocked US ETF to the verified
UK-retail-tradeable UCITS line, so the mirror (and later the funded runner)
places the *equivalent* LSE exposure instead of skipping.

Scope discipline
----------------
EXECUTION layer only. This changes what the mirror places on IBKR, never what
the engine book holds: the frozen forward paper test, the backtest universes
and config.yaml's live sections are untouched. Any UNIVERSE change that
follows from this mapping requires its own pre-registration (the doc says the
same).

Preference rules carried from the doc
-------------------------------------
1. **USD LSE lines over GBp pence lines** — a pence line embeds GBP/USD in its
   daily returns (the ISWD.L artifact: in-window corr vs SPY 0.385) and the
   engine has no quote-currency conversion. This is why XLK maps to **IUIT.L**
   and NOT IITU.L: IITU.L is the GBp pence line of the SAME FUND, SAME ISIN
   (IE00B3WJKG14) — the flagged dirty candidate.
2. **Full in-window history preferred** (2273 bars = full 2016→2024 LSE
   calendar); late launches carry the caveat in the line's ``caveat`` field.
3. **Halal status is recorded, not enforced.** It does not gate execution here
   — the mapping mirrors exposure the book already holds. The doc's compliance
   flags (e.g. "leave IWM unmapped") are preserved in ``caveat`` so the record
   travels with the order path.

Contract metadata
-----------------
Each line carries what an IBKR order needs: ``ibkr_symbol`` (IBKR local
symbol — Yahoo's ``.L`` suffix is dropped), ``exchange`` ``"SMART"`` (the
router; ``qualifyContracts`` resolves the LSE USD line server-side at first
use — ib_insync/ib_async idiom for LSE UCITS), ``currency`` ``"USD"``, plus
the ISIN and the doc's venue of record ``primary_exchange`` ``"LSE"``.
"""

from __future__ import annotations

__all__ = [
    "UCITS_MAP",
    "NO_EQUIVALENT_REASON",
    "resolve_for_venue",
    "line_for_symbol",
    "venue_contract_spec",
]

#: US ticker -> UCITS line dict, or None when NO clean equivalent exists
#: (reason recorded in :data:`NO_EQUIVALENT_REASON`; the position stays
#: engine-only). All keys are members of the mirror's KID_BLOCKED set — the
#: mirror only consults this map for instruments it would otherwise skip.
UCITS_MAP: dict[str, dict | None] = {
    "QQQ": {
        "us_ticker": "QQQ",
        "ucits_ticker": "CNDX.L",
        "ibkr_symbol": "CNDX",
        "fund": "iShares Nasdaq 100 UCITS ETF (Acc)",
        "exchange": "SMART",
        "primary_exchange": "LSE",
        "currency": "USD",
        "isin": "IE00B53SZB19",
        "ter_pct": 0.30,
        "halal_status": "not certified — Nasdaq-100 excludes financials by index "
                        "rule but applies no activity/debt screens",
        "caveat": "The doc's halal answer for US mega-cap tech is ISDU.L plus the "
                  "12 screened single stocks the book already runs; CNDX.L is "
                  "mapped for execution parity with the book's QQQ leg, not as a "
                  "halal endorsement.",
        "verified": "2273 in-window bars (full 2016-2024 LSE calendar), USD — probed 2026-07-24",
    },
    "IWM": {
        "us_ticker": "IWM",
        "ucits_ticker": "XRSU.L",
        "ibkr_symbol": "XRSU",
        "fund": "Xtrackers Russell 2000 Swap UCITS ETF 1C",
        "exchange": "SMART",
        "primary_exchange": "LSE",
        "currency": "USD",
        "isin": "IE00BJZ2DD79",
        "ter_pct": 0.30,
        "halal_status": "not certified — and Russell 2000 holds ~15% financials, "
                        "so it fails the activity screen too",
        "caveat": "Execution mapping only. The doc flags IWM as having NO CLEAN "
                  "HALAL equivalent (no Sharia-certified small-cap UCITS exists) "
                  "and recommends dropping the leg, as Book H does. Mirrored for "
                  "exposure parity; review before the funded runner relies on it.",
        "verified": "2273 in-window bars, USD — probed 2026-07-24",
    },
    "XLK": {
        "us_ticker": "XLK",
        "ucits_ticker": "IUIT.L",
        "ibkr_symbol": "IUIT",
        "fund": "iShares S&P 500 Information Technology Sector UCITS ETF (Acc)",
        "exchange": "SMART",
        "primary_exchange": "LSE",
        "currency": "USD",
        "isin": "IE00B3WJKG14",
        "ter_pct": 0.15,
        "halal_status": "not certified — same documented borderline call as XLK "
                        "itself (holds Visa/Mastercard, GICS-IT payment networks)",
        "caveat": "The clean USD line of the fund — NOT IITU.L, which is the GBp "
                  "pence line of the SAME ISIN and embeds GBP/USD in daily returns "
                  "(the flagged dirty candidate, rejected under rule 1).",
        "verified": "2273 in-window bars, USD — probed 2026-07-24",
    },
    "XLE": {
        "us_ticker": "XLE",
        "ucits_ticker": "SXLE.L",
        "ibkr_symbol": "SXLE",
        "fund": "SPDR S&P US Energy Select Sector UCITS ETF",
        "exchange": "SMART",
        "primary_exchange": "LSE",
        "currency": "USD",
        "isin": "IE00BWBXM492",
        "ter_pct": 0.15,
        "halal_status": "not certified — energy passes the activity screen "
                        "(no financials), same rationale as Book H keeping XLE",
        "caveat": "",
        "verified": "2273 in-window bars, USD — probed 2026-07-24",
    },
    "XBI": {
        "us_ticker": "XBI",
        "ucits_ticker": "BTEC.L",
        "ibkr_symbol": "BTEC",
        "fund": "iShares Nasdaq US Biotechnology UCITS ETF (Acc)",
        "exchange": "SMART",
        "primary_exchange": "LSE",
        "currency": "USD",
        "isin": "IE00BYXG2H39",
        "ter_pct": 0.35,
        "halal_status": "not certified — biotech passes the activity screen",
        "caveat": "PARTIAL: late start (in-window bars only from 2017-10-19) and a "
                  "DIFFERENT INDEX — cap-weighted Nasdaq biotech vs XBI's "
                  "equal-weight S&P Biotech Select, so the small-cap tilt is not "
                  "replicated. Treat as a different instrument; forward-usable "
                  "with that understanding.",
        "verified": "1818 in-window bars from 2017-10-19, USD — probed 2026-07-24",
    },
    # ── no clean equivalent — these stay engine-only ─────────────────────────
    "SPY": None,
    "SMH": None,
    "SOXX": None,
}

#: Why the None entries in :data:`UCITS_MAP` stay engine-only (from the doc).
NO_EQUIVALENT_REASON: dict[str, str] = {
    "SPY": "Book H already carries the US equity leg via the Sharia-certified "
           "ISWD.L/ISDU.L lines — mapping SPY would double that exposure.",
    "SMH": "SEMI.L is the only candidate and it has two strikes: a GBP sterling "
           "line (no USD LSE line exists — SEMD.L probed, 404) and a 2021-08 "
           "launch (843 in-window bars vs 2273). No clean equivalent.",
    "SOXX": "Same candidate as SMH (SEMI.L) with the same two strikes — GBP "
            "sterling line and a 2021-08 launch. No clean equivalent.",
}

#: Reverse index: IBKR local symbol -> US ticker (built once from UCITS_MAP).
_BY_IBKR_SYMBOL: dict[str, str] = {
    line["ibkr_symbol"]: us for us, line in UCITS_MAP.items() if line
}


def resolve_for_venue(instrument: str) -> dict | None:
    """The tradeable UCITS replacement for a PRIIPs-blocked US ticker, or None.

    ``instrument`` is an engine symbol (``"QQQ"``). Returns a copy of the UCITS
    line dict for mapped tickers. Returns None when the ticker has no clean
    equivalent (SPY/SMH/SOXX — the position stays engine-only; reason in
    :data:`NO_EQUIVALENT_REASON`) and for instruments that are not blocked US
    ETFs at all (plain shares, FX, crypto trade directly and never consult
    this function).
    """
    line = UCITS_MAP.get(str(instrument).strip().upper())
    return dict(line) if line else None


def line_for_symbol(symbol: str) -> dict | None:
    """Reverse lookup: a UCITS venue symbol -> its line dict (copy).

    Accepts the IBKR local form (``"CNDX"``) and the Yahoo form (``"CNDX.L"``);
    case-insensitive. None when the symbol is not a mapped UCITS line — plain
    shares and the US ETFs themselves fall through to the default contract
    mapping untouched.
    """
    s = str(symbol).strip().upper()
    if s.endswith(".L"):
        s = s[:-2]
    us = _BY_IBKR_SYMBOL.get(s)
    if us is None:
        return None
    line = UCITS_MAP[us]
    return dict(line) if line else None


def venue_contract_spec(instrument: str) -> dict | None:
    """Executor-shaped contract spec for a US ticker's UCITS replacement.

    Same shape as ``ibkr_executor.contract_spec`` — pass straight to
    ``make_contract``. None when there is no equivalent.
    """
    line = resolve_for_venue(instrument)
    if line is None:
        return None
    return {
        "asset_class": "equity",
        "secType": "STK",
        "symbol": line["ibkr_symbol"],
        "currency": line["currency"],
        "exchange": line["exchange"],
    }
