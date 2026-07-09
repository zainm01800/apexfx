"""Point-in-time accessor - the structural defence against look-ahead bias.

A feature, label, or decision computed for time ``t`` may use ONLY data that
existed at ``t``. Rather than trusting every call site to slice correctly, we
funnel all historical access through this accessor:

  * It holds the full series privately and never hands out a reference to it.
  * ``as_of(t)`` returns a *copy* containing only rows with ``timestamp <= t``.
  * ``walk()`` drives event-driven backtests, yielding a clean view per step.

Because callers only ever receive ``<= t`` copies, a feature *cannot* see the
future even by accident. The leakage test suite proves this by injecting a
poison future bar and asserting feature values are unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd

from apex_quant.data.schema import validate_ohlcv


class LookAheadError(RuntimeError):
    """Raised when an operation would require data from the future."""


class PointInTimeAccessor:
    """Read-only, leakage-safe view over an OHLCV series."""

    def __init__(self, df: pd.DataFrame, *, validate: bool = True):
        # Defensive copy + contract check. The private frame is never exposed.
        self._df = validate_ohlcv(df) if validate else df.copy()

    # -- introspection --------------------------------------------------------
    @property
    def start(self) -> pd.Timestamp | None:
        return self._df.index[0] if len(self._df) else None

    @property
    def end(self) -> pd.Timestamp | None:
        """Last timestamp present in the underlying series (NOT a peek tool -
        decisions must still pass an explicit ``t`` to :meth:`as_of`)."""
        return self._df.index[-1] if len(self._df) else None

    def __len__(self) -> int:
        return len(self._df)

    @staticmethod
    def _norm(t: pd.Timestamp | str) -> pd.Timestamp:
        ts = pd.Timestamp(t)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    # -- the core leakage-safe slice -------------------------------------------
    def as_of(self, t: pd.Timestamp | str, *, inclusive: bool = True) -> pd.DataFrame:
        """All bars known at ``t`` - i.e. ``timestamp <= t`` (or ``< t``).

        Returns a *copy*; mutating it cannot affect the accessor or leak future
        rows into another caller.
        """
        ts = self._norm(t)
        mask = self._df.index <= ts if inclusive else self._df.index < ts
        return self._df.loc[mask].copy()

    def window(self, t: pd.Timestamp | str, n: int, *, inclusive: bool = True) -> pd.DataFrame:
        """The last ``n`` bars known at ``t`` (most recent last)."""
        if n <= 0:
            raise ValueError("n must be positive")
        return self.as_of(t, inclusive=inclusive).iloc[-n:]

    def latest(self, t: pd.Timestamp | str, *, inclusive: bool = True) -> pd.Series | None:
        """The most recent bar known at ``t`` (or ``None`` if none exists)."""
        sub = self.as_of(t, inclusive=inclusive)
        return sub.iloc[-1] if len(sub) else None

    def value_at(self, t: pd.Timestamp | str, column: str, *, inclusive: bool = True) -> float | None:
        bar = self.latest(t, inclusive=inclusive)
        return None if bar is None else float(bar[column])

    # -- event-driven iteration ------------------------------------------------
    def timestamps(
        self,
        start: pd.Timestamp | str | None = None,
        end: pd.Timestamp | str | None = None,
    ) -> pd.DatetimeIndex:
        idx = self._df.index
        if start is not None:
            idx = idx[idx >= self._norm(start)]
        if end is not None:
            idx = idx[idx <= self._norm(end)]
        return idx

    def walk(
        self,
        start: pd.Timestamp | str | None = None,
        end: pd.Timestamp | str | None = None,
        *,
        warmup: int = 0,
    ) -> Iterator[tuple[pd.Timestamp, pd.DataFrame]]:
        """Yield ``(t, history_as_of_t)`` for each bar in ``[start, end]``.

        ``warmup`` skips the first N bars so features have enough history. The
        yielded frame is exactly what was knowable at ``t`` - the backtest makes
        its decision at ``t`` using only this, then realises the next bar.
        """
        stamps = self.timestamps(start, end)
        for i, t in enumerate(stamps):
            if i < warmup:
                continue
            yield t, self.as_of(t)

    def require(self, t: pd.Timestamp | str, n: int) -> pd.DataFrame:
        """Like :meth:`window` but raises if fewer than ``n`` bars are available
        at ``t`` - for features that cannot honestly compute on short history."""
        w = self.window(t, n)
        if len(w) < n:
            raise LookAheadError(
                f"only {len(w)} bars available as_of {self._norm(t)}, need {n}"
            )
        return w
