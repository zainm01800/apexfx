"""Scratch check: does OandaAdapter paginate a multi-year 1h window fully?

Read-only against the OANDA API; does NOT touch parquet stores or the daemon.
Prints rows, first/last bar, and a per-month gap report (expected weekday
hours vs actual bars) so multi-month holes are obvious.

Usage: engine/.venv-mac/bin/python engine/scratch/check_oanda_pagination.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from dotenv import load_dotenv

load_dotenv(ENGINE_ROOT / ".env")

from apex_quant.data.oanda_adapter import OandaAdapter

INSTRUMENT = "EUR/USD"
START = "2021-01-01"
END = "2026-01-01"


def main() -> None:
    adapter = OandaAdapter()
    df = adapter.get_history(INSTRUMENT, START, END, "1h")

    print(f"instrument={INSTRUMENT} timeframe=1h window=[{START}, {END}]")
    print(f"rows={len(df)}")
    if not len(df):
        print("EMPTY RESULT")
        return
    print(f"first={df.index[0]}")
    print(f"last ={df.index[-1]}")

    # Per-month gap report: expected weekday hours vs actual bars.
    expected = pd.bdate_range(START, END, tz="UTC")
    expected_hours = expected.to_series().groupby(expected.to_period("M")).count() * 24
    months = df.index.to_period("M")
    actual = df.groupby(months).size()

    print("\nmonth    actual  expected  missing")
    hole_months = []
    for period in pd.period_range(START, END, freq="M"):
        a = int(actual.get(period, 0))
        e = int(expected_hours.get(period, 0))
        miss = e - a
        flag = ""
        if a == 0:
            hole_months.append(str(period))
            flag = "  <-- HOLE"
        elif miss > e * 0.25:
            flag = "  <-- >25% missing"
        print(f"{period}  {a:7d}  {e:8d}  {miss:7d}{flag}")

    coverage_days = (df.index[-1] - df.index[0]).days
    print(f"\ncoverage_days={coverage_days}  hole_months={len(hole_months)}")
    if hole_months:
        print("holes:", ", ".join(hole_months))


if __name__ == "__main__":
    main()
