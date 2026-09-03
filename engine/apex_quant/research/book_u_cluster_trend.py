"""Frozen, causal research implementation of Book U cluster trend.

Book U is deliberately separate from the production/paper engines.  It is a
USD-only ETF research portfolio whose rules were frozen before its historical
outcome was inspected.  The implementation therefore favours explicit state
and audit records over clever abstractions:

* a fixed ten-ETF universe and six economic clusters;
* month-end decisions filled at the next common-session open;
* one positive 252-session trend winner per cluster;
* inverse-volatility/covariance sizing to a 6% annual volatility target;
* cost-inclusive per-leg and aggregate stop-loss reservations;
* gap-aware, non-loosening 2.5 x ATR(20) long stops; and
* cash-only accounting with a charged terminal liquidation.

Daily OHLC bars cannot prove executable funded-account behaviour.  This module
is consequently a deterministic research simulator, not a broker adapter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any, Iterable

import exchange_calendars as xcals
import numpy as np
import pandas as pd


USD_ETF_UNIVERSE: tuple[str, ...] = (
    "SPY",
    "QQQ",
    "IWM",
    "XLK",
    "SMH",
    "SOXX",
    "GLD",
    "TLT",
    "XLE",
    "XBI",
)

CLUSTERS: dict[str, str] = {
    "SPY": "broad_equity",
    "QQQ": "broad_equity",
    "IWM": "broad_equity",
    "XLK": "technology",
    "SMH": "technology",
    "SOXX": "technology",
    "GLD": "gold",
    "TLT": "rates",
    "XLE": "energy",
    "XBI": "biotech",
}

CLUSTER_MEMBERS: dict[str, tuple[str, ...]] = {
    "broad_equity": ("SPY", "QQQ", "IWM"),
    "technology": ("XLK", "SMH", "SOXX"),
    "gold": ("GLD",),
    "rates": ("TLT",),
    "energy": ("XLE",),
    "biotech": ("XBI",),
}

_CLUSTER_ORDER = tuple(CLUSTER_MEMBERS)
_PRICE_COLUMNS = ("open", "high", "low", "close")
_TOL = 1e-9
_XNYS = xcals.get_calendar("XNYS")

BOOK_U_SCHEMA_VERSION = "book_u_cluster_trend_run_v2"
BOOK_U_PROTOCOL_SHA256 = "bcf4c94cdd2c1ecf0afa42c558a43c8b29bf1706dfcb7883fcc4b876a1f700cc"
FROZEN_RISK_PAIRS: tuple[tuple[float, float], ...] = (
    (0.0075, 0.0225),
    (0.0085, 0.0255),
    (0.0100, 0.0300),
)
FROZEN_COST_STRESS_PAIRS: tuple[tuple[float, float], ...] = (
    (5.0, 0.0),
    (10.0, 25.0),
)


@dataclass(frozen=True)
class BookUSpec:
    """The frozen architecture plus the declared risk/cost stress cells."""

    name: str = "U075"
    risk_per_leg: float = 0.0075
    aggregate_risk: float = 0.0225
    cost_bps_per_side: float = 5.0
    stop_slippage_bps: float = 0.0
    momentum_lookback: int = 252
    vol_window: int = 63
    atr_window: int = 20
    portfolio_vol_target: float = 0.06
    gross_cap: float = 0.95
    position_cap: float = 0.25
    stop_atr_multiple: float = 2.5

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Book U spec requires a name")
        risk_pair = (float(self.risk_per_leg), float(self.aggregate_risk))
        if risk_pair not in FROZEN_RISK_PAIRS:
            raise ValueError("Book U permits only frozen U075/U085/U100 risk pairs")
        cost_pair = (float(self.cost_bps_per_side), float(self.stop_slippage_bps))
        if cost_pair not in FROZEN_COST_STRESS_PAIRS:
            raise ValueError("Book U permits only frozen base or binding-stress cost pairs")
        frozen = {
            "momentum_lookback": (self.momentum_lookback, 252),
            "vol_window": (self.vol_window, 63),
            "atr_window": (self.atr_window, 20),
            "portfolio_vol_target": (self.portfolio_vol_target, 0.06),
            "gross_cap": (self.gross_cap, 0.95),
            "position_cap": (self.position_cap, 0.25),
            "stop_atr_multiple": (self.stop_atr_multiple, 2.5),
        }
        changed = [name for name, (actual, expected) in frozen.items() if actual != expected]
        if changed:
            raise ValueError("Book U architecture is frozen; changed: " + ", ".join(changed))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BookURun:
    """Auditable output from one independently flat Book U segment."""

    spec: BookUSpec
    start: pd.Timestamp
    end: pd.Timestamp
    equity: pd.Series
    events: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    episodes: list[dict[str, Any]]
    cluster_attribution: dict[str, dict[str, Any]]
    metrics: dict[str, Any]

    def to_dict(self, *, equity_points: int = 512) -> dict[str, Any]:
        step = max(1, int(np.ceil(len(self.equity) / equity_points)))
        return _round_values(
            {
                "spec": self.spec.to_dict(),
                "start": _date_str(self.start),
                "end": _date_str(self.end),
                "metrics": self.metrics,
                "events": self.events,
                "decisions": self.decisions,
                "trace": self.trace,
                "episodes": self.episodes,
                "cluster_attribution": self.cluster_attribution,
                "equity_curve": [
                    {"date": _date_str(date), "equity_usd": float(value)}
                    for date, value in self.equity.iloc[::step].items()
                ],
            }
        )


@dataclass
class _Position:
    instrument: str
    cluster: str
    units: float
    stop_price: float
    average_entry_price: float
    entry_date: pd.Timestamp
    decision_date: pd.Timestamp
    episode_id: int
    episode_pnl_usd: float = 0.0
    episode_cost_usd: float = 0.0


def _utc_timestamp(value: pd.Timestamp | str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _date_str(value: pd.Timestamp | str) -> str:
    return _utc_timestamp(value).strftime("%Y-%m-%d")


def _round_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _round_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_values(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return _date_str(value)
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        return round(float(value), 10)
    if isinstance(value, (int, np.integer)):
        return int(value)
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _round_values(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _panel_hash(panel: dict[str, pd.DataFrame]) -> str:
    digest = sha256()
    for instrument in sorted(panel):
        frame = panel[instrument]
        digest.update(instrument.encode("ascii"))
        digest.update(np.asarray(frame.index.asi8, dtype="<i8").tobytes())
        digest.update(np.asarray(frame.loc[:, list(_PRICE_COLUMNS)], dtype="<f8").tobytes())
    return digest.hexdigest()


def _official_sessions(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    sessions = pd.DatetimeIndex(
        _XNYS.sessions_in_range(start.tz_localize(None), end.tz_localize(None))
    )
    return sessions.tz_localize("UTC") if sessions.tz is None else sessions.tz_convert("UTC")


def validate_book_u_universe(instruments: Iterable[str]) -> tuple[str, ...]:
    """Require the complete frozen whitelist and return canonical ordering."""

    requested = tuple(instruments)
    if len(set(requested)) != len(requested):
        raise ValueError("Book U universe contains duplicate instruments")
    missing = sorted(set(USD_ETF_UNIVERSE) - set(requested))
    unknown = sorted(set(requested) - set(USD_ETF_UNIVERSE))
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("rejected: " + ", ".join(unknown))
        raise ValueError("Book U requires the fixed ten-ETF universe (" + "; ".join(details) + ")")
    return tuple(sorted(USD_ETF_UNIVERSE))


def common_book_u_panel(panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Validate OHLC data and take the strict common-session intersection.

    No forward fill is performed.  The returned dictionary and every later
    execution loop use canonical symbol order, making results independent of
    caller dictionary order.
    """

    instruments = validate_book_u_universe(panel.keys())
    checked: dict[str, pd.DataFrame] = {}
    common: pd.DatetimeIndex | None = None
    for instrument in instruments:
        frame = panel[instrument].copy()
        absent = sorted(set(_PRICE_COLUMNS) - set(frame.columns))
        if absent:
            raise ValueError(f"{instrument} lacks required columns: {', '.join(absent)}")
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError(f"{instrument} has no DatetimeIndex")
        utc_index = (
            frame.index.tz_localize("UTC")
            if frame.index.tz is None
            else frame.index.tz_convert("UTC")
        )
        # Cached daily vendors can label a US session at its 20:00/21:00 UTC
        # close.  Book U operates on session dates, so normalize first and then
        # reject collisions instead of silently choosing one bar.
        frame.index = utc_index.normalize()
        frame = frame.sort_index()
        if frame.index.has_duplicates:
            raise ValueError(f"{instrument} has duplicate session dates after UTC normalization")
        prices = frame.loc[:, list(_PRICE_COLUMNS)].astype(float)
        if not np.isfinite(prices.to_numpy()).all() or (prices <= 0.0).any().any():
            raise ValueError(f"{instrument} contains non-finite or non-positive OHLC values")
        invalid_ohlc = (
            (prices["high"] < prices[["open", "close"]].max(axis=1))
            | (prices["low"] > prices[["open", "close"]].min(axis=1))
            | (prices["high"] < prices["low"])
        )
        if invalid_ohlc.any():
            raise ValueError(f"{instrument} violates OHLC ordering")
        frame.loc[:, _PRICE_COLUMNS] = prices
        checked[instrument] = frame
        common = frame.index if common is None else common.intersection(frame.index)

    assert common is not None
    common = common.sort_values()
    minimum = 1 + max(252, 63, 20)
    if len(common) < minimum:
        raise ValueError(f"common Book U panel has fewer than {minimum} sessions")
    expected = _official_sessions(common[0], common[-1])
    missing_sessions = expected.difference(common)
    unexpected_sessions = common.difference(expected)
    if len(missing_sessions) or len(unexpected_sessions):
        detail: list[str] = []
        if len(missing_sessions):
            detail.append(
                "missing expected XNYS sessions: "
                + ", ".join(_date_str(date) for date in missing_sessions[:5])
            )
        if len(unexpected_sessions):
            detail.append(
                "non-XNYS session dates: "
                + ", ".join(_date_str(date) for date in unexpected_sessions[:5])
            )
        raise ValueError("Book U panel is not a complete official XNYS session span (" + "; ".join(detail) + ")")
    return {instrument: checked[instrument].loc[common].copy() for instrument in instruments}


def _is_month_end(index: pd.DatetimeIndex, i: int) -> bool:
    return i + 1 < len(index) and index[i].month != index[i + 1].month


def _daily_log_returns(close: pd.Series, i: int, window: int) -> pd.Series:
    return np.log(close.iloc[i - window : i + 1].astype(float)).diff().dropna()


def _atr(frame: pd.DataFrame, i: int, window: int) -> float:
    start = i - window + 1
    if start < 1:
        return float("nan")
    window_frame = frame.iloc[start : i + 1]
    previous_close = frame["close"].shift(1).iloc[start : i + 1]
    true_range = pd.concat(
        (
            window_frame["high"] - window_frame["low"],
            (window_frame["high"] - previous_close).abs(),
            (window_frame["low"] - previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    if len(true_range) != window or not np.isfinite(true_range.to_numpy()).all():
        return float("nan")
    return float(true_range.mean())


def select_book_u(panel: dict[str, pd.DataFrame], spec: BookUSpec, i: int) -> list[dict[str, Any]]:
    """Choose one strictly positive trend winner from every eligible cluster."""

    index = next(iter(panel.values())).index
    if i < max(spec.momentum_lookback, spec.vol_window, spec.atr_window) or i >= len(index):
        return []
    selected: list[dict[str, Any]] = []
    for cluster in _CLUSTER_ORDER:
        candidates: list[dict[str, Any]] = []
        for instrument in sorted(CLUSTER_MEMBERS[cluster]):
            frame = panel[instrument]
            close = frame["close"].astype(float)
            momentum = float(close.iloc[i] / close.iloc[i - spec.momentum_lookback] - 1.0)
            daily_returns = _daily_log_returns(close, i, spec.vol_window)
            daily_vol = float(daily_returns.std(ddof=1))
            atr = _atr(frame, i, spec.atr_window)
            if (
                not np.isfinite(momentum)
                or not np.isfinite(daily_vol)
                or daily_vol <= 0.0
                or not np.isfinite(atr)
                or atr <= 0.0
            ):
                continue
            candidates.append(
                {
                    "instrument": instrument,
                    "cluster": cluster,
                    "momentum_252": momentum,
                    "daily_volatility_63": daily_vol,
                    "annualized_volatility_63": daily_vol * np.sqrt(252.0),
                    "score": momentum / daily_vol,
                    "atr_20": atr,
                }
            )
        candidates.sort(key=lambda row: (-float(row["score"]), str(row["instrument"])))
        if candidates and float(candidates[0]["momentum_252"]) > 0.0:
            selected.append(candidates[0])
    return selected


def build_book_u_decision(
    panel: dict[str, pd.DataFrame], spec: BookUSpec, i: int
) -> dict[str, Any]:
    """Build the frozen close-known target weights for one decision session."""

    index = next(iter(panel.values())).index
    if i < 0 or i >= len(index):
        raise IndexError("decision index outside Book U panel")
    selected = select_book_u(panel, spec, i)
    decision: dict[str, Any] = {
        "decision_date": _date_str(index[i]),
        "selected": selected,
        "blocked": [],
        "projected_volatility": 0.0,
        "volatility_scale": 0.0,
        "target_gross_weight": 0.0,
        "covariance_annualized": {},
    }
    if not selected:
        return decision

    symbols = [str(row["instrument"]) for row in selected]
    returns = pd.DataFrame(
        {
            instrument: _daily_log_returns(panel[instrument]["close"], i, spec.vol_window).to_numpy()
            for instrument in symbols
        }
    )
    covariance = returns.cov(ddof=1) * 252.0
    covariance_values = covariance.to_numpy(dtype=float)
    if covariance.shape != (len(symbols), len(symbols)) or not np.isfinite(covariance_values).all():
        decision["blocked"].append("non_finite_covariance")
        decision["selected"] = []
        return decision

    annual_vol = np.asarray(
        [float(row["annualized_volatility_63"]) for row in selected], dtype=float
    )
    inverse_vol = 1.0 / annual_vol
    inverse_vol /= inverse_vol.sum()
    variance = float(inverse_vol @ covariance_values @ inverse_vol)
    if not np.isfinite(variance) or variance <= 0.0:
        decision["blocked"].append("non_positive_projected_variance")
        decision["selected"] = []
        return decision
    projected_volatility = float(np.sqrt(variance))
    gross_scale = min(spec.gross_cap, spec.portfolio_vol_target / projected_volatility)
    weights = np.minimum(inverse_vol * gross_scale, spec.position_cap)
    if not np.isfinite(weights).all():
        decision["blocked"].append("non_finite_target_weight")
        decision["selected"] = []
        return decision

    for row, inverse_weight, target_weight in zip(selected, inverse_vol, weights, strict=True):
        row["inverse_vol_weight"] = float(inverse_weight)
        row["target_weight"] = float(target_weight)
    decision["projected_volatility"] = projected_volatility
    decision["volatility_scale"] = float(gross_scale)
    decision["target_gross_weight"] = float(weights.sum())
    decision["covariance_annualized"] = {
        instrument: {other: float(covariance.loc[instrument, other]) for other in symbols}
        for instrument in symbols
    }
    return decision


def _planned_loss_per_unit(
    entry_price: float,
    stop_price: float,
    *,
    cost_rate: float,
    stop_slippage_rate: float,
) -> tuple[float, float]:
    stressed_stop_fill = stop_price * (1.0 - stop_slippage_rate)
    if (
        not np.isfinite(entry_price)
        or not np.isfinite(stop_price)
        or not np.isfinite(stressed_stop_fill)
        or entry_price <= 0.0
        or stop_price <= 0.0
        or stressed_stop_fill <= 0.0
    ):
        return float("nan"), float("nan")
    distance = max(0.0, entry_price - stressed_stop_fill)
    planned = distance + entry_price * cost_rate + stressed_stop_fill * cost_rate
    return float(planned), float(stressed_stop_fill)


def _rotation_inclusive_loss(
    scale: float,
    targets: dict[str, dict[str, float]],
    current_units: dict[str, float],
    prices: dict[str, float],
    *,
    cost_rate: float,
) -> tuple[float, float, float]:
    """Loss from pre-trade open equity through simultaneous stressed stops.

    Immediate rotation cost is charged on every delta, including liquidation
    of names absent from the replacement book.  Target stop loss then charges
    mark-to-stressed-stop distance and the eventual exit cost.  Entry cost is
    already in the immediate rotation term and is intentionally not counted a
    second time.
    """

    rotation_cost = 0.0
    for instrument, current in current_units.items():
        target = scale * targets.get(instrument, {}).get("units", 0.0)
        rotation_cost += abs(target - current) * prices[instrument] * cost_rate
    stop_loss = 0.0
    for instrument, target in targets.items():
        units = scale * target["units"]
        price = prices[instrument]
        stressed_stop = target["stressed_stop_fill"]
        stop_loss += units * (price - stressed_stop)
        stop_loss += units * stressed_stop * cost_rate
    return float(rotation_cost), float(stop_loss), float(rotation_cost + stop_loss)


def _largest_rotation_feasible_scale(
    targets: dict[str, dict[str, float]],
    current_units: dict[str, float],
    prices: dict[str, float],
    *,
    cost_rate: float,
    budget: float,
    upper: float,
) -> float:
    """Solve the piecewise-linear rotation-aware aggregate budget exactly."""

    upper = max(0.0, min(1.0, float(upper)))
    if upper == 0.0:
        return 0.0
    points = {0.0, upper}
    for instrument, target in targets.items():
        target_units = float(target["units"])
        if target_units > 0.0:
            crossing = current_units.get(instrument, 0.0) / target_units
            if 0.0 < crossing < upper:
                points.add(float(crossing))
    ordered = sorted(points)

    def total(scale: float) -> float:
        return _rotation_inclusive_loss(
            scale, targets, current_units, prices, cost_rate=cost_rate
        )[2]

    feasible = [point for point in ordered if total(point) <= budget + 1e-9]
    for lower, higher in zip(ordered, ordered[1:]):
        low_value, high_value = total(lower), total(higher)
        if high_value <= budget + 1e-9:
            feasible.append(higher)
        elif low_value <= budget + 1e-9 and high_value > low_value:
            fraction = (budget - low_value) / (high_value - low_value)
            feasible.append(lower + (higher - lower) * max(0.0, min(1.0, fraction)))
    return float(max(feasible, default=0.0))


def _position_row(position: _Position, mark: float) -> dict[str, Any]:
    return {
        "instrument": position.instrument,
        "cluster": position.cluster,
        "units": float(position.units),
        "mark_price_usd": float(mark),
        "notional_usd": float(position.units * mark),
        "average_entry_price_usd": float(position.average_entry_price),
        "stop_price_usd": float(position.stop_price),
        "entry_date": _date_str(position.entry_date),
        "decision_date": _date_str(position.decision_date),
        "episode_id": int(position.episode_id),
    }


def _risk_snapshot(
    positions: dict[str, _Position],
    prices: dict[str, float],
    equity: float,
    spec: BookUSpec,
) -> dict[str, Any]:
    capital = max(0.0, min(float(equity), 100_000.0))
    cost_rate = spec.cost_bps_per_side / 10_000.0
    slip_rate = spec.stop_slippage_bps / 10_000.0
    legs: list[dict[str, Any]] = []
    gross = 0.0
    max_position = 0.0
    aggregate = 0.0
    for instrument in sorted(positions):
        position = positions[instrument]
        price = float(prices[instrument])
        per_unit, stressed_stop = _planned_loss_per_unit(
            price,
            position.stop_price,
            cost_rate=cost_rate,
            stop_slippage_rate=slip_rate,
        )
        risk = float(position.units * per_unit) if np.isfinite(per_unit) else float("inf")
        notional = float(position.units * price)
        gross += notional
        max_position = max(max_position, notional)
        aggregate += risk
        legs.append(
            {
                "instrument": instrument,
                "cluster": position.cluster,
                "planned_loss_usd": risk,
                "planned_loss_fraction_capital": risk / capital if capital > 0.0 else float("inf"),
                "planned_loss_per_unit_usd": per_unit,
                "stressed_stop_fill_usd": stressed_stop,
            }
        )
    finite_leg_risks = [float(row["planned_loss_usd"]) for row in legs]
    max_leg = max(finite_leg_risks, default=0.0)
    return {
        "capital_usd": capital,
        "legs": legs,
        "max_leg_planned_loss_usd": max_leg,
        "max_leg_planned_loss_fraction_capital": max_leg / capital if capital > 0.0 else 0.0,
        "aggregate_planned_loss_usd": aggregate,
        "aggregate_planned_loss_fraction_capital": aggregate / capital if capital > 0.0 else 0.0,
        "gross_exposure_usd": gross,
        "gross_exposure_fraction_equity": gross / equity if equity > 0.0 else 0.0,
        "max_position_notional_usd": max_position,
        "max_position_fraction_equity": max_position / equity if equity > 0.0 else 0.0,
    }


def _metrics(
    equity: pd.Series,
    events: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    *,
    spec: BookUSpec,
    requested_start: pd.Timestamp,
    requested_end: pd.Timestamp,
    initial_equity_usd: float,
    full_source_panel_hash: str,
    consumed_panel_hash: str,
    consumed_start: pd.Timestamp,
    consumed_end: pd.Timestamp,
    cluster_attribution: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / initial_equity_usd - 1.0)
    years = len(returns) / 252.0
    annualized_return = (
        float((equity.iloc[-1] / initial_equity_usd) ** (1.0 / years) - 1.0)
        if years > 0.0 and equity.iloc[-1] > 0.0
        else 0.0
    )
    standard_deviation = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    annualized_volatility = standard_deviation * np.sqrt(252.0)
    sharpe = (
        float(returns.mean() / standard_deviation * np.sqrt(252.0))
        if standard_deviation > 0.0
        else 0.0
    )
    downside = returns[returns < 0.0]
    downside_deviation = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = (
        float(returns.mean() / downside_deviation * np.sqrt(252.0))
        if downside_deviation > 0.0
        else 0.0
    )
    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = float(-drawdown.min())
    calmar = annualized_return / max_drawdown if max_drawdown > 0.0 else 0.0
    positive_episodes = sum(max(0.0, float(row["net_pnl_usd"])) for row in episodes)
    negative_episodes = -sum(min(0.0, float(row["net_pnl_usd"])) for row in episodes)
    execution_rows = [
        decision["execution"]
        for decision in decisions
        if isinstance(decision.get("execution"), dict)
    ]
    open_risk_rows = [
        row["open_execution_risk"]
        for row in trace
        if isinstance(row.get("open_execution_risk"), dict)
    ]
    cluster_sum = sum(float(row["net_pnl_usd"]) for row in cluster_attribution.values())
    net_pnl = float(equity.iloc[-1] - initial_equity_usd)
    annual_returns: dict[str, float] = {}
    for year, values in equity.groupby(equity.index.year):
        if len(values) > 1:
            annual_returns[str(int(year))] = float(values.iloc[-1] / values.iloc[0] - 1.0)
    outcome_payload = {
        "equity": [[_date_str(date), float(value)] for date, value in equity.items()],
        "events": events,
        "decisions": decisions,
        "trace": trace,
        "episodes": episodes,
        "cluster_attribution": cluster_attribution,
    }
    outcome_hash = sha256(_canonical_json(outcome_payload)).hexdigest()
    fingerprint_payload = {
        "schema_version": BOOK_U_SCHEMA_VERSION,
        "spec": spec.to_dict(),
        "requested_start": _date_str(requested_start),
        "requested_end": _date_str(requested_end),
        "effective_start": _date_str(equity.index[0]),
        "effective_end": _date_str(equity.index[-1]),
        "initial_equity_usd": float(initial_equity_usd),
        "protocol_sha256": BOOK_U_PROTOCOL_SHA256,
        "consumed_panel_sha256": consumed_panel_hash,
        "consumed_start": _date_str(consumed_start),
        "consumed_end": _date_str(consumed_end),
        "outcome_sha256": outcome_hash,
    }
    run_fingerprint = sha256(_canonical_json(fingerprint_payload)).hexdigest()
    return {
        "schema_version": BOOK_U_SCHEMA_VERSION,
        "account_currency": "USD",
        "initial_equity_usd": float(initial_equity_usd),
        "final_equity_usd": float(equity.iloc[-1]),
        "net_pnl_usd": net_pnl,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "annualized_volatility": float(annualized_volatility),
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": float(calmar),
        "worst_close_day": float(returns.min()) if len(returns) else 0.0,
        "worst_conservative_intraday_day": min(
            (float(row["conservative_intraday_return"]) for row in trace), default=0.0
        ),
        "sessions": int(len(equity)),
        "decision_count": int(len(decisions)),
        "buy_fills": sum(1 for row in events if row["side"] == "buy"),
        "sell_fills": sum(1 for row in events if row["side"] == "sell"),
        "stop_fills": sum(1 for row in events if str(row["reason"]).startswith("stop")),
        "gap_stop_fills": sum(1 for row in events if row["reason"] == "stop_gap"),
        "risk_trim_fills": sum(1 for row in events if row["reason"] == "risk_budget_trim"),
        "transaction_cost_usd": sum(float(row["cost_usd"]) for row in events),
        "turnover_usd": sum(float(row["notional_usd"]) for row in events),
        "closed_episodes": int(len(episodes)),
        "winning_episodes": sum(1 for row in episodes if float(row["net_pnl_usd"]) > 0.0),
        "episode_win_rate": (
            sum(1 for row in episodes if float(row["net_pnl_usd"]) > 0.0) / len(episodes)
            if episodes
            else 0.0
        ),
        "episode_profit_factor": (
            positive_episodes / negative_episodes
            if negative_episodes > 0.0
            else (None if positive_episodes > 0.0 else 0.0)
        ),
        "max_execution_leg_planned_risk_fraction": max(
            (float(row["max_leg_planned_loss_fraction_capital"]) for row in execution_rows),
            default=0.0,
        ),
        "max_execution_aggregate_planned_risk_fraction": max(
            (float(row["aggregate_planned_loss_fraction_capital"]) for row in execution_rows),
            default=0.0,
        ),
        "max_execution_gross_fraction": max(
            (float(row["gross_exposure_fraction_equity"]) for row in execution_rows),
            default=0.0,
        ),
        "max_execution_position_fraction": max(
            (float(row["max_position_fraction_equity"]) for row in execution_rows),
            default=0.0,
        ),
        "max_rotation_inclusive_planned_risk_fraction": max(
            (
                float(row["rotation_inclusive_planned_loss"]["fraction_capital"])
                for row in execution_rows
            ),
            default=0.0,
        ),
        "rotation_inclusive_cap_breach_count": sum(
            not bool(row["rotation_inclusive_planned_loss"]["within_cap"])
            for row in execution_rows
        ),
        "max_open_leg_planned_risk_fraction": max(
            (float(row["max_leg_planned_loss_fraction_capital"]) for row in open_risk_rows),
            default=0.0,
        ),
        "max_open_aggregate_planned_risk_fraction": max(
            (float(row["aggregate_planned_loss_fraction_capital"]) for row in open_risk_rows),
            default=0.0,
        ),
        "max_open_gross_fraction": max(
            (float(row["gross_exposure_fraction_equity"]) for row in open_risk_rows),
            default=0.0,
        ),
        "max_open_position_fraction": max(
            (float(row["max_position_fraction_equity"]) for row in open_risk_rows),
            default=0.0,
        ),
        "open_cap_breach_count": sum(not bool(row["open_execution_caps_satisfied"]) for row in trace),
        "close_risk_overrun_days": sum(bool(row["risk_trim_required_next_open"]) for row in trace),
        "minimum_cash_usd": min((float(row["day_end_cash_usd"]) for row in trace), default=0.0),
        "borrow_breach_count": sum(float(row["day_end_cash_usd"]) < -1e-6 for row in trace),
        "daily_accounting_reconciliation_failures": sum(
            not bool(row["daily_accounting_reconciles"]) for row in trace
        ),
        "max_cash_holdings_equity_error_usd": max(
            (abs(float(row["cash_holdings_equity_error_usd"])) for row in trace),
            default=0.0,
        ),
        "max_balance_unrealized_equity_error_usd": max(
            (abs(float(row["balance_unrealized_equity_error_usd"])) for row in trace),
            default=0.0,
        ),
        "verified_flat_at_end": bool(trace and trace[-1]["verified_flat_at_end"]),
        "cluster_attribution_reconciles": bool(abs(cluster_sum - net_pnl) <= 1e-6),
        "cluster_attribution_error_usd": float(cluster_sum - net_pnl),
        "annual_returns": annual_returns,
        "protocol_sha256": BOOK_U_PROTOCOL_SHA256,
        "requested_start": _date_str(requested_start),
        "requested_end": _date_str(requested_end),
        "effective_start": _date_str(equity.index[0]),
        "effective_end": _date_str(equity.index[-1]),
        "consumed_start": _date_str(consumed_start),
        "consumed_end": _date_str(consumed_end),
        "full_source_panel_sha256": full_source_panel_hash,
        "consumed_panel_sha256": consumed_panel_hash,
        "outcome_sha256": outcome_hash,
        "run_fingerprint_sha256": run_fingerprint,
        # Backward-compatible aliases with explicit semantics above.
        "input_panel_sha256": full_source_panel_hash,
        "order_invariant_result_sha256": run_fingerprint,
    }


def run_book_u(
    panel: dict[str, pd.DataFrame],
    spec: BookUSpec,
    *,
    start: pd.Timestamp | str,
    end: pd.Timestamp | str,
    initial_equity_usd: float = 100_000.0,
) -> BookURun:
    """Run a causal, independently flat Book U research segment."""

    if not np.isfinite(initial_equity_usd) or initial_equity_usd <= 0.0:
        raise ValueError("initial_equity_usd must be finite and positive")
    checked = common_book_u_panel(panel)
    full_source_panel_digest = _panel_hash(checked)
    index = next(iter(checked.values())).index
    start_ts, end_ts = _utc_timestamp(start).normalize(), _utc_timestamp(end).normalize()
    if end_ts <= start_ts:
        raise ValueError("end must be after start")
    active_dates = index[(index >= start_ts) & (index <= end_ts)]
    if len(active_dates) < 2:
        raise ValueError("requested segment has fewer than two common sessions")
    first_active_i = int(index.get_loc(active_dates[0]))
    final_active_i = int(index.get_loc(active_dates[-1]))
    seed_decision_i: int | None = None
    if first_active_i > 0 and _is_month_end(index, first_active_i - 1):
        prior_and_first = _official_sessions(index[first_active_i - 1], index[first_active_i])
        if len(prior_and_first) == 2 and prior_and_first[-1] == index[first_active_i]:
            seed_decision_i = first_active_i - 1
    earliest_decision_i = seed_decision_i if seed_decision_i is not None else first_active_i
    consumed_start_i = max(0, earliest_decision_i - spec.momentum_lookback)
    consumed_panel = {
        instrument: frame.iloc[consumed_start_i : final_active_i + 1]
        for instrument, frame in checked.items()
    }
    consumed_panel_digest = _panel_hash(consumed_panel)
    consumed_start = index[consumed_start_i]
    consumed_end = index[final_active_i]

    instruments = tuple(sorted(USD_ETF_UNIVERSE))
    cost_rate = spec.cost_bps_per_side / 10_000.0
    slip_rate = spec.stop_slippage_bps / 10_000.0
    cash = float(initial_equity_usd)
    balance = float(initial_equity_usd)
    positions: dict[str, _Position] = {}
    pending: dict[str, Any] | None = None
    stopped_at: dict[str, pd.Timestamp] = {}
    events: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    episode_number = 0
    previous_close_equity = float(initial_equity_usd)
    cluster_pnl = {cluster: 0.0 for cluster in _CLUSTER_ORDER}
    cluster_cost = {cluster: 0.0 for cluster in _CLUSTER_ORDER}
    cluster_stops = {cluster: 0 for cluster in _CLUSTER_ORDER}

    if seed_decision_i is not None:
        seeded = build_book_u_decision(checked, spec, seed_decision_i)
        seeded["fill_date"] = _date_str(active_dates[0])
        seeded["segment_boundary_seed"] = True
        decisions.append(seeded)
        pending = seeded

    def attribute(position: _Position, pnl: float) -> None:
        position.episode_pnl_usd += float(pnl)
        cluster_pnl[position.cluster] += float(pnl)

    def close_episode(position: _Position, date: pd.Timestamp, reason: str) -> None:
        episodes.append(
            {
                "episode_id": int(position.episode_id),
                "instrument": position.instrument,
                "cluster": position.cluster,
                "entry_date": _date_str(position.entry_date),
                "exit_date": _date_str(date),
                "entry_price_usd": float(position.average_entry_price),
                "exit_reason": reason,
                "net_pnl_usd": float(position.episode_pnl_usd),
                "transaction_cost_usd": float(position.episode_cost_usd),
            }
        )

    def buy(
        instrument: str,
        quantity: float,
        price: float,
        *,
        stop: float,
        date: pd.Timestamp,
        decision_date: pd.Timestamp,
        reason: str,
        opened_today: list[str],
    ) -> None:
        nonlocal cash, balance, episode_number
        if quantity <= _TOL:
            return
        cost = quantity * price * cost_rate
        position = positions.get(instrument)
        if position is None:
            episode_number += 1
            position = _Position(
                instrument=instrument,
                cluster=CLUSTERS[instrument],
                units=0.0,
                stop_price=float(stop),
                average_entry_price=float(price),
                entry_date=date,
                decision_date=decision_date,
                episode_id=episode_number,
            )
            positions[instrument] = position
            opened_today.append(instrument)
        old_units = position.units
        position.average_entry_price = (
            (old_units * position.average_entry_price + quantity * price) / (old_units + quantity)
        )
        position.units += quantity
        position.stop_price = max(position.stop_price, float(stop))
        position.decision_date = decision_date
        position.episode_cost_usd += cost
        attribute(position, -cost)
        cluster_cost[position.cluster] += cost
        cash -= quantity * price + cost
        balance -= cost
        events.append(
            {
                "date": _date_str(date),
                "decision_date": _date_str(decision_date),
                "instrument": instrument,
                "cluster": position.cluster,
                "side": "buy",
                "units": float(quantity),
                "price_usd": float(price),
                "notional_usd": float(quantity * price),
                "cost_usd": float(cost),
                "stop_price_usd": float(position.stop_price),
                "reason": reason,
            }
        )

    def sell(
        instrument: str,
        quantity: float,
        price: float,
        *,
        date: pd.Timestamp,
        decision_date: pd.Timestamp,
        reason: str,
        closed_today: list[str],
        extra: dict[str, Any] | None = None,
    ) -> None:
        nonlocal cash, balance
        position = positions[instrument]
        quantity = min(float(quantity), position.units)
        if quantity <= _TOL:
            return
        notional = quantity * price
        cost = notional * cost_rate
        realized = quantity * (price - position.average_entry_price) - cost
        cash += notional - cost
        balance += realized
        position.episode_cost_usd += cost
        attribute(position, -cost)
        cluster_cost[position.cluster] += cost
        position.units -= quantity
        event = {
            "date": _date_str(date),
            "decision_date": _date_str(decision_date),
            "instrument": instrument,
            "cluster": position.cluster,
            "side": "sell",
            "units": float(quantity),
            "price_usd": float(price),
            "notional_usd": float(notional),
            "cost_usd": float(cost),
            "balance_change_usd": float(realized),
            "stop_price_usd": float(position.stop_price),
            "reason": reason,
        }
        if extra:
            event.update(extra)
        events.append(event)
        if position.units <= _TOL:
            position.units = 0.0
            close_episode(position, date, reason)
            del positions[instrument]
            closed_today.append(instrument)

    def execute_monthly(
        decision: dict[str, Any], date: pd.Timestamp, i: int, opened: list[str], closed: list[str]
    ) -> float:
        nonlocal cash
        decision_date = _utc_timestamp(decision["decision_date"])
        open_prices = {instrument: float(checked[instrument]["open"].iloc[i]) for instrument in instruments}
        pre_trade_equity = cash + sum(
            position.units * open_prices[instrument] for instrument, position in positions.items()
        )
        capital = max(0.0, min(pre_trade_equity, 100_000.0))
        allowed_rows = [
            row
            for row in decision["selected"]
            if not (row["cluster"] in stopped_at and stopped_at[row["cluster"]] >= decision_date)
        ]
        targets: dict[str, dict[str, float]] = {}
        blocked = list(decision.get("blocked", []))
        for row in allowed_rows:
            instrument = str(row["instrument"])
            price = open_prices[instrument]
            candidate_stop = price - spec.stop_atr_multiple * float(row["atr_20"])
            old = positions.get(instrument)
            effective_stop = max(old.stop_price, candidate_stop) if old is not None else candidate_stop
            per_unit, stressed_stop = _planned_loss_per_unit(
                price, effective_stop, cost_rate=cost_rate, stop_slippage_rate=slip_rate
            )
            if not np.isfinite(per_unit) or per_unit <= 0.0:
                blocked.append(f"{instrument}:invalid_stop_or_planned_loss")
                continue
            volatility_units = pre_trade_equity * float(row["target_weight"]) / price
            position_cap_units = pre_trade_equity * spec.position_cap / price
            leg_risk_units = spec.risk_per_leg * capital / per_unit if capital > 0.0 else 0.0
            units = max(0.0, min(volatility_units, position_cap_units, leg_risk_units))
            targets[instrument] = {
                "units": float(units),
                "stop": float(effective_stop),
                "planned_loss_per_unit": float(per_unit),
                "stressed_stop_fill": float(stressed_stop),
            }

        conservative_target_risk_before = sum(
            row["units"] * row["planned_loss_per_unit"] for row in targets.values()
        )
        aggregate_budget = spec.aggregate_risk * capital
        conservative_risk_upper = (
            min(1.0, aggregate_budget / conservative_target_risk_before)
            if conservative_target_risk_before > 0.0
            else 1.0
        )
        pre_trade_units = {
            instrument: positions[instrument].units if instrument in positions else 0.0
            for instrument in instruments
        }
        aggregate_scale = _largest_rotation_feasible_scale(
            targets,
            pre_trade_units,
            open_prices,
            cost_rate=cost_rate,
            budget=aggregate_budget,
            upper=conservative_risk_upper,
        )
        rotation_cost, stressed_target_loss, rotation_inclusive_total = (
            _rotation_inclusive_loss(
                aggregate_scale,
                targets,
                pre_trade_units,
                open_prices,
                cost_rate=cost_rate,
            )
        )
        for row in targets.values():
            row["units"] *= aggregate_scale

        desired = {instrument: targets.get(instrument, {}).get("units", 0.0) for instrument in instruments}
        deltas = {
            instrument: desired[instrument] - positions.get(instrument, _Position(
                instrument, CLUSTERS[instrument], 0.0, 1.0, 1.0, date, decision_date, -1
            )).units
            for instrument in instruments
        }
        # Canonical sells-before-buys bookkeeping; targets were all calculated
        # from the same pre-trade equity snapshot above.
        for instrument in instruments:
            if deltas[instrument] < -_TOL:
                sell(
                    instrument,
                    -deltas[instrument],
                    open_prices[instrument],
                    date=date,
                    decision_date=decision_date,
                    reason="monthly_rebalance",
                    closed_today=closed,
                )
        for instrument in instruments:
            if deltas[instrument] > _TOL:
                buy(
                    instrument,
                    deltas[instrument],
                    open_prices[instrument],
                    stop=targets[instrument]["stop"],
                    date=date,
                    decision_date=decision_date,
                    reason="monthly_rebalance",
                    opened_today=opened,
                )
        for instrument, target in targets.items():
            if instrument in positions:
                positions[instrument].stop_price = max(
                    positions[instrument].stop_price, target["stop"]
                )
                positions[instrument].decision_date = decision_date

        if cash < -1e-6:
            raise RuntimeError("Book U sizing attempted to borrow cash")
        execution_risk = _risk_snapshot(positions, open_prices, pre_trade_equity, spec)
        decision["fill_date"] = _date_str(date)
        decision["blocked"] = blocked
        decision["execution"] = {
            "pre_trade_equity_usd": float(pre_trade_equity),
            "pre_trade_capital_usd": float(capital),
            "aggregate_scale": float(aggregate_scale),
            "conservative_target_planned_loss_before_scale_usd": float(
                conservative_target_risk_before
            ),
            "rotation_inclusive_planned_loss": {
                "immediate_rotation_cost_usd": float(rotation_cost),
                "target_mark_to_stressed_stop_loss_usd": float(stressed_target_loss),
                "total_pretrade_to_stressed_stops_usd": float(rotation_inclusive_total),
                "fraction_capital": (
                    float(rotation_inclusive_total / capital) if capital > 0.0 else 0.0
                ),
                "aggregate_budget_usd": float(aggregate_budget),
                "within_cap": bool(rotation_inclusive_total <= aggregate_budget + 1e-6),
            },
            "targets": {
                instrument: {
                    "units": float(target["units"]),
                    "stop_price_usd": float(target["stop"]),
                    "planned_loss_per_unit_usd": float(target["planned_loss_per_unit"]),
                }
                for instrument, target in sorted(targets.items())
            },
            **{key: value for key, value in execution_risk.items() if key != "legs"},
            "legs": execution_risk["legs"],
        }
        return float(pre_trade_equity)

    def trim_open_risk(
        date: pd.Timestamp, i: int, opened: list[str], closed: list[str]
    ) -> float:
        del opened  # A trim is reduction-only by construction.
        if not positions:
            return float(cash)
        open_prices = {instrument: float(checked[instrument]["open"].iloc[i]) for instrument in positions}
        pre_trade_equity = cash + sum(
            position.units * open_prices[instrument] for instrument, position in positions.items()
        )
        capital = max(0.0, min(pre_trade_equity, 100_000.0))
        target_units: dict[str, float] = {}
        per_unit_risk: dict[str, float] = {}
        for instrument in sorted(positions):
            position = positions[instrument]
            price = open_prices[instrument]
            planned, _ = _planned_loss_per_unit(
                price, position.stop_price, cost_rate=cost_rate, stop_slippage_rate=slip_rate
            )
            if not np.isfinite(planned) or planned <= 0.0:
                units = 0.0
                planned = 0.0
            else:
                units = min(
                    position.units,
                    pre_trade_equity * spec.position_cap / price,
                    spec.risk_per_leg * capital / planned if capital > 0.0 else 0.0,
                )
            target_units[instrument] = max(0.0, float(units))
            per_unit_risk[instrument] = float(planned)
        gross_before_scale = sum(target_units[x] * open_prices[x] for x in target_units)
        risk_before_scale = sum(target_units[x] * per_unit_risk[x] for x in target_units)
        scales = [1.0]
        if gross_before_scale > 0.0:
            scales.append(spec.gross_cap * pre_trade_equity / gross_before_scale)
        if risk_before_scale > 0.0:
            scales.append(spec.aggregate_risk * capital / risk_before_scale)
        common_scale = max(0.0, min(scales))
        for instrument in sorted(positions):
            desired = min(positions[instrument].units, target_units[instrument] * common_scale)
            reduction = positions[instrument].units - desired
            if reduction > max(_TOL, positions[instrument].units * 1e-12):
                sell(
                    instrument,
                    reduction,
                    open_prices[instrument],
                    date=date,
                    decision_date=date,
                    reason="risk_budget_trim",
                    closed_today=closed,
                )
        if cash < -1e-6:
            raise RuntimeError("Book U risk trim produced negative cash")
        return float(pre_trade_equity)

    for i, date in enumerate(index):
        if date < start_ts:
            continue
        if date > end_ts:
            break
        is_terminal = date == active_dates[-1]
        day_start_cash = float(cash)
        day_start_balance = float(balance)
        day_start_equity = float(previous_close_equity)
        opened_today: list[str] = []
        closed_today: list[str] = []

        # Attribute the overnight move for the units that survived yesterday.
        for instrument in sorted(positions):
            position = positions[instrument]
            previous_close = float(checked[instrument]["close"].iloc[i - 1])
            open_price = float(checked[instrument]["open"].iloc[i])
            attribute(position, position.units * (open_price - previous_close))

        # Resting stops execute through adverse opening gaps before any scheduled
        # rebalance.  A stopped cluster cannot be re-opened by that same fill.
        for instrument in sorted(tuple(positions)):
            position = positions.get(instrument)
            if position is None:
                continue
            open_price = float(checked[instrument]["open"].iloc[i])
            if open_price <= position.stop_price:
                fill = open_price * (1.0 - slip_rate)
                attribute(position, position.units * (fill - open_price))
                cluster = position.cluster
                stop = position.stop_price
                sell(
                    instrument,
                    position.units,
                    fill,
                    date=date,
                    decision_date=position.decision_date,
                    reason="stop_gap",
                    closed_today=closed_today,
                    extra={
                        "resting_stop_price_usd": float(stop),
                        "gap_open_price_usd": float(open_price),
                        "stop_slippage_bps": float(spec.stop_slippage_bps),
                    },
                )
                stopped_at[cluster] = date
                cluster_stops[cluster] += 1

        if pending is not None and _utc_timestamp(pending["fill_date"]) == date:
            open_risk_reference_equity = execute_monthly(
                pending, date, i, opened_today, closed_today
            )
            pending = None
        else:
            # This makes a close-detected or opening-gap risk overrun actionable
            # at the next executable open; the operation can only reduce units.
            open_risk_reference_equity = trim_open_risk(
                date, i, opened_today, closed_today
            )

        open_prices_for_positions = {
            instrument: float(checked[instrument]["open"].iloc[i]) for instrument in positions
        }
        open_execution_risk = _risk_snapshot(
            positions, open_prices_for_positions, open_risk_reference_equity, spec
        )
        open_execution_caps_satisfied = bool(
            open_execution_risk["max_leg_planned_loss_fraction_capital"]
            <= spec.risk_per_leg + 1e-12
            and open_execution_risk["aggregate_planned_loss_fraction_capital"]
            <= spec.aggregate_risk + 1e-12
            and open_execution_risk["gross_exposure_fraction_equity"] <= spec.gross_cap + 1e-12
            and open_execution_risk["max_position_fraction_equity"] <= spec.position_cap + 1e-12
        )
        pre_intraday_cash = float(cash)
        pre_intraday_positions = {
            instrument: (position.units, position.stop_price)
            for instrument, position in positions.items()
        }
        conservative_intraday_equity = pre_intraday_cash
        for instrument in sorted(pre_intraday_positions):
            quantity, stop = pre_intraday_positions[instrument]
            low = float(checked[instrument]["low"].iloc[i])
            if low <= stop:
                fill = stop * (1.0 - slip_rate)
                conservative_intraday_equity += quantity * fill * (1.0 - cost_rate)
            else:
                conservative_intraday_equity += quantity * low

        # Entry-day and ordinary intraday stops use the resting stop unless the
        # open already crossed it (handled above).  Non-stopped positions are
        # marked from today's open to today's close.
        for instrument in sorted(tuple(positions)):
            position = positions.get(instrument)
            if position is None:
                continue
            open_price = open_prices_for_positions[instrument]
            low = float(checked[instrument]["low"].iloc[i])
            close = float(checked[instrument]["close"].iloc[i])
            if low <= position.stop_price:
                fill = position.stop_price * (1.0 - slip_rate)
                attribute(position, position.units * (fill - open_price))
                cluster = position.cluster
                stop = position.stop_price
                sell(
                    instrument,
                    position.units,
                    fill,
                    date=date,
                    decision_date=position.decision_date,
                    reason="stop_intraday",
                    closed_today=closed_today,
                    extra={
                        "resting_stop_price_usd": float(stop),
                        "gap_open_price_usd": None,
                        "stop_slippage_bps": float(spec.stop_slippage_bps),
                    },
                )
                stopped_at[cluster] = date
                cluster_stops[cluster] += 1
            else:
                attribute(position, position.units * (close - open_price))

        decision_formed: dict[str, Any] | None = None
        if not is_terminal and _is_month_end(index, i):
            next_date = index[i + 1]
            if next_date <= end_ts:
                decision_formed = build_book_u_decision(checked, spec, i)
                decision_formed["fill_date"] = _date_str(next_date)
                decisions.append(decision_formed)
                pending = decision_formed

        if is_terminal:
            close_prices = {
                instrument: float(checked[instrument]["close"].iloc[i]) for instrument in positions
            }
            for instrument in sorted(tuple(positions)):
                position = positions[instrument]
                sell(
                    instrument,
                    position.units,
                    close_prices[instrument],
                    date=date,
                    decision_date=date,
                    reason="final_liquidation",
                    closed_today=closed_today,
                )

        close_prices_all = {
            instrument: float(checked[instrument]["close"].iloc[i]) for instrument in instruments
        }
        end_equity = cash + sum(
            position.units * close_prices_all[instrument]
            for instrument, position in positions.items()
        )
        conservative_min = min(
            day_start_equity,
            conservative_intraday_equity,
            end_equity,
        )
        reference_equity = max(day_start_balance, day_start_equity)
        risk = _risk_snapshot(positions, close_prices_all, end_equity, spec)
        risk_trim_required = bool(
            risk["max_leg_planned_loss_fraction_capital"] > spec.risk_per_leg + 1e-12
            or risk["aggregate_planned_loss_fraction_capital"] > spec.aggregate_risk + 1e-12
            or risk["gross_exposure_fraction_equity"] > spec.gross_cap + 1e-12
            or risk["max_position_fraction_equity"] > spec.position_cap + 1e-12
        )
        position_rows = [
            _position_row(positions[instrument], close_prices_all[instrument])
            for instrument in sorted(positions)
        ]
        cash_holdings_equity = cash + sum(
            float(row["notional_usd"]) for row in position_rows
        )
        balance_unrealized_equity = balance + sum(
            float(row["units"])
            * (float(row["mark_price_usd"]) - float(row["average_entry_price_usd"]))
            for row in position_rows
        )
        trace.append(
            {
                "date": _date_str(date),
                "day_start_cash_usd": day_start_cash,
                "day_start_balance_usd": day_start_balance,
                "day_start_equity_usd": day_start_equity,
                "firm_day_reference_equity_usd": reference_equity,
                "day_end_cash_usd": float(cash),
                "day_end_balance_usd": float(balance),
                "day_end_equity_usd": float(end_equity),
                "daily_pnl_usd": float(end_equity - day_start_equity),
                "daily_return": float(end_equity / day_start_equity - 1.0),
                "conservative_intraday_min_equity_usd": float(conservative_min),
                "conservative_intraday_return": (
                    float(conservative_min / reference_equity - 1.0)
                    if reference_equity > 0.0
                    else 0.0
                ),
                "positions_opened": sorted(set(opened_today)),
                "positions_closed": sorted(set(closed_today)),
                "positions": position_rows,
                "cash_holdings_equity_error_usd": float(cash_holdings_equity - end_equity),
                "balance_unrealized_equity_error_usd": float(
                    balance_unrealized_equity - end_equity
                ),
                "daily_accounting_reconciles": bool(
                    abs(cash_holdings_equity - end_equity) <= 1e-6
                    and abs(balance_unrealized_equity - end_equity) <= 1e-6
                ),
                "decision_formed": (
                    decision_formed["decision_date"] if decision_formed is not None else None
                ),
                "open_risk_reference_equity_usd": float(open_risk_reference_equity),
                "open_execution_risk": open_execution_risk,
                "open_execution_caps_satisfied": open_execution_caps_satisfied,
                **{key: value for key, value in risk.items() if key != "legs"},
                "planned_risk_legs": risk["legs"],
                "risk_trim_required_next_open": risk_trim_required and not is_terminal,
                "verified_flat_at_end": bool(is_terminal and not positions and abs(cash - balance) <= 1e-6),
            }
        )
        equity_rows.append((date, float(end_equity)))
        previous_close_equity = float(end_equity)

    if not equity_rows:
        raise ValueError("no Book U equity observations were created")
    if positions:
        raise RuntimeError("Book U terminal liquidation left an open position")
    if cash < -1e-6:
        raise RuntimeError("Book U borrowed cash")

    equity = pd.Series(
        [value for _, value in equity_rows],
        index=pd.DatetimeIndex([date for date, _ in equity_rows], name="timestamp"),
        name="equity_usd",
        dtype=float,
    )
    cluster_attribution: dict[str, dict[str, Any]] = {}
    positive_cluster_pnl = sum(max(0.0, value) for value in cluster_pnl.values())
    for cluster in _CLUSTER_ORDER:
        cluster_episodes = [row for row in episodes if row["cluster"] == cluster]
        cluster_attribution[cluster] = {
            "net_pnl_usd": float(cluster_pnl[cluster]),
            "positive_pnl_share": (
                max(0.0, cluster_pnl[cluster]) / positive_cluster_pnl
                if positive_cluster_pnl > 0.0
                else 0.0
            ),
            "transaction_cost_usd": float(cluster_cost[cluster]),
            "stop_count": int(cluster_stops[cluster]),
            "closed_episodes": int(len(cluster_episodes)),
            "winning_episodes": sum(
                1 for row in cluster_episodes if float(row["net_pnl_usd"]) > 0.0
            ),
        }
    metrics = _metrics(
        equity,
        events,
        decisions,
        trace,
        episodes,
        spec=spec,
        requested_start=start_ts,
        requested_end=end_ts,
        initial_equity_usd=initial_equity_usd,
        full_source_panel_hash=full_source_panel_digest,
        consumed_panel_hash=consumed_panel_digest,
        consumed_start=consumed_start,
        consumed_end=consumed_end,
        cluster_attribution=cluster_attribution,
    )
    return BookURun(
        spec=spec,
        start=start_ts,
        end=end_ts,
        equity=equity,
        events=events,
        decisions=decisions,
        trace=trace,
        episodes=episodes,
        cluster_attribution=cluster_attribution,
        metrics=metrics,
    )
