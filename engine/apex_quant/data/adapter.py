"""Data source adapter interface + registry.

The engine never talks to a vendor API directly - it talks to a ``DataAdapter``.
This keeps the source swappable (Yahoo today, a broker feed later) and lets the
backtester run against a local store without code changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from apex_quant.data.schema import Bar

_REGISTRY: dict[str, type["DataAdapter"]] = {}


def register_adapter(name: str):
    """Class decorator: register an adapter under a provider id."""

    def _wrap(cls: type["DataAdapter"]) -> type["DataAdapter"]:
        _REGISTRY[name.lower()] = cls
        return cls

    return _wrap


def get_adapter(name: str, **kwargs) -> "DataAdapter":
    key = name.lower()
    if key not in _REGISTRY:
        raise KeyError(f"unknown data provider '{name}'; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[key](**kwargs)


class DataAdapter(ABC):
    """Abstract OHLCV source. Implementations return contract-valid frames."""

    @abstractmethod
    def get_history(
        self,
        instrument: str,
        start: pd.Timestamp | str,
        end: pd.Timestamp | str,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        """Return OHLCV bars for ``[start, end]`` obeying the schema contract."""

    @abstractmethod
    def get_latest(self, instrument: str, timeframe: str = "1d") -> Bar | None:
        """Return the most recent completed bar, or ``None`` if unavailable."""
