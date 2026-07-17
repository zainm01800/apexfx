"""Engine-simulated forward paper portfolio - the day-by-day twin of PortfolioBacktester.

The paper book must behave EXACTLY like the backtest it continues, so this
module adds no math of its own: ``PaperPortfolio`` subclasses
``PortfolioBacktester`` and ``step()`` is a 1:1 port of ``run()``'s loop body
(manage exits -> fill pending entries at the next bar's open -> mark to market
-> sequential risk-gated decisions with a provisional book), driven over a
growing panel one union-calendar date at a time, with the full portfolio state
serializable to JSON between invocations.

Parity by construction - the shared components:
  * the same strategy objects the caller passes (for the frozen trend book, the
    gate's TrendBook construction - see scripts/run_paper_portfolio.py)
  * the same RiskManager (config risk caps), TradeManager (managed exits) and
    RuleBasedRegime a PortfolioBacktester builds
  * the same cost mechanics: _fill/_pip/_mech (v5 per-pair forex pips,
    equity/crypto bps), _enter/_record/_unrealized/_open_record
  * the same causal precomputations (ATR / realized vol / squeeze / log-return
    correlation frame). All rolling windows are causal, so values at date t are
    identical whether computed over full history or a windowed slice.
  * the warmup gate counts each instrument's own bars from its first cached bar,
    exactly as run() does with start=None on full history.

tests/test_paper_portfolio.py::test_stepper_matches_backtester proves the port:
stepping day-by-day reproduces run()'s equity curve, trades, per-instrument
accounting and constraint log on a synthetic panel.

Two deliberate paper-only overlays (NOT part of the backtest math; both off in
the parity test):
  * ``halt_drawdown``: the pre-registration's experiment-level HALT rule blocks
    NEW entries once drawdown from peak reaches the threshold (exits keep being
    managed). None disables. This sits on top of the config drawdown breaker,
    which remains the in-book risk mechanism.
  * embedded-cost accounting: every simulated fill pays the model's
    spread/slippage; a running total is kept for the evaluation protocol's
    cost-drag metric. Exact for entries and single-fill exit bars; when several
    fills land on one bar (partial + partial), the bar's mean per-unit cost
    rate is applied to the units closed that bar - an approximation, immaterial
    in size, and it never affects trading.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from apex_quant.backtest.portfolio import PortfolioBacktester, _vol_series
from apex_quant.backtest.result import Trade, compute_metrics
from apex_quant.config import AppConfig, get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.risk.types import AccountState, Direction, MarketState, OpenPosition, Position
from apex_quant.strategies.labeling import atr_series

SCHEMA_VERSION = 1


def _norm_ts(t) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _posd_to_json(p: dict) -> dict:
    d = dict(p)
    d["direction"] = p["direction"].value if hasattr(p["direction"], "value") else str(p["direction"])
    d["entry_time"] = _norm_ts(p["entry_time"]).isoformat()
    return d


def _posd_from_json(d: dict) -> dict:
    p = dict(d)
    p["direction"] = Direction(p["direction"])
    p["entry_time"] = _norm_ts(p["entry_time"])
    return p


class PaperPortfolio(PortfolioBacktester):
    """PortfolioBacktester stepped one daily bar at a time with persistent state.

    ``panel`` maps instrument -> full OHLCV history up to (and including) the
    latest closed bar the caller wants processed; the caller re-tops the panel
    and reconstructs the stepper from the persisted state on every invocation.
    """

    def __init__(
        self,
        panel: dict[str, pd.DataFrame],
        strategies: dict,
        *,
        cfg: AppConfig | None = None,
        timeframes: dict[str, str] | None = None,
        warmup: int = 250,
        state: dict | None = None,
        book: str = "",
        params: dict | None = None,
        halt_drawdown: float | None = None,
        initial_equity: float | None = None,
    ):
        super().__init__(cfg or get_config(), exit_mode="managed")
        if self.exit_mode != "managed":
            raise ValueError("PaperPortfolio supports exit_mode='managed' only")
        self.warmup = warmup
        self.strategies = strategies
        self.book = book
        self.params = params or {}
        self.halt_drawdown = halt_drawdown
        timeframes = timeframes or {}

        # -- per-instrument arrays + union log-return frame: the same causal
        #    precomputation run() does (with start=end=None, i.e. full history).
        self.data: dict[str, dict] = {}
        logret_cols: dict[str, pd.Series] = {}
        for inst, df in panel.items():
            mech = self._mech(inst)
            close = df["close"]

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

            self.data[inst] = {
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
                "hold": int(getattr(strategies[inst], "holding_horizon", 20)),
            }
            logret_cols[inst] = np.log(close).diff()
        self.R = pd.DataFrame(logret_cols).sort_index()
        self.pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}

        # -- mutable portfolio state (native runtime types; JSON at the edges) --
        self.initial_equity = float(
            initial_equity if initial_equity is not None else self.bt.initial_equity
        )
        self._realized = self.initial_equity
        self._peak = self.initial_equity
        self._open: dict[str, dict] = {}
        self._pending: dict[str, dict] = {}
        self._trades: list[Trade] = []
        self._per_inst: dict[str, dict] = {
            inst: {"n_trades": 0, "net_pnl": 0.0} for inst in self.data
        }
        self._constraint_log: dict[str, int] = defaultdict(int)
        self._eq_points: list[tuple[pd.Timestamp, float]] = []
        self._last_processed: pd.Timestamp | None = None
        self._cost_total = 0.0
        self._halted = False
        if state is not None:
            self._load_state(state)

    # -- state (de)serialization ------------------------------------------------
    def _load_state(self, st: dict) -> None:
        self.initial_equity = float(st["initial_equity"])
        self._realized = float(st["realized"])
        self._peak = float(st["peak"])
        self._halted = bool(st.get("halted", False))
        self._cost_total = float(st.get("cost_total", 0.0))
        self._open = {k: _posd_from_json(v) for k, v in st.get("open_positions", {}).items()}
        self._pending = {}
        for inst, d in st.get("pending", {}).items():
            self._pending[inst] = {
                "pos": Position(**d["pos"]),
                "dec": float(d["dec"]),
                "risk_abs": float(d["risk_abs"]),
                "tf": d["tf"],
            }
        self._trades = [Trade(**t) for t in st.get("trades", [])]
        self._per_inst = {
            inst: {"n_trades": int(v.get("n_trades", 0)), "net_pnl": float(v.get("net_pnl", 0.0))}
            for inst, v in st.get("per_inst", {}).items()
        }
        for inst in self.data:
            self._per_inst.setdefault(inst, {"n_trades": 0, "net_pnl": 0.0})
        self._constraint_log = defaultdict(int, {k: int(v) for k, v in st.get("constraint_log", {}).items()})
        self._eq_points = [(_norm_ts(ts), float(eq)) for ts, eq in st.get("equity_curve", [])]
        lp = st.get("last_processed_date")
        self._last_processed = _norm_ts(lp) if lp else None
        self.book = st.get("book", self.book)
        self.params = st.get("params", self.params)

    def to_state(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "book": self.book,
            "params": self.params,
            "universe": list(self.data.keys()),
            "initial_equity": self.initial_equity,
            "realized": self._realized,
            "peak": self._peak,
            "halted": self._halted,
            "cost_total": self._cost_total,
            "open_positions": {k: _posd_to_json(v) for k, v in self._open.items()},
            "pending": {
                inst: {
                    "pos": d["pos"].model_dump(),
                    "dec": d["dec"],
                    "risk_abs": d["risk_abs"],
                    "tf": d["tf"],
                }
                for inst, d in self._pending.items()
            },
            "trades": [t.model_dump() for t in self._trades],
            "per_inst": self._per_inst,
            "constraint_log": dict(self._constraint_log),
            "equity_curve": [[str(ts.date()), eq] for ts, eq in self._eq_points],
            "last_processed_date": str(self._last_processed.date()) if self._last_processed is not None else None,
        }

    def save_state(self, path: str | Path) -> Path:
        """Atomic JSON write (tmp file + rename) so a crash mid-write can't
        corrupt the one authoritative local state."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_state(), fh, indent=2)
        os.replace(tmp, path)
        return path

    @staticmethod
    def load_state_file(path: str | Path) -> dict | None:
        path = Path(path)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    # -- read-only views for the driver script -------------------------------------
    @property
    def open_positions(self) -> dict:
        return self._open

    @property
    def pending_entries(self) -> dict:
        return self._pending

    @property
    def cost_total(self) -> float:
        return self._cost_total

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def last_processed(self) -> pd.Timestamp | None:
        return self._last_processed

    def set_halted(self, value: bool) -> None:
        self._halted = bool(value)

    # -- calendar ---------------------------------------------------------------
    def union_dates(self, cutoff=None) -> pd.DatetimeIndex:
        """All bar dates in the panel (the union calendar run() uses), ≤ cutoff."""
        idx = self.R.index
        if cutoff is not None:
            idx = idx[idx <= _norm_ts(cutoff)]
        return idx

    def candidate_dates(self, cutoff) -> pd.DatetimeIndex:
        """Union-calendar dates not yet processed, ≤ cutoff, oldest first."""
        idx = self.union_dates(cutoff)
        if self._last_processed is not None:
            idx = idx[idx > self._last_processed]
        return idx

    def seed_watermark(self, cutoff) -> pd.Timestamp | None:
        """Fresh book: mark everything up to the second-to-last union date as
        'already processed', so the first advance() steps over exactly ONE bar -
        the most recent closed one. That bar's decisions become PENDING-ENTRY
        for the next bar; no history is backfilled."""
        dates = self.union_dates(cutoff)
        if len(dates) >= 2:
            self._last_processed = dates[-2]
        return self._last_processed

    # -- the daily step (1:1 port of PortfolioBacktester.run's loop body) --------
    def step(self, t) -> dict:
        t = _norm_ts(t)
        data, pits, R = self.data, self.pits, self.R
        open_pos, pending = self._open, self._pending
        realized, peak = self._realized, self._peak
        cost_before = self._cost_total
        rec: dict = {
            "date": str(t.date()), "exits": [], "entries": [], "decisions": [],
            "n_flat_signals": 0, "halt_triggered": False, "halted": self._halted,
        }

        # 1. manage exits on open positions via TradeManager
        for inst in list(open_pos.keys()):
            d = data.get(inst)
            if d is None:
                continue
            i = d["pos"].get(t)
            if i is None:
                continue
            posd = open_pos[inst]

            high_window = d["high"][max(0, i - 21):i + 1]
            low_window = d["low"][max(0, i - 21):i + 1]
            bars_history = {
                "high": float(high_window.max()),
                "low": float(low_window.min()),
                "len": i + 1,
            }

            fills: list[tuple[float, float]] = []

            def fill_fn(price, buying, inst_name=inst, tf=posd["tf"]):
                filled = self._fill(price, inst_name, buying, timeframe=tf)
                fills.append((float(price), float(filled)))
                return filled

            units_before = posd["units"]
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
                max_bars=d["hold"],
            )

            if fills:
                units_closed = units_before - posd["units"]
                if units_closed > 0:
                    rate = sum(abs(f - r) for r, f in fills) / len(fills)
                    self._cost_total += rate * units_closed

            if realized_pnl != 0.0 or exit_reason != "":
                # Subtract commission for any close transaction
                realized += realized_pnl - d["commission"]
                posd["realized_pnl_total"] = posd.get("realized_pnl_total", 0.0) + (realized_pnl - d["commission"])

            if exit_reason != "":
                exit_price = d["close"][i] if exit_reason == "time" else (posd["stop"] if exit_reason == "stop" else posd["target"])
                self._trades.append(self._record(posd, exit_price, t, exit_reason, posd["realized_pnl_total"], inst))
                self._per_inst[inst]["n_trades"] += 1
                self._per_inst[inst]["net_pnl"] += posd["realized_pnl_total"]
                rec["exits"].append({
                    "instrument": inst, "reason": exit_reason,
                    "exit_price": float(exit_price), "trade_pnl": round(posd["realized_pnl_total"], 2),
                })
                del open_pos[inst]

        # 2. execute pending entries at THIS bar's open
        for inst in list(pending.keys()):
            if inst in open_pos:
                continue
            d = data.get(inst)
            if d is None:
                continue
            i = d["pos"].get(t)
            if i is None:
                continue
            open_pos[inst] = self._enter(pending.pop(inst), d["open"][i], t, i, inst)
            self._cost_total += abs(open_pos[inst]["entry_price"] - d["open"][i]) * open_pos[inst]["units"]
            rec["entries"].append({
                "instrument": inst, "direction": open_pos[inst]["direction"].value,
                "units": round(open_pos[inst]["units"], 4),
                "entry_price": open_pos[inst]["entry_price"],
            })

        # 3. mark-to-market portfolio equity
        eq = realized
        for inst, posd in open_pos.items():
            d = data.get(inst)
            i = d["pos"].get(t) if d is not None else None
            if i is not None:
                posd["last_px"] = float(d["close"][i])
            eq += self._unrealized(posd, posd["last_px"])
        peak = max(peak, eq)
        self._eq_points.append((t, eq))

        gross = sum(abs(p["units"] * p["last_px"]) for p in open_pos.values())
        rec.update({
            "equity": eq, "cash": realized, "peak": peak, "n_open": len(open_pos),
            "gross_exposure_x": (gross / eq) if eq > 0 else 0.0,
            "cost_total": self._cost_total, "day_cost": self._cost_total - cost_before,
        })

        # 4. decisions (sequential; provisional book so same-bar caps bind)
        if eq > 0 and not self._halted:
            book = [self._open_record(inst, posd) for inst, posd in open_pos.items()]
            cm = None
            for inst in data:
                if inst in open_pos or inst in pending:
                    continue
                d = data[inst]
                i = d["pos"].get(t)
                if i is None or i < self.warmup:
                    continue
                atr_i, vol_i = d["atr"][i], d["vol"][i]
                if not (np.isfinite(atr_i) and atr_i > 0 and np.isfinite(vol_i) and vol_i > 0):
                    continue
                signal = self.strategies[inst].generate(pits[inst], t, inst)
                if signal.direction == Direction.FLAT:
                    rec["n_flat_signals"] += 1
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
                regime = self._regime_for(inst, d["tf"]).classify(pits[inst], t) if self.use_regime else None
                pos = self.risk.permit(signal, account, market, regime=regime, t=t)
                for c in pos.constraints_applied:
                    self._constraint_log[c] += 1
                rec["decisions"].append({
                    "instrument": inst, "direction": signal.direction.value,
                    "permitted": bool(pos.permitted),
                    "notional": round(pos.notional, 2),
                    "risk_fraction": round(pos.risk_fraction, 6),
                    "constraints": list(pos.constraints_applied),
                })
                if pos.permitted:
                    pending[inst] = {"pos": pos, "dec": float(d["close"][i]),
                                     "risk_abs": pos.risk_fraction * eq, "tf": d["tf"]}
                    # provisionally add so later candidates this bar respect the caps
                    book = book + [OpenPosition(
                        instrument=inst, direction=pos.direction, notional=pos.notional,
                        risk=pos.risk_fraction * eq, timeframe=d["tf"],
                    )]

        self._realized, self._peak = realized, peak
        return rec

    # -- multi-day advance + experiment HALT overlay ------------------------------
    def advance(self, cutoff) -> list[dict]:
        """Step over every unprocessed union-calendar date ≤ cutoff, in order."""
        recs = []
        for t in self.candidate_dates(cutoff):
            rec = self.step(t)
            self._last_processed = _norm_ts(t)
            if self.halt_drawdown is not None and self._peak > 0 and not self._halted:
                if 1.0 - rec["equity"] / self._peak >= self.halt_drawdown:
                    self._halted = True
                    rec["halt_triggered"] = True
            rec["halted"] = self._halted
            recs.append(rec)
        return recs

    # -- reporting ------------------------------------------------------------------
    def equity_series(self) -> pd.Series:
        return pd.Series(
            [v for _, v in self._eq_points],
            index=pd.DatetimeIndex([ts for ts, _ in self._eq_points], name="timestamp"),
        )

    def metrics(self, periods_per_year: float | None = None) -> dict:
        """Metrics-to-date over the paper equity curve (same compute_metrics the
        backtester uses). Defaults to the book's own bars-per-year resolution
        (finest timeframe present), matching PortfolioBacktester.run."""
        if periods_per_year is None:
            periods_per_year = max(
                (self.cfg.bars_per_year(inst, d["tf"]) for inst, d in self.data.items()),
                default=252.0,
            )
        return compute_metrics(self.equity_series(), self._trades, periods_per_year)

    @property
    def drawdown_from_peak(self) -> float:
        return max(0.0, 1.0 - (self._eq_points[-1][1] / self._peak)) if self._eq_points else 0.0
