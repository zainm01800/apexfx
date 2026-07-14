"""Portfolio-level (multi-instrument) event-driven backtester.

The single-instrument :class:`Backtester` holds at most one position, so the
RiskManager's *book-level* rules — gross-exposure cap, correlation-cluster cap,
per-timeframe slot buckets, and the portfolio-risk cap — never actually bind in
simulation. They only ever fire live, untested. This backtester runs many
instruments on ONE shared equity curve through ONE shared RiskManager, passing the
true portfolio state into every ``permit()`` call, so those rules are finally
exercised and measurable (see ``PortfolioResult.constraint_log``).

Mechanics mirror the single-instrument engine bar-by-bar: decide at ``t`` on
``as_of(t)``, fill at the next bar's open, exit intrabar on stop / target / time,
apply per-asset-class costs to every fill. Candidates on the same bar are evaluated
sequentially and each is provisionally added to the book, so two correlated entries
on the same bar see one another and the caps bind correctly rather than both
slipping through.

Strategies passed in must already be fitted (or stateless) — exactly as the
validation harness fits them per CPCV fold before backtesting.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from apex_quant.config import AppConfig, get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.regime.rule_based import RuleBasedRegime
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.trade_manager import TradeManager
from apex_quant.risk.types import AccountState, Direction, MarketState, OpenPosition
from apex_quant.strategies.base import Strategy
from apex_quant.strategies.labeling import atr_series
from apex_quant.backtest.result import Trade, compute_metrics


def _vol_series(close: pd.Series, window: int, ann: int) -> np.ndarray:
    logret = np.log(close).diff()
    return (logret.rolling(window).std(ddof=1) * np.sqrt(ann)).to_numpy()


@dataclass
class PortfolioResult:
    instruments: list[str]
    equity: pd.Series
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    per_instrument: dict = field(default_factory=dict)
    constraint_log: dict = field(default_factory=dict)

    @property
    def returns(self) -> pd.Series:
        return self.equity.pct_change().dropna()

    def summary(self) -> str:
        m = self.metrics
        if m.get("insufficient_data"):
            return f"portfolio: insufficient data ({m.get('n_trades', 0)} trades)"
        caps = ", ".join(f"{k}×{v}" for k, v in sorted(self.constraint_log.items())) or "none"
        return (
            f"portfolio[{len(self.instruments)}]: ret={m['total_return']*100:.1f}% "
            f"sharpe={m['sharpe']:.2f} maxDD={m['max_drawdown']*100:.1f}% "
            f"trades={m['n_trades']} | caps bound: {caps}"
        )


class PortfolioBacktester:
    def __init__(
        self,
        cfg: AppConfig | None = None,
        risk_manager: RiskManager | None = None,
        *,
        use_regime: bool = True,
        vol_window: int = 63,
        corr_window: int = 63,
    ):
        self.cfg = cfg or get_config()
        self.bt = self.cfg.backtest
        self.risk = risk_manager or RiskManager(self.cfg.risk)
        self.use_regime = use_regime
        self.vol_window = vol_window
        self.corr_window = corr_window
        self._regime = RuleBasedRegime()
        self.trade_manager = TradeManager()
        self._mech_cache: dict = {}

    def _mech(self, instrument: str):
        m = self._mech_cache.get(instrument)
        if m is None:
            m = self.cfg.mechanics_for(instrument)
            self._mech_cache[instrument] = m
        return m

    def _pip(self, instrument: str) -> float:
        return 0.01 if "JPY" in instrument.upper() else self._mech(instrument).pip_size

    def _fill(self, price: float, instrument: str, buying: bool) -> float:
        m = self._mech(instrument)
        if m.cost_model == "pips":
            cost = 0.5 * m.spread_pips * self._pip(instrument) + m.slippage_bps / 1e4 * price
        else:
            cost = (0.5 * m.spread_bps + m.slippage_bps) / 1e4 * price
        return price + cost if buying else price - cost

    # -- run ------------------------------------------------------------------
    def run(
        self,
        pits: dict[str, PointInTimeAccessor],
        strategies: dict[str, Strategy],
        *,
        timeframes: dict[str, str] | None = None,
        start=None,
        end=None,
        warmup: int = 250,
        max_hold: int | None = None,
        periods_per_year: int = 252,
    ) -> PortfolioResult:
        def _utc(ts):
            ts = pd.Timestamp(ts)
            return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

        instruments = list(pits.keys())
        timeframes = timeframes or {}

        # Precompute per-instrument arrays + a union log-return frame for correlation.
        data: dict[str, dict] = {}
        logret_cols: dict[str, pd.Series] = {}
        for inst, pit in pits.items():
            df = pit.as_of(pit.end)
            if start is not None:
                df = df[df.index >= _utc(start)]
            if end is not None:
                df = df[df.index <= _utc(end)]
            mech = self._mech(inst)
            close = df["close"]

            # Precompute Squeeze
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

            data[inst] = {
                "pos": {ts: i for i, ts in enumerate(df.index)},
                "open": df["open"].to_numpy(),
                "high": df["high"].to_numpy(),
                "low": df["low"].to_numpy(),
                "close": close.to_numpy(),
                "atr": atr_series(df, self.cfg.risk.atr_window),
                "vol": _vol_series(close, self.vol_window, mech.annualization),
                "squeeze": squeeze_arr,
                "commission": mech.commission_per_trade,
                "tf": timeframes.get(inst, "1d"),
                "hold": max_hold if max_hold is not None else int(getattr(strategies[inst], "holding_horizon", 20)),
            }
            logret_cols[inst] = np.log(close).diff()

        R = pd.DataFrame(logret_cols).sort_index()
        timeline = R.index

        realized = float(self.bt.initial_equity)
        peak = realized
        open_pos: dict[str, dict] = {}
        pending: dict[str, dict] = {}
        trades: list[Trade] = []
        per_inst = {inst: {"n_trades": 0, "net_pnl": 0.0} for inst in instruments}
        constraint_log: dict[str, int] = defaultdict(int)
        eq_points: list[tuple[pd.Timestamp, float]] = []

        for t in timeline:
            # 1. manage exits on open positions via TradeManager
            for inst in list(open_pos.keys()):
                d = data[inst]
                i = d["pos"].get(t)
                if i is None:
                    continue
                posd = open_pos[inst]

                # Prepare past 22 bars high/low window for Chandelier trail
                high_window = d["high"][max(0, i-21):i+1]
                low_window = d["low"][max(0, i-21):i+1]
                bars_history = {
                    "high": float(high_window.max()),
                    "low": float(low_window.min()),
                    "len": i + 1,
                }

                def fill_fn(price, buying, inst_name=inst):
                    return self._fill(price, inst_name, buying)

                realized_pnl, exit_reason = self.trade_manager.update_position(
                    position=posd,
                    high=d["high"][i],
                    low=d["low"][i],
                    close=d["close"][i],
                    atr=d["atr"][i],
                    is_squeeze=bool(d["squeeze"][i]),
                    bars_history=bars_history,
                    timeframe=posd["tf"],
                    pip_size=self._pip(inst),
                    fill_fn=fill_fn,
                )

                if realized_pnl != 0.0 or exit_reason != "":
                    # Subtract commission for any close transaction
                    realized += realized_pnl - d["commission"]
                    posd["realized_pnl_total"] = posd.get("realized_pnl_total", 0.0) + (realized_pnl - d["commission"])

                if exit_reason != "":
                    exit_price = d["close"][i] if exit_reason == "time" else (posd["stop"] if exit_reason == "stop" else posd["target"])
                    trades.append(self._record(posd, exit_price, t, exit_reason, posd["realized_pnl_total"], inst))
                    per_inst[inst]["n_trades"] += 1
                    per_inst[inst]["net_pnl"] += posd["realized_pnl_total"]
                    del open_pos[inst]

            # 2. execute pending entries at THIS bar's open
            for inst in list(pending.keys()):
                if inst in open_pos:
                    continue
                d = data[inst]
                i = d["pos"].get(t)
                if i is None:
                    continue
                open_pos[inst] = self._enter(pending.pop(inst), d["open"][i], t, i, inst)

            # 3. mark-to-market portfolio equity
            eq = realized
            for inst, posd in open_pos.items():
                i = data[inst]["pos"].get(t)
                if i is not None:
                    posd["last_px"] = float(data[inst]["close"][i])
                eq += self._unrealized(posd, posd["last_px"])
            peak = max(peak, eq)
            eq_points.append((t, eq))

            # 4. decisions (sequential; provisional book so same-bar caps bind)
            if eq <= 0:
                continue
            book = [self._open_record(inst, posd) for inst, posd in open_pos.items()]
            cm = None
            for inst in instruments:
                if inst in open_pos or inst in pending:
                    continue
                d = data[inst]
                i = d["pos"].get(t)
                if i is None or i < warmup:
                    continue
                atr_i, vol_i = d["atr"][i], d["vol"][i]
                if not (np.isfinite(atr_i) and atr_i > 0 and np.isfinite(vol_i) and vol_i > 0):
                    continue
                signal = strategies[inst].generate(pits[inst], t, inst)
                if signal.direction == Direction.FLAT:
                    continue
                signal = signal.model_copy(update={"timeframe": d["tf"]})

                corrs: dict[str, float] = {}
                if book:
                    if cm is None:
                        cm = R[R.index <= t].tail(self.corr_window).corr()
                    for op in book:
                        c = (cm.loc[inst, op.instrument]
                             if inst in cm.index and op.instrument in cm.columns else np.nan)
                        corrs[op.instrument] = float(abs(c)) if np.isfinite(c) else 0.0

                account = AccountState(equity=eq, peak_equity=peak, open_positions=book)
                market = MarketState(
                    instrument=inst, price=float(d["close"][i]), ann_vol=float(vol_i),
                    atr=float(atr_i), correlations=corrs,
                )
                regime = self._regime.classify(pits[inst], t) if self.use_regime else None
                pos = self.risk.permit(signal, account, market, regime=regime)
                for c in pos.constraints_applied:
                    constraint_log[c] += 1
                if pos.permitted:
                    pending[inst] = {"pos": pos, "dec": float(d["close"][i]),
                                     "risk_abs": pos.risk_fraction * eq, "tf": d["tf"]}
                    # provisionally add so later candidates this bar respect the caps
                    book = book + [OpenPosition(
                        instrument=inst, direction=pos.direction, notional=pos.notional,
                        risk=pos.risk_fraction * eq, timeframe=d["tf"],
                    )]

        equity_series = pd.Series(
            [v for _, v in eq_points],
            index=pd.DatetimeIndex([ts for ts, _ in eq_points], name="timestamp"),
        )
        metrics = compute_metrics(equity_series, trades, periods_per_year)
        return PortfolioResult(
            instruments=instruments, equity=equity_series, trades=trades, metrics=metrics,
            per_instrument=per_inst, constraint_log=dict(constraint_log),
        )

    # -- mechanics (per-instrument) -------------------------------------------
    def _enter(self, pend: dict, open_price: float, t, i, instrument) -> dict:
        pos = pend["pos"]
        dec = pend["dec"]                       # close at decision time
        buying = pos.direction == Direction.LONG
        entry = self._fill(open_price, instrument, buying)
        shift = entry - dec                     # move stop/target by the decision->fill gap
        stop_price = (pos.stop_price or dec) + shift
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
            "target": (pos.target_price or dec) + shift,
            "risk_abs": pend["risk_abs"],
            "tf": pend["tf"],
            "last_px": entry,
            "tms_p1": False,
            "tms_p2": False,
            "tms_be": False,
            "bars_open": 0,
            "tms_log": [],
            "realized_pnl_total": -self._mech(instrument).commission_per_trade,
        }

    def _open_record(self, inst: str, posd: dict) -> OpenPosition:
        scale = (posd["units"] / posd["initial_units"]) if posd.get("initial_units", 0.0) > 0.0 else 1.0
        return OpenPosition(
            instrument=inst, direction=posd["direction"],
            notional=abs(posd["units"] * posd["last_px"]),
            risk=posd["risk_abs"] * scale,
            timeframe=posd["tf"],
        )

    def _check_exit(self, position, hi, lo, close_px, i, max_hold, instrument):
        long = position["direction"] == Direction.LONG or position["direction"] == "long" or getattr(position["direction"], "value", "") == "long"
        stop, target = position["stop"], position["target"]
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
        if position["direction"] == Direction.SHORT or position["direction"] == "short" or getattr(position["direction"], "value", "") == "short":
            d = -d
        return d * position["units"]

    def _unrealized(self, position, price) -> float:
        if not position or position["units"] <= 0:
            return 0.0
        d = price - position["entry_price"]
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
