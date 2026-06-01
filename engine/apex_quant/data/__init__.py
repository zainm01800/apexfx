"""Data layer - typed OHLCV, source adapters, local store, and the
point-in-time accessor that structurally prevents look-ahead bias."""

from apex_quant.data.adapter import DataAdapter, get_adapter
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.data.quality import QualityReport, check_quality, clean
from apex_quant.data.schema import OHLCV_COLUMNS, Bar, validate_ohlcv
from apex_quant.data.store import ParquetStore

# Import concrete adapters for their registration side effects.
from apex_quant.data import yahoo_adapter as _yahoo_adapter  # noqa: E402,F401

__all__ = [
    "Bar",
    "OHLCV_COLUMNS",
    "validate_ohlcv",
    "DataAdapter",
    "get_adapter",
    "ParquetStore",
    "PointInTimeAccessor",
    "QualityReport",
    "check_quality",
    "clean",
]
