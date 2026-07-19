"""Resample the cached Binance 1h klines (BTC/ETH) to 4h bars for the 4h trend
candidate (pre-registration: data_store/crypto_4h_prereg_2026-07-17.md).

Conventions — identical to the 1h cache built by scripts/fetch_binance_1h.py:
  * index: tz-aware UTC DatetimeIndex named ``timestamp``, labeled by the bar's
    OPEN time. A 4h bar labeled 04:00 covers [04:00, 08:00).
  * bars are aligned to 00:00 UTC day boundaries: bins [00,04), [04,08), ...,
    [20,24) — six bars per calendar day, 24/7 (crypto never closes).
  * columns: open (first), high (max), low (min), close (last), volume (sum).
  * USDT ~ USD: no conversion (documented in the 1h fetch).
  * INCOMPLETE bins are DROPPED: a 4h bin contributes to the output only if all
    four 1h bars are present, so every output bar is a true 4-hour bar. The 1h
    cache has ~27 exchange-outage gaps (2018-19 mostly, reported not filled);
    each gap makes its touched bins incomplete. Dropped bins are reported, never
    filled — same philosophy as the 1h cache.
  * written DIRECTLY via ``df.to_parquet`` next to the 1h files (never through
    ParquetStore.save — these BINANCE_* files are a documented parallel cache
    the live daemon does not read; the name matches ``_safe_name`` so a
    read-only ``store.load("BINANCE_BTC_USD", "4h")`` still works).

Hard rule: the output contains nothing from 2025-01-01T00:00:00Z onward (the
1h source already stops 2024-12-31 23:00; asserted here again).

Usage:
    cd engine && .venv-mac/bin/python scripts/build_binance_4h.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from apex_quant.data.quality import clean  # noqa: E402
from apex_quant.data.schema import validate_ohlcv  # noqa: E402

HOLDOUT_START = pd.Timestamp("2025-01-01T00:00:00Z")  # exclusive — never touched
INSTRUMENTS = ["BTC", "ETH"]
BARS_PER_BIN = 4  # 4 x 1h per 4h bin


def resample_4h(df_1h: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """1h open-time bars -> 4h open-time bars aligned to 00:00 UTC day boundaries.

    Returns (4h frame, number of incomplete bins dropped)."""
    grouped = df_1h.resample("4h", origin="start_day", label="left", closed="left")
    out = grouped.agg({"open": "first", "high": "max", "low": "min",
                       "close": "last", "volume": "sum"})
    n_contrib = grouped["close"].count()
    # empty bins come back NaN; incomplete bins (outage gaps) are dropped whole
    out = out.dropna(subset=["close"])
    n_contrib = n_contrib.loc[out.index]
    incomplete = n_contrib[n_contrib < BARS_PER_BIN]
    out = out[n_contrib == BARS_PER_BIN]
    return out, len(incomplete)


def main() -> int:
    store_dir = ENGINE_DIR / "data_store"
    for inst in INSTRUMENTS:
        src = store_dir / f"BINANCE_{inst}_USD_1h.parquet"
        df_1h = pd.read_parquet(src)
        assert df_1h.index[-1] < HOLDOUT_START, f"{inst} 1h source reaches into 2025+"

        df_4h, n_dropped = resample_4h(df_1h)
        df_4h = clean(df_4h)            # sort, dedup, drop NaN/non-positive, clamp OHLC
        df_4h = validate_ohlcv(df_4h)   # contract check (loud on corruption)
        assert df_4h.index[-1] < HOLDOUT_START, f"{inst} 4h output reaches into 2025+"

        # structural audit: every label on the 4h grid, every step a multiple of 4h
        assert (df_4h.index.hour % 4 == 0).all() and (df_4h.index.minute == 0).all(), \
            f"{inst}: 4h labels not aligned to 00:00 UTC day boundaries"
        steps = df_4h.index.to_series().diff().dropna()
        off_grid = steps[steps % pd.Timedelta(hours=4) != pd.Timedelta(0)]
        assert off_grid.empty, f"{inst}: non-4h-multiple steps found"
        gaps = steps[steps != pd.Timedelta(hours=4)]

        path = store_dir / f"BINANCE_{inst}_USD_4h.parquet"
        df_4h.to_parquet(path)
        print(f"{inst}/USD 4h: {len(df_1h)} 1h bars -> {len(df_4h)} 4h bars "
              f"({df_4h.index[0]} -> {df_4h.index[-1]})")
        print(f"  incomplete bins dropped (exchange outages, not filled): {n_dropped}")
        print(f"  4h-grid gaps in output: {len(gaps)} (expected ~1 per outage)")
        print(f"  wrote {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
