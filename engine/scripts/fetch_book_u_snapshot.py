#!/usr/bin/env python3
"""Fetch and freeze the adjusted USD-ETF input panel for Book U.

The Book U protocol was committed before this downloader was executed.  This
script persists the vendor responses as well as a common-session adjusted-OHLC
snapshot so the one-shot historical robustness result can be reproduced from
immutable bytes instead of a mutable cache.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd


ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.research.book_u_cluster_trend import USD_ETF_UNIVERSE  # noqa: E402


PROTOCOL = ENGINE_DIR / "data_store" / "book_u_cluster_trend_prereg_2026-09-03.md"
DEFAULT_SNAPSHOT = (
    ENGINE_DIR / "data_store" / "validation" / "book_u_inputs_2026-09-03.parquet"
)
DEFAULT_MANIFEST = (
    ENGINE_DIR / "data_store" / "validation" / "book_u_inputs_2026-09-03.manifest.json"
)
DEFAULT_RAW_DIR = (
    ENGINE_DIR / "data_store" / "validation" / "book_u_yahoo_raw_2026-09-03"
)
DOWNLOAD_START = "2008-01-01"
# Freeze only completed sessions.  The audit is being run on 2026-09-03 and a
# same-day Yahoo bar can be partial before the US close, so the exclusive bound
# is deliberately the current UTC date (last eligible session: 2026-09-02).
DOWNLOAD_END_EXCLUSIVE = "2026-09-03"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {
    # Yahoo's chart edge currently rate-limits a fabricated full Chrome UA on
    # this host while serving the same public response to a generic browser UA.
    # Keep the transport identifier minimal and freeze the raw response bytes.
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_text(path: Path, payload: str) -> None:
    _atomic_bytes(path, payload.encode("utf-8"))


def adjusted_ohlcv_from_yahoo(payload: dict[str, Any]) -> pd.DataFrame:
    """Convert a Yahoo chart response into split/dividend-adjusted OHLCV.

    Yahoo supplies an adjusted close but unadjusted OHLC.  Multiplying every
    price field by ``adjclose / close`` keeps each bar internally consistent and
    removes split/distribution jumps from momentum, covariance, ATR and stops.
    The result is a total-return proxy, not an executable CFD price history.
    """

    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result or not result.get("timestamp"):
        raise ValueError("Yahoo response contains no chart timestamps")
    quote = (result.get("indicators", {}).get("quote") or [None])[0]
    adjusted = (result.get("indicators", {}).get("adjclose") or [None])[0]
    if not quote or not adjusted or not adjusted.get("adjclose"):
        raise ValueError("Yahoo response contains no quote/adjusted-close arrays")

    frame = pd.DataFrame(
        {
            "open_raw": quote.get("open"),
            "high_raw": quote.get("high"),
            "low_raw": quote.get("low"),
            "close_raw": quote.get("close"),
            "adj_close": adjusted.get("adjclose"),
            "volume": quote.get("volume"),
        },
        index=pd.to_datetime(result["timestamp"], unit="s", utc=True).normalize(),
    )
    frame.index.name = "timestamp"
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    required = ["open_raw", "high_raw", "low_raw", "close_raw", "adj_close"]
    frame = frame.dropna(subset=required)
    frame["volume"] = frame["volume"].fillna(0.0)
    factor = frame["adj_close"] / frame["close_raw"]
    if not np.isfinite(factor).all() or (factor <= 0.0).any():
        raise ValueError("Yahoo adjustment factor is non-finite or non-positive")

    out = pd.DataFrame(index=frame.index)
    out["open"] = frame["open_raw"] * factor
    out["high"] = frame["high_raw"] * factor
    out["low"] = frame["low_raw"] * factor
    out["close"] = frame["adj_close"]
    out["volume"] = frame["volume"].astype(float)
    out["adjustment_factor"] = factor.astype(float)
    if (out[["open", "high", "low", "close"]] <= 0.0).any().any():
        raise ValueError("adjusted OHLC contains a non-positive price")
    tolerance = 1e-10
    if (out["high"] + tolerance < out[["open", "close"]].max(axis=1)).any():
        raise ValueError("adjusted high is below open/close")
    if (out["low"] - tolerance > out[["open", "close"]].min(axis=1)).any():
        raise ValueError("adjusted low is above open/close")
    return out


def _fetch(client: httpx.Client, ticker: str, start: str, end: str) -> tuple[bytes, dict[str, Any]]:
    begin = pd.Timestamp(start, tz="UTC")
    finish = pd.Timestamp(end, tz="UTC")
    response = client.get(
        YAHOO_CHART.format(ticker=ticker),
        params={
            "period1": int(begin.timestamp()),
            "period2": int(finish.timestamp()),
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
            "includePrePost": "false",
        },
    )
    response.raise_for_status()
    raw = response.content
    parsed = response.json()
    error = parsed.get("chart", {}).get("error")
    if error:
        raise RuntimeError(f"Yahoo chart error for {ticker}: {error}")
    return raw, parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze Book U adjusted Yahoo input data")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--start", default=DOWNLOAD_START)
    parser.add_argument("--end-exclusive", default=DOWNLOAD_END_EXCLUSIVE)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if not PROTOCOL.is_file():
        raise FileNotFoundError(f"missing frozen protocol: {PROTOCOL}")
    outputs = [args.snapshot, args.manifest]
    if not args.force and any(path.exists() for path in outputs):
        raise FileExistsError("Book U snapshot/manifest already exists; use --force deliberately")
    if not args.force and args.raw_dir.exists() and any(args.raw_dir.iterdir()):
        raise FileExistsError("Book U raw-response directory is not empty; use --force deliberately")

    frames: dict[str, pd.DataFrame] = {}
    sources: dict[str, dict[str, Any]] = {}
    raw_blobs: dict[str, bytes] = {}
    with httpx.Client(timeout=45.0, headers=HEADERS, follow_redirects=True) as client:
        for ticker in USD_ETF_UNIVERSE:
            raw, parsed = _fetch(client, ticker, args.start, args.end_exclusive)
            frame = adjusted_ohlcv_from_yahoo(parsed)
            begin = pd.Timestamp(args.start, tz="UTC")
            finish = pd.Timestamp(args.end_exclusive, tz="UTC")
            frame = frame.loc[(frame.index >= begin) & (frame.index < finish)].copy()
            if frame.empty:
                raise RuntimeError(f"Yahoo returned no in-range rows for {ticker}")
            frames[ticker] = frame
            compressed = gzip.compress(raw, compresslevel=9, mtime=0)
            raw_path = args.raw_dir / f"{ticker}.json.gz"
            # Keep the bytes in memory until every symbol has downloaded and
            # validated.  A transient failure therefore cannot leave a
            # misleading partly frozen vendor directory behind.
            raw_blobs[ticker] = compressed
            sources[ticker] = {
                "ticker": ticker,
                "raw_gzip_path": str(raw_path.relative_to(ENGINE_DIR.parent)),
                "raw_response_sha256": _sha256_bytes(raw),
                "raw_gzip_sha256": _sha256_bytes(compressed),
                "rows_before_common_intersection": int(len(frame)),
                "start_before_common_intersection": frame.index.min().date().isoformat(),
                "end_before_common_intersection": frame.index.max().date().isoformat(),
            }
            print(
                f"{ticker}: {len(frame)} rows "
                f"{frame.index.min().date()}..{frame.index.max().date()}",
                flush=True,
            )

    common: pd.DatetimeIndex | None = None
    for frame in frames.values():
        common = frame.index if common is None else common.intersection(frame.index)
    if common is None or len(common) < 1_500:
        raise RuntimeError("Book U common panel has insufficient history")
    common = common.sort_values()

    for ticker in sorted(raw_blobs):
        _atomic_bytes(args.raw_dir / f"{ticker}.json.gz", raw_blobs[ticker])

    rows: list[pd.DataFrame] = []
    for ticker in sorted(frames):
        part = frames[ticker].loc[common].reset_index()
        part.insert(0, "instrument", ticker)
        rows.append(part)
    snapshot = pd.concat(rows, ignore_index=True)
    args.snapshot.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.snapshot.with_name(f"{args.snapshot.name}.tmp{os.getpid()}")
    try:
        snapshot.to_parquet(temporary, index=False)
        os.replace(temporary, args.snapshot)
    finally:
        if temporary.exists():
            temporary.unlink()

    manifest = {
        "schema_version": 1,
        "kind": "book_u_adjusted_usd_etf_snapshot",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_path": str(PROTOCOL.relative_to(ENGINE_DIR.parent)),
        "protocol_sha256": _sha256_file(PROTOCOL),
        "download": {
            "vendor": "Yahoo Finance chart endpoint",
            "requested_start": args.start,
            "requested_end_exclusive": args.end_exclusive,
            "vendor_request_bounds": "explicit_unix_period1_period2",
            "local_date_filter_applied": True,
            "interval": "1d",
            "events": "div,splits",
            "include_adjusted_close": True,
        },
        "adjustment_policy": (
            "adjust every OHLC field by Yahoo adjclose/close on the same session; "
            "use adjusted close as close; retain raw volume and the adjustment factor"
        ),
        "execution_limitation": (
            "adjusted ETF bars are a USD total-return proxy, not executable FTMO CFD bid/ask data"
        ),
        "instruments": list(USD_ETF_UNIVERSE),
        "common_sessions": int(len(common)),
        "common_start": common.min().date().isoformat(),
        "common_end": common.max().date().isoformat(),
        "sources": sources,
        "snapshot_path": str(args.snapshot.relative_to(ENGINE_DIR.parent)),
        "snapshot_sha256": _sha256_file(args.snapshot),
        "snapshot_bytes": args.snapshot.stat().st_size,
        "snapshot_rows": int(len(snapshot)),
    }
    _atomic_text(
        args.manifest,
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    print(f"snapshot: {args.snapshot} ({manifest['snapshot_sha256']})")
    print(f"manifest: {args.manifest} ({_sha256_file(args.manifest)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
