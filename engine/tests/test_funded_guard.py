"""Focused behavioural tests for the fail-closed funded-account guard."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone

import pytest

import apex_quant.risk.funded_guard as funded_guard_module
from apex_quant.risk.funded_guard import (
    AccountSnapshot,
    EVALUATION_GUARD_POLICY,
    FUNDED_PAYOUT_GUARD_POLICY,
    FundedAccountRules,
    FundedGuard,
    FundedGuardState,
    GuardAction,
    GuardPolicy,
    LEGACY_GUARD_POLICY,
    MaxLossMode,
    PlannedRisk,
    PlannedRiskBasis,
    evaluate_guard,
    firm_session_key,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)  # 14:00 Europe/Prague
ZERO_RISK = PlannedRisk(0.0, 0.0)
_UNSET = object()


def rules(**overrides) -> FundedAccountRules:
    defaults = {
        "account_id": "funded-100k-01",
        "initial_balance": 100_000.0,
        "official_daily_loss_fraction": 0.03,
        "official_max_loss_fraction": 0.10,
    }
    defaults.update(overrides)
    return FundedAccountRules(**defaults)


def snapshot(
    equity: float = 100_000.0,
    *,
    balance=_UNSET,
    at: datetime = NOW,
    account_id: str = "funded-100k-01",
    all_costs: bool = True,
    **overrides,
) -> AccountSnapshot:
    defaults = {
        "account_id": account_id,
        "as_of": at,
        "balance": equity if balance is _UNSET else balance,
        "equity": equity,
        "equity_includes_all_costs": all_costs,
    }
    defaults.update(overrides)
    return AccountSnapshot(**defaults)


def initialise(tmp_path, *, account_rules=None, initial_snapshot=None) -> FundedGuard:
    account_rules = account_rules or rules()
    initial_snapshot = initial_snapshot or snapshot(
        account_id=account_rules.account_id,
        balance=account_rules.initial_balance,
        equity=account_rules.initial_balance,
        firm_session_start_balance=account_rules.initial_balance,
    )
    guard = FundedGuard(account_rules, tmp_path)
    decision = guard.initialize(initial_snapshot, now=initial_snapshot.as_of)
    assert decision.action is GuardAction.ALLOW
    return guard


def read_state(guard: FundedGuard) -> FundedGuardState:
    raw = json.loads(guard.state_path.read_text(encoding="utf-8"))
    return FundedGuardState.from_json_dict(raw)


# ---------------------------------------------------------------------------
# Rule and session-clock integrity
# ---------------------------------------------------------------------------


def test_rules_default_to_exact_prague_timezone_and_reject_bad_rules():
    cfg = rules()
    assert cfg.firm_timezone == "Europe/Prague"
    assert cfg.timezone.key == "Europe/Prague"
    assert cfg.daily_allowance_dollars == pytest.approx(3_000.0)
    assert cfg.max_loss_allowance_dollars == pytest.approx(10_000.0)
    assert cfg.guard_policy == LEGACY_GUARD_POLICY

    with pytest.raises(ValueError, match="unknown IANA"):
        rules(firm_timezone="Prague-ish")
    with pytest.raises(ValueError, match="below"):
        rules(official_daily_loss_fraction=0.10)
    with pytest.raises(ValueError, match="naive"):
        rules(session_reset_local_time=time(0, tzinfo=UTC))
    with pytest.raises(ValueError, match="GuardPolicy"):
        rules(guard_policy="evaluation")


def test_guard_policy_is_validated_and_v1_fingerprint_remains_compatible():
    assert rules().config_fingerprint == (
        "06e96e768a908f42fe21e602f2ceb7b66f9f13cd0aa26bc954f1de8caf04694b"
    )
    with pytest.raises(ValueError, match="daily_block_fraction"):
        GuardPolicy(0.0, 0.5, 0.5, 0.009, 0.0045)
    with pytest.raises(ValueError, match="below daily_flatten_fraction"):
        GuardPolicy(0.5, 0.5, 0.5, 0.009, 0.0045)
    with pytest.raises(ValueError, match="cannot exceed"):
        GuardPolicy(0.3, 0.5, 0.5, 0.004, 0.005)
    with pytest.raises(ValueError, match="planned_risk_basis"):
        GuardPolicy(0.3, 0.5, 0.5, 0.009, 0.0045, "equity")

    default = rules().config_fingerprint
    evaluation = rules(guard_policy=EVALUATION_GUARD_POLICY).config_fingerprint
    payout = rules(guard_policy=FUNDED_PAYOUT_GUARD_POLICY).config_fingerprint
    same_numbers_other_basis = rules(
        guard_policy=replace(
            EVALUATION_GUARD_POLICY,
            planned_risk_basis=PlannedRiskBasis.DAILY_ALLOWANCE,
        )
    ).config_fingerprint
    assert len({default, evaluation, payout, same_numbers_other_basis}) == 4


def test_prague_midnight_is_the_session_boundary_not_utc_midnight():
    cfg = rules()
    assert firm_session_key(datetime(2026, 1, 1, 22, 59, tzinfo=UTC), cfg) == "2026-01-01"
    # Prague is UTC+1 in January, so this instant is local midnight on Jan 2.
    assert firm_session_key(datetime(2026, 1, 1, 23, 0, tzinfo=UTC), cfg) == "2026-01-02"


def test_custom_wall_clock_reset_assigns_pre_reset_time_to_prior_session():
    cfg = rules(session_reset_local_time=time(17, 0))
    # Prague is UTC+2 in September: 14:59 UTC is 16:59 local.
    assert firm_session_key(datetime(2026, 9, 3, 14, 59, tzinfo=UTC), cfg) == "2026-09-02"
    assert firm_session_key(datetime(2026, 9, 3, 15, 0, tzinfo=UTC), cfg) == "2026-09-03"


def test_dst_spring_and_repeated_autumn_hour_do_not_create_phantom_sessions():
    cfg = rules(session_reset_local_time=time(2, 30))

    # Spring-forward day: 02:30 local does not exist. The first actual local
    # instant after it is 03:00 CEST, and that begins the new session.
    assert firm_session_key(datetime(2026, 3, 29, 0, 59, tzinfo=UTC), cfg) == "2026-03-28"
    assert firm_session_key(datetime(2026, 3, 29, 1, 0, tzinfo=UTC), cfg) == "2026-03-29"

    # Autumn: 02:45 occurs twice, but both instants retain the same session key.
    first_0245 = datetime(2026, 10, 25, 0, 45, tzinfo=UTC)
    second_0215 = datetime(2026, 10, 25, 1, 15, tzinfo=UTC)
    second_0245 = datetime(2026, 10, 25, 1, 45, tzinfo=UTC)
    assert firm_session_key(first_0245, cfg) == "2026-10-25"
    assert firm_session_key(second_0215, cfg) == "2026-10-25"
    assert firm_session_key(second_0245, cfg) == "2026-10-25"


def test_session_key_rejects_naive_instants():
    with pytest.raises(ValueError, match="timezone-aware"):
        firm_session_key(datetime(2026, 9, 3, 12, 0), rules())


# ---------------------------------------------------------------------------
# Deterministic policy thresholds and budget telemetry
# ---------------------------------------------------------------------------


def test_pure_evaluation_is_deterministic_and_has_no_io(tmp_path):
    guard = initialise(tmp_path)
    state = read_state(guard)
    snap = snapshot(99_500.0, at=NOW + timedelta(seconds=1))
    first = evaluate_guard(rules(), state, snap, ZERO_RISK, now=snap.as_of)
    second = evaluate_guard(rules(), state, snap, ZERO_RISK, now=snap.as_of)
    assert first == second
    assert first.decision.action is GuardAction.ALLOW
    assert read_state(guard) == state, "pure evaluation must not touch persisted state"


def test_exact_40_percent_daily_allowance_blocks_and_latches_for_session(tmp_path):
    guard = initialise(tmp_path)
    at = NOW + timedelta(seconds=1)
    decision = guard.assess(snapshot(98_800.0, at=at), ZERO_RISK, now=at)
    assert decision.action is GuardAction.BLOCK_NEW
    assert decision.reason_codes == ("internal_daily_block",)
    assert decision.daily_loss_used_dollars == pytest.approx(1_200.0)

    # Even a full equity recovery cannot automatically re-enter this session.
    recovered_at = at + timedelta(seconds=1)
    recovered = guard.assess(snapshot(100_000.0, at=recovered_at), ZERO_RISK, now=recovered_at)
    assert recovered.action is GuardAction.BLOCK_NEW
    assert recovered.reason_codes == ("daily_block_latched",)


def test_just_below_40_percent_daily_allowance_is_allowed(tmp_path):
    guard = initialise(tmp_path)
    at = NOW + timedelta(seconds=1)
    decision = guard.assess(snapshot(98_800.01, at=at), ZERO_RISK, now=at)
    assert decision.action is GuardAction.ALLOW


def test_exact_60_percent_daily_allowance_flattens_and_survives_restart(tmp_path):
    first_process = initialise(tmp_path)
    at = NOW + timedelta(seconds=1)
    decision = first_process.assess(snapshot(98_200.0, at=at), ZERO_RISK, now=at)
    assert decision.action is GuardAction.CANCEL_AND_FLATTEN
    assert decision.requires_cancel and decision.requires_flatten

    second_process = FundedGuard(rules(), tmp_path)
    recovered_at = at + timedelta(seconds=1)
    still_halted = second_process.assess(
        snapshot(100_000.0, at=recovered_at), ZERO_RISK, now=recovered_at
    )
    assert still_halted.action is GuardAction.CANCEL_AND_FLATTEN
    assert still_halted.reason_codes == ("daily_flatten_latched",)


def test_daily_latches_clear_only_at_the_next_prague_session(tmp_path):
    guard = initialise(tmp_path)
    stopped_at = NOW + timedelta(seconds=1)
    assert guard.assess(snapshot(98_200.0, at=stopped_at), ZERO_RISK, now=stopped_at).action \
        is GuardAction.CANCEL_AND_FLATTEN

    # 22:00 UTC is local midnight in Prague in September.
    next_session = datetime(2026, 9, 3, 22, 0, tzinfo=UTC)
    decision = guard.assess(
        snapshot(
            98_200.0,
            balance=98_200.0,
            at=next_session,
            firm_session_start_balance=98_200.0,
        ),
        ZERO_RISK,
        now=next_session,
    )
    assert decision.action is GuardAction.ALLOW
    assert decision.daily_loss_used_dollars == pytest.approx(0.0)
    assert read_state(guard).session_anchor_balance == pytest.approx(98_200.0)


def test_daily_floor_uses_session_start_closed_balance_not_live_balance_or_equity(tmp_path):
    initial = snapshot(
        106_000.0,
        balance=105_000.0,
        firm_session_start_balance=100_000.0,
    )
    guard = initialise(tmp_path, initial_snapshot=initial)
    at = NOW + timedelta(seconds=1)
    decision = guard.assess(
        snapshot(98_800.0, balance=104_000.0, at=at), ZERO_RISK, now=at
    )

    assert decision.official_daily_floor_dollars == pytest.approx(97_000.0)
    assert decision.daily_loss_used_dollars == pytest.approx(1_200.0)
    assert decision.action is GuardAction.BLOCK_NEW


def test_official_daily_floor_is_hard_and_inclusive(tmp_path):
    guard = initialise(tmp_path)
    at = NOW + timedelta(seconds=1)
    decision = guard.assess(snapshot(97_000.0, at=at), ZERO_RISK, now=at)
    assert decision.action is GuardAction.CYCLE_HALT
    assert decision.reason_codes == ("official_daily_floor_reached",)
    assert decision.official_daily_floor_dollars == pytest.approx(97_000.0)


def test_internal_total_loss_cycle_halt_is_inclusive_and_persists(tmp_path):
    guard = initialise(tmp_path)
    next_session = datetime(2026, 9, 3, 22, 1, tzinfo=UTC)
    decision = guard.assess(
        snapshot(
            94_000.0,
            balance=94_000.0,
            at=next_session,
            firm_session_start_balance=94_000.0,
        ),
        ZERO_RISK,
        now=next_session,
    )
    assert decision.action is GuardAction.CYCLE_HALT
    assert decision.reason_codes == ("internal_total_loss_cycle_halt",)
    assert decision.total_loss_used_dollars == pytest.approx(6_000.0)

    much_later = next_session + timedelta(days=2)
    restarted = FundedGuard(rules(), tmp_path)
    latched = restarted.assess(
        snapshot(
            100_000.0,
            balance=100_000.0,
            at=much_later,
            firm_session_start_balance=100_000.0,
        ),
        ZERO_RISK,
        now=much_later,
    )
    assert latched.action is GuardAction.CYCLE_HALT
    assert latched.reason_codes == ("cycle_halt_latched",)


def test_stricter_firm_supplied_floor_is_enforced(tmp_path):
    guard = initialise(tmp_path)
    at = NOW + timedelta(seconds=1)
    snap = snapshot(99_000.0, at=at, firm_max_loss_floor=99_000.0)
    decision = guard.assess(snap, ZERO_RISK, now=at)
    assert decision.action is GuardAction.CYCLE_HALT
    assert decision.reason_codes == ("official_max_loss_floor_reached",)
    assert decision.official_max_floor_dollars == pytest.approx(99_000.0)


def test_trailing_floor_advances_only_from_next_session_authoritative_balance(tmp_path):
    trailing_rules = rules(account_id="trailing", max_loss_mode=MaxLossMode.TRAILING)
    static_rules = rules(account_id="static", max_loss_mode=MaxLossMode.STATIC)
    trailing = initialise(
        tmp_path / "trailing",
        account_rules=trailing_rules,
        initial_snapshot=snapshot(
            account_id="trailing", firm_session_start_balance=100_000.0,
        ),
    )
    static = initialise(
        tmp_path / "static",
        account_rules=static_rules,
        initial_snapshot=snapshot(
            account_id="static", firm_session_start_balance=100_000.0,
        ),
    )

    unrealized_at = NOW + timedelta(seconds=1)
    trailing_unrealized = trailing.assess(
        snapshot(
            110_000.0, balance=100_000.0,
            at=unrealized_at, account_id="trailing",
        ),
        ZERO_RISK,
        now=unrealized_at,
    )
    static_unrealized = static.assess(
        snapshot(
            110_000.0, balance=100_000.0,
            at=unrealized_at, account_id="static",
        ),
        ZERO_RISK,
        now=unrealized_at,
    )
    assert trailing_unrealized.official_max_floor_dollars == pytest.approx(90_000.0)
    assert static_unrealized.official_max_floor_dollars == pytest.approx(90_000.0)

    # Realising the profit intraday still cannot advance an EOD trailing floor.
    realized_at = unrealized_at + timedelta(seconds=1)
    trailing_realized = trailing.assess(
        snapshot(
            110_000.0, balance=110_000.0,
            at=realized_at, account_id="trailing",
        ),
        ZERO_RISK,
        now=realized_at,
    )
    assert trailing_realized.official_max_floor_dollars == pytest.approx(90_000.0)
    assert read_state(trailing).peak_eod_balance == pytest.approx(100_000.0)
    assert read_state(trailing).last_balance == pytest.approx(110_000.0)

    # A restart inside the same session cannot reinterpret the latest realised
    # balance as a completed EOD value.
    trailing = FundedGuard(trailing_rules, tmp_path / "trailing")
    restarted_at = realized_at + timedelta(seconds=1)
    restarted_gain = trailing.assess(
        snapshot(
            112_000.0, balance=112_000.0,
            at=restarted_at, account_id="trailing",
        ),
        ZERO_RISK,
        now=restarted_at,
    )
    assert restarted_gain.official_max_floor_dollars == pytest.approx(90_000.0)
    assert read_state(trailing).peak_eod_balance == pytest.approx(100_000.0)

    # At the next reset, the authoritative opening balance represents the prior
    # completed EOD balance and may advance the trailing reference.
    next_session = datetime(2026, 9, 3, 22, 0, tzinfo=UTC)
    trailing_eod = trailing.assess(
        snapshot(
            110_000.0, balance=110_000.0, at=next_session,
            account_id="trailing", firm_session_start_balance=110_000.0,
        ),
        ZERO_RISK,
        now=next_session,
    )
    static_eod = static.assess(
        snapshot(
            110_000.0, balance=110_000.0, at=next_session,
            account_id="static", firm_session_start_balance=110_000.0,
        ),
        ZERO_RISK,
        now=next_session,
    )
    assert trailing_eod.official_max_floor_dollars == pytest.approx(100_000.0)
    assert static_eod.official_max_floor_dollars == pytest.approx(90_000.0)
    assert read_state(trailing).peak_eod_balance == pytest.approx(110_000.0)


def test_planned_risk_boundaries_are_inclusive_on_the_safe_side(tmp_path):
    guard = initialise(tmp_path)
    at = NOW + timedelta(seconds=1)
    at_limit = PlannedRisk(
        portfolio_risk_dollars=1_050.0,  # 35% of a $3,000 daily allowance
        stressed_symbol_loss_dollars=450.0,  # 15%
    )
    allowed = guard.assess(snapshot(at=at), at_limit, now=at)
    assert allowed.action is GuardAction.ALLOW
    assert allowed.max_planned_portfolio_risk_dollars == pytest.approx(1_050.0)
    assert allowed.max_stressed_symbol_loss_dollars == pytest.approx(450.0)

    at2 = at + timedelta(seconds=1)
    too_much_portfolio = guard.assess(
        snapshot(at=at2), replace(at_limit, portfolio_risk_dollars=1_050.01), now=at2
    )
    assert too_much_portfolio.action is GuardAction.BLOCK_NEW
    assert too_much_portfolio.reason_codes == ("planned_portfolio_risk_exceeds_limit",)

    at3 = at2 + timedelta(seconds=1)
    too_much_symbol = guard.assess(
        snapshot(at=at3),
        PlannedRisk(portfolio_risk_dollars=1_000.0, stressed_symbol_loss_dollars=450.01),
        now=at3,
    )
    assert too_much_symbol.action is GuardAction.BLOCK_NEW
    assert too_much_symbol.reason_codes == ("stressed_symbol_loss_exceeds_limit",)


@pytest.mark.parametrize(
    (
        "policy",
        "block_loss",
        "flatten_loss",
        "portfolio_cap",
        "symbol_cap",
    ),
    [
        (EVALUATION_GUARD_POLICY, 900.0, 1_500.0, 900.0, 450.0),
        (FUNDED_PAYOUT_GUARD_POLICY, 600.0, 1_200.0, 600.0, 350.0),
    ],
    ids=("evaluation", "funded-payout"),
)
def test_v2_policy_daily_and_capital_risk_boundaries(
    tmp_path,
    policy,
    block_loss,
    flatten_loss,
    portfolio_cap,
    symbol_cap,
):
    guard = initialise(tmp_path, account_rules=rules(guard_policy=policy))
    at = NOW + timedelta(seconds=1)

    at_caps = guard.assess(
        snapshot(at=at),
        PlannedRisk(portfolio_cap, symbol_cap),
        now=at,
    )
    assert at_caps.action is GuardAction.ALLOW
    assert at_caps.max_planned_portfolio_risk_dollars == pytest.approx(portfolio_cap)
    assert at_caps.max_stressed_symbol_loss_dollars == pytest.approx(symbol_cap)

    at += timedelta(seconds=1)
    lower_capital = guard.assess(
        snapshot(99_500.0, at=at), ZERO_RISK, now=at
    )
    assert lower_capital.max_planned_portfolio_risk_dollars == pytest.approx(
        99_500.0 * policy.max_planned_portfolio_risk_fraction
    )
    assert lower_capital.max_stressed_symbol_loss_dollars == pytest.approx(
        99_500.0 * policy.max_stressed_symbol_loss_fraction
    )

    at += timedelta(seconds=1)
    portfolio_exceeded = guard.assess(
        snapshot(at=at),
        PlannedRisk(portfolio_cap + 0.01, symbol_cap),
        now=at,
    )
    assert portfolio_exceeded.action is GuardAction.BLOCK_NEW
    assert portfolio_exceeded.reason_codes == (
        "planned_portfolio_risk_exceeds_limit",
    )

    at += timedelta(seconds=1)
    symbol_exceeded = guard.assess(
        snapshot(at=at),
        PlannedRisk(portfolio_cap, symbol_cap + 0.01),
        now=at,
    )
    assert symbol_exceeded.action is GuardAction.BLOCK_NEW
    assert symbol_exceeded.reason_codes == (
        "stressed_symbol_loss_exceeds_limit",
    )

    at += timedelta(seconds=1)
    below_block = guard.assess(
        snapshot(100_000.0 - block_loss + 0.01, at=at), ZERO_RISK, now=at
    )
    assert below_block.action is GuardAction.ALLOW

    at += timedelta(seconds=1)
    exact_block = guard.assess(
        snapshot(100_000.0 - block_loss, at=at), ZERO_RISK, now=at
    )
    assert exact_block.action is GuardAction.BLOCK_NEW
    assert exact_block.reason_codes == ("internal_daily_block",)

    at += timedelta(seconds=1)
    exact_flatten = guard.assess(
        snapshot(100_000.0 - flatten_loss, at=at), ZERO_RISK, now=at
    )
    assert exact_flatten.action is GuardAction.CANCEL_AND_FLATTEN
    assert exact_flatten.reason_codes == ("internal_daily_flatten",)


@pytest.mark.parametrize(
    ("policy", "cycle_balance"),
    [
        (EVALUATION_GUARD_POLICY, 95_000.0),
        (FUNDED_PAYOUT_GUARD_POLICY, 96_000.0),
    ],
    ids=("evaluation", "funded-payout"),
)
def test_v2_policy_cycle_boundary_latches_across_restart(
    tmp_path, policy, cycle_balance
):
    account_rules = rules(guard_policy=policy)
    guard = initialise(tmp_path, account_rules=account_rules)
    next_session = datetime(2026, 9, 3, 22, 0, tzinfo=UTC)
    decision = guard.assess(
        snapshot(
            cycle_balance,
            balance=cycle_balance,
            at=next_session,
            firm_session_start_balance=cycle_balance,
        ),
        ZERO_RISK,
        now=next_session,
    )
    assert decision.action is GuardAction.CYCLE_HALT
    assert decision.reason_codes == ("internal_total_loss_cycle_halt",)

    restarted = FundedGuard(account_rules, tmp_path)
    recovered_at = next_session + timedelta(seconds=1)
    recovered = restarted.assess(
        snapshot(100_000.0, balance=100_000.0, at=recovered_at),
        ZERO_RISK,
        now=recovered_at,
    )
    assert recovered.action is GuardAction.CYCLE_HALT
    assert recovered.reason_codes == ("cycle_halt_latched",)


@pytest.mark.parametrize(
    ("policy", "boundary_equity"),
    [
        (EVALUATION_GUARD_POLICY, 105_000.0),
        (FUNDED_PAYOUT_GUARD_POLICY, 106_000.0),
    ],
    ids=("evaluation-5pct", "funded-payout-4pct"),
)
def test_v2_static_mode_cycle_uses_completed_eod_peak_drawdown_boundary(
    tmp_path, policy, boundary_equity
):
    account_rules = rules(
        max_loss_mode=MaxLossMode.STATIC,
        guard_policy=policy,
    )
    guard = initialise(tmp_path, account_rules=account_rules)

    peak_session = datetime(2026, 9, 3, 22, 0, tzinfo=UTC)
    peak = guard.assess(
        snapshot(
            110_000.0,
            balance=110_000.0,
            at=peak_session,
            firm_session_start_balance=110_000.0,
        ),
        ZERO_RISK,
        now=peak_session,
    )
    assert peak.action is GuardAction.ALLOW
    assert read_state(guard).peak_eod_balance == pytest.approx(110_000.0)

    # A later authoritative opening balance avoids conflating this test with
    # the firm-day loss rule.  One cent inside the peak-drawdown trigger is safe.
    drawdown_session = peak_session + timedelta(days=1)
    inside = guard.assess(
        snapshot(
            boundary_equity + 0.01,
            balance=boundary_equity + 0.01,
            at=drawdown_session,
            firm_session_start_balance=boundary_equity + 0.01,
        ),
        ZERO_RISK,
        now=drawdown_session,
    )
    assert inside.action is GuardAction.ALLOW

    boundary_at = drawdown_session + timedelta(seconds=1)
    boundary = guard.assess(
        snapshot(boundary_equity, balance=boundary_equity, at=boundary_at),
        ZERO_RISK,
        now=boundary_at,
    )
    assert boundary.action is GuardAction.CYCLE_HALT
    assert boundary.reason_codes == ("internal_total_loss_cycle_halt",)
    assert boundary.total_loss_used_dollars == pytest.approx(
        110_000.0 - boundary_equity
    )
    assert boundary.official_max_floor_dollars == pytest.approx(90_000.0)


def test_legacy_static_cycle_reference_does_not_change_after_completed_eod_profit(
    tmp_path,
):
    account_rules = rules(max_loss_mode=MaxLossMode.STATIC)
    guard = initialise(tmp_path, account_rules=account_rules)

    peak_session = datetime(2026, 9, 3, 22, 0, tzinfo=UTC)
    assert guard.assess(
        snapshot(
            110_000.0,
            balance=110_000.0,
            at=peak_session,
            firm_session_start_balance=110_000.0,
        ),
        ZERO_RISK,
        now=peak_session,
    ).action is GuardAction.ALLOW

    later_session = peak_session + timedelta(days=1)
    legacy = guard.assess(
        snapshot(
            104_000.0,
            balance=104_000.0,
            at=later_session,
            firm_session_start_balance=104_000.0,
        ),
        ZERO_RISK,
        now=later_session,
    )
    assert legacy.action is GuardAction.ALLOW
    assert legacy.total_loss_used_dollars == pytest.approx(0.0)
    assert legacy.official_max_floor_dollars == pytest.approx(90_000.0)
    assert read_state(guard).peak_eod_balance == pytest.approx(110_000.0)


def test_restart_rejects_policy_change_but_accepts_identical_policy(tmp_path):
    evaluation_rules = rules(guard_policy=EVALUATION_GUARD_POLICY)
    initialise(tmp_path, account_rules=evaluation_rules)

    same_policy = FundedGuard(evaluation_rules, tmp_path)
    at = NOW + timedelta(seconds=1)
    assert same_policy.assess(snapshot(at=at), ZERO_RISK, now=at).action \
        is GuardAction.ALLOW

    changed_policy = FundedGuard(
        rules(guard_policy=FUNDED_PAYOUT_GUARD_POLICY), tmp_path
    )
    changed_at = at + timedelta(seconds=1)
    rejected = changed_policy.assess(
        snapshot(at=changed_at), ZERO_RISK, now=changed_at
    )
    assert rejected.action is GuardAction.FAIL_CLOSED
    assert rejected.reason_codes == ("state_rules_fingerprint_mismatch",)


def test_size_and_risk_bases_use_current_initial_and_remaining_buffer(tmp_path):
    guard = initialise(tmp_path)
    at = NOW + timedelta(seconds=1)
    decision = guard.assess(snapshot(99_500.0, at=at), ZERO_RISK, now=at)
    assert decision.capital_base_dollars == pytest.approx(99_500.0)
    assert decision.remaining_official_loss_buffer_dollars == pytest.approx(2_500.0)
    assert decision.sizing_risk_budget_base_dollars == pytest.approx(2_500.0)

    # Profits do not increase the capital sizing base above the initial account.
    at2 = at + timedelta(seconds=1)
    profitable = guard.assess(snapshot(102_000.0, at=at2), ZERO_RISK, now=at2)
    assert profitable.capital_base_dollars == pytest.approx(100_000.0)

    # Whichever official floor is closer constrains the dollar-risk budget.  A
    # stricter firm max floor can therefore dominate the ordinary daily buffer.
    at3 = at2 + timedelta(seconds=1)
    max_floor_limited = guard.assess(
        snapshot(
            99_500.0,
            balance=99_500.0,
            at=at3,
            firm_max_loss_floor=99_400.0,
        ),
        ZERO_RISK,
        now=at3,
    )
    assert max_floor_limited.remaining_official_loss_buffer_dollars == pytest.approx(
        100.0
    )
    assert max_floor_limited.sizing_risk_budget_base_dollars == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Snapshot/state fail-closed behavior and durable persistence
# ---------------------------------------------------------------------------


def test_initialization_requires_authoritative_session_start_balance(tmp_path):
    guard = FundedGuard(rules(), tmp_path)
    decision = guard.initialize(snapshot(), now=NOW)

    assert decision.action is GuardAction.FAIL_CLOSED
    assert decision.reason_codes == (
        "firm_session_start_balance_required_for_initialization",
    )
    assert not guard.state_path.exists()


def test_initialize_never_resumes_existing_state_as_zero_planned_risk(tmp_path):
    guard = initialise(tmp_path)
    at = NOW + timedelta(seconds=1)

    decision = guard.initialize(snapshot(at=at), now=at)

    assert decision.action is GuardAction.FAIL_CLOSED
    assert decision.reason_codes == (
        "state_already_exists_use_assess_with_full_book_risk",
    )


def test_new_session_without_authoritative_anchor_fails_closed_without_reanchoring(
    tmp_path,
):
    guard = initialise(tmp_path)
    persisted = guard.state_path.read_bytes()
    next_session = datetime(2026, 9, 3, 22, 0, tzinfo=UTC)

    missing = guard.assess(
        snapshot(105_000.0, balance=105_000.0, at=next_session),
        ZERO_RISK,
        now=next_session,
    )
    assert missing.action is GuardAction.FAIL_CLOSED
    assert missing.reason_codes == (
        "firm_session_start_balance_required_for_new_session",
    )
    assert guard.state_path.read_bytes() == persisted

    # A later authoritative retry may safely perform the transition.
    retry_at = next_session + timedelta(seconds=1)
    accepted = guard.assess(
        snapshot(
            105_000.0,
            balance=105_000.0,
            at=retry_at,
            firm_session_start_balance=105_000.0,
        ),
        ZERO_RISK,
        now=retry_at,
    )
    assert accepted.action is GuardAction.ALLOW
    state = read_state(guard)
    assert state.session_anchor_balance == pytest.approx(105_000.0)
    assert state.last_balance == pytest.approx(105_000.0)


@pytest.mark.parametrize(
    "bad_snapshot, now, reason",
    [
        (snapshot(all_costs=False), NOW, "snapshot_equity_does_not_include_all_costs"),
        (snapshot(account_id="other"), NOW, "snapshot_account_mismatch"),
        (
            snapshot(balance=float("nan"), equity=100_000.0),
            NOW,
            "snapshot_balance_invalid",
        ),
        (
            snapshot(balance=100_000.0, equity=float("nan")),
            NOW,
            "snapshot_equity_invalid",
        ),
        (
            snapshot(at=NOW - timedelta(seconds=61)),
            NOW,
            "snapshot_stale",
        ),
        (
            snapshot(at=NOW + timedelta(seconds=6)),
            NOW,
            "snapshot_timestamp_too_far_in_future",
        ),
        (
            snapshot(at=datetime(2026, 9, 3, 12, 0)),
            NOW,
            "snapshot_timestamp_not_timezone_aware",
        ),
    ],
)
def test_invalid_or_stale_snapshots_fail_closed(tmp_path, bad_snapshot, now, reason):
    guard = initialise(tmp_path)
    decision = guard.assess(bad_snapshot, ZERO_RISK, now=now)
    assert decision.action is GuardAction.FAIL_CLOSED
    assert decision.reason_codes == (reason,)
    assert decision.requires_cancel and decision.requires_flatten
    assert not decision.permits_new_risk


def test_wrong_snapshot_object_fails_closed_in_pure_evaluator(tmp_path):
    guard = initialise(tmp_path)
    state = read_state(guard)
    evaluation = evaluate_guard(rules(), state, None, ZERO_RISK, now=NOW)  # type: ignore[arg-type]
    assert evaluation.next_state is None
    assert evaluation.decision.action is GuardAction.FAIL_CLOSED
    assert evaluation.decision.reason_codes == ("snapshot_type_invalid",)


def test_out_of_order_and_conflicting_same_timestamp_snapshots_fail_closed(tmp_path):
    guard = initialise(tmp_path)
    newer = NOW + timedelta(seconds=10)
    assert guard.assess(snapshot(99_900.0, at=newer), ZERO_RISK, now=newer).action \
        is GuardAction.ALLOW

    older = newer - timedelta(seconds=1)
    out_of_order = guard.assess(snapshot(99_900.0, at=older), ZERO_RISK, now=newer)
    assert out_of_order.action is GuardAction.FAIL_CLOSED
    assert out_of_order.reason_codes == ("snapshot_out_of_order",)

    equity_conflict = guard.assess(
        snapshot(99_800.0, balance=99_900.0, at=newer), ZERO_RISK, now=newer
    )
    assert equity_conflict.action is GuardAction.FAIL_CLOSED
    assert equity_conflict.reason_codes == ("snapshot_conflicts_at_same_timestamp",)

    balance_conflict = guard.assess(
        snapshot(99_900.0, balance=99_800.0, at=newer), ZERO_RISK, now=newer
    )
    assert balance_conflict.action is GuardAction.FAIL_CLOSED
    assert balance_conflict.reason_codes == ("snapshot_conflicts_at_same_timestamp",)


def test_firm_session_anchor_conflict_fails_closed(tmp_path):
    first = snapshot(firm_session_start_balance=100_000.0)
    guard = initialise(tmp_path, initial_snapshot=first)
    at = NOW + timedelta(seconds=1)
    decision = guard.assess(
        snapshot(at=at, firm_session_start_balance=99_999.0), ZERO_RISK, now=at
    )
    assert decision.action is GuardAction.FAIL_CLOSED
    assert decision.reason_codes == (
        "firm_session_start_balance_conflicts_with_persisted_anchor",
    )


def test_state_is_account_scoped_and_restart_reuses_original_anchor(tmp_path):
    one_rules = rules(account_id="account/one")
    two_rules = rules(account_id="account/two")
    one = initialise(
        tmp_path,
        account_rules=one_rules,
        initial_snapshot=snapshot(
            account_id="account/one", firm_session_start_balance=100_000.0,
        ),
    )
    two = initialise(
        tmp_path,
        account_rules=two_rules,
        initial_snapshot=snapshot(
            account_id="account/two", firm_session_start_balance=100_000.0,
        ),
    )
    assert one.state_path != two.state_path
    assert "/" not in one.state_path.name
    assert read_state(one).account_id == "account/one"
    assert read_state(two).account_id == "account/two"

    restarted = FundedGuard(one_rules, tmp_path)
    at = NOW + timedelta(seconds=1)
    decision = restarted.assess(
        snapshot(99_000.0, at=at, account_id="account/one"), ZERO_RISK, now=at
    )
    assert decision.daily_loss_used_dollars == pytest.approx(1_000.0)
    state = read_state(restarted)
    assert state.session_anchor_balance == pytest.approx(100_000.0)
    assert state.last_balance == pytest.approx(99_000.0)


def test_balance_state_schema_is_explicit_and_old_schema_fails_closed(tmp_path):
    guard = initialise(tmp_path)
    raw = json.loads(guard.state_path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert {
        "session_anchor_balance", "last_balance", "peak_eod_balance",
    }.issubset(raw)
    assert "session_anchor_equity" not in raw
    assert "peak_equity" not in raw

    raw["schema_version"] = 1
    guard.state_path.write_text(json.dumps(raw), encoding="utf-8")
    at = NOW + timedelta(seconds=1)
    decision = guard.assess(snapshot(at=at), ZERO_RISK, now=at)
    assert decision.action is GuardAction.FAIL_CLOSED
    assert decision.reason_codes == ("state_schema_version_mismatch",)


def test_missing_state_after_initialization_fails_closed_instead_of_reanchoring(tmp_path):
    guard = initialise(tmp_path)
    guard.state_path.unlink()
    at = NOW + timedelta(seconds=1)
    decision = guard.assess(snapshot(97_000.0, at=at), ZERO_RISK, now=at)
    assert decision.action is GuardAction.FAIL_CLOSED
    assert decision.reason_codes == ("state_missing",)
    assert not guard.state_path.exists()


def test_corrupt_state_fails_closed_and_initialize_does_not_overwrite_it(tmp_path):
    guard = initialise(tmp_path)
    guard.state_path.write_text("{broken json", encoding="utf-8")
    original = guard.state_path.read_bytes()
    at = NOW + timedelta(seconds=1)

    assessed = guard.assess(snapshot(at=at), ZERO_RISK, now=at)
    assert assessed.action is GuardAction.FAIL_CLOSED
    assert assessed.reason_codes[0].startswith("state_read_or_write_failed:JSONDecodeError")

    provision_attempt = guard.initialize(snapshot(at=at), now=at)
    assert provision_attempt.action is GuardAction.FAIL_CLOSED
    assert guard.state_path.read_bytes() == original


def test_semantically_invalid_state_and_changed_rules_fail_closed(tmp_path):
    guard = initialise(tmp_path)
    raw = json.loads(guard.state_path.read_text(encoding="utf-8"))
    raw["peak_eod_balance"] = "not-money"
    guard.state_path.write_text(json.dumps(raw), encoding="utf-8")
    at = NOW + timedelta(seconds=1)
    invalid = guard.assess(snapshot(at=at), ZERO_RISK, now=at)
    assert invalid.action is GuardAction.FAIL_CLOSED

    # Restore a valid state, then prove that a rule change cannot silently reuse it.
    guard.state_path.unlink()
    guard = initialise(tmp_path)
    changed = FundedGuard(rules(official_daily_loss_fraction=0.025), tmp_path)
    changed_decision = changed.assess(snapshot(at=at), ZERO_RISK, now=at)
    assert changed_decision.action is GuardAction.FAIL_CLOSED
    assert changed_decision.reason_codes == ("state_rules_fingerprint_mismatch",)


def test_atomic_replace_failure_fails_closed_and_preserves_previous_json(tmp_path, monkeypatch):
    guard = initialise(tmp_path)
    original = guard.state_path.read_bytes()

    def cannot_replace(source, destination):
        raise PermissionError("read-only funded state volume")

    monkeypatch.setattr(funded_guard_module.os, "replace", cannot_replace)
    at = NOW + timedelta(seconds=1)
    decision = guard.assess(snapshot(99_900.0, at=at), ZERO_RISK, now=at)
    assert decision.action is GuardAction.FAIL_CLOSED
    assert decision.reason_codes == ("state_read_or_write_failed:PermissionError",)
    assert guard.state_path.read_bytes() == original
    assert not [path for path in tmp_path.iterdir() if path.suffix == ".tmp"]


def test_successful_state_writes_are_valid_json_and_owner_only(tmp_path):
    guard = initialise(tmp_path)
    at = NOW + timedelta(seconds=1)
    assert guard.assess(snapshot(99_900.0, at=at), ZERO_RISK, now=at).action \
        is GuardAction.ALLOW
    state = read_state(guard)
    assert state.last_balance == pytest.approx(99_900.0)
    assert state.last_equity == pytest.approx(99_900.0)
    assert state.peak_eod_balance == pytest.approx(100_000.0)
    assert guard.state_path.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# Explicit cycle-halt acknowledgement
# ---------------------------------------------------------------------------


def test_cycle_halt_cannot_be_acknowledged_while_trigger_is_active(tmp_path):
    guard = initialise(tmp_path)
    trigger_at = datetime(2026, 9, 3, 22, 1, tzinfo=UTC)
    trigger = snapshot(
        94_000.0,
        balance=94_000.0,
        at=trigger_at,
        firm_session_start_balance=94_000.0,
    )
    assert guard.assess(trigger, ZERO_RISK, now=trigger_at).action is GuardAction.CYCLE_HALT

    ack_at = trigger_at + timedelta(seconds=1)
    rejected = guard.acknowledge_cycle_halt(
        snapshot(94_000.0, at=ack_at), acknowledged_by="risk-officer", now=ack_at
    )
    assert rejected.action is GuardAction.CYCLE_HALT
    assert rejected.reason_codes == ("cycle_halt_acknowledgement_rejected_active_trigger",)
    assert read_state(guard).cycle_halted is True


def test_recovered_cycle_halt_needs_explicit_ack_and_then_same_session_lock(tmp_path):
    guard = initialise(tmp_path)
    trigger_at = datetime(2026, 9, 3, 22, 1, tzinfo=UTC)
    trigger = snapshot(
        94_000.0,
        balance=94_000.0,
        at=trigger_at,
        firm_session_start_balance=94_000.0,
    )
    assert guard.assess(trigger, ZERO_RISK, now=trigger_at).action is GuardAction.CYCLE_HALT

    recovered_at = trigger_at + timedelta(seconds=1)
    still_latched = guard.assess(snapshot(95_000.0, at=recovered_at), ZERO_RISK, now=recovered_at)
    assert still_latched.action is GuardAction.CYCLE_HALT

    ack_at = recovered_at + timedelta(seconds=1)
    acknowledged = guard.acknowledge_cycle_halt(
        snapshot(95_000.0, at=ack_at), acknowledged_by="risk-officer", now=ack_at
    )
    assert acknowledged.action is GuardAction.CANCEL_AND_FLATTEN
    assert acknowledged.reason_codes == ("cycle_halt_acknowledged_same_session_lock",)
    assert read_state(guard).cycle_halted is False
    assert read_state(guard).acknowledged_by == "risk-officer"

    # A restart still cannot re-enter in the acknowledgement session.
    restarted = FundedGuard(rules(), tmp_path)
    same_session = ack_at + timedelta(seconds=1)
    locked = restarted.assess(snapshot(100_000.0, at=same_session), ZERO_RISK, now=same_session)
    assert locked.action is GuardAction.CANCEL_AND_FLATTEN

    next_session = same_session + timedelta(days=1)
    allowed = restarted.assess(
        snapshot(
            100_000.0,
            balance=100_000.0,
            at=next_session,
            firm_session_start_balance=100_000.0,
        ),
        ZERO_RISK,
        now=next_session,
    )
    assert allowed.action is GuardAction.ALLOW


def test_acknowledgement_requires_operator_identity(tmp_path):
    guard = initialise(tmp_path)
    decision = guard.acknowledge_cycle_halt(
        snapshot(), acknowledged_by="   ", now=NOW
    )
    assert decision.action is GuardAction.FAIL_CLOSED
    assert decision.reason_codes == ("acknowledgement_identity_missing",)
