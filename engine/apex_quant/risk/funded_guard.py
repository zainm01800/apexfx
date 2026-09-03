"""Fail-closed funded-account risk guard.

This module is deliberately independent from the legacy daily-loss stop and from
the live runner.  It provides two layers:

* :func:`evaluate_guard` is a deterministic, side-effect-free state transition.
* :class:`FundedGuard` serialises that transition to account-scoped JSON under an
  advisory lock and uses an atomic ``fsync`` + ``os.replace`` write.

The caller must supply both authoritative closed balance and *live equity after
every cost*, including unrealised P&L, commissions, swaps/financing, and fees.
A stale, incomplete, out-of-order, or account-mismatched snapshot is not usable
evidence of safety and therefore returns :class:`GuardAction.FAIL_CLOSED`.

The omitted/default policy preserves the original V1 thresholds exactly:

* 40% of the official daily allowance consumed -> block new risk for the session;
* 60% of the official daily allowance consumed -> cancel orders and flatten;
* 60% of the official total-loss allowance consumed -> cycle halt until an
  explicit operator acknowledgement (and recovery above the trigger);
* aggregate planned stop-risk <= 35% of the daily allowance; and
* worst stressed single-symbol loss <= 15% of the daily allowance.

Reaching a threshold is a breach (comparisons are inclusive).  These controls do
not make a funded account impossible to breach: gaps, venue failures, stale market
data, and execution slippage can jump through a floor.  ``FAIL_CLOSED`` therefore
has the same cancel/flatten operational severity as a hard halt.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apex_quant.data._filelock import file_lock


STATE_SCHEMA_VERSION = 2

# Fixed safety policy.  Changing one is a policy migration and must also bump the
# state schema/config fingerprint rather than silently changing a running account.
DAILY_BLOCK_FRACTION = 0.40
DAILY_FLATTEN_FRACTION = 0.60
TOTAL_CYCLE_HALT_FRACTION = 0.60
MAX_PLANNED_PORTFOLIO_RISK_FRACTION = 0.35
MAX_STRESSED_SYMBOL_LOSS_FRACTION = 0.15

_MONEY_ABS_TOLERANCE = 1e-7
_FLOAT_REL_TOLERANCE = 1e-12


class GuardAction(str, Enum):
    """Operational command emitted by the guard, in increasing severity."""

    ALLOW = "ALLOW"
    BLOCK_NEW = "BLOCK_NEW"
    CANCEL_AND_FLATTEN = "CANCEL_AND_FLATTEN"
    CYCLE_HALT = "CYCLE_HALT"
    FAIL_CLOSED = "FAIL_CLOSED"


class MaxLossMode(str, Enum):
    """How the official total-loss floor is calculated."""

    STATIC = "static"
    TRAILING = "trailing"


class PlannedRiskBasis(str, Enum):
    """Reference amount used for aggregate and stressed planned-risk caps."""

    DAILY_ALLOWANCE = "daily_allowance"
    CAPITAL_BASE = "capital_base"


@dataclass(frozen=True)
class GuardPolicy:
    """Validated immutable internal limits layered inside the official floors.

    Daily and total-loss trigger fields are fractions of the corresponding
    official allowance.  Planned-risk fields are fractions of
    :attr:`planned_risk_basis`.
    """

    daily_block_fraction: float
    daily_flatten_fraction: float
    total_cycle_halt_fraction: float
    max_planned_portfolio_risk_fraction: float
    max_stressed_symbol_loss_fraction: float
    planned_risk_basis: PlannedRiskBasis = PlannedRiskBasis.DAILY_ALLOWANCE

    def __post_init__(self) -> None:
        for field_name in (
            "daily_block_fraction",
            "daily_flatten_fraction",
            "total_cycle_halt_fraction",
            "max_planned_portfolio_risk_fraction",
            "max_stressed_symbol_loss_fraction",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not 0.0 < float(value) < 1.0
            ):
                raise ValueError(f"{field_name} must be between 0 and 1")
            object.__setattr__(self, field_name, float(value))
        if self.daily_block_fraction >= self.daily_flatten_fraction:
            raise ValueError("daily_block_fraction must be below daily_flatten_fraction")
        if (
            self.max_stressed_symbol_loss_fraction
            > self.max_planned_portfolio_risk_fraction
        ):
            raise ValueError(
                "max_stressed_symbol_loss_fraction cannot exceed "
                "max_planned_portfolio_risk_fraction"
            )
        if not isinstance(self.planned_risk_basis, PlannedRiskBasis):
            try:
                object.__setattr__(
                    self,
                    "planned_risk_basis",
                    PlannedRiskBasis(self.planned_risk_basis),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "planned_risk_basis must be 'daily_allowance' or 'capital_base'"
                ) from exc

    def fingerprint_payload(self) -> dict[str, float | str]:
        """Return legacy-compatible policy fields for rule fingerprinting."""

        payload: dict[str, float | str] = {
            "daily_block_fraction": self.daily_block_fraction,
            "daily_flatten_fraction": self.daily_flatten_fraction,
            "total_cycle_halt_fraction": self.total_cycle_halt_fraction,
            "max_planned_portfolio_risk_fraction": (
                self.max_planned_portfolio_risk_fraction
            ),
            "max_stressed_symbol_loss_fraction": (
                self.max_stressed_symbol_loss_fraction
            ),
        }
        # Absence historically meant daily allowance.  Keeping it absent for
        # V1 preserves existing persisted-state fingerprints byte-for-byte.
        if self.planned_risk_basis is not PlannedRiskBasis.DAILY_ALLOWANCE:
            payload["planned_risk_basis"] = self.planned_risk_basis.value
        return payload


LEGACY_GUARD_POLICY = GuardPolicy(
    daily_block_fraction=DAILY_BLOCK_FRACTION,
    daily_flatten_fraction=DAILY_FLATTEN_FRACTION,
    total_cycle_halt_fraction=TOTAL_CYCLE_HALT_FRACTION,
    max_planned_portfolio_risk_fraction=MAX_PLANNED_PORTFOLIO_RISK_FRACTION,
    max_stressed_symbol_loss_fraction=MAX_STRESSED_SYMBOL_LOSS_FRACTION,
)

EVALUATION_GUARD_POLICY = GuardPolicy(
    daily_block_fraction=0.30,
    daily_flatten_fraction=0.50,
    total_cycle_halt_fraction=0.50,
    max_planned_portfolio_risk_fraction=0.0090,
    max_stressed_symbol_loss_fraction=0.0045,
    planned_risk_basis=PlannedRiskBasis.CAPITAL_BASE,
)

FUNDED_PAYOUT_GUARD_POLICY = GuardPolicy(
    daily_block_fraction=0.20,
    daily_flatten_fraction=0.40,
    total_cycle_halt_fraction=0.40,
    max_planned_portfolio_risk_fraction=0.0060,
    max_stressed_symbol_loss_fraction=0.0035,
    planned_risk_basis=PlannedRiskBasis.CAPITAL_BASE,
)


@dataclass(frozen=True)
class FundedAccountRules:
    """Immutable firm rules for one account.

    ``session_reset_local_time`` is interpreted as wall-clock time in
    ``firm_timezone``.  A named IANA timezone is mandatory so DST changes are
    handled by :mod:`zoneinfo`; fixed UTC offsets are not accepted as substitutes.
    """

    account_id: str
    initial_balance: float
    official_daily_loss_fraction: float
    official_max_loss_fraction: float
    max_loss_mode: MaxLossMode = MaxLossMode.STATIC
    firm_timezone: str = "Europe/Prague"
    session_reset_local_time: time = time(0, 0)
    max_snapshot_age: timedelta = timedelta(seconds=60)
    future_snapshot_tolerance: timedelta = timedelta(seconds=5)
    guard_policy: GuardPolicy = LEGACY_GUARD_POLICY

    def __post_init__(self) -> None:
        account_id = self.account_id.strip() if isinstance(self.account_id, str) else ""
        if not account_id:
            raise ValueError("account_id must be a non-empty string")
        object.__setattr__(self, "account_id", account_id)

        _require_positive_finite(self.initial_balance, "initial_balance")
        _require_fraction(self.official_daily_loss_fraction, "official_daily_loss_fraction")
        _require_fraction(self.official_max_loss_fraction, "official_max_loss_fraction")
        if self.official_daily_loss_fraction >= self.official_max_loss_fraction:
            raise ValueError("official_daily_loss_fraction must be below official_max_loss_fraction")

        if not isinstance(self.max_loss_mode, MaxLossMode):
            try:
                object.__setattr__(self, "max_loss_mode", MaxLossMode(self.max_loss_mode))
            except (TypeError, ValueError) as exc:
                raise ValueError("max_loss_mode must be 'static' or 'trailing'") from exc

        try:
            ZoneInfo(self.firm_timezone)
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise ValueError(f"unknown IANA firm timezone: {self.firm_timezone!r}") from exc

        if not isinstance(self.session_reset_local_time, time):
            raise ValueError("session_reset_local_time must be datetime.time")
        if self.session_reset_local_time.tzinfo is not None:
            raise ValueError("session_reset_local_time must be a naive firm wall-clock time")
        if not isinstance(self.max_snapshot_age, timedelta):
            raise ValueError("max_snapshot_age must be datetime.timedelta")
        if self.max_snapshot_age <= timedelta(0):
            raise ValueError("max_snapshot_age must be positive")
        if not isinstance(self.future_snapshot_tolerance, timedelta):
            raise ValueError("future_snapshot_tolerance must be datetime.timedelta")
        if self.future_snapshot_tolerance < timedelta(0):
            raise ValueError("future_snapshot_tolerance cannot be negative")
        if not isinstance(self.guard_policy, GuardPolicy):
            raise ValueError("guard_policy must be a GuardPolicy")

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.firm_timezone)

    @property
    def daily_allowance_dollars(self) -> float:
        return self.initial_balance * self.official_daily_loss_fraction

    @property
    def max_loss_allowance_dollars(self) -> float:
        return self.initial_balance * self.official_max_loss_fraction

    @property
    def config_fingerprint(self) -> str:
        """Stable identifier preventing silent rule changes on persisted state."""

        payload = {
            "schema": STATE_SCHEMA_VERSION,
            "account_id": self.account_id,
            "initial_balance": self.initial_balance,
            "official_daily_loss_fraction": self.official_daily_loss_fraction,
            "official_max_loss_fraction": self.official_max_loss_fraction,
            "max_loss_mode": self.max_loss_mode.value,
            "firm_timezone": self.firm_timezone,
            "session_reset_local_time": self.session_reset_local_time.isoformat(),
            "max_snapshot_age_seconds": self.max_snapshot_age.total_seconds(),
            "future_snapshot_tolerance_seconds": self.future_snapshot_tolerance.total_seconds(),
        }
        payload.update(self.guard_policy.fingerprint_payload())
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AccountSnapshot:
    """One broker/account snapshot used by the guard.

    ``balance`` is closed/realised account balance; ``equity`` is live marked
    account equity.  ``equity_includes_all_costs`` has no default on purpose:
    every integration must explicitly attest that unrealised P&L, commissions,
    financing/swaps, and fees are already reflected in ``equity``.

    ``firm_session_start_balance`` is optional for ordinary same-session updates,
    but mandatory for initialization and the first snapshot of every new firm
    session.  It is the authoritative closed balance at the reset boundary.  The
    optional firm-supplied floors are combined conservatively with calculated
    floors: the stricter value always wins.
    """

    account_id: str
    as_of: datetime
    balance: float
    equity: float
    equity_includes_all_costs: bool
    firm_session_start_balance: float | None = None
    firm_daily_loss_floor: float | None = None
    firm_max_loss_floor: float | None = None


@dataclass(frozen=True)
class PlannedRisk:
    """Authoritative aggregate post-order risk for the *entire* account book.

    This value is not an incremental candidate amount and :class:`FundedGuard`
    does not reserve an approved order.  Until an execution adapter supplies one
    atomic open-plus-pending snapshot/reservation transaction, callers must not
    treat separate ``ALLOW`` decisions as independently spendable risk budgets.
    """

    portfolio_risk_dollars: float
    stressed_symbol_loss_dollars: float


@dataclass(frozen=True)
class FundedGuardState:
    """Persisted, account-scoped state needed across process restarts."""

    schema_version: int
    account_id: str
    config_fingerprint: str
    initialized_at: datetime
    session_key: str
    session_anchor_balance: float
    peak_eod_balance: float
    last_balance: float
    last_equity: float
    last_snapshot_at: datetime
    block_new_session: str | None = None
    daily_halt_session: str | None = None
    cycle_halted: bool = False
    cycle_halt_reason: str | None = None
    cycle_halt_at: datetime | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    generation: int = 0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "account_id": self.account_id,
            "config_fingerprint": self.config_fingerprint,
            "initialized_at": _datetime_to_json(self.initialized_at),
            "session_key": self.session_key,
            "session_anchor_balance": self.session_anchor_balance,
            "peak_eod_balance": self.peak_eod_balance,
            "last_balance": self.last_balance,
            "last_equity": self.last_equity,
            "last_snapshot_at": _datetime_to_json(self.last_snapshot_at),
            "block_new_session": self.block_new_session,
            "daily_halt_session": self.daily_halt_session,
            "cycle_halted": self.cycle_halted,
            "cycle_halt_reason": self.cycle_halt_reason,
            "cycle_halt_at": _optional_datetime_to_json(self.cycle_halt_at),
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": _optional_datetime_to_json(self.acknowledged_at),
            "generation": self.generation,
        }

    @classmethod
    def from_json_dict(cls, raw: Any) -> "FundedGuardState":
        if not isinstance(raw, dict):
            raise ValueError("state JSON root must be an object")
        required = {
            "schema_version",
            "account_id",
            "config_fingerprint",
            "initialized_at",
            "session_key",
            "session_anchor_balance",
            "peak_eod_balance",
            "last_balance",
            "last_equity",
            "last_snapshot_at",
            "block_new_session",
            "daily_halt_session",
            "cycle_halted",
            "cycle_halt_reason",
            "cycle_halt_at",
            "acknowledged_by",
            "acknowledged_at",
            "generation",
        }
        missing = required.difference(raw)
        if missing:
            raise ValueError(f"state is missing fields: {', '.join(sorted(missing))}")
        return cls(
            schema_version=_strict_int(raw["schema_version"], "schema_version"),
            account_id=_strict_string(raw["account_id"], "account_id"),
            config_fingerprint=_strict_string(raw["config_fingerprint"], "config_fingerprint"),
            initialized_at=_datetime_from_json(raw["initialized_at"], "initialized_at"),
            session_key=_strict_string(raw["session_key"], "session_key"),
            session_anchor_balance=_strict_float(
                raw["session_anchor_balance"], "session_anchor_balance"
            ),
            peak_eod_balance=_strict_float(raw["peak_eod_balance"], "peak_eod_balance"),
            last_balance=_strict_float(raw["last_balance"], "last_balance"),
            last_equity=_strict_float(raw["last_equity"], "last_equity"),
            last_snapshot_at=_datetime_from_json(raw["last_snapshot_at"], "last_snapshot_at"),
            block_new_session=_optional_string(raw["block_new_session"], "block_new_session"),
            daily_halt_session=_optional_string(raw["daily_halt_session"], "daily_halt_session"),
            cycle_halted=_strict_bool(raw["cycle_halted"], "cycle_halted"),
            cycle_halt_reason=_optional_string(raw["cycle_halt_reason"], "cycle_halt_reason"),
            cycle_halt_at=_optional_datetime_from_json(raw["cycle_halt_at"], "cycle_halt_at"),
            acknowledged_by=_optional_string(raw["acknowledged_by"], "acknowledged_by"),
            acknowledged_at=_optional_datetime_from_json(raw["acknowledged_at"], "acknowledged_at"),
            generation=_strict_int(raw["generation"], "generation"),
        )


@dataclass(frozen=True)
class GuardDecision:
    """Complete decision and risk-budget telemetry for the live caller."""

    action: GuardAction
    reason_codes: tuple[str, ...]
    message: str
    session_key: str
    equity: float
    official_daily_floor_dollars: float
    official_max_floor_dollars: float
    daily_loss_used_dollars: float
    total_loss_used_dollars: float
    daily_allowance_dollars: float
    max_loss_allowance_dollars: float
    daily_consumption_fraction: float
    total_consumption_fraction: float
    capital_base_dollars: float
    remaining_official_loss_buffer_dollars: float
    sizing_risk_budget_base_dollars: float
    max_planned_portfolio_risk_dollars: float
    max_stressed_symbol_loss_dollars: float
    state_generation: int

    @property
    def permits_new_risk(self) -> bool:
        return self.action is GuardAction.ALLOW

    @property
    def requires_cancel(self) -> bool:
        return self.action in {
            GuardAction.CANCEL_AND_FLATTEN,
            GuardAction.CYCLE_HALT,
            GuardAction.FAIL_CLOSED,
        }

    @property
    def requires_flatten(self) -> bool:
        return self.requires_cancel


@dataclass(frozen=True)
class GuardEvaluation:
    """Pure evaluation result; ``next_state`` is persisted only by FundedGuard."""

    decision: GuardDecision
    next_state: FundedGuardState | None


def firm_session_key(at: datetime, rules: FundedAccountRules) -> str:
    """Return the firm session label for an aware instant.

    The local reset is resolved to one UTC instant: the earliest occurrence when
    a wall time repeats in autumn, and the first valid wall minute after a spring
    gap.  Comparing instants (rather than only local clock fields) prevents the
    repeated hour from moving a session backwards or causing a second reset.
    """

    _require_aware_datetime(at, "at")
    local = at.astimezone(rules.timezone)
    session_date = local.date()
    boundary_utc = _local_session_boundary_utc(
        session_date.isoformat(),
        rules.session_reset_local_time.isoformat(),
        rules.firm_timezone,
    )
    if at.astimezone(timezone.utc) < boundary_utc:
        session_date -= timedelta(days=1)
    return session_date.isoformat()


@lru_cache(maxsize=4096)
def _local_session_boundary_utc(
    session_date_iso: str,
    reset_time_iso: str,
    timezone_name: str,
) -> datetime:
    """Resolve a local wall-clock boundary without DST ambiguity.

    Firm reset times are normally minute-aligned.  On the rare spring date where
    that minute does not exist, advancing minute-by-minute finds the first valid
    wall minute after the gap.  Results are cached per local date.
    """

    zone = ZoneInfo(timezone_name)
    local_date = date.fromisoformat(session_date_iso)
    reset_time = time.fromisoformat(reset_time_iso)
    naive = datetime.combine(local_date, reset_time)

    def valid_utc_candidates(local_naive: datetime) -> list[datetime]:
        candidates: list[datetime] = []
        for fold in (0, 1):
            candidate = local_naive.replace(tzinfo=zone, fold=fold)
            candidate_utc = candidate.astimezone(timezone.utc)
            roundtrip = candidate_utc.astimezone(zone)
            if roundtrip.replace(tzinfo=None) == local_naive:
                candidates.append(candidate_utc)
        return list(set(candidates))

    candidates = valid_utc_candidates(naive)
    if candidates:
        return min(candidates)

    # A nonexistent local reset during a forward clock jump.  IANA offset gaps
    # are bounded in practice; two local days also covers historical 24h jumps.
    probe = naive
    for _ in range(48 * 60):
        probe += timedelta(minutes=1)
        candidates = valid_utc_candidates(probe)
        if candidates:
            return min(candidates)
    raise ValueError("could not resolve firm session boundary in IANA timezone")


def evaluate_guard(
    rules: FundedAccountRules,
    state: FundedGuardState,
    snapshot: AccountSnapshot,
    planned_risk: PlannedRisk,
    *,
    now: datetime,
) -> GuardEvaluation:
    """Pure funded-risk state transition.

    No filesystem or wall-clock access occurs here.  Given identical arguments,
    the returned decision and state are identical.
    """

    validation_error = _validate_state(rules, state)
    if validation_error is None:
        validation_error = _validate_snapshot(rules, snapshot, now, state=state)
    if validation_error is None:
        validation_error = _validate_planned_risk(planned_risk)
    if validation_error is not None:
        return GuardEvaluation(
            decision=_fail_closed_decision(rules, snapshot, validation_error),
            next_state=None,
        )

    snapshot_at = snapshot.as_of.astimezone(timezone.utc)
    current_session = firm_session_key(snapshot_at, rules)
    if current_session < state.session_key:
        return GuardEvaluation(
            decision=_fail_closed_decision(
                rules, snapshot, "snapshot_session_precedes_persisted_session"
            ),
            next_state=None,
        )

    next_state = state
    if current_session > state.session_key:
        anchor = snapshot.firm_session_start_balance
        if anchor is None:
            return GuardEvaluation(
                decision=_fail_closed_decision(
                    rules,
                    snapshot,
                    "firm_session_start_balance_required_for_new_session",
                ),
                next_state=None,
            )
        next_state = replace(
            next_state,
            session_key=current_session,
            session_anchor_balance=anchor,
            # A new session's authoritative opening balance is the prior
            # completed session's EOD balance.  It is the only live transition
            # allowed to advance a trailing maximum-loss reference.
            peak_eod_balance=max(
                next_state.peak_eod_balance,
                anchor,
                rules.initial_balance,
            ),
            block_new_session=None,
            daily_halt_session=None,
        )
    elif snapshot.firm_session_start_balance is not None and not _money_equal(
        snapshot.firm_session_start_balance, state.session_anchor_balance
    ):
        return GuardEvaluation(
            decision=_fail_closed_decision(
                rules,
                snapshot,
                "firm_session_start_balance_conflicts_with_persisted_anchor",
            ),
            next_state=None,
        )

    next_state = replace(
        next_state,
        last_balance=snapshot.balance,
        last_equity=snapshot.equity,
        last_snapshot_at=snapshot_at,
        generation=next_state.generation + 1,
    )

    metrics = _metrics(rules, next_state, snapshot)
    policy = rules.guard_policy
    action = GuardAction.ALLOW
    reasons: tuple[str, ...] = ("within_all_limits",)
    message = "Snapshot and planned risk are within every funded-account limit."

    # A persisted cycle halt dominates every recoverable/session-scoped state.
    if next_state.cycle_halted:
        action = GuardAction.CYCLE_HALT
        reasons = ("cycle_halt_latched",)
        message = "Cycle halt is latched; explicit operator acknowledgement is required."
    elif _reached_floor(snapshot.equity, metrics.daily_floor):
        action = GuardAction.CYCLE_HALT
        reasons = ("official_daily_floor_reached",)
        message = "Live equity reached the official daily-loss floor."
        next_state = _latch_cycle_halt(next_state, reasons[0], snapshot_at)
    elif _reached_floor(snapshot.equity, metrics.max_floor):
        action = GuardAction.CYCLE_HALT
        reasons = ("official_max_loss_floor_reached",)
        message = "Live equity reached the official maximum-loss floor."
        next_state = _latch_cycle_halt(next_state, reasons[0], snapshot_at)
    elif _reached(
        metrics.total_loss_used,
        policy.total_cycle_halt_fraction * metrics.max_allowance,
    ):
        action = GuardAction.CYCLE_HALT
        reasons = ("internal_total_loss_cycle_halt",)
        message = (
            f"{_percentage_label(policy.total_cycle_halt_fraction)} of the official "
            "total-loss allowance has been consumed."
        )
        next_state = _latch_cycle_halt(next_state, reasons[0], snapshot_at)
    elif next_state.daily_halt_session == current_session:
        action = GuardAction.CANCEL_AND_FLATTEN
        reasons = ("daily_flatten_latched",)
        message = "The daily flatten is latched until the next firm session."
    elif _reached(
        metrics.daily_loss_used,
        policy.daily_flatten_fraction * metrics.daily_allowance,
    ):
        action = GuardAction.CANCEL_AND_FLATTEN
        reasons = ("internal_daily_flatten",)
        message = (
            f"{_percentage_label(policy.daily_flatten_fraction)} of the official "
            "daily-loss allowance has been consumed."
        )
        next_state = replace(
            next_state,
            block_new_session=current_session,
            daily_halt_session=current_session,
        )
    elif next_state.block_new_session == current_session:
        action = GuardAction.BLOCK_NEW
        reasons = ("daily_block_latched",)
        message = "New risk remains blocked until the next firm session."
    elif _reached(
        metrics.daily_loss_used,
        policy.daily_block_fraction * metrics.daily_allowance,
    ):
        action = GuardAction.BLOCK_NEW
        reasons = ("internal_daily_block",)
        message = (
            f"{_percentage_label(policy.daily_block_fraction)} of the official "
            "daily-loss allowance has been consumed."
        )
        next_state = replace(next_state, block_new_session=current_session)
    elif _exceeds(
        planned_risk.portfolio_risk_dollars,
        metrics.max_planned_portfolio_risk,
    ):
        action = GuardAction.BLOCK_NEW
        reasons = ("planned_portfolio_risk_exceeds_limit",)
        message = (
            "Aggregate post-order stop-risk exceeds "
            f"{_percentage_label(policy.max_planned_portfolio_risk_fraction)} of the "
            f"{_planned_risk_basis_label(policy.planned_risk_basis)}."
        )
    elif _exceeds(
        planned_risk.stressed_symbol_loss_dollars,
        metrics.max_stressed_symbol_loss,
    ):
        action = GuardAction.BLOCK_NEW
        reasons = ("stressed_symbol_loss_exceeds_limit",)
        message = (
            "Worst stressed single-symbol loss exceeds "
            f"{_percentage_label(policy.max_stressed_symbol_loss_fraction)} of the "
            f"{_planned_risk_basis_label(policy.planned_risk_basis)}."
        )

    decision = _decision_from_metrics(
        action=action,
        reasons=reasons,
        message=message,
        state=next_state,
        metrics=metrics,
        equity=snapshot.equity,
    )
    return GuardEvaluation(decision=decision, next_state=next_state)


class FundedGuard:
    """Persistent orchestration for :func:`evaluate_guard`.

    Call :meth:`initialize` once during deliberate account provisioning.  Every
    ordinary/restart cycle must call :meth:`assess`; a missing state file there is
    a fail-closed condition and never silently re-anchors the account.
    """

    def __init__(self, rules: FundedAccountRules, state_directory: str | Path) -> None:
        self.rules = rules
        self.state_directory = Path(state_directory)
        account_digest = hashlib.sha256(rules.account_id.encode("utf-8")).hexdigest()
        self.state_path = self.state_directory / f"funded_guard_{account_digest}.json"

    def initialize(self, snapshot: AccountSnapshot, *, now: datetime) -> GuardDecision:
        """Provision state exactly once.

        This is an explicit provisioning operation, not a fallback for
        :meth:`assess`.  Existing or corrupt state is never resumed/overwritten
        here because initialization has no authoritative open-plus-pending risk
        input.  A restart must use :meth:`assess` with the full book risk.
        """

        try:
            with file_lock(self.state_path):
                if self.state_path.exists():
                    # Read once so corrupt state still reports an I/O/parse failure,
                    # then refuse to evaluate a live book as zero risk.
                    self._read_state_unlocked()
                    return _fail_closed_decision(
                        self.rules,
                        snapshot,
                        "state_already_exists_use_assess_with_full_book_risk",
                    )

                validation_error = _validate_snapshot(self.rules, snapshot, now, state=None)
                if validation_error is not None:
                    return _fail_closed_decision(self.rules, snapshot, validation_error)
                if snapshot.firm_session_start_balance is None:
                    return _fail_closed_decision(
                        self.rules,
                        snapshot,
                        "firm_session_start_balance_required_for_initialization",
                    )

                snapshot_at = snapshot.as_of.astimezone(timezone.utc)
                session = firm_session_key(snapshot_at, self.rules)
                anchor = snapshot.firm_session_start_balance
                state = FundedGuardState(
                    schema_version=STATE_SCHEMA_VERSION,
                    account_id=self.rules.account_id,
                    config_fingerprint=self.rules.config_fingerprint,
                    initialized_at=snapshot_at,
                    session_key=session,
                    session_anchor_balance=anchor,
                    peak_eod_balance=max(self.rules.initial_balance, anchor),
                    last_balance=snapshot.balance,
                    last_equity=snapshot.equity,
                    last_snapshot_at=snapshot_at,
                )
                return self._assess_and_write_unlocked(state, snapshot, PlannedRisk(0.0, 0.0), now)
        except Exception as exc:  # state I/O and lock failures are safety failures
            return _fail_closed_decision(
                self.rules,
                snapshot,
                f"state_initialization_failed:{type(exc).__name__}",
            )

    def assess(
        self,
        snapshot: AccountSnapshot,
        planned_risk: PlannedRisk,
        *,
        now: datetime,
    ) -> GuardDecision:
        """Evaluate and persist one live cycle; never auto-initializes state."""

        try:
            with file_lock(self.state_path):
                if not self.state_path.is_file():
                    return _fail_closed_decision(self.rules, snapshot, "state_missing")
                state = self._read_state_unlocked()
                return self._assess_and_write_unlocked(state, snapshot, planned_risk, now)
        except Exception as exc:
            return _fail_closed_decision(
                self.rules,
                snapshot,
                f"state_read_or_write_failed:{type(exc).__name__}",
            )

    def acknowledge_cycle_halt(
        self,
        snapshot: AccountSnapshot,
        *,
        acknowledged_by: str,
        now: datetime,
    ) -> GuardDecision:
        """Explicitly clear a recovered cycle halt, retaining a same-session lock.

        Acknowledgement cannot override a floor or an active policy total-loss trigger.
        Once accepted, cancel/flatten remains latched for the current firm session;
        new risk can be considered only after the next reset.
        """

        operator = acknowledged_by.strip() if isinstance(acknowledged_by, str) else ""
        if not operator:
            return _fail_closed_decision(self.rules, snapshot, "acknowledgement_identity_missing")
        try:
            with file_lock(self.state_path):
                if not self.state_path.is_file():
                    return _fail_closed_decision(self.rules, snapshot, "state_missing")
                state = self._read_state_unlocked()
                if not state.cycle_halted:
                    return _fail_closed_decision(self.rules, snapshot, "cycle_halt_not_latched")

                # First update/validate the still-latched state so stale snapshots
                # and persistence failures cannot be used to clear a halt.
                latched = evaluate_guard(
                    self.rules, state, snapshot, PlannedRisk(0.0, 0.0), now=now
                )
                if latched.next_state is None:
                    return latched.decision

                candidate = replace(
                    latched.next_state,
                    cycle_halted=False,
                    cycle_halt_reason=None,
                    cycle_halt_at=None,
                )
                recovered = evaluate_guard(
                    self.rules, candidate, snapshot, PlannedRisk(0.0, 0.0), now=now
                )
                if recovered.next_state is None:
                    return recovered.decision
                if recovered.decision.action is GuardAction.CYCLE_HALT:
                    # Still unsafe: retain the original latch, but persist the latest
                    # balance/equity/timestamp for a trustworthy subsequent review.
                    retained = replace(
                        recovered.next_state,
                        cycle_halted=True,
                        cycle_halt_reason=state.cycle_halt_reason,
                        cycle_halt_at=state.cycle_halt_at,
                    )
                    self._write_state_unlocked(retained)
                    return replace(
                        recovered.decision,
                        reason_codes=("cycle_halt_acknowledgement_rejected_active_trigger",),
                        message="Acknowledgement rejected because the cycle-halt trigger remains active.",
                        state_generation=retained.generation,
                    )

                current_session = recovered.next_state.session_key
                acknowledged_at = snapshot.as_of.astimezone(timezone.utc)
                final_state = replace(
                    recovered.next_state,
                    cycle_halted=False,
                    cycle_halt_reason=None,
                    cycle_halt_at=None,
                    block_new_session=current_session,
                    daily_halt_session=current_session,
                    acknowledged_by=operator,
                    acknowledged_at=acknowledged_at,
                )
                self._write_state_unlocked(final_state)
                metrics = _metrics(self.rules, final_state, snapshot)
                return _decision_from_metrics(
                    action=GuardAction.CANCEL_AND_FLATTEN,
                    reasons=("cycle_halt_acknowledged_same_session_lock",),
                    message="Cycle halt acknowledged; cancel/flatten remains latched until next session.",
                    state=final_state,
                    metrics=metrics,
                    equity=snapshot.equity,
                )
        except Exception as exc:
            return _fail_closed_decision(
                self.rules,
                snapshot,
                f"state_acknowledgement_failed:{type(exc).__name__}",
            )

    def _assess_and_write_unlocked(
        self,
        state: FundedGuardState,
        snapshot: AccountSnapshot,
        planned_risk: PlannedRisk,
        now: datetime,
    ) -> GuardDecision:
        evaluation = evaluate_guard(self.rules, state, snapshot, planned_risk, now=now)
        if evaluation.next_state is None:
            return evaluation.decision
        self._write_state_unlocked(evaluation.next_state)
        return evaluation.decision

    def _read_state_unlocked(self) -> FundedGuardState:
        with self.state_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return FundedGuardState.from_json_dict(raw)

    def _write_state_unlocked(self, state: FundedGuardState) -> None:
        _atomic_write_json(self.state_path, state.to_json_dict())


@dataclass(frozen=True)
class _RiskMetrics:
    daily_allowance: float
    max_allowance: float
    daily_floor: float
    max_floor: float
    daily_loss_used: float
    total_loss_used: float
    daily_consumption: float
    total_consumption: float
    capital_base: float
    remaining_buffer: float
    sizing_risk_budget_base: float
    max_planned_portfolio_risk: float
    max_stressed_symbol_loss: float


def _metrics(
    rules: FundedAccountRules,
    state: FundedGuardState,
    snapshot: AccountSnapshot,
) -> _RiskMetrics:
    daily_allowance = rules.daily_allowance_dollars
    max_allowance = rules.max_loss_allowance_dollars

    daily_reference = state.session_anchor_balance
    calculated_daily_floor = daily_reference - daily_allowance
    daily_floor = calculated_daily_floor
    if snapshot.firm_daily_loss_floor is not None:
        daily_floor = max(daily_floor, snapshot.firm_daily_loss_floor)
        daily_reference = max(daily_reference, daily_floor + daily_allowance)

    if rules.max_loss_mode is MaxLossMode.TRAILING:
        max_reference = max(rules.initial_balance, state.peak_eod_balance)
    else:
        max_reference = rules.initial_balance
    max_floor = max_reference - max_allowance
    if snapshot.firm_max_loss_floor is not None:
        max_floor = max(max_floor, snapshot.firm_max_loss_floor)
        max_reference = max(max_reference, max_floor + max_allowance)

    daily_loss_used = max(0.0, daily_reference - snapshot.equity)
    # V2's internal cycle guard is explicitly peak-to-current even when the
    # firm's official maximum floor is static.  Only authoritative completed-
    # session anchors can advance ``peak_eod_balance``.  Legacy V1 retains its
    # original official-floor reference semantics byte-for-byte.
    internal_cycle_reference = (
        max(rules.initial_balance, state.peak_eod_balance)
        if rules.guard_policy.planned_risk_basis is PlannedRiskBasis.CAPITAL_BASE
        else max_reference
    )
    total_loss_used = max(0.0, internal_cycle_reference - snapshot.equity)
    daily_consumption = daily_loss_used / daily_allowance
    total_consumption = total_loss_used / max_allowance

    daily_remaining = max(0.0, snapshot.equity - daily_floor)
    max_remaining = max(0.0, snapshot.equity - max_floor)
    remaining_buffer = min(daily_remaining, max_remaining)
    capital_base = max(0.0, min(snapshot.equity, rules.initial_balance))

    # Capital sizing uses the smaller of current and initial equity.  Dollar risk
    # additionally cannot treat more than the remaining official buffer as usable
    # budget; exposing both values prevents a caller from confusing risk budget
    # with trade notional.
    sizing_risk_budget_base = min(capital_base, remaining_buffer)
    planned_risk_reference = (
        daily_allowance
        if rules.guard_policy.planned_risk_basis is PlannedRiskBasis.DAILY_ALLOWANCE
        else capital_base
    )
    max_portfolio_risk = min(
        rules.guard_policy.max_planned_portfolio_risk_fraction
        * planned_risk_reference,
        remaining_buffer,
    )
    max_symbol_loss = min(
        rules.guard_policy.max_stressed_symbol_loss_fraction
        * planned_risk_reference,
        remaining_buffer,
    )

    return _RiskMetrics(
        daily_allowance=daily_allowance,
        max_allowance=max_allowance,
        daily_floor=daily_floor,
        max_floor=max_floor,
        daily_loss_used=daily_loss_used,
        total_loss_used=total_loss_used,
        daily_consumption=daily_consumption,
        total_consumption=total_consumption,
        capital_base=capital_base,
        remaining_buffer=remaining_buffer,
        sizing_risk_budget_base=sizing_risk_budget_base,
        max_planned_portfolio_risk=max_portfolio_risk,
        max_stressed_symbol_loss=max_symbol_loss,
    )


def _decision_from_metrics(
    *,
    action: GuardAction,
    reasons: tuple[str, ...],
    message: str,
    state: FundedGuardState,
    metrics: _RiskMetrics,
    equity: float,
) -> GuardDecision:
    return GuardDecision(
        action=action,
        reason_codes=reasons,
        message=message,
        session_key=state.session_key,
        equity=equity,
        official_daily_floor_dollars=metrics.daily_floor,
        official_max_floor_dollars=metrics.max_floor,
        daily_loss_used_dollars=metrics.daily_loss_used,
        total_loss_used_dollars=metrics.total_loss_used,
        daily_allowance_dollars=metrics.daily_allowance,
        max_loss_allowance_dollars=metrics.max_allowance,
        daily_consumption_fraction=metrics.daily_consumption,
        total_consumption_fraction=metrics.total_consumption,
        capital_base_dollars=metrics.capital_base,
        remaining_official_loss_buffer_dollars=metrics.remaining_buffer,
        sizing_risk_budget_base_dollars=metrics.sizing_risk_budget_base,
        max_planned_portfolio_risk_dollars=metrics.max_planned_portfolio_risk,
        max_stressed_symbol_loss_dollars=metrics.max_stressed_symbol_loss,
        state_generation=state.generation,
    )


def _fail_closed_decision(
    rules: FundedAccountRules,
    snapshot: AccountSnapshot | Any,
    reason: str,
) -> GuardDecision:
    snapshot_equity = getattr(snapshot, "equity", None)
    equity = float(snapshot_equity) if _is_positive_finite(snapshot_equity) else 0.0
    capital_base = min(equity, rules.initial_balance) if equity > 0.0 else 0.0
    return GuardDecision(
        action=GuardAction.FAIL_CLOSED,
        reason_codes=(reason,),
        message=f"Funded guard failed closed: {reason}.",
        session_key="unknown",
        equity=equity,
        official_daily_floor_dollars=0.0,
        official_max_floor_dollars=0.0,
        daily_loss_used_dollars=0.0,
        total_loss_used_dollars=0.0,
        daily_allowance_dollars=rules.daily_allowance_dollars,
        max_loss_allowance_dollars=rules.max_loss_allowance_dollars,
        daily_consumption_fraction=0.0,
        total_consumption_fraction=0.0,
        capital_base_dollars=capital_base,
        remaining_official_loss_buffer_dollars=0.0,
        sizing_risk_budget_base_dollars=0.0,
        max_planned_portfolio_risk_dollars=0.0,
        max_stressed_symbol_loss_dollars=0.0,
        state_generation=-1,
    )


def _latch_cycle_halt(
    state: FundedGuardState,
    reason: str,
    at: datetime,
) -> FundedGuardState:
    return replace(
        state,
        block_new_session=state.session_key,
        daily_halt_session=state.session_key,
        cycle_halted=True,
        cycle_halt_reason=reason,
        cycle_halt_at=at,
    )


def _validate_snapshot(
    rules: FundedAccountRules,
    snapshot: AccountSnapshot | Any,
    now: datetime | Any,
    *,
    state: FundedGuardState | None,
) -> str | None:
    if not isinstance(snapshot, AccountSnapshot):
        return "snapshot_type_invalid"
    try:
        _require_aware_datetime(now, "now")
    except (TypeError, ValueError):
        return "invalid_now_timestamp"
    if snapshot.account_id != rules.account_id:
        return "snapshot_account_mismatch"
    try:
        _require_aware_datetime(snapshot.as_of, "snapshot.as_of")
    except (TypeError, ValueError):
        return "snapshot_timestamp_not_timezone_aware"
    if not _is_positive_finite(snapshot.balance):
        return "snapshot_balance_invalid"
    if not _is_positive_finite(snapshot.equity):
        return "snapshot_equity_invalid"
    if snapshot.equity_includes_all_costs is not True:
        return "snapshot_equity_does_not_include_all_costs"

    for value, label in (
        (
            snapshot.firm_session_start_balance,
            "firm_session_start_balance_invalid",
        ),
        (snapshot.firm_daily_loss_floor, "firm_daily_loss_floor_invalid"),
        (snapshot.firm_max_loss_floor, "firm_max_loss_floor_invalid"),
    ):
        if value is not None and not _is_positive_finite(value):
            return label

    now_utc = now.astimezone(timezone.utc)
    snapshot_utc = snapshot.as_of.astimezone(timezone.utc)
    if snapshot_utc - now_utc > rules.future_snapshot_tolerance:
        return "snapshot_timestamp_too_far_in_future"
    if now_utc - snapshot_utc > rules.max_snapshot_age:
        return "snapshot_stale"
    if state is not None:
        last_at = state.last_snapshot_at.astimezone(timezone.utc)
        if snapshot_utc < last_at:
            return "snapshot_out_of_order"
        if snapshot_utc == last_at:
            if (
                not _money_equal(snapshot.balance, state.last_balance)
                or not _money_equal(snapshot.equity, state.last_equity)
            ):
                return "snapshot_conflicts_at_same_timestamp"
    return None


def _validate_planned_risk(planned: PlannedRisk) -> str | None:
    if not isinstance(planned, PlannedRisk):
        return "planned_risk_missing_or_invalid"
    if not _is_nonnegative_finite(planned.portfolio_risk_dollars):
        return "planned_portfolio_risk_invalid"
    if not _is_nonnegative_finite(planned.stressed_symbol_loss_dollars):
        return "stressed_symbol_loss_invalid"
    return None


def _validate_state(rules: FundedAccountRules, state: FundedGuardState) -> str | None:
    if not isinstance(state, FundedGuardState):
        return "state_type_invalid"
    if (
        not isinstance(state.schema_version, int)
        or isinstance(state.schema_version, bool)
        or state.schema_version != STATE_SCHEMA_VERSION
    ):
        return "state_schema_version_mismatch"
    if state.account_id != rules.account_id:
        return "state_account_mismatch"
    if state.config_fingerprint != rules.config_fingerprint:
        return "state_rules_fingerprint_mismatch"
    if (
        not isinstance(state.generation, int)
        or isinstance(state.generation, bool)
        or state.generation < 0
    ):
        return "state_generation_invalid"
    if not isinstance(state.cycle_halted, bool):
        return "state_cycle_halt_flag_invalid"
    try:
        _require_aware_datetime(state.initialized_at, "initialized_at")
        _require_aware_datetime(state.last_snapshot_at, "last_snapshot_at")
        if state.cycle_halt_at is not None:
            _require_aware_datetime(state.cycle_halt_at, "cycle_halt_at")
        if state.acknowledged_at is not None:
            _require_aware_datetime(state.acknowledged_at, "acknowledged_at")
        _require_session_key(state.session_key, "session_key")
        if state.block_new_session is not None:
            _require_session_key(state.block_new_session, "block_new_session")
        if state.daily_halt_session is not None:
            _require_session_key(state.daily_halt_session, "daily_halt_session")
        _require_positive_finite(
            state.session_anchor_balance, "session_anchor_balance"
        )
        _require_positive_finite(state.peak_eod_balance, "peak_eod_balance")
        _require_positive_finite(state.last_balance, "last_balance")
        _require_positive_finite(state.last_equity, "last_equity")
    except (TypeError, ValueError):
        return "state_invariant_invalid"
    if state.peak_eod_balance + _MONEY_ABS_TOLERANCE < rules.initial_balance:
        return "state_eod_peak_below_initial_balance"
    if state.peak_eod_balance + _MONEY_ABS_TOLERANCE < state.session_anchor_balance:
        return "state_eod_peak_below_session_anchor"
    if state.last_snapshot_at < state.initialized_at:
        return "state_timestamp_order_invalid"
    try:
        if firm_session_key(state.last_snapshot_at, rules) != state.session_key:
            return "state_session_timestamp_mismatch"
    except (TypeError, ValueError):
        return "state_session_timestamp_mismatch"
    if state.block_new_session not in (None, state.session_key):
        return "state_block_latch_session_invalid"
    if state.daily_halt_session not in (None, state.session_key):
        return "state_daily_halt_session_invalid"
    if state.cycle_halted and (not state.cycle_halt_reason or state.cycle_halt_at is None):
        return "state_cycle_halt_metadata_missing"
    if not state.cycle_halted and (state.cycle_halt_reason is not None or state.cycle_halt_at is not None):
        return "state_cycle_halt_metadata_inconsistent"
    if (state.acknowledged_by is None) != (state.acknowledged_at is None):
        return "state_acknowledgement_metadata_inconsistent"
    return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Durably replace ``path`` without ever exposing a partially-written JSON file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        # Persist the directory entry as well as file contents on POSIX filesystems.
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _percentage_label(fraction: float) -> str:
    return f"{fraction * 100.0:g}%"


def _planned_risk_basis_label(basis: PlannedRiskBasis) -> str:
    return (
        "daily allowance"
        if basis is PlannedRiskBasis.DAILY_ALLOWANCE
        else "capital base"
    )


def _reached(value: float, threshold: float) -> bool:
    return value > threshold or math.isclose(
        value,
        threshold,
        rel_tol=_FLOAT_REL_TOLERANCE,
        abs_tol=_MONEY_ABS_TOLERANCE,
    )


def _reached_floor(equity: float, floor: float) -> bool:
    return equity < floor or math.isclose(
        equity,
        floor,
        rel_tol=_FLOAT_REL_TOLERANCE,
        abs_tol=_MONEY_ABS_TOLERANCE,
    )


def _exceeds(value: float, limit: float) -> bool:
    return value > limit and not math.isclose(
        value,
        limit,
        rel_tol=_FLOAT_REL_TOLERANCE,
        abs_tol=_MONEY_ABS_TOLERANCE,
    )


def _money_equal(left: float, right: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=_FLOAT_REL_TOLERANCE,
        abs_tol=_MONEY_ABS_TOLERANCE,
    )


def _is_positive_finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0.0
    )


def _is_nonnegative_finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _require_positive_finite(value: Any, label: str) -> None:
    if not _is_positive_finite(value):
        raise ValueError(f"{label} must be positive and finite")


def _require_fraction(value: Any, label: str) -> None:
    if not _is_positive_finite(value) or float(value) >= 1.0:
        raise ValueError(f"{label} must be between 0 and 1")


def _require_aware_datetime(value: Any, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_session_key(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be a canonical ISO date")


def _datetime_to_json(value: datetime) -> str:
    _require_aware_datetime(value, "datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_datetime_to_json(value: datetime | None) -> str | None:
    return None if value is None else _datetime_to_json(value)


def _datetime_from_json(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO datetime string")
    normalised = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO datetime string") from exc
    _require_aware_datetime(parsed, label)
    return parsed.astimezone(timezone.utc)


def _optional_datetime_from_json(value: Any, label: str) -> datetime | None:
    return None if value is None else _datetime_from_json(value, label)


def _strict_float(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _strict_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _strict_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _strict_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _strict_string(value, label)


__all__ = [
    "AccountSnapshot",
    "DAILY_BLOCK_FRACTION",
    "DAILY_FLATTEN_FRACTION",
    "EVALUATION_GUARD_POLICY",
    "FUNDED_PAYOUT_GUARD_POLICY",
    "FundedAccountRules",
    "FundedGuard",
    "FundedGuardState",
    "GuardPolicy",
    "GuardAction",
    "GuardDecision",
    "GuardEvaluation",
    "MAX_PLANNED_PORTFOLIO_RISK_FRACTION",
    "MAX_STRESSED_SYMBOL_LOSS_FRACTION",
    "MaxLossMode",
    "LEGACY_GUARD_POLICY",
    "PlannedRisk",
    "PlannedRiskBasis",
    "TOTAL_CYCLE_HALT_FRACTION",
    "evaluate_guard",
    "firm_session_key",
]
