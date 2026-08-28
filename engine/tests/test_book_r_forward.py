"""Forward-paper invariants for the frozen Book R-252 account."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from apex_quant.research.book_r_forward import (
    INITIAL_EQUITY_USD,
    advance_book_r_forward,
    display_daily_rows,
    runtime_payload,
    validate_forward_state,
)
from apex_quant.research.book_r_usd_etf import USD_ETF_UNIVERSE
from scripts.run_paper_portfolio_r import _xnys_month_ends


def _panel(end: str) -> dict[str, pd.DataFrame]:
    index = pd.bdate_range("2022-01-03", end, tz="UTC")
    panel = {}
    for j, inst in enumerate(USD_ETF_UNIVERSE):
        # Different slopes keep the cross-sectional rank deterministic while
        # every ETF remains above the positive absolute-momentum gate.
        close = 80.0 + j * 3.0 + np.arange(len(index)) * (0.04 + j * 0.002)
        panel[inst] = pd.DataFrame(
            {"open": close * 1.0005, "close": close},
            index=index,
        )
    return panel


def test_seed_is_exactly_100k_and_month_end_decision_is_pending() -> None:
    state, rows = advance_book_r_forward(
        _panel("2024-01-31"),
        None,
        month_end_sessions={"2024-01-31"},
    )
    assert len(rows) == 1
    assert state["cash"] == INITIAL_EQUITY_USD
    assert rows[0]["equity"] == INITIAL_EQUITY_USD
    assert not state["positions"]
    assert state["pending"]
    assert all(item["decision_date"] == "2024-01-31" for item in state["pending"].values())


def test_pending_decision_fills_only_at_next_common_open_with_costs() -> None:
    state, _ = advance_book_r_forward(
        _panel("2024-01-31"), None, month_end_sessions={"2024-01-31"}
    )
    selected = set(state["pending"])
    advanced, rows = advance_book_r_forward(
        _panel("2024-02-01"), state, month_end_sessions={"2024-01-31"}
    )

    assert len(rows) == 1
    assert rows[0]["date"] == "2024-02-01"
    assert set(advanced["positions"]) == selected
    assert not advanced["pending"]
    assert {fill["date"] for fill in advanced["fills"]} == {"2024-02-01"}
    assert {fill["decision_date"] for fill in advanced["fills"]} == {"2024-01-31"}
    assert advanced["cost_total"] > 0.0
    assert 0.0 < advanced["cash"] < INITIAL_EQUITY_USD * 0.06


def test_repeat_is_an_idempotent_noop() -> None:
    state, _ = advance_book_r_forward(
        _panel("2024-01-31"), None, month_end_sessions={"2024-01-31"}
    )
    state, _ = advance_book_r_forward(
        _panel("2024-02-01"), state, month_end_sessions={"2024-01-31"}
    )
    repeated, rows = advance_book_r_forward(
        _panel("2024-02-01"), state, month_end_sessions={"2024-01-31"}
    )
    assert rows == []
    assert repeated == state


def test_cash_target_liquidates_without_shorting_or_borrowing() -> None:
    state, _ = advance_book_r_forward(
        _panel("2024-01-31"), None, month_end_sessions={"2024-01-31"}
    )
    state, _ = advance_book_r_forward(
        _panel("2024-02-01"), state, month_end_sessions={"2024-01-31"}
    )
    state["pending"] = {
        "CASH": {
            "pos": {"direction": "flat"},
            "decision_date": "2024-02-01",
            "reason": "absolute_momentum_gate",
        }
    }
    liquidated, rows = advance_book_r_forward(
        _panel("2024-02-02"), state, month_end_sessions=set()
    )
    assert len(rows) == 1
    assert not liquidated["positions"]
    assert not liquidated["pending"]
    assert liquidated["cash"] > 0.0
    assert all(trade["direction"] == "long" for trade in liquidated["trades"])


def test_runtime_payload_is_dashboard_ready_and_separate_from_state() -> None:
    state, _ = advance_book_r_forward(
        _panel("2024-01-31"), None, month_end_sessions={"2024-01-31"}
    )
    payload = runtime_payload(state)
    assert payload["state"]["account_currency"] == "USD"
    assert payload["daily"] == display_daily_rows(state)
    assert payload["positions"] == []
    assert payload["daily"][-1]["state_extra"]["pending"]


def test_parameter_drift_is_rejected() -> None:
    state, _ = advance_book_r_forward(
        _panel("2024-01-31"), None, month_end_sessions={"2024-01-31"}
    )
    changed = copy.deepcopy(state)
    changed["params"]["lookback"] = 126
    with pytest.raises(ValueError, match="parameters changed"):
        validate_forward_state(changed)


def test_exchange_calendar_identifies_holiday_shortened_month_end() -> None:
    month_ends = _xnys_month_ends(
        pd.Timestamp("2024-03-01", tz="UTC"),
        pd.Timestamp("2024-04-10", tz="UTC"),
    )
    # Good Friday was 29 March 2024, so the official final XNYS session was
    # Thursday the 28th rather than a generic weekday month-end.
    assert "2024-03-28" in month_ends
