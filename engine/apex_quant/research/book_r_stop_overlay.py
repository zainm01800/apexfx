"""Causal stop-loss challenger for the frozen Book R-252 research control.

The baseline implementation remains untouched.  This module adds the single
pre-registered stop/risk-sizing overlay documented in
``data_store/book_r_stop_overlay_prereg_2026-09-03.md`` plus its two frozen
neighbour sensitivity checks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from apex_quant.research.book_r_usd_etf import (
    BookRSpec,
    _date_str,
    _metrics,
    _round_values,
    _utc_timestamp,
    common_panel,
    select_book_r,
)


@dataclass(frozen=True)
class BookRStopSpec:
    """Frozen stop-overlay parameters; signal parameters remain Book R-252."""

    name: str = "R-252-stop-2.5ATR"
    lookback: int = 252
    vol_window: int = 63
    max_positions: int = 3
    gross_target: float = 0.95
    cost_bps_per_side: float = 5.0
    atr_window: int = 20
    atr_multiple: float = 2.5
    risk_fraction: float | None = 0.0085
    stop_slippage_bps: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def signal_spec(self) -> BookRSpec:
        return BookRSpec(
            name=self.name,
            lookback=self.lookback,
            vol_window=self.vol_window,
            max_positions=self.max_positions,
            gross_target=self.gross_target,
            cost_bps_per_side=self.cost_bps_per_side,
        )


@dataclass
class BookRStopRun:
    spec: BookRStopSpec
    start: pd.Timestamp
    end: pd.Timestamp
    equity: pd.Series
    events: list[dict[str, Any]]
    selections: list[dict[str, Any]]
    metrics: dict[str, Any]

    def to_dict(self, *, equity_points: int = 512) -> dict[str, Any]:
        step = max(1, int(np.ceil(len(self.equity) / equity_points)))
        return {
            "spec": self.spec.to_dict(),
            "start": _date_str(self.start),
            "end": _date_str(self.end),
            "metrics": _round_values(self.metrics),
            "events": [_round_values(row) for row in self.events],
            "selections": [_round_values(row) for row in self.selections],
            "equity_curve": [
                {"date": _date_str(t), "equity_usd": round(float(v), 6)}
                for t, v in self.equity.iloc[::step].items()
            ],
        }


def _atr(frame: pd.DataFrame, window: int) -> pd.Series:
    """Simple rolling ATR, matching the existing A/B/C engine convention."""
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    prior_close = frame["close"].astype(float).shift(1)
    true_range = pd.concat(
        [high - low, (high - prior_close).abs(), (low - prior_close).abs()], axis=1
    ).max(axis=1)
    return true_range.rolling(window=window, min_periods=window).mean()


def _extended_metrics(
    equity: pd.Series,
    events: list[dict[str, Any]],
    *,
    total_cost: float,
    selections: int,
    gross_exposure: pd.Series,
) -> dict[str, Any]:
    metrics = _metrics(equity, events, total_cost=total_cost, selections=selections)
    returns = equity.pct_change().dropna()
    drawdown = equity / equity.cummax() - 1.0
    monthly = returns.groupby([returns.index.year, returns.index.month]).apply(
        lambda values: float((1.0 + values).prod() - 1.0)
    )
    tail_count = max(1, int(np.ceil(len(returns) * 0.05))) if len(returns) else 0
    underwater = drawdown < 0.0
    longest = current = 0
    for value in underwater:
        current = current + 1 if bool(value) else 0
        longest = max(longest, current)
    stop_events = [row for row in events if row["reason"] == "stop_loss"]
    metrics.update({
        "worst_day": float(returns.min()) if len(returns) else 0.0,
        "expected_shortfall_5pct": (
            float(returns.nsmallest(tail_count).mean()) if tail_count else 0.0
        ),
        "worst_month": float(monthly.min()) if len(monthly) else 0.0,
        "max_drawdown_duration_sessions": int(longest),
        "average_close_gross_exposure": (
            float(gross_exposure.mean()) if len(gross_exposure) else 0.0
        ),
        "stop_exit_count": len(stop_events),
        "gap_stop_count": sum(bool(row.get("gap_through_stop")) for row in stop_events),
        "drawdown_breach_days": {
            "5pct": int((drawdown <= -0.05).sum()),
            "8pct": int((drawdown <= -0.08).sum()),
            "10pct": int((drawdown <= -0.10).sum()),
            "12pct": int((drawdown <= -0.12).sum()),
        },
    })
    return metrics


def run_book_r_stop_overlay(
    panel: dict[str, pd.DataFrame],
    spec: BookRStopSpec,
    *,
    start: pd.Timestamp | str,
    end: pd.Timestamp | str,
    initial_equity_usd: float = 100_000.0,
) -> BookRStopRun:
    """Run the stop overlay with causal signals and conservative stop fills."""
    if initial_equity_usd <= 0:
        raise ValueError("initial equity must be positive")
    if spec.risk_fraction is not None and not (0.0 < spec.risk_fraction <= 0.05):
        raise ValueError("risk_fraction must be None or in (0, 0.05]")
    if spec.atr_window < 2 or spec.atr_multiple <= 0.0:
        raise ValueError("ATR window and multiple must be positive")
    if not (0.0 < spec.gross_target <= 1.0):
        raise ValueError("gross_target must be in (0, 1]")
    if spec.cost_bps_per_side < 0.0 or spec.stop_slippage_bps < 0.0:
        raise ValueError("cost and stop slippage cannot be negative")

    checked = common_panel(panel, panel.keys())
    for instrument, frame in checked.items():
        absent = sorted({"high", "low"} - set(frame.columns))
        if absent:
            raise ValueError(f"{instrument} lacks stop-test columns: {', '.join(absent)}")
        if (frame[["high", "low"]] <= 0).any().any() or frame[["high", "low"]].isna().any().any():
            raise ValueError(f"{instrument} contains invalid high/low values")

    index = next(iter(checked.values())).index
    start_ts, end_ts = _utc_timestamp(start), _utc_timestamp(end)
    if end_ts <= start_ts:
        raise ValueError("end must be after start")
    active_dates = index[(index >= start_ts) & (index <= end_ts)]
    if len(active_dates) < 2:
        raise ValueError("requested segment has fewer than two common sessions")

    signal_spec = spec.signal_spec()
    instruments = tuple(checked.keys())
    atr = {instrument: _atr(frame, spec.atr_window) for instrument, frame in checked.items()}
    units = {instrument: 0.0 for instrument in instruments}
    stops: dict[str, float | None] = {instrument: None for instrument in instruments}
    cash = float(initial_equity_usd)
    cost_rate = spec.cost_bps_per_side / 10_000.0
    stop_slippage_rate = spec.stop_slippage_bps / 10_000.0
    pending: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    gross_rows: list[tuple[pd.Timestamp, float]] = []
    total_cost = 0.0
    planned_position_risk_fractions: list[float] = []
    planned_aggregate_risk_fractions: list[float] = []
    planned_gross_fractions: list[float] = []
    minimum_cash = cash

    def execute(
        instrument: str,
        delta: float,
        price: float,
        *,
        date: pd.Timestamp,
        decision_date: pd.Timestamp,
        reason: str,
        stop_price: float | None = None,
        gap_through_stop: bool = False,
    ) -> None:
        nonlocal cash, total_cost, minimum_cash
        if abs(delta) < 1e-12:
            return
        side = "buy" if delta > 0 else "sell"
        quantity = abs(delta)
        notional = quantity * price
        fill_cost = notional * cost_rate
        cash -= delta * price + fill_cost
        minimum_cash = min(minimum_cash, cash)
        total_cost += fill_cost
        units[instrument] += delta
        if abs(units[instrument]) < 1e-10:
            units[instrument] = 0.0
        events.append({
            "date": _date_str(date),
            "decision_date": _date_str(decision_date),
            "instrument": instrument,
            "side": side,
            "units": quantity,
            "price_usd": price,
            "notional_usd": notional,
            "cost_usd": fill_cost,
            "reason": reason,
            "stop_price_usd": stop_price,
            "gap_through_stop": bool(gap_through_stop),
        })

    for i, date in enumerate(index):
        if date < start_ts:
            continue
        if date > end_ts:
            break

        open_prices = {inst: float(checked[inst]["open"].iloc[i]) for inst in instruments}
        blocked_at_pending_open: set[str] = set()

        # A resting stop crossed by an opening gap receives the worse open,
        # followed by the frozen adverse stop-slippage stress when applicable.
        for inst in instruments:
            stop = stops[inst]
            if units[inst] <= 0.0 or stop is None or open_prices[inst] > stop:
                continue
            fill = open_prices[inst] * (1.0 - stop_slippage_rate)
            execute(
                inst,
                -units[inst],
                fill,
                date=date,
                decision_date=date,
                reason="stop_loss",
                stop_price=stop,
                gap_through_stop=True,
            )
            stops[inst] = None
            blocked_at_pending_open.add(inst)

        if pending is not None and pending["fill_date"] == date:
            selected = pending["selected"]
            decision_date = pending["decision_date"]
            selected_names = {row["instrument"] for row in selected}
            pre_trade_equity = cash + sum(units[inst] * open_prices[inst] for inst in instruments)
            selected_count = len(selected)
            desired_units: dict[str, float] = {inst: 0.0 for inst in instruments}
            proposed_stops: dict[str, float] = {}
            for row in selected:
                inst = row["instrument"]
                if inst in blocked_at_pending_open:
                    continue
                atr_value = float(pending["atr"][inst])
                if not np.isfinite(atr_value) or atr_value <= 0.0:
                    continue
                proposed = open_prices[inst] - spec.atr_multiple * atr_value
                if proposed <= 0.0:
                    continue
                effective_stop = max(stops[inst] or proposed, proposed)
                distance = open_prices[inst] - effective_stop
                if distance <= 0.0:
                    continue
                allocation_units = (
                    pre_trade_equity * spec.gross_target / selected_count / open_prices[inst]
                    if selected_count else 0.0
                )
                risk_units = (
                    pre_trade_equity * spec.risk_fraction / distance
                    if spec.risk_fraction is not None else allocation_units
                )
                desired_units[inst] = min(risk_units, allocation_units)
                proposed_stops[inst] = effective_stop

            # Sells precede buys for transparent cash accounting.  Desired
            # notionals are fixed from the same pre-trade equity snapshot.
            deltas = {inst: desired_units[inst] - units[inst] for inst in instruments}
            for inst in instruments:
                if deltas[inst] < -1e-12:
                    execute(
                        inst,
                        deltas[inst],
                        open_prices[inst],
                        date=date,
                        decision_date=decision_date,
                        reason="monthly_rebalance",
                    )
            for inst in instruments:
                if deltas[inst] > 1e-12:
                    execute(
                        inst,
                        deltas[inst],
                        open_prices[inst],
                        date=date,
                        decision_date=decision_date,
                        reason="monthly_rebalance",
                        stop_price=proposed_stops.get(inst),
                    )
            if cash < -1e-6:
                raise RuntimeError("Book R stop sizing attempted to borrow cash")
            cash = max(0.0, cash)
            for inst in instruments:
                stops[inst] = proposed_stops.get(inst) if units[inst] > 0.0 else None
            if pre_trade_equity > 0.0:
                position_risks = [
                    desired_units[inst] * (open_prices[inst] - proposed_stops[inst])
                    / pre_trade_equity
                    for inst in proposed_stops
                    if desired_units[inst] > 0.0
                ]
                planned_position_risk_fractions.extend(position_risks)
                planned_aggregate_risk_fractions.append(sum(position_risks))
                planned_gross_fractions.append(
                    sum(desired_units[inst] * open_prices[inst] for inst in instruments)
                    / pre_trade_equity
                )
            pending = None

        # With no competing profit target, a daily low touching the stop has no
        # ambiguous intrabar ordering.  A newly entered position may stop on its
        # entry day after the opening fill.
        for inst in instruments:
            stop = stops[inst]
            if units[inst] <= 0.0 or stop is None:
                continue
            low = float(checked[inst]["low"].iloc[i])
            if low <= stop:
                fill = stop * (1.0 - stop_slippage_rate)
                execute(
                    inst,
                    -units[inst],
                    fill,
                    date=date,
                    decision_date=date,
                    reason="stop_loss",
                    stop_price=stop,
                    gap_through_stop=False,
                )
                stops[inst] = None

        close_prices = {inst: float(checked[inst]["close"].iloc[i]) for inst in instruments}
        gross = sum(units[inst] * close_prices[inst] for inst in instruments)
        close_equity = cash + gross
        equity_rows.append((date, close_equity))
        gross_rows.append((date, gross / close_equity if close_equity > 0.0 else 0.0))

        is_month_end = i + 1 < len(index) and index[i].month != index[i + 1].month
        if is_month_end and date >= start_ts:
            next_date = index[i + 1]
            if next_date <= end_ts:
                selected = select_book_r(checked, signal_spec, i)
                selected_with_atr = [
                    row for row in selected
                    if np.isfinite(float(atr[row["instrument"]].iloc[i]))
                    and float(atr[row["instrument"]].iloc[i]) > 0.0
                ]
                selections.append({
                    "decision_date": _date_str(date),
                    "fill_date": _date_str(next_date),
                    "selected": selected_with_atr,
                })
                pending = {
                    "decision_date": date,
                    "fill_date": next_date,
                    "selected": selected_with_atr,
                    "atr": {
                        row["instrument"]: float(atr[row["instrument"]].iloc[i])
                        for row in selected_with_atr
                    },
                }

    if not equity_rows:
        raise ValueError("no Book R stop-overlay equity observations were created")

    final_date = equity_rows[-1][0]
    final_i = int(index.get_loc(final_date))
    final_prices = {inst: float(checked[inst]["close"].iloc[final_i]) for inst in instruments}
    for inst in instruments:
        if units[inst] <= 0.0:
            continue
        execute(
            inst,
            -units[inst],
            final_prices[inst],
            date=final_date,
            decision_date=final_date,
            reason="final_liquidation",
        )
        stops[inst] = None
    equity_rows[-1] = (final_date, cash)

    equity = pd.Series(
        [value for _, value in equity_rows],
        index=pd.DatetimeIndex([date for date, _ in equity_rows], name="timestamp"),
        name="equity_usd",
        dtype=float,
    )
    gross_exposure = pd.Series(
        [value for _, value in gross_rows],
        index=equity.index,
        name="gross_exposure",
        dtype=float,
    )
    metrics = _extended_metrics(
        equity,
        events,
        total_cost=total_cost,
        selections=len(selections),
        gross_exposure=gross_exposure,
    )
    metrics.update({
        "max_planned_position_price_risk_fraction_before_costs": (
            max(planned_position_risk_fractions) if planned_position_risk_fractions else 0.0
        ),
        "max_planned_aggregate_price_risk_fraction_before_costs": (
            max(planned_aggregate_risk_fractions) if planned_aggregate_risk_fractions else 0.0
        ),
        "max_planned_gross_fraction": max(planned_gross_fractions) if planned_gross_fractions else 0.0,
        "minimum_cash_usd": float(minimum_cash),
    })
    return BookRStopRun(
        spec=spec,
        start=start_ts,
        end=end_ts,
        equity=equity,
        events=events,
        selections=selections,
        metrics=metrics,
    )
