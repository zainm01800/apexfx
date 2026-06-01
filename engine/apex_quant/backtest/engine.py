"""Event-driven backtester.

Bar-by-bar, no vectorised shortcuts that risk leakage. The decision at bar ``t``
uses only ``pit.as_of(t)``; the entry fills at the NEXT bar's open (you cannot
trade on a close you just used to decide). Open positions are checked intrabar
against an ATR stop / reward:risk target / time barrier. Costs (spread, slippage,
commission) are applied to every fill.

Equity is a margin-style mark-to-market: equity = realised + unrealised. Notional
may exceed equity (leverage), as in real forex. Single-instrument per run.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_quant.config import AppConfig, get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.regime.rule_based import RuleBasedRegime
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.types import AccountState, Direction, MarketState, Position
from apex_quant.strategies.base import Strategy
from apex_quant.strategies.labeling import atr_series
from apex_quant.backtest.result import BacktestResult, Trade, compute_metrics


def _vol_series(close: pd.Series, window: int, ann: int) -> np.ndarray:
    logret = np.log(close).diff()
    return (logret.rolling(window).std(ddof=1) * np.sqrt(ann)).to_numpy()


class Backtester:
    def __init__(
        self,
        cfg: AppConfig | None = None,
        risk_manager: RiskManager | None = None,
        *,
        use_regime: bool = True,
        vol_window: int = 63,
    ):
        self.cfg = cfg or get_config()
        self.bt = self.cfg.backtest
        self.risk = risk_manager or RiskManager(self.cfg.risk)
        self.use_regime = use_regime
        self.vol_window = vol_window
        self._regime = RuleBasedRegime()

    def _pip(self, instrument: str) -> float:
        return 0.01 if "JPY" in instrument.upper() else self.bt.pip_size_default

    def _fill(self, price: float, instrument: str, buying: bool) -> float:
        cost = 0.5 * self.bt.spread_pips * self._pip(instrument) + self.bt.slippage_bps / 1e4 * price
        return price + cost if buying else price - cost

    def run(
        self,
        pit: PointInTimeAccessor,
        strategy: Strategy,
        instrument: str,
        *,
        start=None,
        end=None,
        warmup: int = 250,
        max_hold: int | None = None,
    ) -> BacktestResult:
        def _utc(ts):
            ts = pd.Timestamp(ts)
            return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

        df = pit.as_of(pit.end)
        if start is not None:
            df = df[df.index >= _utc(start)]
        if end is not None:
            df = df[df.index <= _utc(end)]

        if max_hold is None:
            max_hold = int(getattr(strategy, "holding_horizon", 20))

        idx = df.index
        close = df["close"]
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        opens = df["open"].to_numpy()
        closes = close.to_numpy()
        atr = atr_series(df, self.cfg.risk.atr_window)
        vol = _vol_series(close, self.vol_window, self.cfg.volatility.annualization_factor)

        equity = self.bt.initial_equity
        realized = equity
        peak = equity
        position: dict | None = None
        pending: tuple[Position, float] | None = None
        trades: list[Trade] = []
        eq_points: list[tuple[pd.Timestamp, float]] = []

        for i, t in enumerate(idx):
            # 1. manage open position (intrabar stop/target/time)
            if position is not None:
                exit_price, reason = self._check_exit(position, high[i], low[i], closes[i], i, max_hold, instrument)
                if exit_price is not None:
                    pnl = self._pnl(position, exit_price)
                    realized += pnl - self.bt.commission_per_trade
                    trades.append(self._record(position, exit_price, t, reason, pnl - self.bt.commission_per_trade, instrument))
                    position = None

            # 2. execute pending entry at THIS bar's open
            if pending is not None and position is None and i > 0:
                position = self._enter(pending, opens[i], t, i, instrument)
                pending = None

            # 3. mark-to-market equity
            eq = realized + (self._unrealized(position, closes[i]) if position else 0.0)
            peak = max(peak, eq)
            eq_points.append((t, eq))

            # 4. decide (entry scheduled for next open) when flat
            if i >= warmup and position is None and pending is None and eq > 0:
                signal = strategy.generate(pit, t, instrument)
                if signal.direction != Direction.FLAT and np.isfinite(atr[i]) and atr[i] > 0 and np.isfinite(vol[i]) and vol[i] > 0:
                    market = MarketState(instrument=instrument, price=float(closes[i]), ann_vol=float(vol[i]), atr=float(atr[i]))
                    account = AccountState(equity=eq, peak_equity=peak)
                    regime = self._regime.classify(pit, t) if self.use_regime else None
                    pos = self.risk.permit(signal, account, market, regime=regime)
                    if pos.permitted:
                        pending = (pos, float(closes[i]))

        equity_series = pd.Series(
            [v for _, v in eq_points], index=pd.DatetimeIndex([ts for ts, _ in eq_points], name="timestamp")
        )
        metrics = compute_metrics(equity_series, trades, self.cfg.volatility.annualization_factor)
        return BacktestResult(instrument=instrument, equity=equity_series, trades=trades, metrics=metrics)

    # -- mechanics -------------------------------------------------------------
    def _enter(self, pending, open_price, t, i, instrument) -> dict:
        pos, decision_price = pending
        buying = pos.direction == Direction.LONG
        entry = self._fill(open_price, instrument, buying)
        shift = entry - decision_price
        return {
            "direction": pos.direction,
            "units": pos.units,
            "entry_price": entry,
            "entry_time": t,
            "entry_idx": i,
            "stop": (pos.stop_price or decision_price) + shift,
            "target": (pos.target_price or decision_price) + shift,
        }

    def _check_exit(self, position, hi, lo, close_px, i, max_hold, instrument):
        long = position["direction"] == Direction.LONG
        stop, target = position["stop"], position["target"]
        # conservative: stop checked before target
        if long:
            if lo <= stop:
                return self._fill(stop, instrument, buying=False), "stop"
            if hi >= target:
                return self._fill(target, instrument, buying=False), "target"
        else:
            if hi >= stop:
                return self._fill(stop, instrument, buying=True), "stop"
            if lo <= target:
                return self._fill(target, instrument, buying=True), "target"
        if i - position["entry_idx"] >= max_hold:
            return self._fill(close_px, instrument, buying=not long), "time"
        return None, ""

    def _pnl(self, position, exit_price) -> float:
        d = exit_price - position["entry_price"]
        if position["direction"] == Direction.SHORT:
            d = -d
        return d * position["units"]

    def _unrealized(self, position, price) -> float:
        if not position:
            return 0.0
        d = price - position["entry_price"]
        if position["direction"] == Direction.SHORT:
            d = -d
        return d * position["units"]

    def _record(self, position, exit_price, t, reason, pnl, instrument="") -> Trade:
        notional = position["entry_price"] * position["units"]
        return Trade(
            instrument=instrument,
            direction=position["direction"].value,
            entry_time=str(position["entry_time"].date()),
            entry_price=round(position["entry_price"], 6),
            exit_time=str(t.date()),
            exit_price=round(exit_price, 6),
            units=round(position["units"], 2),
            pnl=round(pnl, 2),
            return_pct=round(pnl / notional, 5) if notional else 0.0,
            exit_reason=reason,
        )
