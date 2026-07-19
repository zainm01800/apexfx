"""Build the PEAD data layer: a historical earnings-announcement-date cache for the
screened US equity universe, plus daily price parquets for names not yet cached.

Source decision (documented in data_store/pead_prereg.md):

  * FMP's historical earnings calendar is what api/quality-scores.js would use, but
    FMP_API_KEY lives only in the Vercel deployment environment - it is not in
    engine/.env and there is no local vercel CLI session, so FMP is unavailable to
    engine-side research. Checked 2026-07-19.
  * Yahoo's chart endpoint (events=earnings) returns ZERO historical earnings events
    for a 2015-2025 query, and quoteSummary/calendarEvents (what api/events.js uses)
    is forward-looking only. Finnhub's calendar is likewise forward-looking on the
    free tier.
  * SEC EDGAR is free, key-less and complete: every US issuer files an 8-K with
    Item 2.02 ("Results of Operations and Financial Condition") the day it releases
    quarterly earnings. The submissions API (data.sec.gov/submissions/CIK*.json)
    lists every filing with its date back to ~2001. Filing date == release date for
    the overwhelming majority of large caps (same-day filers); treat as the
    announcement date. It carries NO BMO/AMC flag and NO EPS estimates - so the
    surprise proxy is price-based (announcement-window return), per the prereg.

Cache layout: engine/data_store/earnings_calendar/{SYMBOL}.json
  {symbol, cik, source, retrieved_at, n_events, events: [YYYY-MM-DD ...]}  (sorted,
  de-duplicated to one event per >=45 days - amended 8-K/2.02 filings happen).

Usage:
    cd engine
    .venv-mac/bin/python scripts/build_earnings_calendar.py                  # EDGAR cache only
    .venv-mac/bin/python scripts/build_earnings_calendar.py --fetch-prices   # + Yahoo 1d parquets for missing names
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

CACHE_DIR = ENGINE_DIR / "data_store" / "earnings_calendar"

# SEC asks for a declared User-Agent; 10 req/s is the polite ceiling - we are far under it.
_UA = {"User-Agent": "apexfx quant research (research@apexfx.local)", "Accept": "application/json"}
_MIN_GAP_DAYS = 45          # de-dupe amended/duplicate 8-K 2.02 filings

# ── The PEAD universe: halal business-activity screen applied ─────────────────
# Cached single-name US equities EXCLUDING banks (JPM, GS - prohibited activity).
# V/MA (payment networks) and BA (defense) are kept per the task brief but flagged
# as borderline under strict AAOIFI screens; the AAOIFI debt-ratio screen is NOT
# applied (needs balance-sheet data - FMP unavailable locally, see above).
# TSM is excluded: a foreign private issuer filing 6-K/20-F, not 8-K Item 2.02, so
# EDGAR yields no earnings dates for it (checked 2026-07-19).
CACHED_NAMES = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD", "BA", "HD",
    "JNJ", "KO", "MA", "PG", "V", "WMT", "XOM", "PLTR", "NFLX", "UBER",
]
# SEC company_tickers.json quirks: XOM maps to the holding entity (2115436, no
# 8-Ks) - the operating company that files the earnings 8-Ks is CIK 34088.
CIK_OVERRIDES = {"XOM": 34088}
# ~15-25 liquid US names per the task brief (no banks); fetched on --fetch-prices.
NEW_NAMES = [
    "CAT", "PFE", "ABBV", "MRK", "NKE", "MCD", "COST", "PEP", "QCOM", "TXN",
    "AVGO", "MU", "AMAT",
]
UNIVERSE = CACHED_NAMES + NEW_NAMES


def _get_json(url: str, timeout: float = 30.0):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _ticker_cik_map() -> dict[str, int]:
    """SEC's official ticker -> CIK mapping (one call, all US issuers)."""
    data = _get_json("https://www.sec.gov/files/company_tickers.json")
    out = {}
    for row in data.values():
        out[str(row["ticker"]).upper()] = int(row["cik_str"])
    return out


def _earnings_dates(cik: int) -> list[str]:
    """All 8-K Item 2.02 filing dates for one issuer (the earnings-release dates).

    Handles the submissions API's pagination: ``filings.recent`` holds the last
    ~1000 filings; older batches are listed under ``filings.files``.
    """
    data = _get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    dates: list[str] = []

    def _harvest(filings: dict) -> None:
        forms = filings.get("form", [])
        items = filings.get("items", [""] * len(forms))
        fdates = filings.get("filingDate", [])
        for i, form in enumerate(forms):
            if form == "8-K" and i < len(items) and "2.02" in str(items[i]):
                dates.append(fdates[i])

    _harvest(data.get("filings", {}).get("recent", {}))
    for extra in data.get("filings", {}).get("files", []):
        # Only bother with batches that could contain pre-2016 events.
        if str(extra.get("filingTo", "9999")) >= "2015":
            time.sleep(0.15)
            _harvest(_get_json(
                f"https://data.sec.gov/submissions/{extra['name']}"))
    # De-dupe to one event per >=45 days (amended 8-Ks share the quarter).
    dates = sorted(set(dates))
    kept: list[str] = []
    for d in dates:
        if not kept or (datetime.fromisoformat(d) - datetime.fromisoformat(kept[-1])).days >= _MIN_GAP_DAYS:
            kept.append(d)
    return kept


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch-prices", action="store_true",
                    help="also fetch Yahoo 1d parquets for universe names with no cache")
    ap.add_argument("--symbols", default="", help="comma subset (default: full universe)")
    args = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or UNIVERSE
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"PEAD universe: {len(symbols)} names | cache -> {CACHE_DIR}", flush=True)
    mapping = _ticker_cik_map()
    time.sleep(0.15)

    summary_rows = []
    for sym in symbols:
        cik = CIK_OVERRIDES.get(sym, mapping.get(sym))
        if cik is None:
            print(f"  {sym:6s}: NO CIK MAPPING - skipped", flush=True)
            continue
        try:
            events = _earnings_dates(cik)
        except Exception as exc:
            print(f"  {sym:6s}: EDGAR ERROR {type(exc).__name__}: {exc}", flush=True)
            continue
        in_window = [d for d in events if "2016-01-01" <= d < "2025-01-01"]
        payload = {
            "symbol": sym,
            "cik": cik,
            "source": "SEC EDGAR 8-K Item 2.02 filing dates (earnings releases)",
            "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_events": len(events),
            "events": events,
        }
        with open(CACHE_DIR / f"{sym}.json", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        summary_rows.append({
            "symbol": sym, "cik": cik, "n_events": len(events),
            "n_2016_2024": len(in_window),
            "oldest": events[0] if events else "", "newest": events[-1] if events else "",
        })
        print(f"  {sym:6s}: {len(events):3d} events ({len(in_window):3d} in 2016-2024) "
              f"{events[0] if events else '-'} -> {events[-1] if events else '-'}", flush=True)
        time.sleep(0.15)

    import csv
    with open(CACHE_DIR / "_summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "cik", "n_events", "n_2016_2024", "oldest", "newest"])
        w.writeheader()
        w.writerows(summary_rows)
    total = sum(r["n_2016_2024"] for r in summary_rows)
    print(f"\ncached {len(summary_rows)} symbols | total events 2016-2024: {total} "
          f"(~{total / max(len(summary_rows), 1) / 9:.1f}/stock/yr)", flush=True)

    if args.fetch_prices:
        import pandas as pd
        from apex_quant.config import get_config
        from apex_quant.data import ParquetStore
        from apex_quant.data.yahoo_adapter import YahooAdapter

        cfg = get_config()
        store = ParquetStore(cfg.store_path)
        adapter = YahooAdapter()
        start = pd.Timestamp("2015-11-01", tz="UTC")
        end = pd.Timestamp.utcnow()
        for sym in symbols:
            existing = store.load(sym, "1d")
            if len(existing) >= 300:
                print(f"  prices {sym:6s}: cached ({len(existing)} bars) - skip", flush=True)
                continue
            print(f"  prices {sym:6s}: fetching {start.date()} -> {end.date()} ...", flush=True)
            try:
                df = store.get_or_fetch(sym, adapter, start, end, "1d")
                print(f"  prices {sym:6s}: {len(df)} bars "
                      f"({df.index[0].date() if len(df) else '-'} -> {df.index[-1].date() if len(df) else '-'})",
                      flush=True)
            except Exception as exc:
                print(f"  prices {sym:6s}: FETCH ERROR {type(exc).__name__}: {exc}", flush=True)
            time.sleep(0.3)

    return 0


if __name__ == "__main__":
    sys.exit(main())
