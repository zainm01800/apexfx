from scripts.run_paper_portfolio_c import (
    CERTIFIED_MRPT,
    RISK_PROMOTION,
    STATE_PARAMS,
    _cfg,
    _migrate_promoted_risk_state,
)


def _legacy_state(**updates):
    state = {
        "book": "book_c_champion_ensemble_63_126_252",
        "params": {"momentum_lookbacks": [63, 126, 252]},
        "open_positions": {},
        "pending": {"MSFT": {"decision_date": "2026-08-10"}},
        "trades": [],
        "equity_curve": [["2026-08-10", 100_000.0]],
    }
    state.update(updates)
    return state


def test_book_c_promoted_risk_is_pinned_in_config_and_state_metadata():
    assert CERTIFIED_MRPT == 0.0085
    assert _cfg().risk.max_risk_per_trade == CERTIFIED_MRPT
    assert STATE_PARAMS["max_risk_per_trade"] == CERTIFIED_MRPT
    assert STATE_PARAMS["risk_promotion"] == RISK_PROMOTION


def test_seed_only_legacy_state_is_rebuilt_to_rescale_pending_entries():
    migrated, note = _migrate_promoted_risk_state(_legacy_state())

    assert migrated is None
    assert "discarded seed-only state" in note
    assert "1 stale pending" in note


def test_legacy_history_is_preserved_but_unfilled_entries_are_discarded():
    legacy = _legacy_state(
        open_positions={"AAPL": {"units": 5}},
        trades=[{"instrument": "TSLA", "pnl": 50}],
    )

    migrated, note = _migrate_promoted_risk_state(legacy)

    assert migrated is not legacy
    assert migrated["open_positions"] == legacy["open_positions"]
    assert migrated["trades"] == legacy["trades"]
    assert migrated["pending"] == {}
    assert migrated["params"] == STATE_PARAMS
    assert "preserved history/open positions" in note


def test_current_risk_state_is_an_idempotent_noop():
    current = _legacy_state(params=dict(STATE_PARAMS), pending={})

    migrated, note = _migrate_promoted_risk_state(current)

    assert migrated is current
    assert note is None
