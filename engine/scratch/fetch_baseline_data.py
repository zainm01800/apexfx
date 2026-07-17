"""One-off data top-up for the live-baseline portfolio backtest (2026-07-17).

Fetches missing/stale OHLCV for the 22 config.yaml forex pairs through the
normal data layer (ParquetStore.get_or_fetch + OandaAdapter). Credentials are
loaded by dotenv from engine/.env — this script never reads that file directly.

Only writes parquet cache files under engine/data_store/ (same as any backtest
data refresh). Does NOT touch the MT4 bridge directory.
"""
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
load_dotenv(ENGINE_DIR / ".env")

from apex_quant.config import get_config
from apex_quant.data import get_adapter
from apex_quant.data.store import ParquetStore

PAIRS = list(get_config().data.instruments)
MAJORS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD",
          "USD/CAD", "NZD/USD", "GBP/JPY", "EUR/GBP", "EUR/JPY"]

NOW = pd.Timestamp.utcnow().tz_convert("UTC").floor("s")
print(f"fetch end (now): {NOW}", flush=True)

# (timeframe, start for majors-with-recent-cache, start for stale/missing cache)
JOBS = [
    ("15m", "2026-06-25", "2025-05-25"),
    ("1h",  "2026-06-25", "2025-05-01"),
    ("1d",  "2026-06-01", "2026-06-01"),
    ("1w",  "2014-01-01", "2014-01-01"),
]

store = ParquetStore()
adapter = get_adapter("oanda")

for tf, start_major, start_stale in JOBS:
    for pair in PAIRS:
        start = start_major if pair in MAJORS else start_stale
        try:
            cached = store.load(pair, tf)
            before = (len(cached), str(cached.index[-1]) if len(cached) else "EMPTY")
            df = store.get_or_fetch(pair, adapter, start, NOW, timeframe=tf)
            print(f"{pair:9s} {tf}: cache n={before[0]} last={before[1]}  ->  "
                  f"n={len(df)} first={df.index[0]} last={df.index[-1]}", flush=True)
        except Exception as e:
            print(f"{pair:9s} {tf}: FETCH FAILED: {e}", flush=True)

print("DONE", flush=True)
