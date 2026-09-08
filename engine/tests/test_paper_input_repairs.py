"""Offline regressions: preserved ledgers, genuine data holes and input audits."""
import copy
import json

import httpx
import pandas as pd
import pytest

from apex_quant.forward_v14.data import DataUnavailable
from apex_quant.forward_v14.spec import BOOKS
from apex_quant.forward_v14.state import advance, new_state, public_payload, DataRevisionError
from apex_quant.forward_v14.storage import RemoteRead, state_sha256, write_runner_status
from apex_quant.models.paper_readiness import PaperInputError, require_daily_panel
from scripts import run_v14_forward as runner
from test_v14_forward import _market, _seed_index


def _dates(*dates):
    return pd.DataFrame({"close": [100.] * len(dates)}, index=pd.to_datetime(list(dates), utc=True))


def test_london_hole_still_blocks_when_a_fresh_terminal_bar_arrives():
    panel = {"ISWD.L": _dates("2026-09-04", "2026-09-08")}
    with pytest.raises(PaperInputError) as result:
        require_daily_panel(panel, panel, "2026-09-09", after="2026-09-05")
    assert result.value.details["missing_sessions"] == ["2026-09-07"]
    assert result.value.details["latest_available_session"] == "2026-09-08"
    assert "do not impute" in result.value.details["action"]


def test_us_holiday_does_not_become_a_london_holiday():
    frame = _dates("2026-09-04")
    require_daily_panel({"SPY": frame}, ["SPY"], "2026-09-08", after="2026-09-05")
    with pytest.raises(PaperInputError, match="2026-09-07"):
        require_daily_panel({"SGLD.L": frame}, ["SGLD.L"], "2026-09-08", after="2026-09-05")


def test_crypto_missing_middle_weekend_day_is_not_hidden_by_fresh_monday():
    panel = {"BTC/USD": _dates("2026-09-05", "2026-09-07")}
    with pytest.raises(PaperInputError) as result:
        require_daily_panel(panel, panel, "2026-09-08", after="2026-09-05")
    assert result.value.details["missing_sessions"] == ["2026-09-06"]


def _seed(book="v6", stress=False):
    spec = BOOKS[book]
    market = _market(_seed_index(), stress=stress)
    state = new_state(spec, market, now=market.retrieved_at_utc)
    return spec, market, state


def _corrected(market, symbol="XLE", field="low"):
    panel = {sym: frame.copy() for sym, frame in market.panel.items()}
    panel[symbol].at[market.latest_completed_session, field] += 0.005
    return _market(_seed_index(), stress=False, panel=panel)


@pytest.mark.parametrize("book", ["v6", "v10"])
def test_seed_high_low_correction_preserves_original_no_signal_evidence(book):
    spec, market, state = _seed(book)
    original = copy.deepcopy(state)
    assert not state["pending_batch"] and state["decisions"][0]["status"] == "NO_SIGNAL"
    corrected = _corrected(market)
    result, rows = advance(state, spec, corrected, now=corrected.retrieved_at_utc)
    assert state == original and rows == []
    for field in ["cash", "peak", "daily", "trades", "positions", "pending_batch", "decisions",
                  "activation_recorded_at_utc", "seed_session", "first_eligible_decision_session"]:
        assert result[field] == original[field]
    audit = result["events"][-1]
    assert audit["event"] == "unexposed_seed_ohlc_correction"
    assert audit["previous_bar"] == original["last_observed_bars"]["XLE"]
    assert audit["replacement_bar"] == result["last_observed_bars"]["XLE"]
    assert audit["preserved_decision_input_sha256"] == [state["decisions"][0]["decision_input_sha256"]]
    repeated, rows = advance(result, spec, corrected, now=corrected.retrieved_at_utc)
    assert repeated == result and rows == []


@pytest.mark.parametrize("field", ["open", "close"])
def test_seed_signal_price_revision_still_fails_closed(field):
    spec, market, state = _seed()
    before = state_sha256(state)
    with pytest.raises(DataRevisionError):
        advance(state, spec, _corrected(market, field=field))
    assert state_sha256(state) == before


def test_seed_with_durable_instruction_does_not_get_correction_exception():
    spec, market, state = _seed(stress=True)
    assert state["pending_batch"]
    before = state_sha256(state)
    with pytest.raises(DataRevisionError):
        advance(state, spec, _corrected(market), pending_was_durable=True)
    assert state_sha256(state) == before


def _runner_fixture(monkeypatch):
    spec, market, state = _seed()
    payload = public_payload(state, spec, generated_at=market.retrieved_at_utc)
    monkeypatch.setattr(runner, "load_local", lambda *a: None)
    monkeypatch.setattr(runner, "fetch_remote", lambda *a: RemoteRead("found", payload=payload))
    monkeypatch.setattr(runner, "save_local", lambda *a: None)
    monkeypatch.setattr(runner, "new_state", lambda *a, **k: pytest.fail("must not reseed"))
    return spec, market, state, payload


def test_v14_data_failure_reports_blocked_metadata_without_mutating_ledger(monkeypatch):
    spec, market, state, payload = _runner_fixture(monkeypatch)
    before = copy.deepcopy(payload)
    def fail():
        raise DataUnavailable("XLE: provider revision needs inspection")
    monkeypatch.setattr(runner, "fetch_market_data", fail)
    statuses = []
    monkeypatch.setattr(runner, "write_runner_status", lambda *a, **k: statuses.append(k))
    monkeypatch.setattr(runner, "write_remote_verified", lambda *a, **k: pytest.fail("no ledger write"))
    assert runner.main(["--book", "v6"]) == 1
    assert len(statuses) == 1 and statuses[0]["status"] == "blocked"
    assert statuses[0]["restored_state_hash"] == state_sha256(state)
    assert "provider revision" in statuses[0]["error"]
    assert payload == before


def test_v14_dry_run_failure_writes_neither_status_nor_ledger(monkeypatch):
    _runner_fixture(monkeypatch)
    def fail():
        raise DataUnavailable("offline")
    monkeypatch.setattr(runner, "fetch_market_data", fail)
    monkeypatch.setattr(runner, "write_runner_status", lambda *a, **k: pytest.fail("dry-run status write"))
    monkeypatch.setattr(runner, "write_remote_verified", lambda *a, **k: pytest.fail("dry-run ledger write"))
    assert runner.main(["--book", "v6", "--dry-run"]) == 1


def test_v14_missing_remote_never_automatically_activates(monkeypatch):
    _runner_fixture(monkeypatch)
    monkeypatch.setattr(runner, "fetch_remote", lambda *a: RemoteRead("missing"))
    monkeypatch.setattr(runner, "fetch_market_data", lambda: pytest.fail("restore must precede data"))
    assert runner.main(["--book", "v6"]) == 1


def test_v14_seed_correction_writes_next_revision_with_original_history(monkeypatch):
    spec, market, state, payload = _runner_fixture(monkeypatch)
    monkeypatch.setattr(runner, "fetch_market_data", lambda: _corrected(market))
    saved = []
    monkeypatch.setattr(runner, "write_remote_verified", lambda p, *a: saved.append(p))
    assert runner.main(["--book", "v6"]) == 0
    assert len(saved) == 1
    result = saved[0]
    assert result["state"]["revision"] == state["revision"] + 1
    assert result["state"]["parent_state_sha256"] == state_sha256(state)
    assert result["state"]["daily"] == state["daily"]
    assert result["state"]["decisions"] == state["decisions"]
    assert result["metadata"]["runner_status"] == "input_revision_recorded"


def test_metadata_cas_preserves_every_ledger_field_and_data_timestamp(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-not-a-real-key")
    spec, market, state = _seed()
    original = public_payload(state, spec, generated_at=market.retrieved_at_utc)
    saved = copy.deepcopy(original)
    def route(request):
        nonlocal saved
        if request.method == "PATCH":
            assert request.url.params["feature_vector->state->>revision"] == "eq.1"
            assert request.url.params["feature_vector->metadata->>runner_checked_at"] == "is.null"
            saved = json.loads(request.content)["feature_vector"]
            return httpx.Response(200, json=[{"feature_vector": saved}])
        return httpx.Response(200, json=[{"feature_vector": saved}])
    with httpx.Client(transport=httpx.MockTransport(route)) as client:
        write_runner_status(spec, restored_state_hash=state_sha256(state), status="blocked",
                            checked_at="2026-11-01T00:00:00+00:00", error="missing XLE", client=client)
    for key in original.keys() - {"metadata"}:
        assert saved[key] == original[key]
    assert saved["metadata"]["runner_status"] == "blocked"
    assert saved["metadata"]["runner_error"] == "missing XLE"


def test_metadata_cas_rejects_concurrent_ledger_change(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-not-a-real-key")
    spec, market, state = _seed()
    payload = public_payload(state, spec, generated_at=market.retrieved_at_utc)
    def route(request):
        if request.method == "PATCH":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{"feature_vector": payload}])
    with httpx.Client(transport=httpx.MockTransport(route)) as client:
        with pytest.raises(RuntimeError, match="compare-and-swap"):
            write_runner_status(spec, restored_state_hash=state_sha256(state), status="blocked",
                                checked_at="2026-11-01T00:00:00+00:00", error="missing", client=client)
