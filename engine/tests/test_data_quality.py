"""Data-quality checks: clean baseline, injected defects, forex weekend logic."""

from __future__ import annotations

import pandas as pd

from apex_quant.data.quality import check_quality, clean


def test_clean_series_reports_clean(clean_daily):
    rep = check_quality(clean_daily, instrument="EUR/USD")
    assert rep.is_clean, rep.summary()
    assert rep.n_bars == len(clean_daily)
    assert rep.n_duplicates == 0
    assert rep.n_holes == 0


def test_weekend_gap_is_not_a_hole(clean_daily):
    """The builder uses a business-day calendar — Fri→Mon must not be flagged."""
    rep = check_quality(clean_daily, instrument="EUR/USD")
    assert rep.n_holes == 0
    assert rep.missing_business_days == 0


def test_detects_duplicates(clean_daily):
    dup = pd.concat([clean_daily, clean_daily.iloc[[10, 20, 30]]]).sort_index()
    rep = check_quality(dup, instrument="EUR/USD")
    assert rep.n_duplicates == 3
    assert not rep.is_clean


def test_detects_ohlc_violation(clean_daily):
    bad = clean_daily.copy()
    # force high < low on one bar
    bad.iloc[40, bad.columns.get_loc("high")] = bad.iloc[40]["low"] - 0.5
    rep = check_quality(bad, instrument="EUR/USD")
    assert rep.n_ohlc_violations >= 1
    assert not rep.is_clean


def test_detects_nonpositive_price(clean_daily):
    bad = clean_daily.copy()
    bad.iloc[5, bad.columns.get_loc("close")] = -1.0
    rep = check_quality(bad, instrument="EUR/USD")
    assert rep.n_nonpositive >= 1


def test_detects_multiday_hole(clean_daily):
    """Removing several consecutive business days creates a real hole."""
    # drop a contiguous block of 4 business days in the middle
    drop_idx = clean_daily.index[100:104]
    holed = clean_daily.drop(index=drop_idx)
    rep = check_quality(holed, instrument="EUR/USD")
    assert rep.n_holes >= 1
    assert rep.missing_business_days >= 4


def test_clean_repairs_duplicates_and_sorts(clean_daily):
    shuffled = pd.concat([clean_daily.iloc[::-1], clean_daily.iloc[[1, 2]]])
    repaired = clean(shuffled)
    assert repaired.index.is_monotonic_increasing
    assert not repaired.index.has_duplicates
    assert len(repaired) == len(clean_daily)


def test_clean_drops_bad_rows(clean_daily):
    bad = clean_daily.copy()
    bad.iloc[7, bad.columns.get_loc("close")] = -1.0  # non-positive -> dropped
    repaired = clean(bad)
    assert len(repaired) == len(clean_daily) - 1
    assert (repaired[["open", "high", "low", "close"]] > 0).all().all()


def test_clean_repairs_ohlc_violations(clean_daily):
    """Yahoo-style feed artifact: close pokes above high -> clamp the envelope."""
    bad = clean_daily.copy()
    i = 40
    bad.iloc[i, bad.columns.get_loc("close")] = bad.iloc[i]["high"] + 0.5
    assert check_quality(bad).n_ohlc_violations >= 1

    repaired = clean(bad, fix_ohlc=True)
    rep = check_quality(repaired)
    assert rep.n_ohlc_violations == 0
    # the row is kept (not dropped) and high now envelops the close
    assert len(repaired) == len(clean_daily)
    assert repaired.iloc[i]["high"] >= repaired.iloc[i]["close"]
