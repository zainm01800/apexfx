"""Data-quality checks: gaps, duplicates, OHLC integrity, forex session/weekend.

Forex trades ~24/5: it closes Friday evening and reopens Sunday evening (UTC).
So a Fri→Mon jump on daily bars is an *expected* weekend gap, NOT a data hole.
The checker uses a business-day calendar to tell the two apart, and reports
findings rather than silently mutating data. ``clean`` is the explicit repair.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from apex_quant.config import QualityConfig, get_config
from apex_quant.data.schema import OHLCV_COLUMNS, INDEX_NAME


class GapDetail(BaseModel):
    after: str
    before: str
    missing_business_days: int


class QualityReport(BaseModel):
    instrument: str
    n_bars: int
    start: str | None = None
    end: str | None = None
    n_duplicates: int = 0
    n_holes: int = 0
    missing_business_days: int = 0
    gaps: list[GapDetail] = []
    n_ohlc_violations: int = 0
    n_nonpositive: int = 0
    n_nan: int = 0
    is_monotonic: bool = True

    @property
    def is_clean(self) -> bool:
        return (
            self.n_duplicates == 0
            and self.n_ohlc_violations == 0
            and self.n_nonpositive == 0
            and self.n_nan == 0
            and self.is_monotonic
            and self.n_holes == 0
        )

    def summary(self) -> str:
        flag = "CLEAN" if self.is_clean else "ISSUES"
        return (
            f"[{flag}] {self.instrument}: {self.n_bars} bars "
            f"({self.start} -> {self.end}) | dupes={self.n_duplicates} "
            f"holes={self.n_holes} (missing_bd={self.missing_business_days}) "
            f"ohlc_violations={self.n_ohlc_violations} nonpositive={self.n_nonpositive} "
            f"nan={self.n_nan} monotonic={self.is_monotonic}"
        )


def _index_utc(df: pd.DataFrame) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(df.index)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def check_quality(
    df: pd.DataFrame,
    *,
    instrument: str = "frame",
    timeframe: str = "1d",
    quality_cfg: QualityConfig | None = None,
) -> QualityReport:
    """Inspect a (possibly dirty) OHLCV frame and report findings."""
    quality_cfg = quality_cfg or get_config().data.quality

    if df is None or len(df) == 0:
        return QualityReport(instrument=instrument, n_bars=0)

    idx = _index_utc(df)
    n = len(df)

    n_duplicates = int(idx.duplicated().sum())
    is_monotonic = bool(idx.is_monotonic_increasing)

    cols = [c for c in OHLCV_COLUMNS if c in df.columns]
    sub = df[cols]
    n_nan = int(sub[["open", "high", "low", "close"]].isna().any(axis=1).sum())
    price = sub[["open", "high", "low", "close"]]
    n_nonpositive = int((price <= 0).any(axis=1).sum())

    hi, lo, op, cl = sub["high"], sub["low"], sub["open"], sub["close"]
    violation = (hi < lo) | (hi < op) | (hi < cl) | (lo > op) | (lo > cl)
    n_ohlc_violations = int(violation.fillna(False).sum())

    # Gap detection (daily only). Use a unique, sorted index so duplicate
    # timestamps don't masquerade as gaps.
    gaps: list[GapDetail] = []
    n_holes = 0
    missing_total = 0
    if timeframe == "1d":
        uidx = idx[~idx.duplicated()].sort_values()
        for prev, cur in zip(uidx[:-1], uidx[1:]):
            # business days strictly between prev and cur
            span = pd.bdate_range(
                prev.normalize() + pd.Timedelta(days=1),
                cur.normalize() - pd.Timedelta(days=1),
            )
            missing = len(span)
            if missing > quality_cfg.max_gap_bars:
                n_holes += 1
                missing_total += missing
                gaps.append(
                    GapDetail(
                        after=str(prev.date()),
                        before=str(cur.date()),
                        missing_business_days=missing,
                    )
                )

    return QualityReport(
        instrument=instrument,
        n_bars=n,
        start=str(idx.min()),
        end=str(idx.max()),
        n_duplicates=n_duplicates,
        n_holes=n_holes,
        missing_business_days=missing_total,
        gaps=gaps[:50],  # cap the report size
        n_ohlc_violations=n_ohlc_violations,
        n_nonpositive=n_nonpositive,
        n_nan=n_nan,
        is_monotonic=is_monotonic,
    )


def clean(
    df: pd.DataFrame,
    *,
    quality_cfg: QualityConfig | None = None,
    fix_ohlc: bool = True,
) -> pd.DataFrame:
    """Repair a frame: UTC index, sort, drop dupes per policy, drop rows with
    NaN/non-positive OHLC. Never fabricates bars to fill gaps.

    When ``fix_ohlc`` is set, OHLC integrity violations (common in Yahoo forex
    feeds: open/close fractionally outside the high/low range) are repaired by
    clamping high = max(o,h,l,c) and low = min(o,h,l,c). This cannot introduce
    look-ahead — it only widens the range to envelope values that, by
    definition, must lie within it.
    """
    quality_cfg = quality_cfg or get_config().data.quality

    out = df.copy()
    out.index = _index_utc(out)
    out.index.name = INDEX_NAME
    out = out.sort_index()

    keep = "last" if quality_cfg.duplicate_policy == "keep_last" else "first"
    out = out[~out.index.duplicated(keep=keep)]

    cols = [c for c in OHLCV_COLUMNS if c in out.columns]
    out = out[cols]
    price = out[["open", "high", "low", "close"]]
    out = out[price.notna().all(axis=1) & (price > 0).all(axis=1)]

    if fix_ohlc and len(out):
        ohlc = out[["open", "high", "low", "close"]]
        out["high"] = ohlc.max(axis=1)
        out["low"] = ohlc.min(axis=1)

    if "volume" in out.columns:
        out["volume"] = out["volume"].fillna(0.0)
    return out
