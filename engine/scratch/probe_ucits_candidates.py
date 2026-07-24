"""Probe UCITS candidate tickers via the engine's own YahooAdapter (the same
chart-endpoint path the Book H gate used for ISWD.L/ISDU.L/ISDE.L/SGLD.L/SPSK).

For each candidate: currency (Yahoo chart meta), total daily bars, in-window
bars (2016-01-01 <= t < 2025-01-01), first/last in-window bar, max calendar gap.
Also re-verifies the already-validated Islamic UCITS from the local cache.

Read-only against the store: nothing is fetched INTO the cache here.

Usage: cd engine && .venv-mac/bin/python scratch/probe_ucits_candidates.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import pandas as pd  # noqa: E402

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import ParquetStore, clean  # noqa: E402
from apex_quant.data.yahoo_adapter import YahooAdapter  # noqa: E402

HOLDOUT_START = pd.Timestamp("2025-01-01", tz="UTC")
WINDOW_START = pd.Timestamp("2016-01-01", tz="UTC")

CANDIDATES = [
    # (ticker, maps US instrument, note)
    ("IITU.L", "XLK", "iShares S&P 500 IT Sector UCITS (flagged candidate)"),
    ("CNDX.L", "QQQ", "iShares Nasdaq 100 UCITS (Acc), USD line"),
    ("EQQQ.L", "QQQ", "Invesco EQQQ Nasdaq-100 UCITS (alt)"),
    ("XRSU.L", "IWM", "Xtrackers Russell 2000 UCITS 1C"),
    ("SXLE.L", "XLE", "SPDR S&P US Energy Select Sector UCITS"),
    ("SEMI.L", "SMH/SOXX", "iShares MSCI Global Semiconductors UCITS (2021 launch)"),
    ("BTEC.L", "XBI", "iShares Nasdaq US Biotech UCITS? (existence probe)"),
    ("SBIU.L", "XBI", "SPDR US Biotech UCITS? (existence probe)"),
    ("SPYL.L", "SPY", "SPDR S&P 500 UCITS, USD line (conventional)"),
    ("VUAA.L", "SPY", "Vanguard S&P 500 UCITS (Acc), USD line (conventional)"),
]

CACHE_RECHECK = ["ISWD.L", "ISDU.L", "ISDE.L", "SGLD.L"]


def probe(adapter: YahooAdapter, ticker: str) -> dict:
    out = {"ticker": ticker}
    try:
        p1 = int(pd.Timestamp("2015-01-01", tz="UTC").timestamp())
        p2 = int(pd.Timestamp("2026-07-24", tz="UTC").timestamp())
        raw = adapter._fetch_json(ticker, p1, p2, "1d")
        result = (raw.get("chart", {}).get("result") or [None])[0]
        if not result:
            out["error"] = str((raw.get("chart", {}).get("error") or "no result"))
            return out
        meta = result.get("meta", {})
        out["currency"] = meta.get("currency")
        out["exchange"] = meta.get("exchangeName")
        out["name"] = meta.get("shortName") or meta.get("longName")
        df = adapter._parse(raw)
        if df.empty:
            out["bars_total"] = 0
            return out
        w = df[(df.index >= WINDOW_START) & (df.index < HOLDOUT_START)]
        out["bars_total"] = int(len(df))
        out["bars_in_window"] = int(len(w))
        if len(w):
            out["first"] = str(w.index[0].date())
            out["last"] = str(w.index[-1].date())
            gaps = w.index.to_series().diff().dt.days.dropna()
            out["max_gap_days"] = int(gaps.max()) if len(gaps) else 0
            out["weekend_bars"] = int((w.index.dayofweek >= 5).sum())
            out["last_close"] = round(float(w["close"].iloc[-1]), 2)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> None:
    adapter = YahooAdapter()
    rows = []
    for ticker, maps, note in CANDIDATES:
        r = probe(adapter, ticker)
        r["maps"] = maps
        r["note"] = note
        rows.append(r)
        print(json.dumps(r, default=str), flush=True)

    print("\n-- cache re-check (already-validated Islamic UCITS) --")
    store = ParquetStore(get_config().store_path)
    for ticker in CACHE_RECHECK:
        df = clean(store.load(ticker, "1d"))
        w = df[df.index < HOLDOUT_START]
        r = {
            "ticker": ticker, "bars_in_window": int(len(w)),
            "first": str(w.index[0].date()) if len(w) else None,
            "last": str(w.index[-1].date()) if len(w) else None,
        }
        rows.append(r)
        print(json.dumps(r), flush=True)

    out = ENGINE_DIR / "scratch" / "probe_ucits_candidates.json"
    out.write_text(json.dumps(rows, indent=2, default=str))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
