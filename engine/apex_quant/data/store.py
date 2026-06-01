"""Local historical store (parquet) with cache-aside fetching.

Backtests must be reproducible and offline-capable, so we persist fetched OHLCV
to parquet and reuse it. The store is a *cache only* — it imposes no point-in-time
semantics itself; leakage safety is the accessor's job.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from apex_quant.config import get_config
from apex_quant.data.adapter import DataAdapter
from apex_quant.data.schema import empty_ohlcv, validate_ohlcv


def _safe_name(instrument: str, timeframe: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{instrument}_{timeframe}").strip("_")
    return f"{slug}.parquet"


class ParquetStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else get_config().store_path
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, instrument: str, timeframe: str) -> Path:
        return self.root / _safe_name(instrument, timeframe)

    def load(self, instrument: str, timeframe: str = "1d") -> pd.DataFrame:
        p = self.path_for(instrument, timeframe)
        if not p.exists():
            return empty_ohlcv()
        return validate_ohlcv(pd.read_parquet(p))

    def save(self, instrument: str, df: pd.DataFrame, timeframe: str = "1d") -> Path:
        df = validate_ohlcv(df)
        p = self.path_for(instrument, timeframe)
        df.to_parquet(p)
        return p

    def get_or_fetch(
        self,
        instrument: str,
        adapter: DataAdapter,
        start: pd.Timestamp | str,
        end: pd.Timestamp | str,
        timeframe: str = "1d",
        *,
        refresh: bool = False,
    ) -> pd.DataFrame:
        """Return OHLCV for ``[start, end]``, fetching+persisting any missing range.

        Merges newly-fetched bars with whatever is cached, de-duplicating on
        timestamp (keep latest fetch). Set ``refresh=True`` to bypass the cache.
        """
        start = pd.Timestamp(start, tz="UTC") if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start)
        end = pd.Timestamp(end, tz="UTC") if pd.Timestamp(end).tzinfo is None else pd.Timestamp(end)

        cached = empty_ohlcv() if refresh else self.load(instrument, timeframe)
        need_fetch = refresh or cached.empty or cached.index[0] > start or cached.index[-1] < end

        if not need_fetch:
            return cached.loc[(cached.index >= start) & (cached.index <= end)]

        fetched = adapter.get_history(instrument, start, end, timeframe)
        combined = pd.concat([cached, fetched])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        combined = validate_ohlcv(combined)
        self.save(instrument, combined, timeframe)
        return combined.loc[(combined.index >= start) & (combined.index <= end)]
