"""Funded-account rule replay and synchronized stationary bootstrap.

This module deliberately sits outside the execution engine.  It is a research primitive:
it replays an observed live-equity path against explicit account rules and resamples whole
trading days for uncertainty estimates.  It never infers an intraday low from an end-of-day
close.  Callers using daily data must supply ``intraday_min_equity`` themselves.

The bootstrap samples *rows*, not trades or individual fields.  Balance changes, adverse
equity excursions, realised P&L, costs already embedded in those values, and every point in
the intraday path therefore travel together.  A :class:`BootstrapPlan` can be reused across
strategies to give them exactly the same random day paths.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from itertools import chain
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isfinite
from statistics import NormalDist, median
from typing import Any, Literal, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd


MaxLossMode: TypeAlias = Literal["static", "eod_trailing"]
DailyLossBasis: TypeAlias = Literal["initial_balance", "day_start_balance"]
BestDayProfitBasis: TypeAlias = Literal["positive_days", "net_profit"]
BootstrapSizingMode: TypeAlias = Literal[
    "conservative_buffer", "fixed_initial", "min_equity_initial", "compound"
]
ReplayStatus: TypeAlias = Literal["passed", "breached", "active"]
ReplayReason: TypeAlias = Literal[
    "profit_target",
    "daily_loss",
    "max_loss",
    "data_exhausted",
]


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def _money_tolerance(*values: float) -> float:
    """Return the simulator's strict scale-aware tolerance for cash equality."""

    scale = max((abs(float(value)) for value in values), default=0.0)
    return max(1e-8, scale * 1e-10)


def _cash_values_equal(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) <= _money_tolerance(left, right)


def _strict_bool(name: str, value: Any) -> bool:
    if not isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{name} must be boolean, got {value!r}")
    return bool(value)


def _nonnegative_int(name: str, value: Any) -> int:
    """Validate explicit event-count evidence without truthiness coercion."""

    if isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{name} must be a non-negative integer, got {value!r}")
    if isinstance(value, (int, np.integer)):
        number = int(value)
    elif isinstance(value, (float, np.floating)) and isfinite(float(value)):
        if not float(value).is_integer():
            raise TypeError(f"{name} must be a non-negative integer, got {value!r}")
        number = int(value)
    else:
        raise TypeError(f"{name} must be a non-negative integer, got {value!r}")
    if number < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return number


def _aware_timestamp(value: Any, *, name: str = "timestamp") -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError(
            f"{name} must be timezone-aware; localising a naive timestamp would make "
            "funded-firm session boundaries ambiguous"
        )
    return timestamp


@dataclass(frozen=True, slots=True)
class FundedRules:
    """Rules for one evaluation or funded-account phase.

    ``daily_loss_pct`` and ``max_loss_pct`` are converted to fixed currency amounts from
    ``initial_balance`` unless ``daily_loss_basis`` explicitly requests a percentage of each
    session's opening balance.  An EOD-trailing floor is advanced only from completed
    end-of-day balances; it is nevertheless enforced against live equity during the next
    session.  ``eod_trailing_floor_cap`` is an optional absolute currency floor cap for firms
    whose trailing threshold stops advancing.  Best-day consistency can use either current
    net profit or (the FTMO-shaped default) the sum of positive closed-P&L days as its
    denominator; loss days do not reduce the latter.  ``minimum_trading_days``
    counts only distinct firm sessions whose input explicitly records at least
    one opened position.  Missing opening evidence never counts; closed P&L is
    not a valid proxy because one position can be partially closed on several
    later sessions.
    """

    initial_balance: float
    profit_target_pct: float | None = 0.10
    daily_loss_pct: float = 0.03
    max_loss_pct: float = 0.10
    max_loss_mode: MaxLossMode = "static"
    daily_loss_basis: DailyLossBasis = "initial_balance"
    best_day_max_profit_share: float | None = None
    best_day_profit_basis: BestDayProfitBasis = "positive_days"
    session_timezone: str = "UTC"
    session_rollover: time = time(0, 0)
    eod_trailing_floor_cap: float | None = None
    minimum_trading_days: int = 0

    def __post_init__(self) -> None:
        initial = _finite("initial_balance", self.initial_balance)
        if initial <= 0.0:
            raise ValueError("initial_balance must be positive")
        object.__setattr__(self, "initial_balance", initial)

        for name in ("daily_loss_pct", "max_loss_pct"):
            value = _finite(name, getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
            object.__setattr__(self, name, value)

        if self.profit_target_pct is not None:
            target = _finite("profit_target_pct", self.profit_target_pct)
            if target <= 0.0:
                raise ValueError("profit_target_pct must be positive or None")
            object.__setattr__(self, "profit_target_pct", target)

        if (
            isinstance(self.minimum_trading_days, (bool, np.bool_))
            or not isinstance(self.minimum_trading_days, (int, np.integer))
        ):
            raise TypeError("minimum_trading_days must be a non-negative integer")
        minimum_trading_days = int(self.minimum_trading_days)
        if minimum_trading_days < 0:
            raise ValueError("minimum_trading_days must be a non-negative integer")
        object.__setattr__(self, "minimum_trading_days", minimum_trading_days)

        if self.max_loss_mode not in ("static", "eod_trailing"):
            raise ValueError(f"unsupported max_loss_mode: {self.max_loss_mode!r}")
        if self.daily_loss_basis not in ("initial_balance", "day_start_balance"):
            raise ValueError(f"unsupported daily_loss_basis: {self.daily_loss_basis!r}")

        if self.best_day_max_profit_share is not None:
            share = _finite(
                "best_day_max_profit_share", self.best_day_max_profit_share
            )
            if not 0.0 < share <= 1.0:
                raise ValueError("best_day_max_profit_share must be in (0, 1]")
            object.__setattr__(self, "best_day_max_profit_share", share)
        if self.best_day_profit_basis not in ("positive_days", "net_profit"):
            raise ValueError(
                f"unsupported best_day_profit_basis: {self.best_day_profit_basis!r}"
            )

        if self.eod_trailing_floor_cap is not None:
            cap = _finite("eod_trailing_floor_cap", self.eod_trailing_floor_cap)
            initial_floor = self.initial_balance * (1.0 - self.max_loss_pct)
            if cap < initial_floor:
                raise ValueError(
                    "eod_trailing_floor_cap cannot be below the initial max-loss floor"
                )
            object.__setattr__(self, "eod_trailing_floor_cap", cap)

        if self.session_rollover.tzinfo is not None:
            raise ValueError("session_rollover must be a naive firm-local wall-clock time")
        try:
            ZoneInfo(self.session_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"unknown IANA session timezone: {self.session_timezone!r}"
            ) from exc


@dataclass(frozen=True, slots=True)
class EquityEvent:
    """One account snapshot; ``closed_pnl`` is a net increment when supplied.

    ``verified_flat_at_end`` is an explicit external attestation.  Only the last
    event in a session can qualify its :class:`DayRecord` as flat; omission is the
    conservative ``False`` default.
    """

    timestamp: pd.Timestamp
    balance: float
    equity: float
    closed_pnl: float | None = None
    day_start_balance: float | None = None
    day_start_equity: float | None = None
    verified_flat_at_end: bool = False
    positions_opened: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _aware_timestamp(self.timestamp))
        object.__setattr__(self, "balance", _finite("balance", self.balance))
        object.__setattr__(self, "equity", _finite("equity", self.equity))
        if self.closed_pnl is not None:
            object.__setattr__(
                self, "closed_pnl", _finite("closed_pnl", self.closed_pnl)
            )
        if self.day_start_balance is not None:
            object.__setattr__(
                self,
                "day_start_balance",
                _finite("day_start_balance", self.day_start_balance),
            )
        if self.day_start_equity is not None:
            object.__setattr__(
                self,
                "day_start_equity",
                _finite("day_start_equity", self.day_start_equity),
            )
        object.__setattr__(
            self,
            "verified_flat_at_end",
            _strict_bool("verified_flat_at_end", self.verified_flat_at_end),
        )
        if self.positions_opened is not None:
            object.__setattr__(
                self,
                "positions_opened",
                _nonnegative_int("positions_opened", self.positions_opened),
            )
        if self.verified_flat_at_end and not _cash_values_equal(
            self.balance, self.equity
        ):
            raise ValueError(
                "verified_flat_at_end requires balance and equity to be equal"
            )


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """A retained point in a day's live-equity path."""

    timestamp: pd.Timestamp
    balance: float
    equity: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _aware_timestamp(self.timestamp))
        object.__setattr__(self, "balance", _finite("balance", self.balance))
        object.__setattr__(self, "equity", _finite("equity", self.equity))


@dataclass(frozen=True, slots=True)
class DayRecord:
    """One indivisible session used by rule replay and block resampling.

    ``source_risk_base`` is the capital base against which the source engine sized that day's
    exposures.  It defaults to day-start balance when unavailable, but research harnesses
    should supply the engine's actual value so sampled P&L and excursions become faithful
    fractions of risk capital rather than silently compounded account returns.

    A profit target requires ``verified_flat_at_end=True`` as explicit external
    evidence that no open exposure remains, as well as balance and equity at the
    threshold.  The default is deliberately fail-safe.
    """

    session: date
    timestamp: pd.Timestamp
    day_start_balance: float
    intraday_min_equity: float
    end_balance: float
    end_equity: float
    closed_pnl: float
    day_start_equity: float | None = None
    source_risk_base: float | None = None
    intraday_min_timestamp: pd.Timestamp | None = None
    equity_path: tuple[EquityPoint, ...] = ()
    verified_flat_at_end: bool = False
    positions_opened: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _aware_timestamp(self.timestamp))
        for name in (
            "day_start_balance",
            "intraday_min_equity",
            "end_balance",
            "end_equity",
            "closed_pnl",
        ):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if self.day_start_balance <= 0.0:
            raise ValueError("day_start_balance must be positive")
        day_start_equity = (
            self.day_start_balance
            if self.day_start_equity is None
            else _finite("day_start_equity", self.day_start_equity)
        )
        object.__setattr__(self, "day_start_equity", day_start_equity)
        source_risk_base = (
            self.day_start_balance
            if self.source_risk_base is None
            else _finite("source_risk_base", self.source_risk_base)
        )
        if source_risk_base <= 0.0:
            raise ValueError("source_risk_base must be positive")
        object.__setattr__(self, "source_risk_base", source_risk_base)
        object.__setattr__(
            self,
            "verified_flat_at_end",
            _strict_bool("verified_flat_at_end", self.verified_flat_at_end),
        )
        if self.positions_opened is not None:
            object.__setattr__(
                self,
                "positions_opened",
                _nonnegative_int("positions_opened", self.positions_opened),
            )
        balance_change = self.end_balance - self.day_start_balance
        money_tolerance = _money_tolerance(
            self.day_start_balance, self.end_balance, self.end_equity
        )
        if abs(self.closed_pnl - balance_change) > money_tolerance:
            raise ValueError(
                "closed_pnl must equal end_balance - day_start_balance after costs"
            )
        if self.verified_flat_at_end and not _cash_values_equal(
            self.end_balance, self.end_equity
        ):
            raise ValueError(
                "verified_flat_at_end requires end_balance and end_equity to be equal"
            )
        if self.intraday_min_equity > self.end_equity + money_tolerance:
            raise ValueError("intraday_min_equity cannot exceed end_equity")
        if self.intraday_min_equity > day_start_equity + money_tolerance:
            raise ValueError("intraday_min_equity cannot exceed day_start_equity")
        if self.intraday_min_timestamp is not None:
            object.__setattr__(
                self,
                "intraday_min_timestamp",
                _aware_timestamp(
                    self.intraday_min_timestamp, name="intraday_min_timestamp"
                ),
            )
        path = tuple(self.equity_path)
        object.__setattr__(self, "equity_path", path)
        if path:
            if not all(isinstance(point, EquityPoint) for point in path):
                raise TypeError("equity_path must contain only EquityPoint records")
            if any(
                left.timestamp > right.timestamp
                for left, right in zip(path, path[1:])
            ):
                raise ValueError("equity_path timestamps must be chronological")
            path_min = min(point.equity for point in path)
            if abs(path_min - self.intraday_min_equity) > money_tolerance:
                raise ValueError(
                    "intraday_min_equity must equal the minimum retained path equity"
                )
            if (
                abs(path[-1].balance - self.end_balance) > money_tolerance
                or abs(path[-1].equity - self.end_equity) > money_tolerance
            ):
                raise ValueError(
                    "the final equity_path point must equal end_balance/end_equity"
                )


@dataclass(frozen=True, slots=True)
class RuleMargins:
    """Smallest realised buffers to each rule; negative means the rule was crossed."""

    daily_loss_buffer: float
    daily_loss_buffer_pct_initial: float
    max_loss_buffer: float
    max_loss_buffer_pct_initial: float
    profit_target_buffer: float | None
    profit_target_buffer_pct_initial: float | None
    best_day_consistency_buffer: float | None
    positive_days_profit: float
    best_day_profit_share: float | None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Terminal result of replaying one ordered account path."""

    status: ReplayStatus
    reason: ReplayReason
    timestamp: pd.Timestamp
    session: date
    sessions_processed: int
    ending_balance: float
    ending_equity: float
    peak_eod_balance: float
    max_loss_floor: float
    best_day_profit: float
    positive_days_profit: float
    trading_days: int
    minimum_trading_days: int
    margins: RuleMargins


@dataclass(frozen=True, slots=True)
class WilsonInterval:
    """Wilson score estimate and two-sided confidence interval."""

    estimate: float
    lower: float
    upper: float
    successes: int
    trials: int
    confidence: float


@dataclass(frozen=True, slots=True)
class BootstrapPaths:
    """Stationary-bootstrap index paths for one expected block length."""

    mean_block_length: int
    paths: tuple[tuple[int, ...], ...]


@dataclass(frozen=True, slots=True)
class BootstrapPlan:
    """Reusable common random paths for synchronized strategy comparisons."""

    n_observations: int
    sample_length: int
    n_paths: int
    seed: int
    blocks: tuple[BootstrapPaths, ...]

    def for_mean_block_length(self, value: int) -> BootstrapPaths:
        for block in self.blocks:
            if block.mean_block_length == value:
                return block
        raise KeyError(f"mean block length {value} is not present in this plan")


@dataclass(frozen=True, slots=True)
class ChunkedBootstrapSpec:
    """Memory-bounded bootstrap design containing no materialized index paths."""

    n_observations: int
    sample_length: int
    n_paths: int
    seed: int
    mean_block_lengths: tuple[int, ...]
    chunk_size: int

    @property
    def maximum_index_chunk_bytes(self) -> int:
        """Upper bound for the one materialized int32 index matrix."""

        return self.chunk_size * self.sample_length * np.dtype(np.int32).itemsize


@dataclass(frozen=True, slots=True)
class BootstrapBlockResult:
    """Outcome frequencies for one expected block length."""

    mean_block_length: int
    pass_probability: WilsonInterval
    breach_probability: WilsonInterval
    survival_probability: WilsonInterval
    median_sessions_to_pass: float | None
    breach_reasons: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class BootstrapReport:
    """Bootstrap results for one strategy under one common plan."""

    plan: BootstrapPlan
    blocks: tuple[BootstrapBlockResult, ...]
    sizing_mode: BootstrapSizingMode = "conservative_buffer"

    def for_mean_block_length(self, value: int) -> BootstrapBlockResult:
        for block in self.blocks:
            if block.mean_block_length == value:
                return block
        raise KeyError(f"mean block length {value} is not present in this report")


@dataclass(frozen=True, slots=True)
class ChunkedBootstrapReport:
    """Memory-bounded bootstrap results for one strategy."""

    spec: ChunkedBootstrapSpec
    blocks: tuple[BootstrapBlockResult, ...]
    sizing_mode: BootstrapSizingMode

    def for_mean_block_length(self, value: int) -> BootstrapBlockResult:
        for block in self.blocks:
            if block.mean_block_length == value:
                return block
        raise KeyError(f"mean block length {value} is not present in this report")


@dataclass(frozen=True, slots=True)
class StrategyBootstrapReport:
    name: str
    report: BootstrapReport


@dataclass(frozen=True, slots=True)
class SynchronizedBootstrapReport:
    """Multiple strategies evaluated on the exact same sampled session indices."""

    plan: BootstrapPlan
    strategies: tuple[StrategyBootstrapReport, ...]


@dataclass(frozen=True, slots=True)
class ChunkedStrategyBootstrapReport:
    name: str
    report: ChunkedBootstrapReport


@dataclass(frozen=True, slots=True)
class ChunkedSynchronizedBootstrapReport:
    """Memory-bounded reports sharing each generated chunk across all strategies."""

    spec: ChunkedBootstrapSpec
    strategies: tuple[ChunkedStrategyBootstrapReport, ...]


EventInput: TypeAlias = (
    pd.DataFrame
    | Sequence[EquityEvent]
    | Sequence[DayRecord]
    | Sequence[Mapping[str, Any]]
)


def firm_session_key(
    timestamp: Any,
    *,
    timezone: str,
    rollover: time = time(0, 0),
) -> date:
    """Return the firm-local session-start date for an aware timestamp.

    Conversion happens before the wall-clock rollover comparison, so UTC offset and DST
    transitions follow the selected IANA timezone rather than a fixed offset.
    """

    if rollover.tzinfo is not None:
        raise ValueError("rollover must be a naive local wall-clock time")
    try:
        local = _aware_timestamp(timestamp).tz_convert(ZoneInfo(timezone))
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {timezone!r}") from exc
    # Comparing only local wall-clock values regresses at the autumn DST fold: 01:45 BST is
    # followed by 01:15 GMT.  Construct the actual rollover instant and compare instants.
    # ``time.fold`` selects the first (0/DST) or second (1/standard) occurrence when the
    # configured rollover itself lies in that repeated hour.  Non-existent spring-forward
    # rollovers raise instead of silently assigning a different boundary.
    local_date = local.date()
    naive_rollover = pd.Timestamp(datetime.combine(local_date, rollover))
    boundary = naive_rollover.tz_localize(
        ZoneInfo(timezone),
        ambiguous=rollover.fold == 0,
        nonexistent="raise",
    )
    return (
        local_date
        if local.tz_convert("UTC") >= boundary.tz_convert("UTC")
        else local_date - timedelta(days=1)
    )


def _frame_with_timestamp(data: pd.DataFrame) -> pd.DataFrame:
    frame = data.copy()
    if "timestamp" not in frame.columns:
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError("input needs a timestamp column or a DatetimeIndex")
        index_name = frame.index.name or "index"
        frame = frame.reset_index().rename(columns={index_name: "timestamp"})
    if frame.empty:
        raise ValueError("rule replay requires at least one observation")
    return frame


def _events_frame(data: Sequence[EquityEvent]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [event.timestamp for event in data],
            "balance": [event.balance for event in data],
            "equity": [event.equity for event in data],
            "closed_pnl": [event.closed_pnl for event in data],
            "day_start_balance": [event.day_start_balance for event in data],
            "day_start_equity": [event.day_start_equity for event in data],
            "verified_flat_at_end": [
                event.verified_flat_at_end for event in data
            ],
            "positions_opened": [event.positions_opened for event in data],
        }
    )


def _coerce_numeric(frame: pd.DataFrame, columns: Sequence[str]) -> None:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        values = frame[column].dropna().to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{column} contains a non-finite value")


def _coerce_optional_bool(frame: pd.DataFrame, column: str) -> None:
    """Validate an optional attestation column without truthiness coercion.

    Missing values retain the conservative ``False`` default.  In particular, strings such
    as ``"false"`` are rejected instead of becoming truthy through ``bool(value)``.
    """

    if column not in frame.columns:
        frame[column] = False
        return
    values: list[bool] = []
    for value in frame[column]:
        if pd.isna(value):
            values.append(False)
        else:
            values.append(_strict_bool(column, value))
    frame[column] = values


def _coerce_optional_open_counts(frame: pd.DataFrame) -> None:
    """Validate complete per-row opening evidence or preserve unknown state."""

    column = "positions_opened"
    if column not in frame.columns:
        frame[column] = None
        return
    supplied = frame[column].notna()
    if supplied.any() and not supplied.all():
        raise ValueError(
            "positions_opened must be supplied for every row or omitted for every row"
        )
    if not supplied.any():
        frame[column] = None
        return
    frame[column] = [
        _nonnegative_int(column, value) for value in frame[column]
    ]


def _reject_contradictory_flat_rows(
    frame: pd.DataFrame,
    *,
    balance_column: str,
    equity_column: str,
) -> None:
    """Reject an externally asserted flat state whose cash marks disagree."""

    for row in frame.loc[frame["verified_flat_at_end"]].itertuples(index=False):
        balance = float(getattr(row, balance_column))
        equity = float(getattr(row, equity_column))
        if not _cash_values_equal(balance, equity):
            raise ValueError(
                "verified_flat_at_end requires balance and equity to be equal"
            )


def aggregate_equity_events(
    data: pd.DataFrame | Sequence[EquityEvent] | Sequence[Mapping[str, Any]],
    rules: FundedRules,
) -> tuple[DayRecord, ...]:
    """Aggregate account snapshots into exact firm-local sessions.

    If present, event-level ``closed_pnl`` values are treated as net increments and summed.
    Otherwise the day's end-balance change is used.  A supplied ``day_start_balance`` wins
    over the first snapshot balance, which is useful when observation starts after rollover.
    """

    if isinstance(data, pd.DataFrame):
        frame = _frame_with_timestamp(data)
    else:
        items = list(data)
        if not items:
            raise ValueError("rule replay requires at least one observation")
        if isinstance(items[0], EquityEvent):
            if not all(isinstance(item, EquityEvent) for item in items):
                raise TypeError("event sequences cannot mix record types")
            frame = _events_frame(items)  # type: ignore[arg-type]
        else:
            frame = _frame_with_timestamp(pd.DataFrame(items))

    required = {"timestamp", "balance", "equity"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"event input is missing columns: {', '.join(missing)}")

    frame["timestamp"] = [
        _aware_timestamp(value).tz_convert(rules.session_timezone)
        for value in frame["timestamp"]
    ]
    _coerce_numeric(frame, ["balance", "equity"])
    if "closed_pnl" in frame.columns:
        _coerce_numeric(frame, ["closed_pnl"])
        supplied = frame["closed_pnl"].notna()
        if supplied.any() and not supplied.all():
            raise ValueError(
                "closed_pnl must be supplied for every event or omitted for every event"
            )
    if "day_start_balance" in frame.columns:
        _coerce_numeric(frame, ["day_start_balance"])
    if "day_start_equity" in frame.columns:
        _coerce_numeric(frame, ["day_start_equity"])
    _coerce_optional_bool(frame, "verified_flat_at_end")
    _coerce_optional_open_counts(frame)
    _reject_contradictory_flat_rows(
        frame, balance_column="balance", equity_column="equity"
    )

    frame = frame.sort_values("timestamp", kind="stable").reset_index(drop=True)
    frame["_session"] = [
        firm_session_key(
            value,
            timezone=rules.session_timezone,
            rollover=rules.session_rollover,
        )
        for value in frame["timestamp"]
    ]

    records: list[DayRecord] = []
    for session, group in frame.groupby("_session", sort=True):
        group = group.sort_values("timestamp", kind="stable")
        explicit_starts = (
            group["day_start_balance"].dropna()
            if "day_start_balance" in group.columns
            else pd.Series(dtype=float)
        )
        start_balance = float(
            explicit_starts.iloc[0]
            if not explicit_starts.empty
            else group["balance"].iloc[0]
        )
        explicit_equity_starts = (
            group["day_start_equity"].dropna()
            if "day_start_equity" in group.columns
            else pd.Series(dtype=float)
        )
        start_equity = float(
            explicit_equity_starts.iloc[0]
            if not explicit_equity_starts.empty
            else group["equity"].iloc[0]
        )
        min_position = int(np.argmin(group["equity"].to_numpy(dtype=float)))
        min_row = group.iloc[min_position]
        last = group.iloc[-1]
        if "closed_pnl" in group.columns and group["closed_pnl"].notna().any():
            closed_pnl = float(group["closed_pnl"].sum(skipna=True))
        else:
            closed_pnl = float(last["balance"]) - start_balance
        positions_opened = (
            None
            if group["positions_opened"].isna().all()
            else int(group["positions_opened"].sum())
        )
        path = tuple(
            EquityPoint(row.timestamp, row.balance, row.equity)
            for row in group.itertuples(index=False)
        )
        records.append(
            DayRecord(
                session=session,
                timestamp=pd.Timestamp(last["timestamp"]),
                day_start_balance=start_balance,
                intraday_min_equity=float(min_row["equity"]),
                end_balance=float(last["balance"]),
                end_equity=float(last["equity"]),
                closed_pnl=closed_pnl,
                day_start_equity=start_equity,
                intraday_min_timestamp=pd.Timestamp(min_row["timestamp"]),
                equity_path=path,
                verified_flat_at_end=bool(last["verified_flat_at_end"]),
                positions_opened=positions_opened,
            )
        )
    return tuple(records)


def _aggregate_day_frame(frame: pd.DataFrame, rules: FundedRules) -> tuple[DayRecord, ...]:
    required = {
        "timestamp",
        "day_start_balance",
        "intraday_min_equity",
        "end_balance",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"day-record input is missing columns: {', '.join(missing)}")
    if "end_equity" not in frame.columns:
        if "equity" not in frame.columns:
            raise ValueError("day-record input needs end_equity (or the equity alias)")
        frame["end_equity"] = frame["equity"]

    frame["timestamp"] = [
        _aware_timestamp(value).tz_convert(rules.session_timezone)
        for value in frame["timestamp"]
    ]
    numeric = [
        "day_start_balance",
        "intraday_min_equity",
        "end_balance",
        "end_equity",
    ]
    if "closed_pnl" in frame.columns:
        numeric.append("closed_pnl")
    if "source_risk_base" in frame.columns:
        numeric.append("source_risk_base")
    if "day_start_equity" in frame.columns:
        numeric.append("day_start_equity")
    _coerce_numeric(frame, numeric)
    _coerce_optional_bool(frame, "verified_flat_at_end")
    _coerce_optional_open_counts(frame)
    _reject_contradictory_flat_rows(
        frame, balance_column="end_balance", equity_column="end_equity"
    )
    if "closed_pnl" in frame.columns:
        supplied = frame["closed_pnl"].notna()
        if supplied.any() and not supplied.all():
            raise ValueError(
                "closed_pnl must be supplied for every day or omitted for every day"
            )
    if "intraday_min_timestamp" in frame.columns:
        frame["intraday_min_timestamp"] = [
            _aware_timestamp(value).tz_convert(rules.session_timezone)
            if not pd.isna(value)
            else None
            for value in frame["intraday_min_timestamp"]
        ]

    records: list[DayRecord] = []
    for row in frame.sort_values("timestamp", kind="stable").itertuples(index=False):
        timestamp = pd.Timestamp(row.timestamp)
        closed_pnl_value = getattr(row, "closed_pnl", None)
        if closed_pnl_value is None or pd.isna(closed_pnl_value):
            closed_pnl_value = float(row.end_balance) - float(row.day_start_balance)
        min_timestamp = getattr(row, "intraday_min_timestamp", None)
        start_equity_value = getattr(row, "day_start_equity", None)
        if start_equity_value is not None and pd.isna(start_equity_value):
            start_equity_value = None
        source_risk_base_value = getattr(row, "source_risk_base", None)
        if source_risk_base_value is not None and pd.isna(source_risk_base_value):
            source_risk_base_value = None
        positions_opened_value = getattr(row, "positions_opened", None)
        if positions_opened_value is not None and pd.isna(positions_opened_value):
            positions_opened_value = None
        records.append(
            DayRecord(
                session=firm_session_key(
                    timestamp,
                    timezone=rules.session_timezone,
                    rollover=rules.session_rollover,
                ),
                timestamp=timestamp,
                day_start_balance=float(row.day_start_balance),
                intraday_min_equity=float(row.intraday_min_equity),
                end_balance=float(row.end_balance),
                end_equity=float(row.end_equity),
                closed_pnl=float(closed_pnl_value),
                day_start_equity=start_equity_value,
                source_risk_base=source_risk_base_value,
                intraday_min_timestamp=(
                    pd.Timestamp(min_timestamp) if min_timestamp is not None else timestamp
                ),
                verified_flat_at_end=bool(row.verified_flat_at_end),
                positions_opened=positions_opened_value,
            )
        )
    return _validated_days(records, rules)


def _validated_days(
    records: Sequence[DayRecord], rules: FundedRules
) -> tuple[DayRecord, ...]:
    if not records:
        raise ValueError("rule replay requires at least one day record")
    ordered = tuple(sorted(records, key=lambda record: record.timestamp))
    seen: set[date] = set()
    for record in ordered:
        expected = firm_session_key(
            record.timestamp,
            timezone=rules.session_timezone,
            rollover=rules.session_rollover,
        )
        if record.session != expected:
            raise ValueError(
                f"record session {record.session} does not match firm-local session "
                f"{expected} for {record.timestamp}"
            )
        if record.session in seen:
            raise ValueError(f"multiple day records supplied for session {record.session}")
        seen.add(record.session)
        for point in record.equity_path:
            point_session = firm_session_key(
                point.timestamp,
                timezone=rules.session_timezone,
                rollover=rules.session_rollover,
            )
            if point_session != record.session:
                raise ValueError(
                    f"equity_path point {point.timestamp} is outside session "
                    f"{record.session}"
                )
    return ordered


def to_day_records(data: EventInput, rules: FundedRules) -> tuple[DayRecord, ...]:
    """Normalise either event snapshots or already aggregated daily records."""

    if isinstance(data, pd.DataFrame):
        frame = _frame_with_timestamp(data)
        if {"day_start_balance", "intraday_min_equity", "end_balance"}.issubset(
            frame.columns
        ):
            return _aggregate_day_frame(frame, rules)
        return aggregate_equity_events(frame, rules)

    items = list(data)
    if not items:
        raise ValueError("rule replay requires at least one observation")
    if isinstance(items[0], DayRecord):
        if not all(isinstance(item, DayRecord) for item in items):
            raise TypeError("day-record sequences cannot mix record types")
        return _validated_days(items, rules)  # type: ignore[arg-type]
    return aggregate_equity_events(items, rules)  # type: ignore[arg-type]


def _rule_margins(
    *,
    rules: FundedRules,
    min_daily_buffer: float,
    min_max_buffer: float,
    balance: float,
    equity: float,
    verified_flat_at_end: bool,
    best_day_profit: float,
    positive_days_profit: float,
) -> RuleMargins:
    if rules.profit_target_pct is None:
        target_buffer = None
        target_buffer_pct = None
    else:
        # Adding the fixed target amount avoids a visible -1e-11 currency margin at an
        # otherwise exact decimal-money threshold (for example 100_000 + 10%).
        target = (
            rules.initial_balance
            + rules.initial_balance * rules.profit_target_pct
        )
        qualifying_value = (
            balance if verified_flat_at_end else min(balance, equity)
        )
        target_buffer = qualifying_value - target
        target_buffer_pct = target_buffer / rules.initial_balance

    net_profit = balance - rules.initial_balance
    consistency_denominator = (
        positive_days_profit
        if rules.best_day_profit_basis == "positive_days"
        else net_profit
    )
    if rules.best_day_max_profit_share is None:
        consistency_buffer = None
        best_share = (
            None
            if consistency_denominator <= 0.0
            else best_day_profit / consistency_denominator
        )
    elif consistency_denominator > 0.0:
        consistency_buffer = (
            rules.best_day_max_profit_share * consistency_denominator - best_day_profit
        )
        best_share = best_day_profit / consistency_denominator
    else:
        consistency_buffer = -best_day_profit
        best_share = None

    return RuleMargins(
        daily_loss_buffer=min_daily_buffer,
        daily_loss_buffer_pct_initial=min_daily_buffer / rules.initial_balance,
        max_loss_buffer=min_max_buffer,
        max_loss_buffer_pct_initial=min_max_buffer / rules.initial_balance,
        profit_target_buffer=target_buffer,
        profit_target_buffer_pct_initial=target_buffer_pct,
        best_day_consistency_buffer=consistency_buffer,
        positive_days_profit=positive_days_profit,
        best_day_profit_share=best_share,
    )


def _profit_target_met(
    rules: FundedRules,
    *,
    balance: float,
    equity: float,
    verified_flat_at_end: bool,
) -> bool:
    """Return whether a terminal snapshot satisfies conservative target semantics."""

    if rules.profit_target_pct is None:
        return False
    target = rules.initial_balance + rules.initial_balance * rules.profit_target_pct
    return verified_flat_at_end and balance >= target and equity >= target


def _counts_as_trading_day(positions_opened: int | None) -> bool:
    """Count only explicit evidence that at least one position opened that session."""

    return positions_opened is not None and positions_opened > 0


def _max_loss_floor(rules: FundedRules, peak_eod_balance: float) -> float:
    loss_amount = rules.initial_balance * rules.max_loss_pct
    if rules.max_loss_mode == "static":
        return rules.initial_balance - loss_amount
    floor = peak_eod_balance - loss_amount
    if rules.eod_trailing_floor_cap is not None:
        floor = min(floor, rules.eod_trailing_floor_cap)
    return floor


def replay_funded_rules(
    data: EventInput,
    rules: FundedRules,
    *,
    validate_balance_continuity: bool = True,
) -> ReplayResult:
    """Replay observations and stop at the first pass or breach.

    Live-equity daily and maximum-loss checks always run before the end-of-day profit target.
    Touching a floor counts as a breach.  With EOD trailing rules, the new floor is also
    checked against end-of-day equity before a same-day pass can be declared.
    """

    records = to_day_records(data, rules)
    return _replay_day_records(
        records,
        rules,
        validate_balance_continuity=validate_balance_continuity,
    )


def _replay_day_records(
    records: Iterable[DayRecord],
    rules: FundedRules,
    *,
    validate_balance_continuity: bool = True,
) -> ReplayResult:
    """Replay validated records from an iterable, stopping consumption at terminal state.

    The lazy form matters for bootstrap paths: a path that breaches on day one must not
    construct later rebased days whose starting capital is already invalid.
    """

    iterator = iter(records)
    try:
        first = next(iterator)
    except StopIteration as exc:
        raise ValueError("rule replay requires at least one day record") from exc

    start_tolerance = max(1e-7, rules.initial_balance * 1e-9)
    if abs(first.day_start_balance - rules.initial_balance) > start_tolerance:
        raise ValueError(
            "the first day_start_balance must equal rules.initial_balance; reset or "
            "rebase each evaluation/funded phase explicitly"
        )
    if abs(float(first.day_start_equity) - rules.initial_balance) > start_tolerance:
        raise ValueError(
            "the first day_start_equity must equal rules.initial_balance; reset or "
            "rebase each evaluation/funded phase explicitly"
        )
    peak_eod_balance = rules.initial_balance
    max_floor = _max_loss_floor(rules, peak_eod_balance)
    min_daily_buffer = float("inf")
    min_max_buffer = float("inf")
    best_day_profit = 0.0
    positive_days_profit = 0.0
    trading_days = 0
    previous_end_balance: float | None = None
    previous_end_equity: float | None = None
    previous_session: date | None = None
    last = first
    sessions_processed = 0

    for position, record in enumerate(chain((first,), iterator), start=1):
        last = record
        sessions_processed = position
        if previous_session is not None and record.session <= previous_session:
            raise ValueError(
                "replayed firm sessions must be strictly increasing and distinct"
            )
        if validate_balance_continuity and previous_end_balance is not None:
            tolerance = max(1e-7, abs(previous_end_balance) * 1e-9)
            if abs(record.day_start_balance - previous_end_balance) > tolerance:
                raise ValueError(
                    "non-contiguous balances between sessions "
                    f"{previous_session} and {record.session}: "
                    f"{previous_end_balance} != {record.day_start_balance}"
                )
            if (
                previous_end_equity is not None
                and abs(float(record.day_start_equity) - previous_end_equity)
                > tolerance
            ):
                raise ValueError(
                    "non-contiguous equity between sessions "
                    f"{previous_session} and {record.session}: "
                    f"{previous_end_equity} != {record.day_start_equity}"
                )

        daily_basis = (
            rules.initial_balance
            if rules.daily_loss_basis == "initial_balance"
            else record.day_start_balance
        )
        daily_floor = record.day_start_balance - daily_basis * rules.daily_loss_pct
        max_floor = _max_loss_floor(rules, peak_eod_balance)

        if record.equity_path:
            observations = record.equity_path
        else:
            observations = (
                EquityPoint(
                    record.intraday_min_timestamp or record.timestamp,
                    record.end_balance,
                    record.intraday_min_equity,
                ),
            )

        for point in observations:
            daily_buffer = point.equity - daily_floor
            max_buffer = point.equity - max_floor
            min_daily_buffer = min(min_daily_buffer, daily_buffer)
            min_max_buffer = min(min_max_buffer, max_buffer)
            # Daily first gives a deterministic reason if one print crosses both floors.
            if daily_buffer <= 0.0:
                margins = _rule_margins(
                    rules=rules,
                    min_daily_buffer=min_daily_buffer,
                    min_max_buffer=min_max_buffer,
                    balance=point.balance,
                    equity=point.equity,
                    verified_flat_at_end=False,
                    best_day_profit=best_day_profit,
                    positive_days_profit=positive_days_profit,
                )
                return ReplayResult(
                    status="breached",
                    reason="daily_loss",
                    timestamp=point.timestamp,
                    session=record.session,
                    sessions_processed=position,
                    ending_balance=point.balance,
                    ending_equity=point.equity,
                    peak_eod_balance=peak_eod_balance,
                    max_loss_floor=max_floor,
                    best_day_profit=best_day_profit,
                    positive_days_profit=positive_days_profit,
                    trading_days=trading_days,
                    minimum_trading_days=rules.minimum_trading_days,
                    margins=margins,
                )
            if max_buffer <= 0.0:
                margins = _rule_margins(
                    rules=rules,
                    min_daily_buffer=min_daily_buffer,
                    min_max_buffer=min_max_buffer,
                    balance=point.balance,
                    equity=point.equity,
                    verified_flat_at_end=False,
                    best_day_profit=best_day_profit,
                    positive_days_profit=positive_days_profit,
                )
                return ReplayResult(
                    status="breached",
                    reason="max_loss",
                    timestamp=point.timestamp,
                    session=record.session,
                    sessions_processed=position,
                    ending_balance=point.balance,
                    ending_equity=point.equity,
                    peak_eod_balance=peak_eod_balance,
                    max_loss_floor=max_floor,
                    best_day_profit=best_day_profit,
                    positive_days_profit=positive_days_profit,
                    trading_days=trading_days,
                    minimum_trading_days=rules.minimum_trading_days,
                    margins=margins,
                )

        positive_day_profit = max(record.closed_pnl, 0.0)
        best_day_profit = max(best_day_profit, positive_day_profit)
        positive_days_profit += positive_day_profit
        if _counts_as_trading_day(record.positions_opened):
            trading_days += 1
        peak_eod_balance = max(peak_eod_balance, record.end_balance)
        max_floor = _max_loss_floor(rules, peak_eod_balance)

        # An EOD floor advance and the EOD equity print are simultaneous.  Apply the risk
        # rule before considering a target reached on that same print.
        eod_max_buffer = record.end_equity - max_floor
        min_max_buffer = min(min_max_buffer, eod_max_buffer)
        if eod_max_buffer <= 0.0:
            margins = _rule_margins(
                rules=rules,
                min_daily_buffer=min_daily_buffer,
                min_max_buffer=min_max_buffer,
                balance=record.end_balance,
                equity=record.end_equity,
                verified_flat_at_end=record.verified_flat_at_end,
                best_day_profit=best_day_profit,
                positive_days_profit=positive_days_profit,
            )
            return ReplayResult(
                status="breached",
                reason="max_loss",
                timestamp=record.timestamp,
                session=record.session,
                sessions_processed=position,
                ending_balance=record.end_balance,
                ending_equity=record.end_equity,
                peak_eod_balance=peak_eod_balance,
                max_loss_floor=max_floor,
                best_day_profit=best_day_profit,
                positive_days_profit=positive_days_profit,
                trading_days=trading_days,
                minimum_trading_days=rules.minimum_trading_days,
                margins=margins,
            )

        total_profit = record.end_balance - rules.initial_balance
        target_met = _profit_target_met(
            rules,
            balance=record.end_balance,
            equity=record.end_equity,
            verified_flat_at_end=record.verified_flat_at_end,
        )
        consistency_met = (
            rules.best_day_max_profit_share is None
            or (
                (
                    positive_days_profit
                    if rules.best_day_profit_basis == "positive_days"
                    else total_profit
                )
                > 0.0
                and best_day_profit
                <= rules.best_day_max_profit_share
                * (
                    positive_days_profit
                    if rules.best_day_profit_basis == "positive_days"
                    else total_profit
                )
                + 1e-12
            )
        )
        minimum_trading_days_met = trading_days >= rules.minimum_trading_days
        if target_met and consistency_met and minimum_trading_days_met:
            margins = _rule_margins(
                rules=rules,
                min_daily_buffer=min_daily_buffer,
                min_max_buffer=min_max_buffer,
                balance=record.end_balance,
                equity=record.end_equity,
                verified_flat_at_end=record.verified_flat_at_end,
                best_day_profit=best_day_profit,
                positive_days_profit=positive_days_profit,
            )
            return ReplayResult(
                status="passed",
                reason="profit_target",
                timestamp=record.timestamp,
                session=record.session,
                sessions_processed=position,
                ending_balance=record.end_balance,
                ending_equity=record.end_equity,
                peak_eod_balance=peak_eod_balance,
                max_loss_floor=max_floor,
                best_day_profit=best_day_profit,
                positive_days_profit=positive_days_profit,
                trading_days=trading_days,
                minimum_trading_days=rules.minimum_trading_days,
                margins=margins,
            )

        previous_end_balance = record.end_balance
        previous_end_equity = record.end_equity
        previous_session = record.session

    return ReplayResult(
        status="active",
        reason="data_exhausted",
        timestamp=last.timestamp,
        session=last.session,
        sessions_processed=sessions_processed,
        ending_balance=last.end_balance,
        ending_equity=last.end_equity,
        peak_eod_balance=peak_eod_balance,
        max_loss_floor=max_floor,
        best_day_profit=best_day_profit,
        positive_days_profit=positive_days_profit,
        trading_days=trading_days,
        minimum_trading_days=rules.minimum_trading_days,
        margins=_rule_margins(
            rules=rules,
            min_daily_buffer=min_daily_buffer,
            min_max_buffer=min_max_buffer,
            balance=last.end_balance,
            equity=last.end_equity,
            verified_flat_at_end=last.verified_flat_at_end,
            best_day_profit=best_day_profit,
            positive_days_profit=positive_days_profit,
        ),
    )


def wilson_interval(
    successes: int,
    trials: int,
    *,
    confidence: float = 0.95,
) -> WilsonInterval:
    """Return a two-sided Wilson score interval without a normal approximation at edges."""

    if trials <= 0:
        raise ValueError("trials must be positive")
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between zero and trials")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")

    probability = successes / trials
    z_score = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    z_squared = z_score * z_score
    denominator = 1.0 + z_squared / trials
    centre = (probability + z_squared / (2.0 * trials)) / denominator
    half_width = (
        z_score
        * (
            probability * (1.0 - probability) / trials
            + z_squared / (4.0 * trials * trials)
        )
        ** 0.5
        / denominator
    )
    return WilsonInterval(
        estimate=probability,
        lower=max(0.0, centre - half_width),
        upper=min(1.0, centre + half_width),
        successes=successes,
        trials=trials,
        confidence=confidence,
    )


_UINT64_MASK = (1 << 64) - 1
_SPLITMIX_INCREMENT = 0x9E3779B97F4A7C15
_SPLITMIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
_SPLITMIX_MULTIPLIER_2 = 0x94D049BB133111EB
_PATH_MIX = 0xD2B74407B1CE6E93
_TIME_MIX = 0xCA5A826395121157
_BLOCK_MIX = 0xA24BAED4963EE407
_CHOICE_STREAM = 0x9FB21C651E98DF25
_RESTART_STREAM = 0xC13FA9A902A6328F


def _normalise_block_lengths(values: Sequence[int]) -> tuple[int, ...]:
    lengths: list[int] = []
    for value in values:
        integer = int(value)
        if integer != value or integer <= 0:
            raise ValueError("mean_block_lengths must contain positive integers")
        lengths.append(integer)
    result = tuple(sorted(set(lengths)))
    if not result:
        raise ValueError("mean_block_lengths must contain positive integers")
    return result


def make_chunked_bootstrap_spec(
    n_observations: int,
    *,
    n_paths: int = 100_000,
    sample_length: int | None = None,
    mean_block_lengths: Sequence[int] = (5, 10, 21),
    seed: int = 42,
    chunk_size: int = 4_096,
) -> ChunkedBootstrapSpec:
    """Create a path design whose random indices are generated one bounded chunk at a time."""

    if n_observations <= 0:
        raise ValueError("n_observations must be positive")
    if n_observations > np.iinfo(np.int32).max:
        raise ValueError("n_observations exceeds the int32 index representation")
    if n_paths <= 0:
        raise ValueError("n_paths must be positive")
    if seed < 0:
        raise ValueError("seed must be non-negative")
    length = n_observations if sample_length is None else int(sample_length)
    if length <= 0:
        raise ValueError("sample_length must be positive")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return ChunkedBootstrapSpec(
        n_observations=int(n_observations),
        sample_length=length,
        n_paths=int(n_paths),
        seed=int(seed),
        mean_block_lengths=_normalise_block_lengths(mean_block_lengths),
        chunk_size=min(int(chunk_size), int(n_paths)),
    )


def _splitmix64(values: np.ndarray) -> np.ndarray:
    """Vectorised fixed-width SplitMix64 mixer used as a stateless counter PRNG."""

    with np.errstate(over="ignore"):
        mixed = values + np.uint64(_SPLITMIX_INCREMENT)
        mixed = (mixed ^ (mixed >> np.uint64(30))) * np.uint64(
            _SPLITMIX_MULTIPLIER_1
        )
        mixed = (mixed ^ (mixed >> np.uint64(27))) * np.uint64(
            _SPLITMIX_MULTIPLIER_2
        )
    return mixed ^ (mixed >> np.uint64(31))


def _stationary_index_chunk(
    spec: ChunkedBootstrapSpec,
    mean_block_length: int,
    *,
    path_offset: int,
    path_count: int,
) -> np.ndarray:
    """Generate one deterministic ``(paths, days)`` int32 stationary-bootstrap chunk."""

    if mean_block_length not in spec.mean_block_lengths:
        raise KeyError(
            f"mean block length {mean_block_length} is not present in this design"
        )
    if path_offset < 0 or path_count <= 0 or path_offset + path_count > spec.n_paths:
        raise ValueError("requested path chunk is outside the bootstrap design")

    path_ids = np.arange(
        path_offset, path_offset + path_count, dtype=np.uint64
    )
    with np.errstate(over="ignore"):
        base = path_ids * np.uint64(_PATH_MIX)
    base ^= np.uint64(spec.seed & _UINT64_MASK)
    base ^= np.uint64((mean_block_length * _BLOCK_MIX) & _UINT64_MASK)

    result = np.empty((path_count, spec.sample_length), dtype=np.int32)
    current = np.empty(path_count, dtype=np.int32)
    restart_threshold = (1 << 64) // mean_block_length

    for step in range(spec.sample_length):
        key = base ^ np.uint64((step * _TIME_MIX) & _UINT64_MASK)
        choices = _splitmix64(key ^ np.uint64(_CHOICE_STREAM))
        selected = (choices % np.uint64(spec.n_observations)).astype(
            np.int32, copy=False
        )
        if step == 0:
            current[:] = selected
        else:
            current += 1
            current[current == spec.n_observations] = 0
            if mean_block_length == 1:
                current[:] = selected
            else:
                restarts = _splitmix64(key ^ np.uint64(_RESTART_STREAM)) < np.uint64(
                    restart_threshold
                )
                current[restarts] = selected[restarts]
        result[:, step] = current
    return result


def iter_stationary_bootstrap_index_chunks(
    spec: ChunkedBootstrapSpec,
    mean_block_length: int,
) -> Iterator[np.ndarray]:
    """Yield deterministic common paths using at most ``spec.chunk_size`` rows of memory.

    Random values are a pure function of seed, block length, global path number, and day
    number.  Results are therefore invariant to chunk size and can be regenerated for every
    candidate or scenario without storing a multi-gigabyte plan.
    """

    for offset in range(0, spec.n_paths, spec.chunk_size):
        count = min(spec.chunk_size, spec.n_paths - offset)
        indices = _stationary_index_chunk(
            spec,
            mean_block_length,
            path_offset=offset,
            path_count=count,
        )
        indices.setflags(write=False)
        yield indices


def make_stationary_bootstrap_plan(
    n_observations: int,
    *,
    n_paths: int = 1_000,
    sample_length: int | None = None,
    mean_block_lengths: Sequence[int] = (5, 10, 21),
    seed: int = 42,
    max_materialized_indices: int | None = 5_000_000,
) -> BootstrapPlan:
    """Materialize circular stationary-bootstrap paths for small reference runs.

    After the first uniformly selected row, a block continues to the next chronological row
    (wrapping at the sample end) with probability ``1 - 1 / mean_block_length``.  Otherwise
    a new row is selected uniformly.  Large production runs must use
    :func:`make_chunked_bootstrap_spec`; the default guard prevents accidental construction
    of millions of heavyweight Python integer objects.
    """

    spec = make_chunked_bootstrap_spec(
        n_observations,
        n_paths=n_paths,
        sample_length=sample_length,
        mean_block_lengths=mean_block_lengths,
        seed=seed,
        chunk_size=min(n_paths, 4_096),
    )
    materialized_count = spec.n_paths * spec.sample_length * len(
        spec.mean_block_lengths
    )
    if (
        max_materialized_indices is not None
        and materialized_count > max_materialized_indices
    ):
        raise ValueError(
            f"refusing to materialize {materialized_count:,} Python indices; use "
            "make_chunked_bootstrap_spec/chunked_synchronized_funded_bootstrap"
        )

    blocks: list[BootstrapPaths] = []
    for mean_length in spec.mean_block_lengths:
        paths: list[tuple[int, ...]] = []
        for chunk in iter_stationary_bootstrap_index_chunks(spec, mean_length):
            paths.extend(tuple(int(index) for index in row) for row in chunk)
        blocks.append(BootstrapPaths(mean_length, tuple(paths)))

    return BootstrapPlan(
        n_observations=spec.n_observations,
        sample_length=spec.sample_length,
        n_paths=spec.n_paths,
        seed=spec.seed,
        blocks=tuple(blocks),
    )


def resample_day_records(
    records: Sequence[DayRecord], indices: Sequence[int]
) -> tuple[DayRecord, ...]:
    """Select whole immutable day rows, preserving every within-row dependency."""

    days = tuple(records)
    if not days:
        raise ValueError("records must not be empty")
    selected: list[DayRecord] = []
    for index in indices:
        if not 0 <= index < len(days):
            raise IndexError(f"bootstrap row index out of range: {index}")
        selected.append(days[index])
    return tuple(selected)


def _validate_sizing_mode(value: BootstrapSizingMode) -> BootstrapSizingMode:
    if value not in (
        "conservative_buffer", "fixed_initial", "min_equity_initial", "compound"
    ):
        raise ValueError(f"unsupported bootstrap sizing mode: {value!r}")
    return value


def _scalar_bootstrap_sizing_base(
    mode: BootstrapSizingMode,
    running_balance: float,
    running_equity: float,
    peak_eod_balance: float,
    rules: FundedRules,
) -> float:
    if mode == "fixed_initial":
        return rules.initial_balance
    if mode == "min_equity_initial":
        return max(0.0, min(running_equity, rules.initial_balance))
    if mode == "compound":
        return running_equity
    max_floor = _max_loss_floor(rules, peak_eod_balance)
    daily_basis = (
        rules.initial_balance
        if rules.daily_loss_basis == "initial_balance"
        else running_balance
    )
    daily_floor = running_balance - daily_basis * rules.daily_loss_pct
    remaining_daily_buffer = max(0.0, running_equity - daily_floor)
    remaining_max_buffer = max(0.0, running_equity - max_floor)
    return max(
        0.0,
        min(
            running_equity,
            rules.initial_balance,
            remaining_daily_buffer,
            remaining_max_buffer,
        ),
    )


def _rebase_sampled_days(
    source_days: Sequence[DayRecord],
    indices: Sequence[int],
    rules: FundedRules,
    sizing_mode: BootstrapSizingMode,
) -> tuple[DayRecord, ...]:
    """Apply sampled whole-day shocks using an explicit simulated risk-sizing base."""

    return tuple(
        _iter_rebased_sampled_days(source_days, indices, rules, sizing_mode)
    )


def _bootstrap_session_timestamp(
    first_session: date,
    ordinal: int,
    rules: FundedRules,
) -> tuple[date, pd.Timestamp]:
    """Build one timestamp per distinct firm-local session, including across DST."""

    session = first_session + timedelta(days=ordinal)
    session_start = pd.Timestamp(datetime.combine(session, rules.session_rollover))
    timezone = ZoneInfo(rules.session_timezone)
    for hours_after_rollover in (12, 8, 16, 4, 20):
        naive = session_start + pd.Timedelta(hours=hours_after_rollover)
        try:
            timestamp = naive.tz_localize(
                timezone,
                ambiguous=True,
                nonexistent="raise",
            )
        except ValueError:
            continue
        if firm_session_key(
            timestamp,
            timezone=rules.session_timezone,
            rollover=rules.session_rollover,
        ) == session:
            return session, timestamp
    raise ValueError(
        f"cannot construct an unambiguous timestamp for firm session {session}"
    )


def _validate_bootstrap_session_horizon(
    first_session: date,
    sample_length: int,
    rules: FundedRules,
) -> None:
    """Fail closed when any synthetic firm session has no valid boundary."""

    for ordinal in range(sample_length):
        _bootstrap_session_timestamp(first_session, ordinal, rules)


def _iter_rebased_sampled_days(
    source_days: Sequence[DayRecord],
    indices: Iterable[int],
    rules: FundedRules,
    sizing_mode: BootstrapSizingMode,
) -> Iterator[DayRecord]:
    """Yield rebased days lazily so terminal replay never builds post-breach state."""

    days = tuple(source_days)
    if not days:
        raise ValueError("source_days must not be empty")
    running_balance = rules.initial_balance
    running_equity = rules.initial_balance
    peak_eod_balance = rules.initial_balance
    first_session = days[0].session

    for ordinal, index in enumerate(indices):
        if not 0 <= index < len(days):
            raise IndexError(f"bootstrap row index out of range: {index}")
        source = days[index]
        sizing_base = _scalar_bootstrap_sizing_base(
            sizing_mode,
            running_balance,
            running_equity,
            peak_eod_balance,
            rules,
        )
        scale = sizing_base / float(source.source_risk_base)
        session, timestamp = _bootstrap_session_timestamp(
            first_session, ordinal, rules
        )
        path: tuple[EquityPoint, ...]
        if source.equity_path:
            point_count = len(source.equity_path)
            path = tuple(
                EquityPoint(
                    timestamp
                    - pd.Timedelta(microseconds=point_count - point_position - 1),
                    running_balance
                    + (point.balance - source.day_start_balance) * scale,
                    running_equity
                    + (point.equity - float(source.day_start_equity)) * scale,
                )
                for point_position, point in enumerate(source.equity_path)
            )
        else:
            path = ()
        end_balance = running_balance + (
            source.end_balance - source.day_start_balance
        ) * scale
        end_equity = running_equity + (
            source.end_equity - float(source.day_start_equity)
        ) * scale
        # A source-day flat attestation does not automatically survive rebasing:
        # independently sampled preceding days can leave a different floating
        # balance/equity gap.  Drop the attestation rather than inventing flatness.
        rebased_verified_flat = bool(
            source.verified_flat_at_end
            and _cash_values_equal(end_balance, end_equity)
        )
        rebased = DayRecord(
            session=session,
            timestamp=timestamp,
            day_start_balance=running_balance,
            intraday_min_equity=running_equity
            + (
                source.intraday_min_equity - float(source.day_start_equity)
            )
            * scale,
            end_balance=end_balance,
            end_equity=end_equity,
            closed_pnl=source.closed_pnl * scale,
            day_start_equity=running_equity,
            source_risk_base=sizing_base,
            intraday_min_timestamp=timestamp,
            equity_path=path,
            verified_flat_at_end=rebased_verified_flat,
            positions_opened=source.positions_opened,
        )
        running_balance = end_balance
        running_equity = rebased.end_equity
        peak_eod_balance = max(peak_eod_balance, end_balance)
        yield rebased


def bootstrap_funded_replay(
    data: EventInput,
    rules: FundedRules,
    plan: BootstrapPlan,
    *,
    sizing_mode: BootstrapSizingMode = "conservative_buffer",
) -> BootstrapReport:
    """Replay all small/reference paths with explicit funded-account sizing semantics."""

    sizing_mode = _validate_sizing_mode(sizing_mode)
    records = to_day_records(data, rules)
    if len(records) != plan.n_observations:
        raise ValueError(
            f"plan expects {plan.n_observations} observations, got {len(records)}"
        )
    _validate_bootstrap_session_horizon(
        records[0].session, plan.sample_length, rules
    )

    block_results: list[BootstrapBlockResult] = []
    for block in plan.blocks:
        passed = 0
        breached = 0
        pass_sessions: list[int] = []
        reasons: dict[str, int] = {}
        for indices in block.paths:
            path = _iter_rebased_sampled_days(records, indices, rules, sizing_mode)
            result = _replay_day_records(path, rules)
            if result.status == "passed":
                passed += 1
                pass_sessions.append(result.sessions_processed)
            elif result.status == "breached":
                breached += 1
                reasons[result.reason] = reasons.get(result.reason, 0) + 1
        survived = plan.n_paths - breached
        block_results.append(
            BootstrapBlockResult(
                mean_block_length=block.mean_block_length,
                pass_probability=wilson_interval(passed, plan.n_paths),
                breach_probability=wilson_interval(breached, plan.n_paths),
                survival_probability=wilson_interval(survived, plan.n_paths),
                median_sessions_to_pass=(
                    float(median(pass_sessions)) if pass_sessions else None
                ),
                breach_reasons=tuple(sorted(reasons.items())),
            )
        )
    return BootstrapReport(
        plan=plan, blocks=tuple(block_results), sizing_mode=sizing_mode
    )


@dataclass(frozen=True, slots=True)
class _DayRatioArrays:
    intraday_min: np.ndarray
    end_balance: np.ndarray
    end_equity: np.ndarray
    closed_pnl: np.ndarray
    equity_path: np.ndarray
    verified_flat_at_end: np.ndarray
    positions_opened: np.ndarray


@dataclass(frozen=True, slots=True)
class _ChunkOutcomes:
    status: np.ndarray
    terminal_session: np.ndarray


class _BootstrapAccumulator:
    """Constant-memory frequency accumulator for one strategy/block length."""

    __slots__ = (
        "breached",
        "daily_breaches",
        "max_breaches",
        "pass_session_histogram",
        "passed",
        "trials",
    )

    def __init__(self, sample_length: int) -> None:
        self.trials = 0
        self.passed = 0
        self.breached = 0
        self.daily_breaches = 0
        self.max_breaches = 0
        self.pass_session_histogram = np.zeros(sample_length + 1, dtype=np.int64)

    def update(self, outcome: _ChunkOutcomes) -> None:
        status = outcome.status
        self.trials += int(status.size)
        pass_mask = status == 1
        daily_mask = status == 2
        max_mask = status == 3
        passed = int(np.count_nonzero(pass_mask))
        daily = int(np.count_nonzero(daily_mask))
        maximum = int(np.count_nonzero(max_mask))
        self.passed += passed
        self.daily_breaches += daily
        self.max_breaches += maximum
        self.breached += daily + maximum
        if passed:
            self.pass_session_histogram += np.bincount(
                outcome.terminal_session[pass_mask],
                minlength=self.pass_session_histogram.size,
            )[: self.pass_session_histogram.size]

    def median_pass_session(self) -> float | None:
        if self.passed == 0:
            return None
        cumulative = np.cumsum(self.pass_session_histogram)
        lower_rank = (self.passed - 1) // 2 + 1
        upper_rank = self.passed // 2 + 1
        lower = int(np.searchsorted(cumulative, lower_rank, side="left"))
        upper = int(np.searchsorted(cumulative, upper_rank, side="left"))
        return (lower + upper) / 2.0

    def result(self, mean_block_length: int) -> BootstrapBlockResult:
        survived = self.trials - self.breached
        reasons: list[tuple[str, int]] = []
        if self.daily_breaches:
            reasons.append(("daily_loss", self.daily_breaches))
        if self.max_breaches:
            reasons.append(("max_loss", self.max_breaches))
        return BootstrapBlockResult(
            mean_block_length=mean_block_length,
            pass_probability=wilson_interval(self.passed, self.trials),
            breach_probability=wilson_interval(self.breached, self.trials),
            survival_probability=wilson_interval(survived, self.trials),
            median_sessions_to_pass=self.median_pass_session(),
            breach_reasons=tuple(reasons),
        )


def _day_ratio_arrays(records: Sequence[DayRecord]) -> _DayRatioArrays:
    balance_starts = np.asarray(
        [record.day_start_balance for record in records], dtype=float
    )
    equity_starts = np.asarray(
        [float(record.day_start_equity) for record in records], dtype=float
    )
    intraday_minima = np.asarray(
        [record.intraday_min_equity for record in records], dtype=float
    )
    risk_bases = np.asarray(
        [float(record.source_risk_base) for record in records], dtype=float
    )
    if np.any(risk_bases <= 0.0) or not np.isfinite(risk_bases).all():
        raise ValueError("bootstrap source risk bases must be finite and positive")
    path_ratios: list[np.ndarray] = []
    for record, equity_start, risk_base in zip(
        records, equity_starts, risk_bases, strict=True
    ):
        if record.equity_path:
            ratios = np.asarray(
                [
                    (point.equity - equity_start) / risk_base
                    for point in record.equity_path
                ],
                dtype=float,
            )
        else:
            ratios = np.asarray(
                [(record.intraday_min_equity - equity_start) / risk_base],
                dtype=float,
            )
        path_ratios.append(ratios)
    maximum_path_length = max(len(path) for path in path_ratios)
    padded_paths = np.full(
        (len(path_ratios), maximum_path_length), np.nan, dtype=float
    )
    for row, path in enumerate(path_ratios):
        padded_paths[row, : len(path)] = path
    return _DayRatioArrays(
        intraday_min=(intraday_minima - equity_starts) / risk_bases,
        end_balance=(
            np.asarray([record.end_balance for record in records], dtype=float)
            - balance_starts
        )
        / risk_bases,
        end_equity=(
            np.asarray([record.end_equity for record in records], dtype=float)
            - equity_starts
        )
        / risk_bases,
        closed_pnl=np.asarray([record.closed_pnl for record in records], dtype=float)
        / risk_bases,
        equity_path=padded_paths,
        verified_flat_at_end=np.asarray(
            [record.verified_flat_at_end for record in records], dtype=bool
        ),
        positions_opened=np.asarray(
            [
                record.positions_opened is not None
                and record.positions_opened > 0
                for record in records
            ],
            dtype=bool,
        ),
    )


def _ordered_vector_breach_masks(
    *,
    active: np.ndarray,
    selected: np.ndarray,
    running_equity: np.ndarray,
    sizing_base: np.ndarray,
    daily_floor: np.ndarray,
    max_floor: np.ndarray,
    ratio_sets: Sequence[_DayRatioArrays],
) -> tuple[np.ndarray, np.ndarray]:
    """Classify each active path by the first ordered live-equity floor crossed.

    A single intraday minimum cannot reveal whether the tighter max floor was crossed before
    a later daily-floor breach.  The padded source paths preserve order while processing one
    case at a time to keep temporary memory bounded by one bootstrap chunk.
    """

    daily_first = np.zeros_like(active)
    max_first = np.zeros_like(active)
    for case_position, ratios in enumerate(ratio_sets):
        path_positions = np.flatnonzero(active[case_position])
        if path_positions.size == 0:
            continue
        ordered_ratios = ratios.equity_path[selected[path_positions]]
        valid = ~np.isnan(ordered_ratios)
        point_equity = (
            running_equity[case_position, path_positions, None]
            + sizing_base[case_position, path_positions, None] * ordered_ratios
        )
        daily_crossed = valid & (
            point_equity <= daily_floor[case_position, path_positions, None]
        )
        max_crossed = valid & (
            point_equity <= max_floor[case_position, path_positions, None]
        )
        crossed = daily_crossed | max_crossed
        has_crossed = np.any(crossed, axis=1)
        if not np.any(has_crossed):
            continue
        crossed_rows = np.flatnonzero(has_crossed)
        first_positions = np.argmax(crossed[has_crossed], axis=1)
        terminal_paths = path_positions[has_crossed]
        # Scalar replay gives daily loss precedence only when the same first print crosses
        # both floors.  A prior max-only print must remain a max-loss breach.
        first_is_daily = daily_crossed[crossed_rows, first_positions]
        daily_first[case_position, terminal_paths[first_is_daily]] = True
        max_first[case_position, terminal_paths[~first_is_daily]] = True
    return daily_first, max_first


def _simulate_synchronized_index_chunk(
    indices: np.ndarray,
    ratio_sets: Sequence[_DayRatioArrays],
    rule_sets: Sequence[FundedRules],
    sizing_modes: Sequence[BootstrapSizingMode],
) -> tuple[_ChunkOutcomes, ...]:
    """Vectorized first-terminal-state replay for several aligned cases."""

    if indices.ndim != 2:
        raise ValueError("bootstrap indices must be a two-dimensional matrix")
    case_count = len(ratio_sets)
    if (
        case_count == 0
        or len(rule_sets) != case_count
        or len(sizing_modes) != case_count
    ):
        raise ValueError(
            "ratio, rule, and sizing-mode sets must contain the same non-zero case count"
        )
    path_count, sample_length = indices.shape
    if path_count == 0 or sample_length == 0:
        raise ValueError("bootstrap index chunks must not be empty")

    end_balance_ratios = np.stack([ratios.end_balance for ratios in ratio_sets])
    end_equity_ratios = np.stack([ratios.end_equity for ratios in ratio_sets])
    closed_pnl_ratios = np.stack([ratios.closed_pnl for ratios in ratio_sets])
    verified_flat_flags = np.stack(
        [ratios.verified_flat_at_end for ratios in ratio_sets]
    )
    positions_opened_flags = np.stack(
        [ratios.positions_opened for ratios in ratio_sets]
    )

    initial = np.asarray(
        [rules.initial_balance for rules in rule_sets], dtype=float
    )[:, None]
    daily_pct = np.asarray(
        [rules.daily_loss_pct for rules in rule_sets], dtype=float
    )[:, None]
    daily_uses_initial = np.asarray(
        [rules.daily_loss_basis == "initial_balance" for rules in rule_sets],
        dtype=bool,
    )[:, None]
    max_loss_pct = np.asarray(
        [rules.max_loss_pct for rules in rule_sets], dtype=float
    )[:, None]
    max_loss_amount = initial * max_loss_pct
    trailing = np.asarray(
        [rules.max_loss_mode == "eod_trailing" for rules in rule_sets], dtype=bool
    )[:, None]
    caps = np.asarray(
        [
            np.nan
            if rules.eod_trailing_floor_cap is None
            else rules.eod_trailing_floor_cap
            for rules in rule_sets
        ],
        dtype=float,
    )[:, None]
    targets = initial * np.asarray(
        [
            np.nan if rules.profit_target_pct is None else rules.profit_target_pct
            for rules in rule_sets
        ],
        dtype=float,
    )[:, None]
    minimum_trading_days = np.asarray(
        [rules.minimum_trading_days for rules in rule_sets], dtype=np.int32
    )[:, None]
    consistency_shares = np.asarray(
        [
            np.nan
            if rules.best_day_max_profit_share is None
            else rules.best_day_max_profit_share
            for rules in rule_sets
        ],
        dtype=float,
    )[:, None]
    consistency_uses_positive_days = np.asarray(
        [rules.best_day_profit_basis == "positive_days" for rules in rule_sets],
        dtype=bool,
    )[:, None]
    fixed_initial_sizing = np.asarray(
        [mode == "fixed_initial" for mode in sizing_modes], dtype=bool
    )[:, None]
    min_equity_initial_sizing = np.asarray(
        [mode == "min_equity_initial" for mode in sizing_modes], dtype=bool
    )[:, None]
    compound_sizing = np.asarray(
        [mode == "compound" for mode in sizing_modes], dtype=bool
    )[:, None]

    running_balance = np.broadcast_to(initial, (case_count, path_count)).copy()
    running_equity = running_balance.copy()
    peak_eod_balance = running_balance.copy()
    best_day_profit = np.zeros_like(running_balance)
    positive_days_profit = np.zeros_like(running_balance)
    trading_days = np.zeros_like(running_balance, dtype=np.int32)
    active = np.ones((case_count, path_count), dtype=bool)
    # 0 = active/data exhausted, 1 = pass, 2 = daily breach, 3 = max-loss breach.
    status = np.zeros((case_count, path_count), dtype=np.int8)
    terminal_session = np.zeros((case_count, path_count), dtype=np.int32)

    def current_max_floor() -> np.ndarray:
        static_floor = initial - max_loss_amount
        floor = np.where(trailing, peak_eod_balance - max_loss_amount, static_floor)
        return np.where(np.isnan(caps), floor, np.minimum(floor, caps))

    for session_position in range(sample_length):
        selected = indices[:, session_position]
        max_floor = current_max_floor()
        daily_basis = np.where(daily_uses_initial, initial, running_balance)
        daily_floor = running_balance - daily_basis * daily_pct
        remaining_daily_buffer = np.maximum(running_equity - daily_floor, 0.0)
        remaining_max_buffer = np.maximum(running_equity - max_floor, 0.0)
        conservative_base = np.maximum(
            0.0,
            np.minimum(
                np.minimum(
                    np.minimum(running_equity, initial),
                    remaining_daily_buffer,
                ),
                remaining_max_buffer,
            ),
        )
        sizing_base = np.where(
            fixed_initial_sizing,
            initial,
            np.where(
                min_equity_initial_sizing,
                np.maximum(0.0, np.minimum(running_equity, initial)),
                np.where(compound_sizing, running_equity, conservative_base),
            ),
        )
        end_balance = (
            running_balance + sizing_base * end_balance_ratios[:, selected]
        )
        end_equity = running_equity + sizing_base * end_equity_ratios[:, selected]
        closed_pnl = sizing_base * closed_pnl_ratios[:, selected]

        daily_breach, max_breach = _ordered_vector_breach_masks(
            active=active,
            selected=selected,
            running_equity=running_equity,
            sizing_base=sizing_base,
            daily_floor=daily_floor,
            max_floor=max_floor,
            ratio_sets=ratio_sets,
        )
        status[daily_breach] = 2
        terminal_session[daily_breach] = session_position + 1
        active[daily_breach] = False

        status[max_breach] = 3
        terminal_session[max_breach] = session_position + 1
        active[max_breach] = False

        # These state transitions happen only for paths that survived the live-equity checks.
        positive_day_profit = np.maximum(closed_pnl, 0.0)
        best_day_profit = np.where(
            active,
            np.maximum(best_day_profit, positive_day_profit),
            best_day_profit,
        )
        positive_days_profit = np.where(
            active,
            positive_days_profit + positive_day_profit,
            positive_days_profit,
        )
        trading_days = np.where(
            active & positions_opened_flags[:, selected],
            trading_days + 1,
            trading_days,
        )
        peak_eod_balance = np.where(
            active, np.maximum(peak_eod_balance, end_balance), peak_eod_balance
        )

        advanced_floor = current_max_floor()
        eod_breach = active & (end_equity <= advanced_floor)
        status[eod_breach] = 3
        terminal_session[eod_breach] = session_position + 1
        active[eod_breach] = False

        total_profit = end_balance - initial
        flat_tolerance = np.maximum(
            1e-8,
            np.maximum(np.abs(end_balance), np.abs(end_equity)) * 1e-10,
        )
        verified_flat_at_end = (
            verified_flat_flags[:, selected]
            & (np.abs(end_balance - end_equity) <= flat_tolerance)
        )
        target_met = (
            (~np.isnan(targets))
            & (total_profit >= targets)
            & verified_flat_at_end
            & ((end_equity - initial) >= targets)
        )
        consistency_denominator = np.where(
            consistency_uses_positive_days, positive_days_profit, total_profit
        )
        consistency_met = np.isnan(consistency_shares) | (
            (consistency_denominator > 0.0)
            & (
                best_day_profit
                <= consistency_shares * consistency_denominator + 1e-12
            )
        )
        minimum_trading_days_met = trading_days >= minimum_trading_days
        passed = active & target_met & consistency_met & minimum_trading_days_met
        status[passed] = 1
        terminal_session[passed] = session_position + 1
        active[passed] = False

        # Terminal paths are never read again; update only paths still under evaluation.
        running_balance = np.where(active, end_balance, running_balance)
        running_equity = np.where(active, end_equity, running_equity)
        if not np.any(active):
            break

    return tuple(
        _ChunkOutcomes(status[row], terminal_session[row])
        for row in range(case_count)
    )


def chunked_synchronized_funded_bootstrap(
    strategies: Mapping[str, EventInput],
    rules: FundedRules | Mapping[str, FundedRules],
    *,
    sizing_mode: BootstrapSizingMode | Mapping[str, BootstrapSizingMode] = (
        "conservative_buffer"
    ),
    n_paths: int = 100_000,
    sample_length: int | None = None,
    mean_block_lengths: Sequence[int] = (5, 10, 21),
    seed: int = 42,
    chunk_size: int = 4_096,
) -> ChunkedSynchronizedBootstrapReport:
    """Memory-bounded synchronized bootstrap for production-sized research runs.

    Each strategy (or strategy/rule scenario represented by a distinct mapping key) is
    evaluated on the same index chunk before that chunk is discarded.  Working index memory
    is therefore ``chunk_size * sample_length * 4`` bytes rather than proportional to all
    paths, while vectorized state arrays preserve first-terminal breach/pass ordering.

    The default sizing base is literally ``min(current equity, initial balance, distance to
    the daily-loss floor, distance to the overall max-loss floor)``.  ``fixed_initial`` holds
    source risk constant at initial capital; ``min_equity_initial`` scales down below the
    seed without compounding above it; ``compound`` is available only as an explicit
    non-conservative choice.  All are day-level sensitivities when positions span days,
    because a sampled row cannot recover each lot's originating sizing base.
    """

    if not strategies:
        raise ValueError("at least one strategy is required")

    normalised: list[
        tuple[str, tuple[DayRecord, ...], FundedRules, BootstrapSizingMode]
    ] = []
    for name, data in strategies.items():
        strategy_rules = rules[name] if isinstance(rules, Mapping) else rules
        strategy_sizing = (
            sizing_mode[name] if isinstance(sizing_mode, Mapping) else sizing_mode
        )
        normalised.append(
            (
                name,
                to_day_records(data, strategy_rules),
                strategy_rules,
                _validate_sizing_mode(strategy_sizing),
            )
        )

    reference_sessions = tuple(record.session for record in normalised[0][1])
    for name, records, _, _ in normalised[1:]:
        if tuple(record.session for record in records) != reference_sessions:
            raise ValueError(
                f"strategy {name!r} is not session-aligned with {normalised[0][0]!r}"
            )

    spec = make_chunked_bootstrap_spec(
        len(reference_sessions),
        n_paths=n_paths,
        sample_length=sample_length,
        mean_block_lengths=mean_block_lengths,
        seed=seed,
        chunk_size=chunk_size,
    )
    for _, records, strategy_rules, _ in normalised:
        _validate_bootstrap_session_horizon(
            records[0].session, spec.sample_length, strategy_rules
        )
    ratio_sets = tuple(_day_ratio_arrays(records) for _, records, _, _ in normalised)
    rule_sets = tuple(strategy_rules for _, _, strategy_rules, _ in normalised)
    sizing_modes = tuple(mode for _, _, _, mode in normalised)
    results_by_name: dict[str, list[BootstrapBlockResult]] = {
        name: [] for name, _, _, _ in normalised
    }

    for mean_length in spec.mean_block_lengths:
        accumulators = [
            _BootstrapAccumulator(spec.sample_length) for _ in normalised
        ]
        for indices in iter_stationary_bootstrap_index_chunks(spec, mean_length):
            outcomes = _simulate_synchronized_index_chunk(
                indices, ratio_sets, rule_sets, sizing_modes
            )
            for accumulator, outcome in zip(accumulators, outcomes, strict=True):
                accumulator.update(outcome)
        for (name, _, _, _), accumulator in zip(
            normalised, accumulators, strict=True
        ):
            if accumulator.trials != spec.n_paths:
                raise RuntimeError("bootstrap path accounting mismatch")
            results_by_name[name].append(accumulator.result(mean_length))

    reports = tuple(
        ChunkedStrategyBootstrapReport(
            name=name,
            report=ChunkedBootstrapReport(
                spec=spec,
                blocks=tuple(results_by_name[name]),
                sizing_mode=mode,
            ),
        )
        for name, _, _, mode in normalised
    )
    return ChunkedSynchronizedBootstrapReport(spec=spec, strategies=reports)


def chunked_bootstrap_funded_replay(
    data: EventInput,
    rules: FundedRules,
    *,
    sizing_mode: BootstrapSizingMode = "conservative_buffer",
    n_paths: int = 100_000,
    sample_length: int | None = None,
    mean_block_lengths: Sequence[int] = (5, 10, 21),
    seed: int = 42,
    chunk_size: int = 4_096,
) -> ChunkedBootstrapReport:
    """Single-strategy convenience wrapper around the synchronized chunked engine."""

    synchronized = chunked_synchronized_funded_bootstrap(
        {"strategy": data},
        rules,
        sizing_mode=sizing_mode,
        n_paths=n_paths,
        sample_length=sample_length,
        mean_block_lengths=mean_block_lengths,
        seed=seed,
        chunk_size=chunk_size,
    )
    return synchronized.strategies[0].report


def synchronized_funded_bootstrap(
    strategies: Mapping[str, EventInput],
    rules: FundedRules | Mapping[str, FundedRules],
    *,
    sizing_mode: BootstrapSizingMode | Mapping[str, BootstrapSizingMode] = (
        "conservative_buffer"
    ),
    plan: BootstrapPlan | None = None,
    n_paths: int = 1_000,
    sample_length: int | None = None,
    mean_block_lengths: Sequence[int] = (5, 10, 21),
    seed: int = 42,
) -> SynchronizedBootstrapReport:
    """Evaluate aligned strategies with identical stationary-bootstrap index paths."""

    if not strategies:
        raise ValueError("at least one strategy is required")

    normalised: list[
        tuple[str, tuple[DayRecord, ...], FundedRules, BootstrapSizingMode]
    ] = []
    for name, data in strategies.items():
        strategy_rules = rules[name] if isinstance(rules, Mapping) else rules
        strategy_sizing = (
            sizing_mode[name] if isinstance(sizing_mode, Mapping) else sizing_mode
        )
        normalised.append(
            (
                name,
                to_day_records(data, strategy_rules),
                strategy_rules,
                _validate_sizing_mode(strategy_sizing),
            )
        )

    reference_sessions = tuple(record.session for record in normalised[0][1])
    for name, records, _, _ in normalised[1:]:
        sessions = tuple(record.session for record in records)
        if sessions != reference_sessions:
            raise ValueError(
                f"strategy {name!r} is not session-aligned with {normalised[0][0]!r}"
            )

    common_plan = plan or make_stationary_bootstrap_plan(
        len(reference_sessions),
        n_paths=n_paths,
        sample_length=sample_length,
        mean_block_lengths=mean_block_lengths,
        seed=seed,
    )
    if common_plan.n_observations != len(reference_sessions):
        raise ValueError(
            f"plan expects {common_plan.n_observations} sessions, "
            f"but strategies have {len(reference_sessions)}"
        )

    reports = tuple(
        StrategyBootstrapReport(
            name=name,
            report=bootstrap_funded_replay(
                records,
                strategy_rules,
                common_plan,
                sizing_mode=strategy_sizing,
            ),
        )
        for name, records, strategy_rules, strategy_sizing in normalised
    )
    return SynchronizedBootstrapReport(plan=common_plan, strategies=reports)


__all__ = [
    "BestDayProfitBasis",
    "BootstrapBlockResult",
    "BootstrapPaths",
    "BootstrapPlan",
    "BootstrapReport",
    "BootstrapSizingMode",
    "ChunkedBootstrapReport",
    "ChunkedBootstrapSpec",
    "ChunkedStrategyBootstrapReport",
    "ChunkedSynchronizedBootstrapReport",
    "DayRecord",
    "EquityEvent",
    "EquityPoint",
    "FundedRules",
    "ReplayResult",
    "RuleMargins",
    "StrategyBootstrapReport",
    "SynchronizedBootstrapReport",
    "WilsonInterval",
    "aggregate_equity_events",
    "bootstrap_funded_replay",
    "chunked_bootstrap_funded_replay",
    "chunked_synchronized_funded_bootstrap",
    "firm_session_key",
    "iter_stationary_bootstrap_index_chunks",
    "make_chunked_bootstrap_spec",
    "make_stationary_bootstrap_plan",
    "replay_funded_rules",
    "resample_day_records",
    "synchronized_funded_bootstrap",
    "to_day_records",
    "wilson_interval",
]
