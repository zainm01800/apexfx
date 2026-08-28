"""A small, auditable USD-only research backtester for Book R.

This module intentionally does *not* reuse the multi-asset portfolio engine.
That engine currently mixes quote currencies while labelling its account GBP and
has intrabar stop/target assumptions which are not needed for this particular
research control.  Book R is deliberately narrow instead:

* US-listed, USD-quoted ETFs only;
* signal known at a month-end close, filled at the next trading-session open;
* long-only cross-sectional momentum, with a positive absolute-momentum gate;
* no intrabar stops, targets, or ambiguous OHLC path assumptions;
* explicit per-fill transaction costs and final liquidation cost; and
* deterministic, cluster-capped selection so it cannot accidentally become a
  pile of highly related technology bets.

It is a research/control benchmark, not a broker-ready live execution engine.
In particular the cached Yahoo bars are price returns rather than a verified
total-return series, so results must not be compared to a total-return index.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


# The whitelist is the account-currency guard: every symbol below is a US-listed
# ETF traded and settled in USD.  Do not add FX crosses, foreign listings, CFDs,
# or instruments with an unknown quote currency to this research book.
USD_ETF_UNIVERSE: tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "GLD", "TLT", "XLK", "XLE", "XBI", "SMH", "SOXX",
)

# Correlation is not measured ex post to make the result prettier.  These groups
# are a fixed economic grouping imposed before the test: at most one instrument
# in each group can be held at a rebalance.
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


@dataclass(frozen=True)
class BookRSpec:
    """Frozen candidate parameters for the Book R selection exercise."""

    name: str
    lookback: int
    vol_window: int = 63
    max_positions: int = 3
    gross_target: float = 0.95
    cost_bps_per_side: float = 5.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BookRRun:
    """Result of one independently started run over a date range."""

    spec: BookRSpec
    start: pd.Timestamp
    end: pd.Timestamp
    equity: pd.Series
    events: list[dict]
    selections: list[dict]
    metrics: dict

    def to_dict(self, *, equity_points: int = 512) -> dict:
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


def _utc_timestamp(value: pd.Timestamp | str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _date_str(value: pd.Timestamp | str) -> str:
    return _utc_timestamp(value).strftime("%Y-%m-%d")


def _round_values(value):
    if isinstance(value, dict):
        return {str(k): _round_values(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round_values(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return round(float(value), 10)
    if isinstance(value, (np.integer,)):
        return int(value)
    return value


def validate_usd_etf_universe(instruments: Iterable[str]) -> tuple[str, ...]:
    """Validate and deterministically order Book R's USD-only instrument list."""
    requested = tuple(instruments)
    unknown = sorted(set(requested) - set(USD_ETF_UNIVERSE))
    if unknown:
        raise ValueError(
            "Book R only permits the declared USD ETF whitelist; rejected: "
            + ", ".join(unknown)
        )
    if len(set(requested)) != len(requested):
        raise ValueError("Book R universe contains duplicate instruments")
    if len(requested) < 4:
        raise ValueError("Book R requires at least four ETFs for a cross-section")
    return tuple(sorted(requested))


def _validate_panel(panel: dict[str, pd.DataFrame], instruments: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    missing = [inst for inst in instruments if inst not in panel]
    if missing:
        raise ValueError(f"panel is missing Book R instruments: {', '.join(missing)}")
    out: dict[str, pd.DataFrame] = {}
    for inst in instruments:
        frame = panel[inst].copy()
        required = {"open", "close"}
        absent = sorted(required - set(frame.columns))
        if absent:
            raise ValueError(f"{inst} lacks required columns: {', '.join(absent)}")
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError(f"{inst} has no DatetimeIndex")
        idx = frame.index
        frame.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        frame = frame.sort_index()
        if frame.index.has_duplicates:
            raise ValueError(f"{inst} has duplicate bars")
        if (frame[["open", "close"]] <= 0).any().any() or frame[["open", "close"]].isna().any().any():
            raise ValueError(f"{inst} contains non-positive or missing open/close values")
        out[inst] = frame
    return out


def common_panel(
    panel: dict[str, pd.DataFrame], instruments: Iterable[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Return a strictly common-date panel, avoiding stale-price substitutions."""
    ordered = validate_usd_etf_universe(panel.keys() if instruments is None else instruments)
    checked = _validate_panel(panel, ordered)
    common = None
    for inst in ordered:
        common = checked[inst].index if common is None else common.intersection(checked[inst].index)
    assert common is not None
    common = common.sort_values()
    if len(common) < 300:
        raise ValueError("common Book R ETF panel has fewer than 300 sessions")
    return {inst: checked[inst].loc[common].copy() for inst in ordered}


def panel_manifest(store_root: Path | str, instruments: Iterable[str] = USD_ETF_UNIVERSE) -> dict[str, dict]:
    """Create a content hash / coverage manifest for the exact cached inputs."""
    root = Path(store_root)
    ordered = validate_usd_etf_universe(instruments)
    manifest: dict[str, dict] = {}
    for inst in ordered:
        path = root / f"{inst}_1d.parquet"
        if not path.exists():
            raise FileNotFoundError(path)
        data = path.read_bytes()
        frame = pd.read_parquet(path)
        manifest[inst] = {
            "path": path.name,
            "sha256": sha256(data).hexdigest(),
            "bytes": len(data),
            "rows": int(len(frame)),
            "start": _date_str(frame.index.min()),
            "end": _date_str(frame.index.max()),
        }
    return manifest


def load_usd_etf_panel(store_root: Path | str, instruments: Iterable[str] = USD_ETF_UNIVERSE) -> dict[str, pd.DataFrame]:
    """Load the fixed Book R universe directly from the immutable cache files."""
    root = Path(store_root)
    ordered = validate_usd_etf_universe(instruments)
    panel = {inst: pd.read_parquet(root / f"{inst}_1d.parquet") for inst in ordered}
    return common_panel(panel, ordered)


def _is_month_end(index: pd.DatetimeIndex, i: int) -> bool:
    return i + 1 < len(index) and index[i].month != index[i + 1].month


def select_book_r(panel: dict[str, pd.DataFrame], spec: BookRSpec, i: int) -> list[dict]:
    """Select positive-momentum ETFs using only data available at close ``i``."""
    if i < max(spec.lookback, spec.vol_window):
        return []
    ranked: list[tuple[str, float, float]] = []
    for inst, frame in panel.items():
        close = frame["close"].astype(float)
        momentum = float(close.iloc[i] / close.iloc[i - spec.lookback] - 1.0)
        log_returns = np.log(close.iloc[i - spec.vol_window : i + 1]).diff().dropna()
        vol = float(log_returns.std(ddof=1))
        if not np.isfinite(momentum) or not np.isfinite(vol) or vol <= 0.0:
            continue
        score = momentum / vol
        # Absolute momentum gate: rank strength is not enough in a broad decline.
        if momentum > 0.0:
            ranked.append((inst, score, momentum))
    ranked.sort(key=lambda row: (-row[1], row[0]))

    selected: list[dict] = []
    seen_clusters: set[str] = set()
    for inst, score, momentum in ranked:
        cluster = CLUSTERS[inst]
        if cluster in seen_clusters:
            continue
        seen_clusters.add(cluster)
        selected.append({
            "instrument": inst,
            "cluster": cluster,
            "score": score,
            "momentum": momentum,
        })
        if len(selected) == spec.max_positions:
            break
    return selected


def _metrics(equity: pd.Series, events: list[dict], *, total_cost: float, selections: int) -> dict:
    if len(equity) < 2:
        raise ValueError("need at least two marked sessions")
    rets = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    years = len(rets) / 252.0
    ann_return = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0) if years > 0 else 0.0
    std = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    ann_vol = std * np.sqrt(252.0)
    sharpe = float(rets.mean() / std * np.sqrt(252.0)) if std > 0 else 0.0
    downside = rets[rets < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = float(rets.mean() / downside_std * np.sqrt(252.0)) if downside_std > 0 else 0.0
    drawdowns = equity / equity.cummax() - 1.0
    max_drawdown = float(-drawdowns.min())
    calmar = float(ann_return / max_drawdown) if max_drawdown > 0 else 0.0
    annual = {}
    for year, values in equity.groupby(equity.index.year):
        if len(values) > 1:
            annual[str(int(year))] = float(values.iloc[-1] / values.iloc[0] - 1.0)
    buy_events = [event for event in events if event["side"] == "buy"]
    sell_events = [event for event in events if event["side"] == "sell"]
    turnover = sum(float(event["notional_usd"]) for event in events)
    mean_equity = float(equity.mean())
    return {
        "account_currency": "USD",
        "initial_equity_usd": float(equity.iloc[0]),
        "final_equity_usd": float(equity.iloc[-1]),
        "net_pnl_usd": float(equity.iloc[-1] - equity.iloc[0]),
        "total_return": total_return,
        "annualized_return": ann_return,
        "annualized_volatility": float(ann_vol),
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "sessions": int(len(equity)),
        "selection_count": int(selections),
        "buy_fills": int(len(buy_events)),
        "sell_fills": int(len(sell_events)),
        "turnover_usd": float(turnover),
        "turnover_multiple": float(turnover / mean_equity) if mean_equity else 0.0,
        "transaction_cost_usd": float(total_cost),
        "annual_returns": annual,
    }


def run_book_r(
    panel: dict[str, pd.DataFrame],
    spec: BookRSpec,
    *,
    start: pd.Timestamp | str,
    end: pd.Timestamp | str,
    initial_equity_usd: float = 100_000.0,
) -> BookRRun:
    """Run Book R with causal next-open fills over an independently flat period.

    A signal is formed only at the close of the final common session of a
    calendar month.  It is executed at the following common-session open.  The
    performance period starts flat, allowing each train/validation/replication
    segment to be evaluated without a hidden position carried from an earlier
    segment.  A final close liquidation cost is applied so ending NAV is not a
    cost-free open-position mark.
    """
    if initial_equity_usd <= 0:
        raise ValueError("initial equity must be positive")
    if not (0.0 < spec.gross_target <= 1.0):
        raise ValueError("gross_target must be in (0, 1]")
    if spec.cost_bps_per_side < 0:
        raise ValueError("cost_bps_per_side cannot be negative")

    checked = common_panel(panel, panel.keys())
    index = next(iter(checked.values())).index
    start_ts, end_ts = _utc_timestamp(start), _utc_timestamp(end)
    if end_ts <= start_ts:
        raise ValueError("end must be after start")
    active_dates = index[(index >= start_ts) & (index <= end_ts)]
    if len(active_dates) < 2:
        raise ValueError("requested segment has fewer than two common sessions")

    instruments = tuple(checked.keys())
    cash = float(initial_equity_usd)
    units = {inst: 0.0 for inst in instruments}
    cost_rate = float(spec.cost_bps_per_side) / 10_000.0
    pending: tuple[pd.Timestamp, list[dict], pd.Timestamp] | None = None
    events: list[dict] = []
    selections: list[dict] = []
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    total_cost = 0.0

    for i, date in enumerate(index):
        if date < start_ts:
            continue
        if date > end_ts:
            break

        if pending is not None and pending[0] == date:
            _, selected, decision_date = pending
            open_prices = {inst: float(checked[inst]["open"].iloc[i]) for inst in instruments}
            pre_trade_equity = cash + sum(units[inst] * open_prices[inst] for inst in instruments)
            n_selected = len(selected)
            target_value = (pre_trade_equity * spec.gross_target / n_selected) if n_selected else 0.0
            desired_units = {
                inst: (target_value / open_prices[inst] if inst in {row["instrument"] for row in selected} else 0.0)
                for inst in instruments
            }
            deltas = {inst: desired_units[inst] - units[inst] for inst in instruments}
            costs = sum(abs(deltas[inst]) * open_prices[inst] * cost_rate for inst in instruments)
            for inst in instruments:
                delta = deltas[inst]
                if abs(delta) < 1e-12:
                    continue
                notional = abs(delta) * open_prices[inst]
                fill_cost = notional * cost_rate
                side = "buy" if delta > 0 else "sell"
                events.append({
                    "date": _date_str(date),
                    "decision_date": _date_str(decision_date),
                    "instrument": inst,
                    "side": side,
                    "units": abs(delta),
                    "price_usd": open_prices[inst],
                    "notional_usd": notional,
                    "cost_usd": fill_cost,
                    "reason": "monthly_rebalance",
                })
            cash -= sum(deltas[inst] * open_prices[inst] for inst in instruments) + costs
            # gross_target leaves 5% cash by default; a negative value indicates
            # an implementation/error in sizing rather than permitted leverage.
            if cash < -1e-6:
                raise RuntimeError("Book R sizing attempted to borrow cash")
            units = desired_units
            total_cost += costs
            pending = None

        close_equity = cash + sum(units[inst] * float(checked[inst]["close"].iloc[i]) for inst in instruments)
        equity_rows.append((date, close_equity))

        if date >= start_ts and _is_month_end(index, i):
            next_date = index[i + 1]
            if next_date <= end_ts:
                selected = select_book_r(checked, spec, i)
                selections.append({
                    "decision_date": _date_str(date),
                    "fill_date": _date_str(next_date),
                    "selected": selected,
                })
                pending = (next_date, selected, date)

    if not equity_rows:
        raise ValueError("no Book R equity observations were created")

    # Apply the cost of converting the last marked portfolio into cash at the
    # final available close.  This avoids reporting an optimistically free exit.
    final_date = equity_rows[-1][0]
    final_i = int(index.get_loc(final_date))
    close_prices = {inst: float(checked[inst]["close"].iloc[final_i]) for inst in instruments}
    final_liquidation_cost = sum(abs(units[inst]) * close_prices[inst] * cost_rate for inst in instruments)
    if final_liquidation_cost:
        for inst in instruments:
            if abs(units[inst]) < 1e-12:
                continue
            notional = abs(units[inst]) * close_prices[inst]
            events.append({
                "date": _date_str(final_date),
                "decision_date": _date_str(final_date),
                "instrument": inst,
                "side": "sell",
                "units": abs(units[inst]),
                "price_usd": close_prices[inst],
                "notional_usd": notional,
                "cost_usd": notional * cost_rate,
                "reason": "final_liquidation",
            })
        equity_rows[-1] = (final_date, equity_rows[-1][1] - final_liquidation_cost)
        total_cost += final_liquidation_cost

    equity = pd.Series(
        [value for _, value in equity_rows],
        index=pd.DatetimeIndex([date for date, _ in equity_rows], name="timestamp"),
        name="equity_usd",
        dtype=float,
    )
    return BookRRun(
        spec=spec,
        start=start_ts,
        end=end_ts,
        equity=equity,
        events=events,
        selections=selections,
        metrics=_metrics(equity, events, total_cost=total_cost, selections=len(selections)),
    )
