"""Regression tests for the pre-registered Book R stop-loss overlay."""

from __future__ import annotations

import gzip
import hashlib
import json
from io import BytesIO
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from apex_quant.research.book_r_stop_overlay import BookRStopSpec, run_book_r_stop_overlay
from scripts.run_book_r_stop_overlay_audit import (
    DEFAULT_JSON,
    EXPECTED_OBSERVED_TRIAL_CELLS,
    ORIGINAL_SELECTION_SPECS,
    SNAPSHOT_SHA256,
    _report,
    _run_exposure_matched_no_stop,
    _snapshot_bytes,
)


def _panel(n: int = 820) -> dict[str, pd.DataFrame]:
    index = pd.bdate_range("2018-01-02", periods=n, tz="UTC", name="timestamp")
    drifts = {
        "SPY": 0.0005,
        "QQQ": 0.0008,
        "IWM": 0.0003,
        "XLK": 0.0010,
        "SMH": 0.0009,
        "GLD": 0.0006,
        "TLT": 0.0002,
    }
    out: dict[str, pd.DataFrame] = {}
    wave = np.sin(np.arange(n) / 11.0) * 0.002
    for number, (instrument, drift) in enumerate(drifts.items()):
        close = 100.0 * np.exp(np.cumsum(drift + wave))
        open_ = close * (0.998 + number * 0.00005)
        out[instrument] = pd.DataFrame(
            {
                "open": open_,
                "high": np.maximum(open_, close) * 1.008,
                "low": np.minimum(open_, close) * 0.992,
                "close": close,
                "volume": 1_000_000.0,
            },
            index=index,
        )
    return out


def _run(panel: dict[str, pd.DataFrame], spec: BookRStopSpec | None = None):
    index = next(iter(panel.values())).index
    return run_book_r_stop_overlay(
        panel,
        spec or BookRStopSpec(),
        start=index[252],
        end=index[-1],
    )


def _first_entry(run):
    return next(
        row for row in run.events
        if row["reason"] == "monthly_rebalance" and row["side"] == "buy"
    )


def test_next_open_entry_has_stop_and_risk_sized_units() -> None:
    run = _run(_panel())
    entry = _first_entry(run)
    assert entry["date"] > entry["decision_date"]
    assert entry["stop_price_usd"] < entry["price_usd"]
    intended_loss = entry["units"] * (entry["price_usd"] - entry["stop_price_usd"])
    assert intended_loss <= 850.0 + 1e-6
    assert run.metrics["average_close_gross_exposure"] <= 0.95
    assert (
        run.metrics["max_planned_position_price_risk_fraction_before_costs"]
        <= 0.0085 + 1e-12
    )
    assert (
        run.metrics["max_planned_aggregate_price_risk_fraction_before_costs"]
        <= 0.0255 + 1e-12
    )
    assert run.metrics["max_planned_gross_fraction"] <= 0.95 + 1e-12
    assert run.metrics["minimum_cash_usd"] >= 0.0


def test_intraday_stop_fills_at_stop_when_open_is_safe() -> None:
    clean_panel = _panel()
    clean = _run(clean_panel)
    entry = _first_entry(clean)
    instrument = entry["instrument"]
    index = clean_panel[instrument].index
    hit_date = index[index.get_loc(pd.Timestamp(entry["date"], tz="UTC")) + 1]
    stopped_panel = {name: frame.copy() for name, frame in clean_panel.items()}
    stopped_panel[instrument].loc[hit_date, "low"] = 1.0
    stopped = _run(stopped_panel)
    event = next(
        row for row in stopped.events
        if row["reason"] == "stop_loss" and row["instrument"] == instrument
    )
    assert event["date"] == hit_date.strftime("%Y-%m-%d")
    assert event["gap_through_stop"] is False
    assert event["price_usd"] == pytest.approx(event["stop_price_usd"])


def test_gap_through_stop_fills_at_worse_open() -> None:
    clean_panel = _panel()
    clean = _run(clean_panel)
    entry = _first_entry(clean)
    instrument = entry["instrument"]
    stop = float(entry["stop_price_usd"])
    index = clean_panel[instrument].index
    gap_date = index[index.get_loc(pd.Timestamp(entry["date"], tz="UTC")) + 1]
    gap_open = stop * 0.80
    stopped_panel = {name: frame.copy() for name, frame in clean_panel.items()}
    stopped_panel[instrument].loc[gap_date, ["open", "close"]] = gap_open
    stopped_panel[instrument].loc[gap_date, "high"] = gap_open * 1.01
    stopped_panel[instrument].loc[gap_date, "low"] = gap_open * 0.99
    stopped = _run(stopped_panel)
    event = next(
        row for row in stopped.events
        if row["reason"] == "stop_loss" and row["instrument"] == instrument
    )
    assert event["date"] == gap_date.strftime("%Y-%m-%d")
    assert event["gap_through_stop"] is True
    assert event["price_usd"] == pytest.approx(gap_open)
    actual_loss = entry["units"] * (entry["price_usd"] - event["price_usd"])
    assert actual_loss > 850.0


def test_cost_and_stop_slippage_stress_reduce_equity() -> None:
    panel = _panel()
    # Force recurring stop touches so the extra stop-slippage assumption binds.
    for frame in panel.values():
        frame.loc[frame.index[300::45], "low"] *= 0.90
    base = _run(panel)
    stressed = _run(
        panel,
        replace(BookRStopSpec(), cost_bps_per_side=10.0, stop_slippage_bps=25.0),
    )
    assert stressed.metrics["transaction_cost_usd"] > base.metrics["transaction_cost_usd"]
    assert stressed.metrics["final_equity_usd"] < base.metrics["final_equity_usd"]


def test_stop_only_control_uses_equal_weight_cap() -> None:
    run = _run(_panel(), replace(BookRStopSpec(), risk_fraction=None))
    entry = _first_entry(run)
    assert entry["units"] * entry["price_usd"] == pytest.approx(95_000.0 / 3.0, rel=0.02)


def test_future_poison_cannot_change_finished_result() -> None:
    panel = _panel()
    index = next(iter(panel.values())).index
    end = index[620]
    spec = BookRStopSpec()
    clean = run_book_r_stop_overlay(panel, spec, start=index[252], end=end)
    poisoned = {name: frame.copy() for name, frame in panel.items()}
    for frame in poisoned.values():
        frame.loc[frame.index > end, ["open", "high", "low", "close"]] *= 1000.0
    rerun = run_book_r_stop_overlay(poisoned, spec, start=index[252], end=end)
    assert clean.metrics == rerun.metrics
    assert clean.events == rerun.events
    assert clean.selections == rerun.selections


def test_missing_high_or_low_is_rejected() -> None:
    panel = _panel()
    panel["SPY"] = panel["SPY"].drop(columns="low")
    with pytest.raises(ValueError, match="lacks stop-test columns"):
        _run(panel)


def test_multiplicity_includes_all_original_selection_cells() -> None:
    assert [(spec.lookback, spec.cost_bps_per_side) for spec in ORIGINAL_SELECTION_SPECS] == [
        (63, 5.0),
        (126, 5.0),
        (252, 5.0),
    ]
    assert len(ORIGINAL_SELECTION_SPECS) * 2 + 6 == EXPECTED_OBSERVED_TRIAL_CELLS


def test_exposure_matched_control_matches_realized_close_exposure() -> None:
    panel = _panel()
    index = next(iter(panel.values())).index
    _, realised = _run_exposure_matched_no_stop(
        panel,
        start=index[252],
        end=index[-1],
        target_average_gross=0.35,
    )
    assert realised == pytest.approx(0.35, abs=1e-8)


def test_gap_stop_blocks_same_open_reentry() -> None:
    panel = _panel()
    clean = _run(panel)
    first_selection = clean.selections[0]
    instrument = first_selection["selected"][0]["instrument"]
    later = next(
        selection
        for selection in clean.selections[1:]
        if instrument in {row["instrument"] for row in selection["selected"]}
    )
    gap_date = pd.Timestamp(later["fill_date"], tz="UTC")
    stopped_panel = {name: frame.copy() for name, frame in panel.items()}
    stopped_panel[instrument].loc[gap_date, ["open", "close", "low", "high"]] = [
        1.00,
        1.01,
        0.99,
        1.02,
    ]
    stopped = _run(stopped_panel)
    assert any(
        row["reason"] == "stop_loss"
        and row["instrument"] == instrument
        and row["date"] == later["fill_date"]
        and row["gap_through_stop"]
        for row in stopped.events
    )
    assert not any(
        row["reason"] == "monthly_rebalance"
        and row["side"] == "buy"
        and row["instrument"] == instrument
        and row["date"] == later["fill_date"]
        for row in stopped.events
    )


def test_final_liquidation_closes_every_position_and_pays_cost() -> None:
    run = _run(_panel())
    net_units: dict[str, float] = {}
    for event in run.events:
        sign = 1.0 if event["side"] == "buy" else -1.0
        net_units[event["instrument"]] = (
            net_units.get(event["instrument"], 0.0) + sign * float(event["units"])
        )
    assert all(abs(value) < 1e-8 for value in net_units.values())
    liquidations = [row for row in run.events if row["reason"] == "final_liquidation"]
    assert liquidations
    assert all(float(row["cost_usd"]) > 0.0 for row in liquidations)


def test_frozen_snapshot_is_available_and_hash_locked() -> None:
    assert hashlib.sha256(_snapshot_bytes()).hexdigest() == SNAPSHOT_SHA256


def test_published_report_includes_every_frozen_comparison() -> None:
    parts_dir = DEFAULT_JSON.parent / f"{DEFAULT_JSON.name}.gz.parts"
    compressed = b"".join(path.read_bytes() for path in sorted(parts_dir.glob("chunk-*")))
    with gzip.GzipFile(fileobj=BytesIO(compressed), mode="rb") as handle:
        payload = json.loads(handle.read())
    report = _report(payload)
    for label in (
        "Baseline",
        "Stop + 0.85% price-risk sizing",
        "Stop only",
        "Exposure-matched no stop",
        "2.0ATR sensitivity",
        "3.0ATR sensitivity",
    ):
        assert label in report
