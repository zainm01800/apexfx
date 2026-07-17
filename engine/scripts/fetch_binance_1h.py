"""Fetch BTC/USDT + ETH/USDT 1h klines from the PUBLIC Binance spot API into the
local parquet store, for the intraday close-momentum candidate
(docs/research/2026-07-17_subdaily_edges_post_cost.md, sec.1).

Why this exists: Yahoo 1h crypto history only reaches ~730d back, which would
leave the <2025-01-01 iteration window ~6 months deep - too thin to validate
anything. Binance has BTCUSDT/ETHUSDT 1h back to 2017 and needs no API key.

Conventions (match the store's existing 1h OANDA caches):
  * index: tz-aware UTC DatetimeIndex named ``timestamp``, one row per bar,
    labeled by the bar's OPEN time (a bar labeled 19:00 covers [19:00, 20:00);
    its close is the 20:00 price). NOTE: data/schema.py aspires to close-time
    labels, but the de-facto 1h store convention (OANDA adapter) is open-time;
    the intraday strategies are written against open-time labels.
  * columns: open, high, low, close, volume (float64) - base-asset volume.
  * USDT ~ USD: no conversion is applied; USDT has traded within ~10bps of USD
    except brief depeg episodes. Documented, accepted.

Hard rule: the fetch stops at 2025-01-01T00:00:00Z (exclusive) - the 2025+
holdout is never touched.

Usage:
    cd engine && .venv-mac/bin/python scripts/fetch_binance_1h.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.data.quality import clean  # noqa: E402
from apex_quant.data.schema import validate_ohlcv  # noqa: E402

BASE_URL = "https://api.binance.com/api/v3/klines"
START = pd.Timestamp("2018-01-01T00:00:00Z")
END = pd.Timestamp("2025-01-01T00:00:00Z")  # exclusive - never touch 2025+
LIMIT = 1000                                 # max klines per call
SYMBOLS = {"BTCUSDT": "BINANCE_BTC_USD", "ETHUSDT": "BINANCE_ETH_USD"}
HOUR_MS = 3_600_000


def fetch_klines(symbol: str) -> pd.DataFrame:
    """Paginate the klines endpoint from START to END (open times, ms epoch)."""
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    rows: list[list] = []
    with httpx.Client(timeout=30.0) as client:
        while start_ms < end_ms:
            params = {
                "symbol": symbol,
                "interval": "1h",
                "startTime": start_ms,
                "endTime": end_ms - 1,
                "limit": LIMIT,
            }
            r = client.get(BASE_URL, params=params)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            rows.extend(batch)
            last_open = int(batch[-1][0])
            start_ms = last_open + HOUR_MS
            print(f"  {symbol}: {len(rows)} bars so far "
                  f"(through {pd.Timestamp(last_open, unit='ms', tz='UTC')})", flush=True)
            if len(batch) < LIMIT:
                break
            time.sleep(0.15)  # be polite; weight 2/call, nowhere near the cap
    if not rows:
        raise RuntimeError(f"{symbol}: no klines returned")

    idx = pd.DatetimeIndex(
        [pd.Timestamp(int(k[0]), unit="ms", tz="UTC") for k in rows], name="timestamp"
    )
    df = pd.DataFrame(
        {
            "open": [float(k[1]) for k in rows],
            "high": [float(k[2]) for k in rows],
            "low": [float(k[3]) for k in rows],
            "close": [float(k[4]) for k in rows],
            "volume": [float(k[5]) for k in rows],
        },
        index=idx,
    )
    df = df[df.index < END]  # hard guarantee: nothing from 2025+
    return df


def main() -> int:
    store_dir = ENGINE_DIR / "data_store"
    for symbol, out_name in SYMBOLS.items():
        print(f"{symbol}: fetching 1h klines {START} -> {END} (exclusive)", flush=True)
        df = fetch_klines(symbol)
        df = clean(df)                      # sort, dedup, drop NaN/non-positive, clamp OHLC
        df = validate_ohlcv(df)             # contract check (loud on corruption)

        # continuity audit: Binance 1h should be perfectly hourly; report, don't fill
        gaps = df.index.to_series().diff().dropna()
        gaps = gaps[gaps != pd.Timedelta(hours=1)]
        if len(gaps):
            print(f"  WARNING: {len(gaps)} non-1h step(s) (exchange outages):")
            for ts, g in gaps.head(10).items():
                print(f"    gap of {g} ending at {ts}")

        path = store_dir / f"{out_name}_1h.parquet"
        df.to_parquet(path)
        print(f"  wrote {path.name}: {len(df)} bars, {df.index[0]} -> {df.index[-1]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
