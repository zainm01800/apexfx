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
from apex_quant.regime.rule_based import RuleBasedRegime, regime_config_for
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.trade_manager import TradeManager
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
        exit_mode: Literal["managed", "barrier"] = "managed",
    ):
        self.cfg = cfg or get_config()
        self.bt = self.cfg.backtest
        self.risk = risk_manager or RiskManager(self.cfg.risk)
        self.use_regime = use_regime
        self.vol_window = vol_window
        self.exit_mode = exit_mode
        self._regime = RuleBasedRegime()
        self.trade_manager = TradeManager()
        self._mech_cache: dict = {}

    def _mech(self, instrument: str):
        """Asset-class trading mechanics (cost model + annualization) for this
        instrument, resolved once and cached."""
        m = self._mech_cache.get(instrument)
        if m is None:
            m = self.cfg.mechanics_for(instrument)
            self._mech_cache[instrument] = m
        return m

    def _pip(self, instrument: str) -> float:
        return 0.01 if "JPY" in instrument.upper() else self._mech(instrument).pip_size

    def _fill(self, price: float, instrument: str, buying: bool, timeframe: str | None = None) -> float:
        m = self._mech(instrument)
        if m.cost_model == "pips":
            spread_pips, slippage_bps = self.cfg.forex_cost_components(instrument, timeframe)
            cost = 0.5 * spread_pips * self._pip(instrument) + slippage_bps / 1e4 * price
        else:  # bps of price — equities & crypto
            cost = (0.5 * m.spread_bps + m.slippage_bps) / 1e4 * price
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
        timeframe: str | None = None,
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

        # Dynamically align trade manager time stops with the strategy's holding horizon
        tf_clean = str(timeframe or getattr(strategy, "timeframe", "1h")).lower().strip()
        self.trade_manager.time_stop_bars[tf_clean] = max_hold

        # Engine-level regime must use the SAME slope-eps scaling as the strategy
        # gate (audit E4): the unscaled global eps reads "ranging" on intraday,
        # so backtests damped risk 40-50% where live (which passes no regime to
        # permit()) never does. This deliberately CHANGES backtest risk-scaling
        # vs the old numbers — that damping was a simulation artifact.
        self._regime = RuleBasedRegime(regime_config_for(tf_clean, self.cfg.asset_class_of(instrument)))

        idx = df.index
        close = df["close"]
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        opens = df["open"].to_numpy()
        closes = close.to_numpy()
        mech = self._mech(instrument)
        ann = mech.annualization
        commission = mech.commission_per_trade
        atr = atr_series(df, self.cfg.risk.atr_window)
        vol = _vol_series(close, self.vol_window, ann)

        # Precompute Volatility Squeeze for the whole series to speed up the loop
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std

        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - close.shift(1)).abs(),
            (df["low"] - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        kc_atr = tr.rolling(20).mean()
        kc_upper = bb_mid + 1.5 * kc_atr
        kc_lower = bb_mid - 1.5 * kc_atr
        squeeze_arr = ((bb_upper < kc_upper) & (bb_lower > kc_lower)).to_numpy()

        equity = self.bt.initial_equity
        realized = equity
        peak = equity
        position: dict | None = None
        pending: tuple[Position, float] | None = None
        trades: list[Trade] = []
        eq_points: list[tuple[pd.Timestamp, float]] = []

        pip_val = self._pip(instrument)

        for i, t in enumerate(idx):
            # 1. manage open position (intrabar stop/target/time via TradeManager or barrier check)
            if position is not None:
                if self.exit_mode == "barrier":
                    exit_price, exit_reason = self._check_exit(
                        position, high[i], low[i], closes[i], i, max_hold, instrument, timeframe=tf_clean
                    )
                    if exit_reason != "":
                        realized_pnl = self._pnl(position, exit_price)
                        realized += realized_pnl - commission
                        position["realized_pnl_total"] += (realized_pnl - commission)
                        trades.append(self._record(position, exit_price, t, exit_reason, position["realized_pnl_total"], instrument))
                        position = None
                else:
                    # Prepare past 22 bars high/low window for Chandelier trail
                    high_window = high[max(0, i-21):i+1]
                    low_window = low[max(0, i-21):i+1]
                    bars_history = {
                        "high": float(high_window.max()),
                        "low": float(low_window.min()),
                        "len": i + 1,
                    }

                    def fill_fn(price, buying):
                        return self._fill(price, instrument, buying, timeframe=tf_clean)

                    realized_pnl, exit_reason = self.trade_manager.update_position(
                        position=position,
                        high=high[i],
                        low=low[i],
                        close=closes[i],
                        atr=atr[i],
                        is_squeeze=bool(squeeze_arr[i]),
                        bars_history=bars_history,
                        timeframe=timeframe or getattr(strategy, "timeframe", "1h"),
                        pip_size=pip_val,
                        fill_fn=fill_fn,
                    )

                    if realized_pnl != 0.0 or exit_reason != "":
                        # Subtract commission for any close transaction (partial or full)
                        realized += realized_pnl - commission
                        position["realized_pnl_total"] = position.get("realized_pnl_total", 0.0) + (realized_pnl - commission)

                    if exit_reason != "":
                        # Record the final trade
                        exit_price = closes[i] if exit_reason == "time" else (position["stop"] if exit_reason == "stop" else position["target"])
                        trades.append(self._record(position, exit_price, t, exit_reason, position["realized_pnl_total"], instrument))
                        position = None

            # 2. execute pending entry at THIS bar's open
            if pending is not None and position is None and i > 0:
                position = self._enter(pending, opens[i], t, i, instrument, timeframe=tf_clean)
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
                    pos = self.risk.permit(signal, account, market, regime=regime, t=t)
                    if pos.permitted:
                        pending = (pos, float(closes[i]))

        equity_series = pd.Series(
            [v for _, v in eq_points], index=pd.DatetimeIndex([ts for ts, _ in eq_points], name="timestamp")
        )
        # Annualize per-bar metrics at the bar's own frequency (audit E5): the
        # class annualization (``ann``) is the DAILY convention and stays on the
        # vol estimate; Sharpe/ann_return/Calmar use bars-per-year for tf_clean.
        metrics = compute_metrics(equity_series, trades, self.cfg.bars_per_year(instrument, tf_clean))
        return BacktestResult(instrument=instrument, equity=equity_series, trades=trades, metrics=metrics)

    # -- mechanics -------------------------------------------------------------
    def _enter(self, pending, open_price, t, i, instrument, timeframe: str | None = None) -> dict:
        pos, decision_price = pending
        buying = pos.direction == Direction.LONG
        entry = self._fill(open_price, instrument, buying, timeframe=timeframe)
        shift = entry - decision_price
        stop_price = (pos.stop_price or decision_price) + shift
        return {
            "symbol": instrument,
            "direction": pos.direction,
            "units": pos.units,
            "initial_units": pos.units,
            "entry_price": entry,
            "entry_time": t,
            "entry_idx": i,
            "stop": stop_price,
            "initial_stop": stop_price,
            "target": (pos.target_price or decision_price) + shift,
            "tms_p1": False,
            "tms_p2": False,
            "tms_be": False,
            "bars_open": 0,
            "tms_log": [],
            "realized_pnl_total": -self._mech(instrument).commission_per_trade, # subtract entry commission immediately
        }

    def _unrealized(self, position, price) -> float:
        if not position or position["units"] <= 0:
            return 0.0
        d = price - position["entry_price"]
        if position["direction"] == Direction.SHORT or position["direction"] == "short" or getattr(position["direction"], "value", "") == "short":
            d = -d
        return d * position["units"]

    def _check_exit(self, position, hi, lo, close_px, i, max_hold, instrument, timeframe: str | None = None):
        long = position["direction"] == Direction.LONG or position["direction"] == "long" or getattr(position["direction"], "value", "") == "long"
        stop, target = position["stop"], position["target"]
        if long:
            if lo <= stop:
                return self._fill(stop, instrument, buying=False, timeframe=timeframe), "stop"
            if hi >= target:
                return self._fill(target, instrument, buying=False, timeframe=timeframe), "target"
        else:
            if hi >= stop:
                return self._fill(stop, instrument, buying=True, timeframe=timeframe), "stop"
            if lo <= target:
                return self._fill(target, instrument, buying=True, timeframe=timeframe), "target"
        if i - position["entry_idx"] >= max_hold:
            return self._fill(close_px, instrument, buying=not long, timeframe=timeframe), "time"
        return None, ""

    def _pnl(self, position, exit_price) -> float:
        d = exit_price - position["entry_price"]
        if position["direction"] == Direction.SHORT or position["direction"] == "short" or getattr(position["direction"], "value", "") == "short":
            d = -d
        return d * position["units"]

    def _record(self, position, exit_price, t, reason, pnl, instrument="") -> Trade:
        notional = position["entry_price"] * position["initial_units"]
        direction_val = position["direction"].value if hasattr(position["direction"], "value") else str(position["direction"])
        return Trade(
            instrument=instrument,
            direction=direction_val,
            entry_time=str(position["entry_time"].date()),
            entry_price=round(position["entry_price"], 6),
            exit_time=str(t.date()),
            exit_price=round(exit_price, 6),
            units=round(position["initial_units"], 2),
            pnl=round(pnl, 2),
            return_pct=round(pnl / notional, 5) if notional else 0.0,
            exit_reason=reason,
        )
