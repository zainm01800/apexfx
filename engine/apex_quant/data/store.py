"""Local historical store (parquet) with cache-aside fetching.

Backtests must be reproducible and offline-capable, so we persist fetched OHLCV
to parquet and reuse it. The store is a *cache only* - it imposes no point-in-time
semantics itself; leakage safety is the accessor's job.

Data-integrity guarantees (2026-07-17 audit, findings D-C1 / D-H2 / D-H3):

* **Session convention** -- every write of day-based bars is remapped to the
  single session-date convention in :mod:`apex_quant.data.calendar` (forex
  daily = Mon-Fri session dates, Sunday 17:00-NY session labelled Monday;
  forex weekly = Monday labels). Rows that still violate the asset-class
  session calendar after mapping (vendor weekend junk) are *rejected on
  write* with a warning - they never reach the cache.
* **Atomic writes** -- every write goes tmp-file + ``os.replace``; readers
  only ever see complete parquets.
* **Locking** -- ``save`` and the whole ``get_or_fetch`` load->merge->save
  sequence hold an fcntl lock on a ``<file>.lock`` sidecar, so concurrent
  processes cannot lose each other's merges.
* **Corruption tolerance** -- ``load`` treats an unreadable/invalid file as
  *missing* (logged), so the next ``get_or_fetch`` refetches/rebuilds instead
  of raising permanently.
* **Forming bars** -- ``get_or_fetch`` trims still-forming terminal bars
  before persisting (and before returning), so a partial bar never enters
  the cache.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pandas as pd

from apex_quant.config import get_config
from apex_quant.data._filelock import file_lock
from apex_quant.data.adapter import DataAdapter
from apex_quant.data.calendar import (
    asset_class_for,
    off_calendar_mask,
    session_normalize,
    trim_forming_tail,
)
from apex_quant.data.schema import empty_ohlcv, validate_ohlcv

logger = logging.getLogger(__name__)


def _safe_name(instrument: str, timeframe: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{instrument}_{timeframe}").strip("_")
    return f"{slug}.parquet"


# Timeframes whose bars span a whole day (or week). The store keeps exactly one
# bar per period, labelled at 00:00 UTC of the bar's session date (forex) or
# calendar date (crypto/equity) — see calendar.session_dates.
_DAY_TIMEFRAMES = {"1d", "1w"}


def normalize_day_bars(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Pin day-based bars to 00:00 UTC of their UTC calendar date.

    LEGACY helper kept for existing callers (dedup migration, paper
    portfolio): it is exactly the non-forex branch of the store convention
    and is idempotent on already session-normalised forex frames (which are
    pinned to 00:00 UTC Mon-Fri). New code should use
    :func:`apex_quant.data.calendar.session_normalize`, which additionally
    remaps vendor-specific labels (OANDA 17:00-NY opens) onto session dates.
    """
    if timeframe not in _DAY_TIMEFRAMES or df.empty:
        return df
    out = df.copy()
    out.index = out.index.normalize()
    return out


def _atomic_to_parquet(df: pd.DataFrame, p: Path) -> None:
    """Write parquet via tmp + os.replace: readers never see a torn file."""
    tmp = p.with_name(f"{p.name}.tmp{os.getpid()}")
    try:
        df.to_parquet(tmp)
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()


class ParquetStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else get_config().store_path
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, instrument: str, timeframe: str) -> Path:
        return self.root / _safe_name(instrument, timeframe)

    def _duplicate_keep(self) -> str:
        return "last" if get_config().data.quality.duplicate_policy == "keep_last" else "first"

    def load(self, instrument: str, timeframe: str = "1d") -> pd.DataFrame:
        """Load the cached frame; a missing OR corrupt file reads as empty.

        Corruption is logged and treated as *missing* so the next
        ``get_or_fetch`` refetches/rebuilds — a torn file must never be a
        permanent failure (D-H3). Atomic writes make corruption rare; this is
        the safety net for files damaged by other means.
        """
        p = self.path_for(instrument, timeframe)
        if not p.exists():
            return empty_ohlcv()
        try:
            return validate_ohlcv(pd.read_parquet(p))
        except Exception as exc:  # parquet errors, SchemaError, OSError, ...
            logger.warning(
                "ParquetStore.load: %s unreadable (%s: %s) — treating as missing",
                p.name, type(exc).__name__, exc,
            )
            return empty_ohlcv()

    def _prepare(self, instrument: str, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """Normalise -> de-duplicate -> reject off-calendar rows -> validate.

        Session mapping (day TFs) runs BEFORE de-duplication so two vendor
        labels for the same session (e.g. Yahoo Monday-midnight and OANDA
        Sunday-21:00, both the Monday session) collapse to one bar. On a
        collision the row *later in the frame* wins under the configured
        ``keep_last`` policy — in ``get_or_fetch`` that is the freshly
        fetched row. Off-calendar rows (Sat/Sun for forex daily, non-Monday
        for forex weekly, out-of-session intraday junk) are dropped with a
        warning: the session calendar is enforced at the boundary (D-C1).
        """
        out = session_normalize(df, instrument, timeframe)
        if out.index.has_duplicates:
            out = out[~out.index.duplicated(keep=self._duplicate_keep())]
        if len(out):
            bad = off_calendar_mask(out.index, instrument, timeframe)
            if bool(bad.any()):
                dropped = pd.DatetimeIndex(out.index[bad.to_numpy()])
                logger.warning(
                    "ParquetStore.save: rejecting %d off-calendar %s %s row(s) "
                    "(%s calendar), e.g. %s",
                    int(bad.sum()), instrument, timeframe,
                    asset_class_for(instrument),
                    [str(t) for t in dropped[:3]],
                )
                out = out[~bad.to_numpy()]
        return validate_ohlcv(out)

    def save(self, instrument: str, df: pd.DataFrame, timeframe: str = "1d") -> Path:
        """Normalise and atomically persist ``df`` under an exclusive lock."""
        prepared = self._prepare(instrument, df, timeframe)
        p = self.path_for(instrument, timeframe)
        with file_lock(p):
            _atomic_to_parquet(prepared, p)
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

        The whole load->fetch->merge->save runs under the file lock, so
        concurrent processes serialise instead of clobbering each other.
        Merges de-duplicate on the *session-normalised* timestamp using the
        configured ``data.quality.duplicate_policy`` (the same policy
        ``save`` uses; previously hard-coded ``keep="last"`` here) — with
        ``concat([cached, fetched])`` the freshly fetched row wins a
        collision under the default ``keep_last``.

        Two conventions guard the cache (see module docstring): day bars are
        remapped to session dates, and still-forming terminal bars (the
        in-progress bar at the live edge) are trimmed BEFORE persisting and
        before returning, so a partial bar never enters the cache (D-H2).
        Note the trade-off: while the current bar is still forming, the cache
        ends at the last completed bar and every call with ``end`` inside the
        forming bar re-fetches — deliberate, since persisting the partial bar
        is worse. Set ``refresh=True`` to bypass the cache.
        """
        start = pd.Timestamp(start, tz="UTC") if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start)
        end = pd.Timestamp(end, tz="UTC") if pd.Timestamp(end).tzinfo is None else pd.Timestamp(end)

        p = self.path_for(instrument, timeframe)
        with file_lock(p):
            cached = empty_ohlcv() if refresh else self.load(instrument, timeframe)
            need_fetch = refresh or cached.empty or cached.index[0] > start or cached.index[-1] < end

            if not need_fetch:
                return cached.loc[(cached.index >= start) & (cached.index <= end)]

            fetched = adapter.get_history(instrument, start, end, timeframe)
            combined = pd.concat([cached, fetched])
            if combined.empty:
                return empty_ohlcv()
            combined = session_normalize(combined, instrument, timeframe)
            combined = combined[~combined.index.duplicated(keep=self._duplicate_keep())].sort_index()
            combined = trim_forming_tail(combined, instrument, timeframe)
            if combined.empty:
                return empty_ohlcv()
            prepared = self._prepare(instrument, combined, timeframe)
            _atomic_to_parquet(prepared, p)
            return prepared.loc[(prepared.index >= start) & (prepared.index <= end)]
