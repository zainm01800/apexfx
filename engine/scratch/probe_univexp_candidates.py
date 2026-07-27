"""Probe universe-expansion candidates via the engine's own YahooAdapter — the same
chart-endpoint path used for ISWD.L/ISDU.L/ISDE.L/SGLD.L/SPSK (Book H) and the
2026-07-24 UCITS mapping probes.

Two buckets:
  1. UCITS / LSE lines (live Yahoo probe): currency, total bars, in-window bars
     (2016-01-01 <= t < 2025-01-01), first/last in-window bar, max gap, weekend bars.
  2. Cached US large caps + SPSK (store re-check): in-window bars, first/last.

READ-ONLY against the store: nothing is fetched INTO the cache here. Fetching the
verified survivors into the store is a separate, deliberate step.

Usage: cd engine && .venv-mac/bin/python scratch/probe_univexp_candidates.py
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

UCITS_CANDIDATES = [
    # (ticker, bucket, note)
    ("SXLV.L", "ucits_sector", "SPDR S&P US Health Care Select Sector UCITS (USD)"),
    ("SXLI.L", "ucits_sector", "SPDR S&P US Industrials Select Sector UCITS (existence probe)"),
    ("SXLB.L", "ucits_sector", "SPDR S&P US Materials Select Sector UCITS"),
    ("SXLP.L", "ucits_sector", "SPDR S&P US Consumer Staples Select Sector UCITS"),
    ("SXLY.L", "ucits_sector", "SPDR S&P US Consumer Discretionary Select Sector UCITS"),
    ("IUHC.L", "ucits_sector", "iShares S&P 500 Health Care Sector UCITS (USD, alt to SXLV)"),
    ("SSLN.L", "metals", "iShares Physical Silver ETC (allocated)"),
    ("SPGP.L", "metals", "iShares Gold Producers UCITS (existence probe)"),
    ("INRG.L", "ucits_thematic", "iShares Global Clean Energy UCITS (USD)"),
    ("RBOT.L", "ucits_thematic", "iShares Automation & Robotics UCITS (USD line)"),
    ("DGTL.L", "ucits_thematic", "iShares Digitalisation UCITS (USD line)"),
    # mapping-doc lines expected to FAIL the independence rule (near-duplicates of
    # existing book holdings) or the halal bar — probed so the exclusion is documented:
    ("IUIT.L", "expected_dup", "iShares S&P 500 IT (USD line; clone of XLK)"),
    ("SXLE.L", "expected_dup", "SPDR US energy (clone of XLE)"),
    ("BTEC.L", "expected_dup", "iShares Nasdaq biotech (partial; vs XBI)"),
    ("CNDX.L", "expected_dup", "iShares Nasdaq 100 (fails halal bar per mapping doc)"),
]

CACHE_RECHECK = {
    "SPSK": "other_asset_class — SP Funds DJ Global Sukuk ETF (halal-certified)",
    "AMGN": "healthcare large cap (untested in Book J/K)",
    "VRTX": "healthcare large cap (untested in Book J/K)",
    "DHR": "healthcare large cap (untested in Book J/K)",
    "SYK": "healthcare large cap (untested in Book J/K)",
    "EMR": "industrials large cap (untested in Book J/K)",
    "ETN": "industrials large cap (untested in Book J/K)",
    "PH": "industrials large cap (untested in Book J/K)",
    "SHW": "materials large cap (untested in Book J/K)",
    "ECL": "industrials/materials large cap (untested in Book J/K)",
    "AMAT": "semis-equipment (Book K audit: expected near-dup of SOXX)",
    "LRCX": "semis-equipment (Book K audit: expected near-dup of SOXX)",
    "KLAC": "semis-equipment (Book K audit: expected near-dup of SOXX)",
}


def probe(adapter: YahooAdapter, ticker: str) -> dict:
    out = {"ticker": ticker}
    try:
        p1 = int(pd.Timestamp("2015-01-01", tz="UTC").timestamp())
        p2 = int(pd.Timestamp("2026-07-27", tz="UTC").timestamp())
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
    for ticker, bucket, note in UCITS_CANDIDATES:
        r = probe(adapter, ticker)
        r["bucket"] = bucket
        r["note"] = note
        rows.append(r)
        print(json.dumps(r, default=str), flush=True)

    print("\n-- cache re-check (large caps + SPSK) --")
    store = ParquetStore(get_config().store_path)
    for ticker, note in CACHE_RECHECK.items():
        df = clean(store.load(ticker, "1d"))
        w = df[(df.index >= WINDOW_START) & (df.index < HOLDOUT_START)]
        r = {
            "ticker": ticker, "bucket": "cache", "note": note,
            "bars_in_window": int(len(w)),
            "first": str(w.index[0].date()) if len(w) else None,
            "last": str(w.index[-1].date()) if len(w) else None,
        }
        rows.append(r)
        print(json.dumps(r), flush=True)

    out = ENGINE_DIR / "scratch" / "probe_univexp_candidates.json"
    out.write_text(json.dumps(rows, indent=2, default=str))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
