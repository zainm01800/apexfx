"""Regression tests for the deliberately narrow, causal Book R research loop."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apex_quant.research.book_r_usd_etf import (
    BookRSpec,
    common_panel,
    run_book_r,
    select_book_r,
    validate_usd_etf_universe,
)


def _panel(n: int = 520) -> dict[str, pd.DataFrame]:
    idx = pd.bdate_range("2018-01-02", periods=n, tz="UTC", name="timestamp")
    # QQQ is the strongest broad-equity ETF and XLK the strongest technology
    # ETF.  Selection should not own both QQQ/SPY/IWM or both XLK/SMH merely
    # because they are individually strong.
    drifts = {
        "SPY": 0.0007,
        "QQQ": 0.0012,
        "IWM": 0.0004,
        "XLK": 0.0014,
        "SMH": 0.0010,
        "GLD": 0.0008,
        "TLT": 0.0005,
    }
    out: dict[str, pd.DataFrame] = {}
    for number, (inst, drift) in enumerate(drifts.items()):
        close = 100.0 * np.exp(np.arange(n) * drift)
        # A deterministic, non-zero overnight difference lets the execution
        # test prove the fill is the next session's open rather than the signal
        # close.
        open_ = close * (0.9975 + number * 0.0001)
        out[inst] = pd.DataFrame(
            {
                "open": open_,
                "high": np.maximum(open_, close) * 1.002,
                "low": np.minimum(open_, close) * 0.998,
                "close": close,
                "volume": 1_000_000.0,
            },
            index=idx,
        )
    return out


def test_rejects_non_usd_or_unknown_instrument():
    with pytest.raises(ValueError, match="rejected"):
        validate_usd_etf_universe(["SPY", "QQQ", "IWM", "EUR/USD"])


def test_selection_requires_positive_momentum_and_fixed_cluster_cap():
    panel = common_panel(_panel())
    spec = BookRSpec(name="test", lookback=63, vol_window=63, max_positions=3)
    picked = select_book_r(panel, spec, len(next(iter(panel.values()))) - 1)
    instruments = {row["instrument"] for row in picked}
    clusters = [row["cluster"] for row in picked]
    assert instruments
    assert len(clusters) == len(set(clusters))
    assert len({"SPY", "QQQ", "IWM"} & instruments) <= 1
    assert len({"XLK", "SMH"} & instruments) <= 1
    assert all(row["momentum"] > 0 for row in picked)


def test_signal_uses_next_session_open_not_same_close():
    panel = common_panel(_panel())
    index = next(iter(panel.values())).index
    spec = BookRSpec(name="test", lookback=63)
    result = run_book_r(panel, spec, start=index[252], end=index[-1])
    rebalance_events = [event for event in result.events if event["reason"] == "monthly_rebalance"]
    assert rebalance_events
    first = rebalance_events[0]
    assert first["date"] > first["decision_date"]
    fill_i = index.get_loc(pd.Timestamp(first["date"], tz="UTC"))
    assert first["price_usd"] == pytest.approx(panel[first["instrument"]]["open"].iloc[fill_i])


def test_future_poison_cannot_change_finished_segment_result():
    panel = common_panel(_panel())
    index = next(iter(panel.values())).index
    start, end = index[252], index[430]
    spec = BookRSpec(name="test", lookback=63)
    clean = run_book_r(panel, spec, start=start, end=end)

    poisoned = {inst: frame.copy() for inst, frame in panel.items()}
    for frame in poisoned.values():
        frame.loc[frame.index > end, ["open", "high", "low", "close"]] *= 1000.0
    rerun = run_book_r(poisoned, spec, start=start, end=end)

    assert clean.metrics == rerun.metrics
    assert clean.events == rerun.events
    assert clean.selections == rerun.selections


def test_final_liquidation_cost_is_not_free():
    panel = common_panel(_panel())
    index = next(iter(panel.values())).index
    free = run_book_r(panel, BookRSpec(name="free", lookback=63, cost_bps_per_side=0.0), start=index[252], end=index[-1])
    paid = run_book_r(panel, BookRSpec(name="paid", lookback=63, cost_bps_per_side=5.0), start=index[252], end=index[-1])
    assert paid.metrics["final_equity_usd"] < free.metrics["final_equity_usd"]
    assert any(event["reason"] == "final_liquidation" for event in paid.events)
