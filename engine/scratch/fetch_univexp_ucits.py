"""Fetch the data-verified universe-expansion UCITS candidates INTO the store — the same
standard Yahoo-adapter -> clean -> ParquetStore.save path used for the 36 halal-screen
names on 2026-07-22 and for the Book H UCITS lines on 2026-07-19.

Only tickers that passed the data probe (scratch/probe_univexp_candidates.json) are
fetched. New parquets only; no existing parquet is touched. The 2025+ holdout rows are
present in the cache exactly as for every other cached instrument — every gate filters
strictly < 2025-01-01 itself.

Usage: cd engine && .venv-mac/bin/python scratch/fetch_univexp_ucits.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import pandas as pd  # noqa: E402

from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import ParquetStore, clean  # noqa: E402
from apex_quant.data.yahoo_adapter import YahooAdapter  # noqa: E402

FETCH_START = "2015-01-01"   # same as scripts/rebuild_daily_caches.py
TICKERS = [
    "SXLV.L", "SXLI.L", "SXLB.L", "SXLP.L", "SXLY.L",   # SPDR US sector UCITS (USD)
    "IUHC.L",                                            # iShares healthcare (USD, alt)
    "RBOT.L", "DGTL.L",                                  # iShares thematic (USD)
    "IUIT.L", "SXLE.L", "BTEC.L", "CNDX.L",              # expected-dup documentation rows
]


def main() -> None:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    adapter = YahooAdapter()
    end = pd.Timestamp.utcnow()
    for t in TICKERS:
        df = clean(adapter.get_history(t, FETCH_START, end, "1d"))
        if df.empty:
            print(f"{t}: FETCH EMPTY — not saved")
            continue
        path = store.save(t, df, "1d")
        print(f"{t}: {len(df)} bars {df.index[0].date()} -> {df.index[-1].date()} -> {path.name}",
              flush=True)


if __name__ == "__main__":
    main()
