"""Causal, gap-aware research simulator for the frozen Book G protocol.

Book G is deliberately isolated from every live and paper engine.  It is a
long-only USD ETF strategy evaluated in independently flat segments.  Signals
formed at a weekly close can trade only at the next XNYS open; all transaction
costs, gap stops and terminal liquidation are explicit.

Daily OHLC data cannot reconstruct an FTMO CE(S)T midnight-to-midnight bid/ask
path.  ``intraday_min_equity`` is therefore a conservative regular-session
co-extreme proxy, not broker-executable proof.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Iterable, Mapping

import exchange_calendars as xcals
import numpy as np
import pandas as pd


ACCOUNT_USD = 100_000.0
REGIME_SYMBOL = "SPY"
SECTOR_SYMBOLS: tuple[str, ...] = (
    "XLK",
    "XLE",
    "XLV",
    "XLI",
    "XLF",
    "XLP",
    "XLU",
)
DEFENSIVE_SYMBOLS: tuple[str, ...] = ("GLD", "TLT", "IEF", "SHY", "UUP")
ALL_SYMBOLS: tuple[str, ...] = (REGIME_SYMBOL, *SECTOR_SYMBOLS, *DEFENSIVE_SYMBOLS)
MOMENTUM_LOOKBACKS: tuple[int, ...] = (63, 126, 252)

PROTOCOL_PATH = "engine/data_store/book_g_macro_guard_prereg_2026-09-04.md"
SCHEMA_VERSION = "book_g_macro_guard_run_v1"
_PRICE_COLUMNS = ("open", "high", "low", "close")
_XNYS = xcals.get_calendar("XNYS")
_TOL = 1e-8


def _utc(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _date(value: Any) -> str:
    return _utc(value).strftime("%Y-%m-%d")


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, pd.DataFrame):
        return [_json_ready(row) for row in value.to_dict(orient="records")]
    if isinstance(value, pd.Series):
        return [_json_ready({"date": index, "value": item}) for index, item in value.items()]
    if isinstance(value, pd.Timestamp):
        return _date(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return round(number, 10) if isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        _json_ready(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Frozen Book G architecture with a declared momentum/cost cell."""

    momentum_lookback: int
    fee_bps: float = 5.0
    stop_slippage_bps: float = 0.0
    volatility_window: int = 63
    sma_window: int = 200
    atr_window: int = 20
    stop_atr_multiple: float = 2.5
    risk_per_trade: float = 0.0035
    aggregate_risk_cap: float = 0.025
    bull_gross_cap: float = 0.50
    bear_gross_cap: float = 0.20
    bull_slots: int = 4
    bear_slots: int = 2

    def __post_init__(self) -> None:
        if int(self.momentum_lookback) not in MOMENTUM_LOOKBACKS:
            raise ValueError(f"momentum_lookback must be one of {MOMENTUM_LOOKBACKS}")
        frozen = {
            "volatility_window": (self.volatility_window, 63),
            "sma_window": (self.sma_window, 200),
            "atr_window": (self.atr_window, 20),
            "stop_atr_multiple": (self.stop_atr_multiple, 2.5),
            "risk_per_trade": (self.risk_per_trade, 0.0035),
            "aggregate_risk_cap": (self.aggregate_risk_cap, 0.025),
            "bull_gross_cap": (self.bull_gross_cap, 0.50),
            "bear_gross_cap": (self.bear_gross_cap, 0.20),
            "bull_slots": (self.bull_slots, 4),
            "bear_slots": (self.bear_slots, 2),
        }
        changed = [name for name, (actual, expected) in frozen.items() if actual != expected]
        if changed:
            raise ValueError("Book G architecture is frozen; changed: " + ", ".join(changed))
        if not isfinite(float(self.fee_bps)) or float(self.fee_bps) < 0.0:
            raise ValueError("fee_bps must be finite and non-negative")
        if not isfinite(float(self.stop_slippage_bps)) or float(self.stop_slippage_bps) < 0.0:
            raise ValueError("stop_slippage_bps must be finite and non-negative")

    @property
    def fee_rate(self) -> float:
        return float(self.fee_bps) / 10_000.0

    @property
    def stop_slippage_rate(self) -> float:
        return float(self.stop_slippage_bps) / 10_000.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BacktestResult:
    config: BacktestConfig
    start: pd.Timestamp
    end: pd.Timestamp
    equity: pd.Series
    trades: pd.DataFrame
    daily: pd.DataFrame
    events: pd.DataFrame
    decisions: list[dict[str, Any]]
    metrics: dict[str, Any]
    invariants: dict[str, Any]

    def to_dict(self, *, equity_points: int = 512) -> dict[str, Any]:
        step = max(1, int(np.ceil(len(self.equity) / max(1, equity_points))))
        curve = [
            {"date": _date(index), "equity_usd": float(value)}
            for index, value in self.equity.iloc[::step].items()
        ]
        return _json_ready(
            {
                "schema_version": SCHEMA_VERSION,
                "config": self.config.to_dict(),
                "start": self.start,
                "end": self.end,
                "metrics": self.metrics,
                "invariants": self.invariants,
                "trades": self.trades,
                "daily": self.daily,
                "events": self.events,
                "decisions": self.decisions,
                "equity_curve": curve,
            }
        )


@dataclass(slots=True)
class _Position:
    symbol: str
    quantity: float
    initial_quantity: float
    entry_price: float
    entry_date: pd.Timestamp
    decision_date: pd.Timestamp
    initial_stop: float
    stop: float
    initial_risk_per_unit: float
    entry_fee: float
    regime: str
    exit_fee: float = 0.0
    gross_pnl: float = 0.0
    exit_notional: float = 0.0
    exited_quantity: float = 0.0
    breakeven_armed: bool = False


def _official_sessions(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    sessions = pd.DatetimeIndex(
        _XNYS.sessions_in_range(start.tz_localize(None), end.tz_localize(None))
    )
    return sessions.tz_localize("UTC") if sessions.tz is None else sessions.tz_convert("UTC")


def _as_panel_dict(
    panel: pd.DataFrame | Mapping[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    if isinstance(panel, pd.DataFrame):
        frame = panel.copy()
        if "date" not in frame.columns and "timestamp" in frame.columns:
            frame = frame.rename(columns={"timestamp": "date"})
        required = {"date", "symbol", *_PRICE_COLUMNS}
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError("long panel lacks columns: " + ", ".join(missing))
        if frame.duplicated(["date", "symbol"]).any():
            raise ValueError("long panel contains duplicate date-symbol rows")
        return {
            str(symbol): part.drop(columns=["symbol"]).set_index("date")
            for symbol, part in frame.groupby("symbol", sort=True)
        }
    return {str(symbol): frame.copy() for symbol, frame in panel.items()}


def validate_panel(
    panel: pd.DataFrame | Mapping[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Return the fixed, complete XNYS panel in canonical symbol order."""

    raw = _as_panel_dict(panel)
    symbols = set(raw)
    missing_symbols = sorted(set(ALL_SYMBOLS) - symbols)
    unknown_symbols = sorted(symbols - set(ALL_SYMBOLS))
    if missing_symbols or unknown_symbols:
        detail: list[str] = []
        if missing_symbols:
            detail.append("missing: " + ", ".join(missing_symbols))
        if unknown_symbols:
            detail.append("unknown: " + ", ".join(unknown_symbols))
        raise ValueError("Book G requires its frozen 13-symbol universe (" + "; ".join(detail) + ")")

    checked: dict[str, pd.DataFrame] = {}
    canonical_index: pd.DatetimeIndex | None = None
    for symbol in sorted(ALL_SYMBOLS):
        frame = raw[symbol].copy()
        absent = sorted(set(_PRICE_COLUMNS) - set(frame.columns))
        if absent:
            raise ValueError(f"{symbol} lacks columns: {', '.join(absent)}")
        if not isinstance(frame.index, pd.DatetimeIndex):
            frame.index = pd.to_datetime(frame.index, errors="raise")
        index = (
            frame.index.tz_localize("UTC")
            if frame.index.tz is None
            else frame.index.tz_convert("UTC")
        ).normalize()
        frame.index = index
        frame = frame.sort_index(kind="stable")
        if frame.index.has_duplicates:
            raise ValueError(f"{symbol} has duplicate normalized dates")
        prices = frame.loc[:, list(_PRICE_COLUMNS)].astype(float)
        values = prices.to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0.0).any():
            raise ValueError(f"{symbol} contains non-finite or non-positive OHLC")
        invalid = (
            prices["high"] + 1e-10 < prices[["open", "close"]].max(axis=1)
        ) | (
            prices["low"] - 1e-10 > prices[["open", "close"]].min(axis=1)
        ) | (prices["high"] + 1e-10 < prices["low"])
        if bool(invalid.any()):
            raise ValueError(f"{symbol} violates OHLC ordering")
        frame.loc[:, list(_PRICE_COLUMNS)] = prices
        checked[symbol] = frame.loc[:, list(_PRICE_COLUMNS)].copy()
        if canonical_index is None:
            canonical_index = frame.index
        elif not canonical_index.equals(frame.index):
            raise ValueError(f"{symbol} does not have the exact common session index")

    assert canonical_index is not None
    minimum = 1 + max(MOMENTUM_LOOKBACKS[-1], 200, 63, 20)
    if len(canonical_index) < minimum:
        raise ValueError(f"Book G panel needs at least {minimum} sessions")
    expected = _official_sessions(canonical_index[0], canonical_index[-1])
    missing = expected.difference(canonical_index)
    extra = canonical_index.difference(expected)
    if len(missing) or len(extra):
        problems: list[str] = []
        if len(missing):
            problems.append("missing XNYS: " + ", ".join(_date(item) for item in missing[:5]))
        if len(extra):
            problems.append("non-XNYS: " + ", ".join(_date(item) for item in extra[:5]))
        raise ValueError("Book G panel is not a complete XNYS span (" + "; ".join(problems) + ")")
    return {symbol: checked[symbol] for symbol in sorted(ALL_SYMBOLS)}


def panel_sha256(panel: pd.DataFrame | Mapping[str, pd.DataFrame]) -> str:
    checked = validate_panel(panel)
    digest = sha256()
    for symbol, frame in checked.items():
        digest.update(symbol.encode("ascii"))
        digest.update(np.asarray(frame.index.asi8, dtype="<i8").tobytes())
        digest.update(np.asarray(frame.loc[:, list(_PRICE_COLUMNS)], dtype="<f8").tobytes())
    return digest.hexdigest()


def _atr(frame: pd.DataFrame, i: int, window: int = 20) -> float:
    start = i - window + 1
    if start < 1:
        return float("nan")
    current = frame.iloc[start : i + 1]
    previous = frame["close"].shift(1).iloc[start : i + 1]
    true_range = pd.concat(
        (
            current["high"] - current["low"],
            (current["high"] - previous).abs(),
            (current["low"] - previous).abs(),
        ),
        axis=1,
    ).max(axis=1)
    return float(true_range.mean()) if len(true_range) == window else float("nan")


def _signal_at(
    panel: Mapping[str, pd.DataFrame], config: BacktestConfig, i: int
) -> dict[str, Any]:
    index = next(iter(panel.values())).index
    if i < max(config.momentum_lookback, config.volatility_window, config.sma_window - 1):
        return {
            "decision_date": _date(index[i]),
            "regime": "unavailable",
            "gross_cap": 0.0,
            "selected": [],
        }
    spy_close = panel[REGIME_SYMBOL]["close"].astype(float)
    spy_sma = float(spy_close.iloc[i - config.sma_window + 1 : i + 1].mean())
    bull = float(spy_close.iloc[i]) >= spy_sma
    regime = "bull" if bull else "bear"
    universe = SECTOR_SYMBOLS if bull else DEFENSIVE_SYMBOLS
    slots = config.bull_slots if bull else config.bear_slots
    gross_cap = config.bull_gross_cap if bull else config.bear_gross_cap
    rows: list[dict[str, Any]] = []
    for symbol in sorted(universe):
        frame = panel[symbol]
        close = frame["close"].astype(float)
        momentum = float(close.iloc[i] / close.iloc[i - config.momentum_lookback] - 1.0)
        sma = float(close.iloc[i - config.sma_window + 1 : i + 1].mean())
        returns = np.log(close.iloc[i - config.volatility_window : i + 1]).diff().dropna()
        annual_vol = float(returns.std(ddof=1) * np.sqrt(252.0))
        atr = _atr(frame, i, config.atr_window)
        eligible = bool(
            isfinite(momentum)
            and isfinite(annual_vol)
            and annual_vol > 0.0
            and isfinite(atr)
            and atr > 0.0
            and momentum > 0.0
            and float(close.iloc[i]) > sma
        )
        rows.append(
            {
                "symbol": symbol,
                "momentum": momentum,
                "annualized_volatility": annual_vol,
                "score": momentum / max(annual_vol, 1e-12),
                "sma200": sma,
                "atr20": atr,
                "eligible": eligible,
            }
        )
    eligible_rows = [row for row in rows if row["eligible"]]
    eligible_rows.sort(key=lambda row: (-float(row["score"]), str(row["symbol"])))
    selected = eligible_rows[:slots]
    return {
        "decision_date": _date(index[i]),
        "regime": regime,
        "spy_close": float(spy_close.iloc[i]),
        "spy_sma200": spy_sma,
        "gross_cap": gross_cap,
        "selected": selected,
        "eligible": rows,
    }


def build_signal_panel(
    panel: pd.DataFrame | Mapping[str, pd.DataFrame], lookback: int
) -> pd.DataFrame:
    """Materialise causal signal diagnostics; no row reads a later close."""

    config = BacktestConfig(momentum_lookback=lookback)
    checked = validate_panel(panel)
    index = next(iter(checked.values())).index
    records: list[dict[str, Any]] = []
    for i, date in enumerate(index):
        decision = _signal_at(checked, config, i)
        selected = {str(row["symbol"]): rank for rank, row in enumerate(decision["selected"], 1)}
        for row in decision.get("eligible", []):
            records.append(
                {
                    "date": date,
                    "symbol": row["symbol"],
                    "regime": decision["regime"],
                    "momentum": row["momentum"],
                    "annualized_volatility": row["annualized_volatility"],
                    "score": row["score"],
                    "sma200": row["sma200"],
                    "atr20": row["atr20"],
                    "eligible": row["eligible"],
                    "selected_rank": selected.get(str(row["symbol"])),
                }
            )
    return pd.DataFrame.from_records(records)


def _is_week_end(index: pd.DatetimeIndex, i: int) -> bool:
    if i + 1 >= len(index):
        return True
    current = index[i].isocalendar()
    following = index[i + 1].isocalendar()
    return (int(current.year), int(current.week)) != (int(following.year), int(following.week))


def _planned_loss_per_unit(
    entry_or_mark: float,
    stop: float,
    *,
    fee_rate: float,
    stop_slippage_rate: float,
    include_entry_fee: bool,
) -> tuple[float, float]:
    stressed_stop = stop * (1.0 - stop_slippage_rate)
    value = max(entry_or_mark - stressed_stop, 0.0) + fee_rate * stressed_stop
    if include_entry_fee:
        value += fee_rate * entry_or_mark
    return float(value), float(stressed_stop)


def _metrics(
    equity: pd.Series,
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    initial: float,
) -> dict[str, Any]:
    end_equity = float(equity.iloc[-1])
    elapsed_days = max(1.0, float((equity.index[-1] - equity.index[0]).days))
    cagr = float((end_equity / initial) ** (365.2425 / elapsed_days) - 1.0)
    predecessor = pd.Series([initial], index=[equity.index[0] - pd.Timedelta(nanoseconds=1)])
    returns = pd.concat([predecessor, equity]).pct_change().iloc[1:]
    std = float(returns.std(ddof=1))
    sharpe = float(returns.mean() / std * np.sqrt(252.0)) if std > 0.0 else None
    curve_with_seed = pd.concat([predecessor, equity])
    drawdown = curve_with_seed / curve_with_seed.cummax() - 1.0
    max_drawdown = float(-drawdown.min())
    months = pd.period_range(equity.index[0].tz_localize(None), equity.index[-1].tz_localize(None), freq="M")
    avg_monthly = float((end_equity - initial) / len(months))
    worst_day = float(
        ((daily["intraday_min_equity"] - daily["day_start_balance"]) / daily["day_start_balance"]).min()
    )
    if trades.empty:
        wins = pd.Series(dtype=float)
        losses = pd.Series(dtype=float)
        profit_factor: float | None = None
        win_rate = None
    else:
        pnl = trades["net_pnl"].astype(float)
        wins = pnl[pnl > 0.0]
        losses = pnl[pnl < 0.0]
        profit_factor = float(wins.sum() / -losses.sum()) if not losses.empty else None
        win_rate = float((pnl > 0.0).mean())
    annual_returns: dict[str, float] = {}
    prior = initial
    for year, group in equity.groupby(equity.index.year, sort=True):
        final = float(group.iloc[-1])
        annual_returns[str(int(year))] = final / prior - 1.0
        prior = final
    return {
        "initial_equity": initial,
        "ending_equity": end_equity,
        "net_profit": end_equity - initial,
        "total_return": end_equity / initial - 1.0,
        "cagr": cagr,
        "avg_monthly_profit": avg_monthly,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "worst_day": worst_day,
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "trades": int(len(trades)),
        "winning_trades": int(len(wins)),
        "losing_trades": int(len(losses)),
        "annual_returns": annual_returns,
        "total_fees": float(trades["entry_fee"].sum() + trades["exit_fee"].sum()) if not trades.empty else 0.0,
        "sessions": int(len(equity)),
        "months": int(len(months)),
    }


def run_backtest(
    panel: pd.DataFrame | Mapping[str, pd.DataFrame],
    start: pd.Timestamp | str,
    end: pd.Timestamp | str,
    config: BacktestConfig,
    *,
    initial_equity: float = ACCOUNT_USD,
) -> BacktestResult:
    """Run one independently flat, causal Book G segment."""

    if not isfinite(float(initial_equity)) or float(initial_equity) <= 0.0:
        raise ValueError("initial_equity must be finite and positive")
    checked = validate_panel(panel)
    index = next(iter(checked.values())).index
    start_ts = _utc(start).normalize()
    end_ts = _utc(end).normalize()
    if end_ts <= start_ts:
        raise ValueError("end must be after start")
    active = index[(index >= start_ts) & (index <= end_ts)]
    expected_active = _official_sessions(start_ts, end_ts)
    if not active.equals(expected_active):
        missing = expected_active.difference(active)
        raise ValueError(
            "segment does not cover every requested XNYS session"
            + (
                ": " + ", ".join(_date(item) for item in missing[:5])
                if len(missing)
                else ""
            )
        )
    if len(active) < 2:
        raise ValueError("segment has fewer than two XNYS sessions")
    first_i = int(index.get_loc(active[0]))
    final_i = int(index.get_loc(active[-1]))
    if first_i < max(config.momentum_lookback, config.sma_window - 1, config.volatility_window):
        raise ValueError("segment lacks the required causal warm-up")

    fee_rate = config.fee_rate
    slip_rate = config.stop_slippage_rate
    cash = float(initial_equity)
    balance = float(initial_equity)
    previous_equity = float(initial_equity)
    positions: dict[str, _Position] = {}
    stopped_on: dict[str, pd.Timestamp] = {}
    pending: dict[str, Any] | None = None
    active_gross_cap = 0.0
    active_regime = "cash"
    trim_next_open = False
    decisions: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    execution_snapshots: list[dict[str, float]] = []
    accounting_ok = True
    marked_overrun_dates: list[pd.Timestamp] = []
    cap_check_dates: set[pd.Timestamp] = set()

    def mark_equity(prices: Mapping[str, float]) -> float:
        return float(cash + sum(position.quantity * prices[symbol] for symbol, position in positions.items()))

    def open_risk(prices: Mapping[str, float]) -> float:
        del prices  # Planned stop loss is measured from the actual entry basis.
        total = 0.0
        for symbol, position in positions.items():
            loss, _ = _planned_loss_per_unit(
                position.entry_price,
                position.stop,
                fee_rate=fee_rate,
                stop_slippage_rate=slip_rate,
                include_entry_fee=False,
            )
            total += position.quantity * loss
        return float(total)

    def snapshot(prices: Mapping[str, float], equity_value: float, cap: float) -> dict[str, float]:
        gross = float(sum(position.quantity * prices[symbol] for symbol, position in positions.items()))
        risk = open_risk(prices)
        capital = max(0.0, min(equity_value, ACCOUNT_USD))
        row = {
            "gross": gross,
            "gross_fraction": gross / equity_value if equity_value > 0.0 else float("inf"),
            "planned_risk": risk,
            "planned_risk_fraction": risk / capital if capital > 0.0 else float("inf"),
            "gross_cap": float(cap),
        }
        execution_snapshots.append(row)
        return row

    def record_sell(
        symbol: str,
        quantity: float,
        price: float,
        date: pd.Timestamp,
        reason: str,
        *,
        decision_date: pd.Timestamp | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        nonlocal cash, balance
        position = positions[symbol]
        quantity = min(float(quantity), position.quantity)
        if quantity <= _TOL:
            return
        fee = quantity * price * fee_rate
        gross = quantity * (price - position.entry_price)
        cash += quantity * price - fee
        balance += gross - fee
        position.quantity -= quantity
        position.exit_fee += fee
        position.gross_pnl += gross
        position.exit_notional += quantity * price
        position.exited_quantity += quantity
        event = {
            "date": date,
            "decision_date": decision_date or position.decision_date,
            "symbol": symbol,
            "side": "sell",
            "reason": reason,
            "quantity": quantity,
            "price": price,
            "fee": fee,
            "resting_stop": position.stop,
        }
        if extra:
            event.update(extra)
        event_rows.append(event)
        if position.quantity <= max(_TOL, position.initial_quantity * 1e-12):
            position.quantity = 0.0
            average_exit = (
                position.exit_notional / position.exited_quantity
                if position.exited_quantity > 0.0
                else price
            )
            trade_rows.append(
                {
                    "symbol": symbol,
                    "decision_date": position.decision_date,
                    "entry_date": position.entry_date,
                    "entry_price": position.entry_price,
                    "exit_date": date,
                    "exit_price": average_exit,
                    "initial_stop": position.initial_stop,
                    "quantity": position.initial_quantity,
                    "entry_fee": position.entry_fee,
                    "exit_fee": position.exit_fee,
                    "gross_pnl": position.gross_pnl,
                    "net_pnl": position.gross_pnl - position.entry_fee - position.exit_fee,
                    "exit_reason": reason,
                    "breakeven_armed": position.breakeven_armed,
                    "regime": position.regime,
                }
            )
            del positions[symbol]

    def trim_to_caps(date: pd.Timestamp, prices: Mapping[str, float]) -> set[str]:
        nonlocal trim_next_open
        cap_check_dates.add(date)
        closed: set[str] = set()
        if not positions or active_gross_cap <= 0.0:
            trim_next_open = False
            return closed

        def caps_satisfied() -> bool:
            equity_value = mark_equity(prices)
            capital = max(0.0, min(equity_value, ACCOUNT_USD))
            gross = sum(
                position.quantity * prices[symbol]
                for symbol, position in positions.items()
            )
            risk = open_risk(prices)
            leg_risks = []
            for position in positions.values():
                per_unit, _ = _planned_loss_per_unit(
                    position.entry_price,
                    position.stop,
                    fee_rate=fee_rate,
                    stop_slippage_rate=slip_rate,
                    include_entry_fee=False,
                )
                leg_risks.append(position.quantity * per_unit)
            return bool(
                equity_value > 0.0
                and gross <= active_gross_cap * equity_value + 1e-7
                and risk <= config.aggregate_risk_cap * capital + 1e-7
                and max(leg_risks, default=0.0)
                <= config.risk_per_trade * capital + 1e-7
            )

        # There are no partial profit-taking exits.  If passive drift creates
        # an executable cap breach, liquidate whole legs, largest notional
        # first (symbol is the deterministic tie-break), until compliant.
        while positions and not caps_satisfied():
            symbol = min(
                positions,
                key=lambda item: (
                    -positions[item].quantity * prices[item],
                    item,
                ),
            )
            record_sell(
                symbol,
                positions[symbol].quantity,
                prices[symbol],
                date,
                "risk_cap_liquidation",
                decision_date=date,
            )
            closed.add(symbol)
        trim_next_open = False
        return closed

    # A segment may execute only a genuine decision made before its first date.
    if first_i > 0 and _is_week_end(index, first_i - 1):
        seeded = _signal_at(checked, config, first_i - 1)
        seeded["fill_date"] = _date(active[0])
        seeded["segment_boundary_seed"] = True
        pending = seeded
        decisions.append(seeded)

    for i in range(first_i, final_i + 1):
        date = index[i]
        is_terminal = i == final_i
        risk_blocked_today = bool(trim_next_open)
        day_start_balance = float(balance)
        day_start_equity = float(previous_equity)
        opened_today = 0
        open_prices = {symbol: float(frame["open"].iloc[i]) for symbol, frame in checked.items()}

        # Resting stops always outrank scheduled exits and entries at the open.
        for symbol in sorted(tuple(positions)):
            position = positions.get(symbol)
            if position is None:
                continue
            opening = open_prices[symbol]
            if opening <= position.stop:
                fill = opening * (1.0 - slip_rate)
                resting = position.stop
                record_sell(
                    symbol, position.quantity, fill, date, "stop_gap",
                    extra={"resting_stop": resting, "gap_open": opening, "stop_slippage_bps": config.stop_slippage_bps},
                )
                stopped_on[symbol] = date

        if trim_next_open and pending is None:
            trim_to_caps(date, open_prices)
            snapshot(open_prices, mark_equity(open_prices), active_gross_cap)

        if pending is not None and _utc(pending["fill_date"]) == date:
            decision_date = _utc(pending["decision_date"])
            target_rows = {str(row["symbol"]): row for row in pending["selected"]}
            # Sells precede buys; a same-open gap stop cannot be re-entered from
            # the older decision that generated this order batch.
            for symbol in sorted(tuple(positions)):
                if symbol not in target_rows:
                    record_sell(symbol, positions[symbol].quantity, open_prices[symbol], date, "weekly_rotation", decision_date=decision_date)

            active_regime = str(pending["regime"])
            active_gross_cap = float(pending["gross_cap"])
            # Retained targets can arrive over the newly applicable cap after
            # passive price drift.  Correct them at this executable open before
            # considering any new risk.
            cap_liquidated = trim_to_caps(date, open_prices)
            pre_trade_equity = mark_equity(open_prices)
            capital = max(0.0, min(pre_trade_equity, ACCOUNT_USD))
            current_gross = sum(position.quantity * open_prices[symbol] for symbol, position in positions.items())
            current_risk = open_risk(open_prices)
            new_rows = [
                target_rows[symbol]
                for symbol in sorted(target_rows)
                if symbol not in positions
                and symbol not in cap_liquidated
                and not risk_blocked_today
                and not (
                    symbol in stopped_on and stopped_on[symbol] > decision_date
                )
            ]
            # Entry fees reduce post-fill equity.  Solve the cap against that
            # post-fee denominator instead of sizing to a pre-fee 50%/20%
            # number that would be microscopically over the limit after costs.
            remaining_gross = max(
                0.0,
                (active_gross_cap * pre_trade_equity - current_gross)
                / (1.0 + active_gross_cap * fee_rate),
            )
            remaining_risk = max(0.0, config.aggregate_risk_cap * capital - current_risk)
            regime_slots = (
                config.bull_slots if active_regime == "bull" else config.bear_slots
            )
            per_target_slot = (
                active_gross_cap * pre_trade_equity / regime_slots
                if regime_slots > 0
                else 0.0
            )
            slot_notional = (
                min(per_target_slot, remaining_gross / len(new_rows))
                if new_rows
                else 0.0
            )
            for offset, row in enumerate(new_rows):
                symbol = str(row["symbol"])
                entry = open_prices[symbol]
                initial_stop = entry - config.stop_atr_multiple * float(row["atr20"])
                if initial_stop <= 0.0 or entry <= initial_stop:
                    continue
                per_unit, _ = _planned_loss_per_unit(
                    entry, initial_stop, fee_rate=fee_rate,
                    stop_slippage_rate=slip_rate, include_entry_fee=True,
                )
                remaining_slots = max(1, len(new_rows) - offset)
                risk_budget = min(config.risk_per_trade * capital, remaining_risk / remaining_slots)
                risk_quantity = risk_budget / per_unit if per_unit > 0.0 else 0.0
                gross_quantity = slot_notional / entry if entry > 0.0 else 0.0
                cash_quantity = max(0.0, cash) / (entry * (1.0 + fee_rate))
                quantity = max(0.0, min(risk_quantity, gross_quantity, cash_quantity))
                if quantity <= _TOL:
                    continue
                fee = quantity * entry * fee_rate
                cash -= quantity * entry + fee
                balance -= fee
                positions[symbol] = _Position(
                    symbol=symbol,
                    quantity=quantity,
                    initial_quantity=quantity,
                    entry_price=entry,
                    entry_date=date,
                    decision_date=decision_date,
                    initial_stop=initial_stop,
                    stop=initial_stop,
                    initial_risk_per_unit=entry - initial_stop,
                    entry_fee=fee,
                    regime=active_regime,
                )
                opened_today += 1
                remaining_risk = max(0.0, remaining_risk - quantity * per_unit)
                event_rows.append(
                    {
                        "date": date,
                        "decision_date": decision_date,
                        "symbol": symbol,
                        "side": "buy",
                        "reason": "weekly_entry",
                        "quantity": quantity,
                        "price": entry,
                        "fee": fee,
                        "resting_stop": initial_stop,
                        "planned_loss_per_unit": per_unit,
                    }
                )
            post_equity = mark_equity(open_prices)
            pending["execution"] = snapshot(open_prices, post_equity, active_gross_cap)
            pending["fill_date"] = _date(date)
            pending = None

        # An opening print at or above +1R has known ordering: the threshold
        # was reached before the session's high/low ambiguity begins.  Arm BE
        # immediately, so a later move back through entry exits on this bar.
        for symbol in sorted(tuple(positions)):
            position = positions[symbol]
            trigger = position.entry_price + position.initial_risk_per_unit
            if position.stop < position.entry_price and open_prices[symbol] >= trigger:
                old_stop = position.stop
                position.stop = position.entry_price
                position.breakeven_armed = True
                event_rows.append(
                    {
                        "date": date,
                        "decision_date": position.decision_date,
                        "symbol": symbol,
                        "side": "modify",
                        "reason": "breakeven_ratchet",
                        "quantity": position.quantity,
                        "price": position.entry_price,
                        "fee": 0.0,
                        "resting_stop": old_stop,
                        "new_stop": position.stop,
                        "effective_next_session": False,
                        "triggered_at_open": True,
                    }
                )

        open_equity = mark_equity(open_prices)
        pre_intraday_cash = float(cash)
        # Simultaneous adverse marks deliberately overstate what daily bars can
        # establish, while including the actual fee/slippage on crossed stops.
        proxy_min = pre_intraday_cash
        for symbol, position in sorted(positions.items()):
            low = float(checked[symbol]["low"].iloc[i])
            if low <= position.stop:
                stop_fill = position.stop * (1.0 - slip_rate)
                proxy_min += position.quantity * stop_fill * (1.0 - fee_rate)
            else:
                proxy_min += position.quantity * low

        # Intraday stops use the stop that was resting before this bar.  A +1R
        # touch arms price breakeven only after a bar survives, so daily-bar
        # ordering can never manufacture a same-bar profitable stop.
        for symbol in sorted(tuple(positions)):
            position = positions.get(symbol)
            if position is None:
                continue
            low = float(checked[symbol]["low"].iloc[i])
            if low <= position.stop:
                resting = position.stop
                fill = resting * (1.0 - slip_rate)
                record_sell(
                    symbol, position.quantity, fill, date, "stop_intraday",
                    extra={"resting_stop": resting, "gap_open": None, "stop_slippage_bps": config.stop_slippage_bps},
                )

        for symbol in sorted(tuple(positions)):
            position = positions[symbol]
            high = float(checked[symbol]["high"].iloc[i])
            trigger = position.entry_price + position.initial_risk_per_unit
            if position.stop < position.entry_price and high >= trigger:
                old_stop = position.stop
                position.stop = position.entry_price
                position.breakeven_armed = True
                event_rows.append(
                    {
                        "date": date,
                        "decision_date": position.decision_date,
                        "symbol": symbol,
                        "side": "modify",
                        "reason": "breakeven_ratchet",
                        "quantity": position.quantity,
                        "price": position.entry_price,
                        "fee": 0.0,
                        "resting_stop": old_stop,
                        "new_stop": position.stop,
                        "effective_next_session": True,
                    }
                )

        if not is_terminal and _is_week_end(index, i) and i + 1 <= final_i:
            formed = _signal_at(checked, config, i)
            formed["fill_date"] = _date(index[i + 1])
            decisions.append(formed)
            pending = formed

        if is_terminal:
            for symbol in sorted(tuple(positions)):
                close = float(checked[symbol]["close"].iloc[i])
                record_sell(symbol, positions[symbol].quantity, close, date, "terminal", decision_date=date)

        close_prices = {symbol: float(frame["close"].iloc[i]) for symbol, frame in checked.items()}
        end_equity = mark_equity(close_prices)
        gross = sum(position.quantity * close_prices[symbol] for symbol, position in positions.items())
        risk = open_risk(close_prices)
        capital = max(0.0, min(end_equity, ACCOUNT_USD))
        gross_fraction = gross / end_equity if end_equity > 0.0 else float("inf")
        risk_fraction = risk / capital if capital > 0.0 else float("inf")
        overrun = bool(
            positions and active_gross_cap > 0.0 and (
                gross_fraction > active_gross_cap + 1e-12
                or risk_fraction > config.aggregate_risk_cap + 1e-12
            )
        )
        trim_next_open = overrun and not is_terminal
        if overrun:
            marked_overrun_dates.append(date)
        conservative_min = min(day_start_equity, open_equity, proxy_min, end_equity)
        holdings_basis = balance + sum(
            position.quantity * (close_prices[symbol] - position.entry_price)
            for symbol, position in positions.items()
        )
        reconciles = abs(holdings_basis - end_equity) <= max(1e-6, abs(end_equity) * 1e-10)
        accounting_ok = accounting_ok and reconciles
        daily_rows.append(
            {
                "date": date,
                "day_start_balance": day_start_balance,
                "day_start_equity": day_start_equity,
                "end_balance": float(balance),
                "equity": end_equity,
                "intraday_min_equity": conservative_min,
                "closed_pnl": float(balance - day_start_balance),
                "positions_opened": opened_today,
                "gross_exposure": float(gross),
                "gross_exposure_fraction": float(gross_fraction),
                "planned_risk": float(risk),
                "planned_risk_fraction": float(risk_fraction),
                "regime": active_regime,
                "gross_cap": float(active_gross_cap),
                "marked_cap_overrun": overrun,
                "flat_end": bool(not positions),
                "accounting_reconciles": reconciles,
            }
        )
        equity_rows.append((date, end_equity))
        previous_equity = end_equity

    if positions:
        raise RuntimeError("terminal liquidation left an open Book G position")
    if cash < -1e-6:
        raise RuntimeError("Book G borrowed cash")

    equity = pd.Series(
        [value for _, value in equity_rows],
        index=pd.DatetimeIndex([date for date, _ in equity_rows], name="date"),
        name="equity",
        dtype=float,
    )
    daily = pd.DataFrame.from_records(daily_rows).set_index("date", drop=False)
    trades = pd.DataFrame.from_records(trade_rows)
    events = pd.DataFrame.from_records(event_rows)
    expected_trade_columns = [
        "symbol", "decision_date", "entry_date", "entry_price", "exit_date",
        "exit_price", "initial_stop", "quantity", "entry_fee", "exit_fee",
        "gross_pnl", "net_pnl", "exit_reason", "breakeven_armed", "regime",
    ]
    if trades.empty:
        trades = pd.DataFrame(columns=expected_trade_columns)
    if events.empty:
        events = pd.DataFrame(columns=["date", "decision_date", "symbol", "side", "reason", "quantity", "price", "fee", "resting_stop"])
    metrics = _metrics(equity, daily, trades, float(initial_equity))
    total_trade_pnl = float(trades["net_pnl"].sum()) if not trades.empty else 0.0
    no_same_close = bool(
        trades.empty
        or (pd.to_datetime(trades["entry_date"], utc=True) > pd.to_datetime(trades["decision_date"], utc=True)).all()
    )
    gap_events = events.loc[events["reason"] == "stop_gap"] if "reason" in events else events.iloc[0:0]
    gap_correct = bool(
        gap_events.empty
        or all(
            float(row.price) <= float(row.gap_open) + _TOL
            and float(row.gap_open) <= float(row.resting_stop) + _TOL
            for row in gap_events.itertuples()
        )
    )
    max_exec_gross = max((row["gross_fraction"] for row in execution_snapshots), default=0.0)
    max_exec_risk = max((row["planned_risk_fraction"] for row in execution_snapshots), default=0.0)
    max_marked_overrun = max(
        (
            max(0.0, float(row.gross_exposure_fraction) - float(row.gross_cap))
            for row in daily.itertuples()
            if float(row.gross_cap) > 0.0
        ),
        default=0.0,
    )
    max_marked_risk_overrun = max(
        (
            max(
                0.0,
                float(row.planned_risk_fraction) - config.aggregate_risk_cap,
            )
            for row in daily.itertuples()
        ),
        default=0.0,
    )
    overrun_repairs = []
    for overrun_date in marked_overrun_dates:
        position = int(index.get_loc(overrun_date))
        if position + 1 <= final_i:
            overrun_repairs.append(index[position + 1] in cap_check_dates)
    invariants = {
        "no_same_close_fill": no_same_close,
        "gap_stops_at_worse_open": gap_correct,
        "costs_reconcile": abs((float(equity.iloc[-1]) - float(initial_equity)) - total_trade_pnl) <= 1e-5,
        "accounting_reconciles": accounting_ok,
        "max_gross_exposure_fraction": float(max_exec_gross),
        "max_planned_risk_fraction": float(max_exec_risk),
        "gross_cap_enforced_at_executions": bool(
            all(row["gross_fraction"] <= row["gross_cap"] + 1e-10 for row in execution_snapshots)
        ),
        "aggregate_risk_cap_enforced_at_executions": bool(max_exec_risk <= config.aggregate_risk_cap + 1e-10),
        "max_passive_marked_gross_overrun": float(max_marked_overrun),
        "max_passive_marked_risk_overrun": float(max_marked_risk_overrun),
        "passive_overruns_trimmed_next_open": bool(
            not trim_next_open and all(overrun_repairs)
        ),
        "segment_flat_start": bool(abs(float(daily.iloc[0]["day_start_equity"]) - float(initial_equity)) <= 1e-6),
        "segment_flat_end": bool(daily.iloc[-1]["flat_end"] and abs(float(daily.iloc[-1]["equity"]) - float(daily.iloc[-1]["end_balance"])) <= 1e-6),
        "cash_never_negative": bool(cash >= -1e-6),
    }
    return BacktestResult(
        config=config,
        start=start_ts,
        end=end_ts,
        equity=equity,
        trades=trades,
        daily=daily,
        events=events,
        decisions=decisions,
        metrics=metrics,
        invariants=invariants,
    )


def select_is_candidate(
    panel: pd.DataFrame | Mapping[str, pd.DataFrame],
    *,
    start: str = "2015-01-01",
    end: str = "2019-12-31",
) -> dict[str, Any]:
    """Evaluate exactly three IS horizons and select by the frozen rule."""

    if _utc(end) >= pd.Timestamp("2020-01-01", tz="UTC"):
        raise ValueError("IS selection is physically barred from dates on/after 2020-01-01")
    candidates: list[tuple[int, BacktestResult, bool]] = []
    for lookback in MOMENTUM_LOOKBACKS:
        result = run_backtest(panel, start, end, BacktestConfig(lookback))
        eligible = bool(
            result.metrics["max_drawdown"] < 0.08
            and result.metrics["worst_day"] > -0.03
        )
        candidates.append((lookback, result, eligible))
    eligible_rows = [row for row in candidates if row[2]]
    forced = not eligible_rows
    if eligible_rows:
        chosen = max(
            eligible_rows,
            key=lambda row: (
                float(row[1].metrics["sharpe"] if row[1].metrics["sharpe"] is not None else -np.inf),
                -float(row[1].metrics["max_drawdown"]),
                int(row[0]),
            ),
        )
    else:
        chosen = min(candidates, key=lambda row: (float(row[1].metrics["max_drawdown"]), -int(row[0])))
    return {
        "selected_lookback": int(chosen[0]),
        "selected_config": BacktestConfig(int(chosen[0])),
        "forced_selection": forced,
        "selection_rule": "eligible(<8% MDD, >-3% worst day), then Sharpe, lower MDD, longer lookback",
        "candidates": [
            {
                "lookback": int(lookback),
                "eligible": eligible,
                "metrics": result.metrics,
                "invariants": result.invariants,
            }
            for lookback, result, eligible in candidates
        ],
        "selected_result": chosen[1],
    }


def funded_replay(result: BacktestResult) -> Any:
    """Replay the daily OHLC proxy through explicit FTMO-shaped rules."""

    from apex_quant.validation.funded_simulator import DayRecord, FundedRules, replay_funded_rules

    records = []
    for row in result.daily.itertuples():
        timestamp = _utc(row.date) + pd.Timedelta(hours=20)
        records.append(
            DayRecord(
                session=_utc(row.date).date(),
                timestamp=timestamp,
                day_start_balance=float(row.day_start_balance),
                day_start_equity=float(row.day_start_equity),
                intraday_min_equity=float(row.intraday_min_equity),
                end_balance=float(row.end_balance),
                end_equity=float(row.equity),
                closed_pnl=float(row.closed_pnl),
                source_risk_base=min(float(row.day_start_balance), ACCOUNT_USD),
                verified_flat_at_end=bool(row.flat_end),
                positions_opened=int(row.positions_opened),
            )
        )
    rules = FundedRules(
        initial_balance=ACCOUNT_USD,
        profit_target_pct=0.10,
        daily_loss_pct=0.05,
        max_loss_pct=0.10,
        max_loss_mode="static",
        daily_loss_basis="initial_balance",
        minimum_trading_days=4,
        session_timezone="Europe/Prague",
    )
    return replay_funded_rules(records, rules)


def _funded_breached(value: Any) -> bool:
    if isinstance(value, Mapping):
        if "breach_count" in value:
            count = value["breach_count"]
            if isinstance(count, (bool, np.bool_)):
                return True
            try:
                parsed = int(count)
            except (TypeError, ValueError):
                return True
            return parsed > 0 if parsed >= 0 else True
        if "daily_loss_breaches" in value and "max_loss_breaches" in value:
            values = (value["daily_loss_breaches"], value["max_loss_breaches"])
            if any(isinstance(item, (bool, np.bool_)) for item in values):
                return True
            try:
                parsed_values = tuple(int(item) for item in values)
            except (TypeError, ValueError):
                return True
            return sum(parsed_values) > 0 if min(parsed_values) >= 0 else True
        return True
    status = str(getattr(value, "status", "")).lower()
    return status == "breached" if status in {"active", "passed", "breached"} else True


def _frozen_configs_match(
    is_result: BacktestResult,
    oos_base: BacktestResult,
    oos_stress: BacktestResult,
) -> bool:
    configs = (
        getattr(is_result, "config", None),
        getattr(oos_base, "config", None),
        getattr(oos_stress, "config", None),
    )
    if not all(isinstance(config, BacktestConfig) for config in configs):
        return False
    is_config, base_config, stress_config = configs
    assert isinstance(is_config, BacktestConfig)
    assert isinstance(base_config, BacktestConfig)
    assert isinstance(stress_config, BacktestConfig)
    return bool(
        is_config.momentum_lookback
        == base_config.momentum_lookback
        == stress_config.momentum_lookback
        and is_config.fee_bps == 5.0
        and is_config.stop_slippage_bps == 0.0
        and base_config.fee_bps == 5.0
        and base_config.stop_slippage_bps == 0.0
        and stress_config.fee_bps == 10.0
        and stress_config.stop_slippage_bps == 25.0
    )


def _all_invariants(result: BacktestResult) -> bool:
    required_boolean_keys = (
        "no_same_close_fill",
        "gap_stops_at_worse_open",
        "costs_reconcile",
        "accounting_reconciles",
        "segment_flat_start",
        "segment_flat_end",
    )
    optional_boolean_keys = (
        "gross_cap_enforced_at_executions",
        "aggregate_risk_cap_enforced_at_executions",
        "passive_overruns_trimmed_next_open",
        "cash_never_negative",
    )
    flags_ok = all(
        bool(result.invariants.get(key, False)) for key in required_boolean_keys
    ) and all(
        bool(result.invariants[key])
        for key in optional_boolean_keys
        if key in result.invariants
    )
    gross = float(result.invariants.get("max_gross_exposure_fraction", float("inf")))
    risk = float(result.invariants.get("max_planned_risk_fraction", float("inf")))
    return flags_ok and gross <= 0.50 + 1e-10 and risk <= 0.025 + 1e-10


def evaluate_final_gate(
    is_result: BacktestResult,
    oos_base: BacktestResult,
    oos_stress: BacktestResult,
    funded_base: Any,
    funded_stress: Any,
) -> dict[str, Any]:
    """Apply the preregistered all-or-nothing historical gate."""

    base = oos_base.metrics
    stress = oos_stress.metrics
    is_metrics = is_result.metrics
    cagr_retention = (
        float(base["cagr"]) / float(is_metrics["cagr"])
        if float(is_metrics["cagr"]) > 0.0
        else float("-inf")
    )
    sharpe_retention = (
        float(base["sharpe"]) / float(is_metrics["sharpe"])
        if is_metrics["sharpe"] is not None and float(is_metrics["sharpe"]) > 0.0 and base["sharpe"] is not None
        else float("-inf")
    )
    checks = {
        "frozen_configs_match": _frozen_configs_match(
            is_result, oos_base, oos_stress
        ),
        "oos_cagr": float(base["cagr"]) >= 0.084,
        "oos_avg_monthly_profit": float(base["avg_monthly_profit"]) >= 700.0,
        "oos_sharpe": base["sharpe"] is not None and float(base["sharpe"]) >= 1.0,
        "oos_profit_factor": base["profit_factor"] is not None and float(base["profit_factor"]) >= 1.6,
        "oos_max_drawdown": float(base["max_drawdown"]) < 0.06,
        "oos_worst_day": float(base["worst_day"]) > -0.025,
        "oos_positive_total_return": float(base["total_return"]) > 0.0,
        "cagr_retention": cagr_retention >= 0.75,
        "sharpe_retention": sharpe_retention >= 0.75,
        "stress_positive_return": float(stress["total_return"]) > 0.0 and float(stress["cagr"]) > 0.0,
        "stress_sharpe": stress["sharpe"] is not None and float(stress["sharpe"]) >= 0.5,
        "stress_profit_factor": stress["profit_factor"] is not None and float(stress["profit_factor"]) >= 1.1,
        "stress_max_drawdown": float(stress["max_drawdown"]) < 0.08,
        "stress_worst_day": float(stress["worst_day"]) > -0.035,
        "base_no_modeled_funded_breach": not _funded_breached(funded_base),
        "stress_no_modeled_funded_breach": not _funded_breached(funded_stress),
        "is_invariants": _all_invariants(is_result),
        "oos_base_invariants": _all_invariants(oos_base),
        "oos_stress_invariants": _all_invariants(oos_stress),
    }
    passed = all(checks.values())
    return {
        "status": "HISTORICAL_GATE_PASS_DATA_LIMITED" if passed else "NO_RESEARCH_CANDIDATE",
        "passed": passed,
        "checks": checks,
        "retention": {"cagr": cagr_retention, "sharpe": sharpe_retention},
        "claim_ceiling": "Historical daily-OHLC screen only; unchanged broker-native forward validation required.",
    }


__all__ = [
    "ACCOUNT_USD",
    "ALL_SYMBOLS",
    "BacktestConfig",
    "BacktestResult",
    "DEFENSIVE_SYMBOLS",
    "MOMENTUM_LOOKBACKS",
    "REGIME_SYMBOL",
    "SECTOR_SYMBOLS",
    "build_signal_panel",
    "canonical_json",
    "evaluate_final_gate",
    "funded_replay",
    "panel_sha256",
    "run_backtest",
    "select_is_candidate",
    "validate_panel",
]
