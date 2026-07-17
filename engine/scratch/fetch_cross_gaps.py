"""Fill the 15m/1h gaps for the 12 forex crosses (2025-05 -> now).

The OandaAdapter paginates in fixed spans of 4800 x tf_seconds and stops when a
span returns < 4800 candles -- which weekend gaps guarantee. So one call covers
at most ~50 days (15m) / ~200 days (1h). This script walks forward in chunks
below that span, merging into the ParquetStore cache via the normal data layer.
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
MAJORS = {"EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD",
          "USD/CAD", "NZD/USD", "GBP/JPY", "EUR/GBP", "EUR/JPY"}
CROSSES = [p for p in PAIRS if p not in MAJORS]

NOW = pd.Timestamp.utcnow().tz_convert("UTC").floor("s")
store = ParquetStore()
adapter = get_adapter("oanda")

JOBS = [  # (timeframe, chunk_days, first_start)
    ("15m", 45, pd.Timestamp("2025-05-25", tz="UTC")),
    ("1h", 170, pd.Timestamp("2025-05-01", tz="UTC")),
]

for tf, chunk_days, first_start in JOBS:
    step = pd.Timedelta(days=chunk_days)
    for pair in CROSSES:
        start = first_start
        while start < NOW:
            end = min(start + step + pd.Timedelta(days=2), NOW)  # small overlap
            try:
                df = store.get_or_fetch(pair, adapter, start, end, timeframe=tf)
            except Exception as e:
                print(f"{pair} {tf}: chunk {start.date()} FAILED: {e}", flush=True)
                break
            start = start + step
        cached = store.load(pair, tf)
        recent = cached[cached.index >= first_start]
        print(f"{pair:9s} {tf}: total n={len(cached)} last={cached.index[-1]} | "
              f"since {first_start.date()}: n={len(recent)}", flush=True)

print("DONE", flush=True)
