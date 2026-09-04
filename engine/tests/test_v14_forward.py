from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest

from apex_quant.forward_v14.data import (
    DataUnavailable,
    MarketData,
    normalize_boe_xml,
    next_session,
    select_fx,
    session_close_utc,
    session_open_utc,
)
from apex_quant.forward_v14.signals import build_decision
from apex_quant.forward_v14.spec import BOOKS, SYMBOLS, book_spec
from apex_quant.forward_v14.state import (
    DataRevisionError,
    ForwardInvariantError,
    advance,
    enforce_persistence_deadline,
    new_state,
    public_payload,
    validate_state,
)
from apex_quant.forward_v14.storage import state_sha256, write_remote_verified
from scripts.run_v14_forward import _has_new_pending


XNYS = xcals.get_calendar("XNYS")


def _sessions() -> pd.DatetimeIndex:
    return pd.DatetimeIndex(
        XNYS.sessions_in_range(pd.Timestamp("2025-07-01"), pd.Timestamp("2026-10-30"))
    )


def _panel(*, calm_rsi: bool = False) -> dict[str, pd.DataFrame]:
    sessions = _sessions()
    result = {}
    for j, symbol in enumerate(SYMBOLS):
        close = 100.0 + j * 3.0 + np.arange(len(sessions)) * (0.08 + j * 0.001)
        if calm_rsi:
            # A sharp two-session pullback remains above the 200-session mean
            # and drives the sealed Wilder RSI2 below ten.
            close[-3:] = [close[-4] - 1.0, close[-4] - 3.0, close[-4] - 4.5]
        opening = close - 0.03
        frame = pd.DataFrame(
            {
                "open": opening,
                "high": np.maximum(opening, close) + 0.20,
                "low": np.minimum(opening, close) - 0.20,
                "close": close,
            },
            index=sessions,
        )
        result[symbol] = frame
    return result


def _market(
    latest_index: int,
    *,
    stress: bool = True,
    panel: dict[str, pd.DataFrame] | None = None,
    retrieved_at=None,
) -> MarketData:
    panel = panel or _panel(calm_rsi=not stress)
    sessions = next(iter(panel.values())).index
    latest = sessions[latest_index]
    days = pd.date_range(sessions[0] - pd.Timedelta(days=10), sessions[-1], freq="D")
    vix = pd.DataFrame({"close": 35.0 if stress else 20.0}, index=days)
    fx = pd.DataFrame(
        {
            "close": 1.25,
            "available_at_utc": pd.DatetimeIndex(days).tz_localize("UTC")
            + pd.Timedelta(hours=8),
        },
        index=days,
    )
    retrieved = retrieved_at or session_close_utc(latest) + pd.Timedelta(hours=1)
    return MarketData(
        panel=panel,
        vix=vix,
        fx=fx,
        latest_completed_session=latest,
        retrieved_at_utc=pd.Timestamp(retrieved),
        provenance={"fixture": True, "latest_completed_session": latest.strftime("%Y-%m-%d")},
    )


def _seed_index() -> int:
    return len(_sessions()) - 12


@pytest.mark.parametrize("book_id", ["v6", "v10"])
def test_fresh_activation_has_no_historical_profit_and_atomic_public_contract(book_id):
    spec = book_spec(book_id)
    market = _market(_seed_index())
    now = session_close_utc(market.latest_completed_session) + pd.Timedelta(hours=1)
    state = new_state(spec, market, now=now)
    validate_state(state, spec)
    assert state["cash"] == 100_000.0
    assert state["daily"] == [state["daily"][0]]
    assert state["daily"][0]["is_seed"] is True
    assert state["daily"][0]["cum_pnl"] == 0
    assert state["activation_recorded_at_utc"] == now.isoformat()
    assert state["first_eligible_decision_session"] == next_session(
        market.latest_completed_session
    ).strftime("%Y-%m-%d")
    payload = public_payload(state, spec, generated_at=now)
    assert set(("state", "daily", "positions", "trades", "pending", "metadata")) <= set(payload)
    assert payload["book_id"] == payload["metadata"]["book_id"] == book_id
    assert payload["metadata"]["account_currency"] == "GBP"
    assert payload["metadata"]["paper_only"] is True
    assert payload["metadata"]["broker_enabled"] is False
    assert payload["metadata"]["funded_qualified"] is False
    assert payload["metadata"]["session_count"] == 0


def test_stress_batch_is_persisted_then_fills_only_at_immediate_next_open():
    spec = BOOKS["v6"]
    seed_i = _seed_index()
    initial = _market(seed_i, stress=True)
    state = new_state(spec, initial, now=initial.retrieved_at_utc)
    pending = state["pending_batch"]
    assert pending and len(pending["legs"]) == 4
    assert sum(leg["direction_sign"] > 0 for leg in pending["legs"]) == 2
    assert sum(leg["direction_sign"] < 0 for leg in pending["legs"]) == 2
    fill_i = seed_i + 1
    later = _market(fill_i, stress=True, panel=initial.panel)
    advanced, rows = advance(
        state,
        spec,
        later,
        now=later.retrieved_at_utc,
        pending_was_durable=True,
    )
    assert len(rows) == 1
    assert len(advanced["positions"]) == 4
    entries = [event for event in advanced["events"] if event["event"] == "entry"]
    assert len(entries) == 4
    assert {event["date"] for event in entries} == {later.latest_completed_session.strftime("%Y-%m-%d")}
    for lot in advanced["positions"].values():
        assert lot["entry_date"] == later.latest_completed_session.strftime("%Y-%m-%d")
        assert lot["stop_price"] > 0
        assert lot["initial_total_risk_gbp"] <= spec.per_trade_risk_fraction * 100_000 + 1e-6
    payload = public_payload(advanced, spec, generated_at=later.retrieved_at_utc)
    assert all(position["risk_abs"] == position["current_risk_gbp"] for position in payload["positions"])
    assert sum(position["initial_total_risk"] for position in payload["positions"]) <= (
        spec.aggregate_risk_fraction * spec.entry_utilization * 100_000 + 1e-5
    )


def test_unrestored_pending_is_cancelled_not_backfilled():
    spec = BOOKS["v6"]
    seed_i = _seed_index()
    initial = _market(seed_i)
    state = new_state(spec, initial, now=initial.retrieved_at_utc)
    later = _market(seed_i + 1, panel=initial.panel)
    advanced, _ = advance(state, spec, later, now=later.retrieved_at_utc)
    assert not [event for event in advanced["events"] if event["event"] == "entry"]
    assert advanced["evidence_gap_count"] == 1
    assert any(
        event["event"] == "pending_cancelled"
        and "durable remote" in event["reason"]
        for event in advanced["events"]
    )


def test_manual_preclose_rerun_preserves_already_durable_pending():
    spec = BOOKS["v6"]
    seed_i = _seed_index()
    market = _market(seed_i)
    state = new_state(spec, market, now=market.retrieved_at_utc)
    pending_before = copy.deepcopy(state["pending_batch"])
    eligible = next_session(market.latest_completed_session)
    # No settled new bar yet, but the eligible open has passed.  A restored
    # durable instruction must survive; runner applies deadline only to new IDs.
    intraday = session_open_utc(eligible) + pd.Timedelta(hours=1)
    same, rows = advance(
        state, spec, market, now=intraday, pending_was_durable=True
    )
    assert rows == []
    assert same["pending_batch"] == pending_before
    assert _has_new_pending(state, same) is False


def test_runner_deadline_classifier_detects_only_a_new_pending_identity():
    spec = BOOKS["v6"]
    market = _market(_seed_index())
    state = new_state(spec, market, now=market.retrieved_at_utc)
    assert _has_new_pending(None, state) is True
    restored = copy.deepcopy(state)
    restored["pending_batch"]["legs"][0]["decision_atr"] *= 0.5
    assert _has_new_pending(state, restored) is False
    changed = copy.deepcopy(state)
    changed["pending_batch"]["eligible_fill_session"] = "2099-01-01"
    assert _has_new_pending(state, changed) is True


def test_new_pending_is_dropped_if_remote_persistence_deadline_is_missed():
    spec = BOOKS["v6"]
    market = _market(_seed_index())
    state = new_state(spec, market, now=market.retrieved_at_utc)
    deadline = pd.Timestamp(state["pending_batch"]["persistence_safety_deadline_utc"])
    expired = enforce_persistence_deadline(state, spec, now=deadline)
    assert expired["pending_batch"] is None
    assert expired["evidence_gap_count"] == 1
    assert state["pending_batch"] is not None  # input was not mutated


def test_time_exit_occurs_at_sixth_open_and_accounting_reconciles():
    spec = BOOKS["v10"]
    seed_i = _seed_index()
    initial = _market(seed_i)
    state = new_state(spec, initial, now=initial.retrieved_at_utc)
    end = _market(seed_i + 6, panel=initial.panel)
    advanced, _ = advance(
        state,
        spec,
        end,
        now=end.retrieved_at_utc,
        pending_was_durable=True,
    )
    exits = [trade for trade in advanced["trades"] if trade["exit_reason"] == "time_exit"]
    assert len(exits) == 4
    fill_session = next_session(initial.latest_completed_session)
    expected_exit = next_session(fill_session, 5).strftime("%Y-%m-%d")
    assert {trade["exit_time"] for trade in exits} == {expected_exit}
    open_charged = sum(
        lot["entry_fee_gbp"] + lot["holding_cost_gbp"] + lot["borrow_cost_gbp"]
        for lot in advanced["positions"].values()
    )
    assert advanced["cash"] == pytest.approx(
        100_000 + sum(trade["net_pnl_gbp"] for trade in advanced["trades"]) - open_charged,
        abs=1e-6,
    )
    validate_state(advanced, spec)


def test_outage_never_backfills_unpersisted_close_decisions():
    spec = BOOKS["v6"]
    seed_i = _seed_index()
    initial = _market(seed_i)
    state = new_state(spec, initial, now=initial.retrieved_at_utc)
    # The original pending batch was durable.  Catch-up happens after its time
    # exit and several later opens; only the newest close can plan forward.
    late = _market(seed_i + 9, panel=initial.panel)
    advanced, _ = advance(
        state,
        spec,
        late,
        now=late.retrieved_at_utc,
        pending_was_durable=True,
    )
    entries = [event for event in advanced["events"] if event["event"] == "entry"]
    assert len(entries) == 4
    assert advanced["evidence_gap_count"] >= 1
    assert advanced["pending_batch"]["decision_date"] == late.latest_completed_session.strftime("%Y-%m-%d")


def test_uniform_adjustment_rebases_pending_atr_but_preserves_original():
    spec = BOOKS["v6"]
    seed_i = _seed_index()
    initial = _market(seed_i)
    state = new_state(spec, initial, now=initial.retrieved_at_utc)
    symbol = state["pending_batch"]["legs"][0]["instrument"]
    original_atr = next(
        leg["decision_atr"] for leg in state["pending_batch"]["legs"] if leg["instrument"] == symbol
    )
    adjusted_panel = {key: frame.copy() for key, frame in initial.panel.items()}
    adjusted_panel[symbol].loc[:, ["open", "high", "low", "close"]] *= 0.5
    later = _market(seed_i + 1, panel=adjusted_panel)
    advanced, _ = advance(
        state,
        spec,
        later,
        now=later.retrieved_at_utc,
        pending_was_durable=True,
    )
    lot = advanced["positions"][symbol]
    assert lot["decision_atr_original"] == pytest.approx(original_atr)
    assert lot["decision_atr"] == pytest.approx(original_atr * 0.5)
    assert any(event["event"] == "adjusted_history_rebase" for event in advanced["events"])


def test_nonuniform_adjusted_anchor_revision_fails_without_mutating_state():
    spec = BOOKS["v6"]
    seed_i = _seed_index()
    initial = _market(seed_i)
    state = new_state(spec, initial, now=initial.retrieved_at_utc)
    before = state_sha256(state)
    changed_panel = {key: frame.copy() for key, frame in initial.panel.items()}
    anchor = initial.latest_completed_session
    changed_panel["XLK"].at[anchor, "high"] *= 1.01
    later = _market(seed_i + 1, panel=changed_panel)
    with pytest.raises(DataRevisionError):
        advance(state, spec, later, now=later.retrieved_at_utc, pending_was_durable=True)
    assert state_sha256(state) == before


def test_calm_and_stress_signal_definitions_are_directionally_exact():
    spec = BOOKS["v6"]
    calm = _market(-1, stress=False)
    calm_decision = build_decision(calm.panel, calm.vix, calm.latest_completed_session, spec)
    assert calm_decision["regime"] == "low_vix_rsi2_long"
    assert calm_decision["legs"]
    assert all(leg["direction"] == "long" and leg["rsi2"] < 10 for leg in calm_decision["legs"])
    stress = _market(_seed_index(), stress=True)
    stress_decision = build_decision(stress.panel, stress.vix, stress.latest_completed_session, spec)
    assert stress_decision["regime"] == "stress_sector_reversal"
    assert sorted(leg["direction"] for leg in stress_decision["legs"]) == ["long", "long", "short", "short"]


def test_boe_publication_timezone_and_fx_causality():
    xml = b"""<?xml version='1.0'?><Envelope><Cube TIME='2026-09-03'
      OBS_VALUE='1.3518' LAST_UPDATED='2026-09-04 09:30:00'/></Envelope>"""
    fx = normalize_boe_xml(xml)
    assert pd.Timestamp(fx.iloc[0]["available_at_utc"]) == pd.Timestamp("2026-09-04T08:30:00Z")
    row = select_fx(fx, pd.Timestamp("2026-09-04"), BOOKS["v6"])
    assert row["rate"] == pytest.approx(1.3518)
    assert row["source_date"] == "2026-09-03"
    late = fx.copy()
    late["available_at_utc"] = pd.Timestamp("2026-09-04T15:00:00Z")
    with pytest.raises(DataUnavailable):
        select_fx(late, pd.Timestamp("2026-09-04"), BOOKS["v6"])


def test_holiday_calendar_assigns_friday_decision_to_tuesday_open():
    assert next_session(pd.Timestamp("2026-09-04")).strftime("%Y-%m-%d") == "2026-09-08"


class _Response:
    def __init__(self, status_code, data=None, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self):
        return self._data


class _MemorySupabase:
    def __init__(self, feature_vector=None):
        self.feature_vector = copy.deepcopy(feature_vector)

    def get(self, *_args, **_kwargs):
        rows = [] if self.feature_vector is None else [{"feature_vector": self.feature_vector}]
        return _Response(200, rows)

    def post(self, *_args, **kwargs):
        self.feature_vector = copy.deepcopy(kwargs["json"][0]["feature_vector"])
        return _Response(201, [])


def test_remote_write_requires_lineage_and_readback(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    spec = BOOKS["v6"]
    market = _market(_seed_index())
    state = new_state(spec, market, now=market.retrieved_at_utc)
    payload = public_payload(state, spec, generated_at=market.retrieved_at_utc)
    client = _MemorySupabase()
    write_remote_verified(payload, spec, client=client)
    assert client.feature_vector["state"] == state
    stale = copy.deepcopy(state)
    stale["revision"] = 2
    stale["parent_state_sha256"] = "0" * 64
    stale_payload = public_payload(stale, spec, generated_at=market.retrieved_at_utc)
    with pytest.raises(RuntimeError, match="advanced since"):
        write_remote_verified(stale_payload, spec, client=client)


def test_state_parameter_and_accounting_tampering_is_rejected():
    spec = BOOKS["v6"]
    market = _market(_seed_index())
    state = new_state(spec, market, now=market.retrieved_at_utc)
    wrong = copy.deepcopy(state)
    wrong["cash"] -= 1
    with pytest.raises(ForwardInvariantError, match="cash attribution"):
        validate_state(wrong, spec)
    drift = copy.deepcopy(state)
    drift["spec"]["fee_bps_each_side"] = 0
    with pytest.raises(ValueError, match="parameters changed"):
        validate_state(drift, spec)


def test_production_sources_have_no_broker_dependency_and_workflow_serializes():
    root = Path(__file__).resolve().parents[2]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((root / "engine/apex_quant/forward_v14").glob("*.py"))
    ).lower()
    assert "import ibkr" not in source
    assert "place_order" not in source
    workflow = (root / ".github/workflows/forward-v14.yml").read_text(encoding="utf-8")
    assert "cancel-in-progress: false" in workflow
    assert "--book v6" in workflow and "--book v10" in workflow
    assert 'cron: "30 23 * * 1-5"' in workflow
