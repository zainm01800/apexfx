"""Leakage suite — the build's most important guarantee.

These tests deliberately inject future data and confirm the point-in-time
accessor blocks it. The negative-control test proves the methodology has teeth:
a deliberately-leaky feature IS caught, so a passing PIT feature is meaningful.
"""

from __future__ import annotations

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from apex_quant.data.point_in_time import LookAheadError, PointInTimeAccessor


# A leakage-safe feature: SMA computed only from the accessor's PIT window.
def sma_as_of(pit: PointInTimeAccessor, t, n: int) -> float:
    return float(pit.window(t, n)["close"].mean())


# ── core leakage proofs ────────────────────────────────────────────────────────
def test_no_future_leakage_in_pit_feature(clean_daily):
    """Corrupting every future bar must NOT change a PIT feature value at t0."""
    pit_clean = PointInTimeAccessor(clean_daily)
    t0 = clean_daily.index[len(clean_daily) // 2]
    base_val = sma_as_of(pit_clean, t0, 20)

    poisoned = clean_daily.copy()
    future = poisoned.index > t0
    poisoned.loc[future, ["open", "high", "low", "close"]] *= 1000.0  # absurd future

    pit_poison = PointInTimeAccessor(poisoned)
    assert sma_as_of(pit_poison, t0, 20) == pytest.approx(base_val)


def test_appending_future_bars_does_not_change_the_past(clean_daily, make_ohlcv):
    """Extending the series into the future leaves as_of(t0) byte-identical."""
    t0 = clean_daily.index[100]
    before = PointInTimeAccessor(clean_daily).as_of(t0)

    future = make_ohlcv(n=50, start="2023-06-01", seed=7, base=5.0)  # disjoint, wild
    extended = pd.concat([clean_daily, future])
    after = PointInTimeAccessor(extended).as_of(t0)

    assert_frame_equal(before, after)


def test_leakage_detector_catches_a_leaky_feature(clean_daily):
    """Negative control: a feature that peeks at the whole frame IS detected.
    This proves the poison methodology would fail a genuinely leaky feature."""

    def leaky_mean(df_full: pd.DataFrame, t, n: int) -> float:
        return float(df_full["close"].mean())  # uses ALL rows incl. future — bad

    t0 = clean_daily.index[len(clean_daily) // 2]
    base = leaky_mean(clean_daily, t0, 20)

    poisoned = clean_daily.copy()
    poisoned.loc[poisoned.index > t0, "close"] *= 1000.0
    leaked = leaky_mean(poisoned, t0, 20)

    assert leaked != pytest.approx(base)  # detector has teeth


# ── accessor invariants ────────────────────────────────────────────────────────
def test_as_of_never_returns_future(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    for t in (clean_daily.index[0], clean_daily.index[50], clean_daily.index[-1]):
        sub = pit.as_of(t)
        assert (sub.index <= t).all()


def test_as_of_inclusive_vs_exclusive(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    t = clean_daily.index[50]
    incl = pit.as_of(t, inclusive=True)
    excl = pit.as_of(t, inclusive=False)
    assert t in incl.index
    assert t not in excl.index
    assert len(incl) == len(excl) + 1


def test_as_of_returns_independent_copy(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    t = clean_daily.index[-1]
    sub = pit.as_of(t)
    sub.loc[:, "close"] = -999.0
    assert (pit.as_of(t)["close"] != -999.0).all()  # mutation didn't leak back


def test_window_bounds(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    t = clean_daily.index[100]
    w = pit.window(t, 30)
    assert len(w) == 30
    assert (w.index <= t).all()
    assert w.index[-1] == t


def test_require_raises_when_insufficient_history(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    early = clean_daily.index[3]
    with pytest.raises(LookAheadError):
        pit.require(early, 100)


def test_walk_yields_only_pit_views(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    count = 0
    for t, hist in pit.walk(warmup=20):
        assert hist.index.max() <= t
        count += 1
    assert count == len(clean_daily) - 20


def test_string_timestamp_is_accepted(clean_daily):
    pit = PointInTimeAccessor(clean_daily)
    sub = pit.as_of("2022-03-01")
    assert (sub.index <= pd.Timestamp("2022-03-01", tz="UTC")).all()
