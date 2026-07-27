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
from apex_quant.regime.rule_based import RuleBasedRegime, regime_config_for
from apex_quant.risk.manager import RiskManager
from apex_quant.risk.trade_manager import TradeManager
from apex_quant.risk.types import AccountState, Direction, MarketState, OpenPosition
from apex_quant.strategies.base import Strategy
from apex_quant.strategies.labeling import atr_series
from apex_quant.backtest.defensive_sleeve import DefensiveSleeveSpec
from apex_quant.backtest.result import Trade, compute_metrics


def _vol_series(close: pd.Series, window: int, ann: int) -> np.ndarray:
    logret = np.log(close).diff()
    return (logret.rolling(window).std(ddof=1) * np.sqrt(ann)).to_numpy()


def _cf_tau_arrays(skew: pd.Series, kurt: pd.Series, z: float,
                   tau_min: float, tau_max: float) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised Cornish-Fisher tail multipliers (W2, 2026-07-25; scalar twin:
    apex_quant.risk.sizing.cornish_fisher_tau). ``skew`` / ``kurt`` are the already
    windowed, already input-clipped rolling moments. Returns (long_tau, short_tau)
    arrays clipped to [tau_min, tau_max]; non-finite moments map to 1.0 (no
    adjustment — certified sizing for bars without a full window)."""

    def _cf(zp: float) -> pd.Series:
        return (zp + (skew / 6.0) * (zp ** 2 - 1.0)
                + (kurt / 24.0) * (zp ** 3 - 3.0 * zp)
                - (skew ** 2 / 36.0) * (2.0 * zp ** 3 - 5.0 * zp))

    zq = abs(z)
    long = (_cf(-zq).abs() / zq).clip(tau_min, tau_max)
    short = (_cf(zq).abs() / zq).clip(tau_min, tau_max)
    long = long.where(long.notna() & np.isfinite(long), 1.0)
    short = short.where(short.notna() & np.isfinite(short), 1.0)
    return long.to_numpy(dtype=float), short.to_numpy(dtype=float)


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
        exit_mode: Literal["managed", "barrier"] = "managed",
        trade_manager: TradeManager | None = None,
        slot_allocation: Literal["order", "expected_value"] | None = None,
        defensive_sleeve: "DefensiveSleeveSpec | None" = None,
    ):
        self.cfg = cfg or get_config()
        self.bt = self.cfg.backtest
        self.risk = risk_manager or RiskManager(self.cfg.risk)
        self.use_regime = use_regime
        self.vol_window = vol_window
        self.corr_window = corr_window
        self.exit_mode = exit_mode
        # Defensive cash-substitute sleeve (U2, 2026-07-27; prereg
        # engine/data_store/defensive_sleeve_prereg.md). None (default) = certified
        # zero-yield GBP cash on idle capital — byte-identical certified behaviour.
        self.defensive_sleeve = defensive_sleeve
        # Engine-level regimes per (timeframe, asset class), built with the SAME
        # slope-eps scaling the strategy gate uses (audit E4) — see engine.py.
        self._regimes: dict[tuple[str, str], RuleBasedRegime] = {}
        self.trade_manager = trade_manager or TradeManager()
        # "order" reproduces the historic (arbitrary, order-dependent) behaviour and is
        # the default so nothing certified changes silently. "expected_value" ranks
        # same-bar candidates by p*b-(1-p) before allocating scarce slots — see
        # data_store/ordering_sensitivity_audit.md. An explicit constructor argument
        # wins; otherwise the RiskManager's own config decides (risk.slot_allocation,
        # default "order"), so gate configs flow into CPCV folds unchanged.
        _rcfg = getattr(self.risk, "cfg", None) or self.cfg.risk
        self.slot_allocation = (
            slot_allocation if slot_allocation is not None
            else str(getattr(_rcfg, "slot_allocation", "order") or "order")
        )
        self._mech_cache: dict = {}

    def _regime_for(self, instrument: str, timeframe: str) -> RuleBasedRegime:
        key = (str(timeframe).lower().strip(), self.cfg.asset_class_of(instrument))
        reg = self._regimes.get(key)
        if reg is None:
            reg = self._regimes[key] = RuleBasedRegime(regime_config_for(*key))
        return reg

    def _mech(self, instrument: str):
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
        periods_per_year: float | None = None,
    ) -> PortfolioResult:
        def _utc(ts):
            ts = pd.Timestamp(ts)
            return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

        instruments = list(pits.keys())
        timeframes = timeframes or {}

        # MUST read the RiskManager's own config, not self.cfg.risk: callers override risk
        # settings by passing `risk_manager=RiskManager(modified_cfg)`, leaving the app-level
        # cfg untouched. Reading self.cfg.risk here silently ignored every override.
        rcfg = getattr(self.risk, "cfg", None) or self.cfg.risk

        # Cornish-Fisher tail sizing (W2, 2026-07-25; prereg
        # engine/data_store/cf_cvar_prereg.md). Off by default; when enabled the
        # per-instrument direction-aware tau series is precomputed below from rolling
        # point-in-time skew / excess kurtosis and consumed by RiskManager step 6a via
        # MarketState.cf_tail_long / cf_tail_short.
        cf_enabled = bool(getattr(rcfg, "cf_cvar_enabled", False))
        cf_window = int(getattr(rcfg, "cf_cvar_window", 60) or 60)
        cf_z = float(getattr(rcfg, "cf_cvar_z", 2.326))
        cf_tau_lo = float(getattr(rcfg, "cf_cvar_tau_min", 1.0))
        cf_tau_hi = float(getattr(rcfg, "cf_cvar_tau_max", 2.0))
        cf_s_clip = float(getattr(rcfg, "cf_cvar_skew_clip", 2.0))
        cf_k_lo = float(getattr(rcfg, "cf_cvar_kurt_min", -2.0))
        cf_k_hi = float(getattr(rcfg, "cf_cvar_kurt_max", 10.0))

        # Precompute per-instrument arrays + a union log-return frame for correlation.
        data: dict[str, dict] = {}
        logret_cols: dict[str, pd.Series] = {}
        for inst, pit in pits.items():
            df = pit.as_of(pit.end)
            if start is not None:
                df = df[df.index >= _utc(start)]
            if end is not None:
                df = df[df.index <= _utc(end)]
            if df.empty:
                # No bars inside [start, end] (e.g. a late-listing instrument in
                # an early CPCV window) - the instrument simply doesn't exist
                # for this run; it can neither be traded nor marked.
                continue
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
            if cf_enabled:
                # W2: point-in-time rolling moments on daily log returns -> clipped
                # direction-aware Cornish-Fisher tau. Insufficient history -> tau 1.0
                # (certified sizing), so the early bars are never distorted.
                lr = np.log(close).diff()
                sw = lr.rolling(cf_window).skew().clip(-cf_s_clip, cf_s_clip)
                ku = lr.rolling(cf_window).kurt().clip(cf_k_lo, cf_k_hi)
                data[inst]["cf_long"], data[inst]["cf_short"] = _cf_tau_arrays(
                    sw, ku, cf_z, cf_tau_lo, cf_tau_hi)
            logret_cols[inst] = np.log(close).diff()

        # Instruments with no bars in [start, end] dropped above are excluded everywhere.
        instruments = [inst for inst in instruments if inst in data]

        if periods_per_year is None:
            # Annualize at the union timeline's effective bar size (audit E5):
            # the finest timeframe present, per asset class session conventions.
            periods_per_year = max(
                (self.cfg.bars_per_year(inst, d["tf"]) for inst, d in data.items()),
                default=252.0,
            )

        R = pd.DataFrame(logret_cols).sort_index()
        timeline = R.index

        # Defensive sleeve precompute (flagged; None = certified zero-yield cash).
        # Per-bar leg returns and sleeve mix on the union timeline — see step 3c.
        sleeve_arrays = (self.defensive_sleeve.align(timeline)
                         if self.defensive_sleeve is not None else None)
        sleeve_prev_w: dict[str, float] = {}
        sleeve_prev_eq = 0.0
        sleeve_net_total = 0.0
        sleeve_cost_total = 0.0
        sleeve_idle_capital_sum = 0.0
        sleeve_idle_frac_sum = 0.0

        # Portfolio vol-target overlay. Reads ONLY equity already realised at or before
        # the decision bar, so it is causal: the scalar applied to bar t's decisions is
        # built from returns up to and including t, and those trades fill at t+1's open.
        pv_target = float(getattr(rcfg, "portfolio_vol_target", 0.0) or 0.0)
        pv_window = int(getattr(rcfg, "portfolio_vol_window", 63) or 63)
        pv_min = float(getattr(rcfg, "portfolio_vol_scalar_min", 0.25))
        pv_max = float(getattr(rcfg, "portfolio_vol_scalar_max", 1.50))
        eq_hist: list[float] = []
        self.risk.risk_scalar = 1.0

        #: Order-invariant allocation (W1, 2026-07-25; prereg
        #: engine/data_store/order_invariant_prereg.md). "simultaneous" defers the
        #: RiskManager's sequential step-5.5 portfolio-risk clamp to a single end-of-bar
        #: gamma applied uniformly to every position's open risk (PASS 2 below). The
        #: defer switch is a runtime attribute on the RiskManager, so the live loop —
        #: which never sets it — always keeps the sequential cap.
        self._simultaneous_risk_cap = (
            str(getattr(rcfg, "portfolio_risk_cap_mode", "sequential")) == "simultaneous"
        )
        self.risk.defer_portfolio_risk_cap = self._simultaneous_risk_cap

        #: Daily-loss stop. On a daily book each bar IS a session, so the day's opening
        #: equity is the previous bar's close. Prop firms measure their daily rule against
        #: exactly this, which is why `drawdown_breaker` (from PEAK) cannot substitute.
        daily_limit = float(getattr(rcfg, "daily_loss_limit", 0.0) or 0.0)
        day_start_eq = 0.0

        realized = float(self.bt.initial_equity)
        peak = realized
        open_pos: dict[str, dict] = {}
        pending: dict[str, dict] = {}
        pending_trims: dict[str, float] = {}   # instrument -> fraction of units to de-risk (W1 gamma)
        trades: list[Trade] = []
        per_inst = {inst: {"n_trades": 0, "net_pnl": 0.0} for inst in instruments}
        constraint_log: dict[str, int] = defaultdict(int)
        eq_points: list[tuple[pd.Timestamp, float]] = []
        total_borrow = 0.0

        for t_i, t in enumerate(timeline):
            # Day's opening equity = last bar's close (daily bars: one bar == one session).
            day_start_eq = eq_points[-1][1] if eq_points else realized
            # 1. manage exits on open positions via TradeManager or barrier check
            for inst in list(open_pos.keys()):
                d = data[inst]
                i = d["pos"].get(t)
                if i is None:
                    continue
                posd = open_pos[inst]

                if self.exit_mode == "barrier":
                    exit_price, exit_reason = self._check_exit(
                        posd, d["high"][i], d["low"][i], d["close"][i], i, d["hold"], inst, timeframe=d["tf"]
                    )
                    if exit_reason != "":
                        realized_pnl = self._pnl(posd, exit_price)
                        realized += realized_pnl - d["commission"]
                        posd["realized_pnl_total"] += (realized_pnl - d["commission"])
                        
                        trades.append(self._record(posd, exit_price, t, exit_reason, posd["realized_pnl_total"], inst))
                        per_inst[inst]["n_trades"] += 1
                        per_inst[inst]["net_pnl"] += posd["realized_pnl_total"]
                        del open_pos[inst]
                else:
                    # Prepare past 22 bars high/low window for Chandelier trail
                    high_window = d["high"][max(0, i-21):i+1]
                    low_window = d["low"][max(0, i-21):i+1]
                    bars_history = {
                        "high": float(high_window.max()),
                        "low": float(low_window.min()),
                        "len": i + 1,
                    }

                    def fill_fn(price, buying, inst_name=inst, tf=posd["tf"]):
                        return self._fill(price, inst_name, buying, timeframe=tf)

                    realized_pnl, exit_reason = self.trade_manager.update_position(
                        position=posd,
                        high=d["high"][i],
                        open_=d["open"][i],
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
            # 2a. gamma trims FIRST (W1 simultaneous mode): de-risk existing positions
            #     queued at last bar's decision before adding new risk. A trim is a
            #     partial reduction of a position that stays open — stop/target and
            #     TradeManager state unchanged, so its open risk scales by exactly the
            #     trimmed fraction. Accounting mirrors a TradeManager partial (fill
            #     cost via _fill + per-trade commission; P&L accrues into
            #     realized_pnl_total, no Trade record — the position has not closed).
            for inst in list(pending_trims.keys()):
                frac = pending_trims.pop(inst)
                posd = open_pos.get(inst)
                if posd is None:
                    continue            # exited on this bar's step 1 — nothing to trim
                d = data[inst]
                i = d["pos"].get(t)
                if i is None:
                    continue
                trim_units = posd["units"] * frac
                if trim_units <= 0.0:
                    continue
                exit_px = self._fill(float(d["open"][i]), inst,
                                     buying=posd["direction"] != Direction.LONG,
                                     timeframe=posd["tf"])
                pnl = ((exit_px - posd["entry_price"]) * trim_units
                       if posd["direction"] == Direction.LONG
                       else (posd["entry_price"] - exit_px) * trim_units)
                posd["units"] -= trim_units
                realized += pnl - d["commission"]
                posd["realized_pnl_total"] = posd.get("realized_pnl_total", 0.0) + (pnl - d["commission"])
                constraint_log["portfolio_risk_gamma_trim"] += 1

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
                    # Short-side financing (v5 cost-model correction, 2026-07-24):
                    # borrow fee accrues per bar held on the mark-to-market short
                    # notional. Off when the class's short_borrow_bps_annual is 0
                    # (default — certified behaviour unchanged). Charged on the
                    # entry bar, not on the exit bar (the position is gone by step
                    # 3 on its exit day): a round trip of N bars pays N accruals.
                    if posd["direction"] == Direction.SHORT:
                        fee_bps = float(getattr(self._mech(inst), "short_borrow_bps_annual", 0.0) or 0.0)
                        if fee_bps > 0.0:
                            accrual = (posd["units"] * posd["last_px"] * (fee_bps / 1e4)
                                       / self.cfg.bars_per_year(inst, posd["tf"]))
                            eq -= accrual
                            realized -= accrual
                            posd["realized_pnl_total"] -= accrual
                            total_borrow += accrual
                eq += self._unrealized(posd, posd["last_px"])
            # 3c. Defensive sleeve accrual (flagged; off = certified zero-yield cash).
            # The cash idle DURING day t — the idle fraction measured at the PREVIOUS
            # mark — earns the sleeve's day-t return; the sleeve is then rebalanced to
            # today's target idle weight at the config one-way cost. Causal: every
            # input is known by the close of t. Accounting mirrors the short-borrow fee.
            if sleeve_arrays is not None:
                gross = sum(abs(posd["units"] * float(posd["last_px"]))
                            for posd in open_pos.values())
                idle_frac = max(0.0, 1.0 - gross / eq) if eq > 0.0 else 0.0
                sleeve_ret = sum(sleeve_prev_w.get(leg, 0.0) * sleeve_arrays["ret"][leg][t_i]
                                 for leg in sleeve_arrays["ret"])
                accrual = sleeve_prev_eq * sleeve_ret
                oneway = self.defensive_sleeve.oneway_cost or {}
                target_w = {leg: sleeve_arrays["mix"][leg][t_i] * idle_frac
                            for leg in sleeve_arrays["mix"]}
                cost = sum(abs(target_w[leg] - sleeve_prev_w.get(leg, 0.0)) * eq
                           * float(oneway.get(leg, 0.0)) for leg in target_w)
                net = accrual - cost
                realized += net
                eq += net
                sleeve_net_total += net
                sleeve_cost_total += cost
                sleeve_idle_capital_sum += idle_frac * eq
                sleeve_idle_frac_sum += idle_frac
                sleeve_prev_w = target_w
                sleeve_prev_eq = eq
            peak = max(peak, eq)
            eq_points.append((t, eq))

            # 3a. DAILY-LOSS STOP — flatten, don't just stop entering.
            # Blocking new entries is not a daily stop: the positions already open are
            # what carry the loss further. A real stop closes the book for the session.
            if daily_limit > 0.0 and day_start_eq > 0.0 and open_pos:
                if (1.0 - eq / day_start_eq) >= daily_limit:
                    for inst in list(open_pos.keys()):
                        posd = open_pos[inst]
                        d = data[inst]
                        i = d["pos"].get(t)
                        px = float(d["close"][i]) if i is not None else posd["last_px"]
                        exit_px = self._fill(px, inst, posd["direction"] != Direction.LONG,
                                             timeframe=posd["tf"])
                        pnl = self._unrealized(posd, exit_px) - d["commission"]
                        realized += pnl
                        posd["realized_pnl_total"] = posd.get("realized_pnl_total", 0.0) + pnl
                        trades.append(self._record(posd, exit_px, t, "daily_loss_stop",
                                                   posd["realized_pnl_total"], inst))
                        per_inst[inst]["n_trades"] += 1
                        per_inst[inst]["net_pnl"] += posd["realized_pnl_total"]
                        del open_pos[inst]
                    constraint_log["daily_loss_stop_flattened"] += 1
                    eq = realized
                    eq_points[-1] = (t, eq)

            # 3b. book-wide vol scalar from the realised equity curve
            if pv_target > 0.0:
                eq_hist.append(eq)
                if len(eq_hist) > pv_window + 1:
                    eq_hist.pop(0)
                if len(eq_hist) > pv_window // 2:
                    a = np.asarray(eq_hist, dtype=float)
                    rets = np.diff(a) / np.where(a[:-1] == 0.0, np.nan, a[:-1])
                    rv = float(np.nanstd(rets, ddof=1) * np.sqrt(periods_per_year))
                    # A book that is mostly flat has near-zero realised vol, which would
                    # otherwise demand unbounded leverage - hence the hard scalar cap.
                    self.risk.risk_scalar = (
                        float(np.clip(pv_target / rv, pv_min, pv_max))
                        if np.isfinite(rv) and rv > 1e-9 else pv_max
                    )

            # 4. decisions (sequential; provisional book so same-bar caps bind)
            if eq <= 0:
                continue
            book = [self._open_record(inst, posd) for inst, posd in open_pos.items()]
            cm = None

            # PASS 1 — collect every live candidate for this bar BEFORE allocating.
            #
            # Slots are scarce: RiskManager caps the swing bucket at 10 and
            # `timeframe_bucket_full` fires 18,147 times in the certified book. Whoever
            # is evaluated first takes the slot, so with the historic single-pass loop
            # the allocation was decided by dict insertion order — and EQUITY_CORE is
            # hardcoded starting with the decade's mega-cap winners. Measured effect
            # (data_store/ordering_sensitivity_audit.md): shuffling the order alone
            # moves Sharpe 0.217 -> 0.863. That is luck, not a decision.
            candidates = []
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
                candidates.append((inst, d, i, atr_i, vol_i, signal))

            if self.slot_allocation != "order":
                def _score(c):
                    inst_c, d_c, i_c, atr_c, vol_c, s_c = c
                    if self.slot_allocation == "probability":
                        return s_c.probability
                    elif self.slot_allocation == "expected_value":
                        return (s_c.probability * s_c.reward_risk) - (1.0 - s_c.probability)
                    elif self.slot_allocation == "ev_regime":
                        base_ev = (s_c.probability * s_c.reward_risk) - (1.0 - s_c.probability)
                        reg = self._regime_for(inst_c, d_c["tf"]).classify(pits[inst_c], t) if self.use_regime else None
                        mult = float(getattr(reg, "risk_multiplier", 1.0)) if reg else 1.0
                        return base_ev * mult
                    else:
                        return (s_c.probability * s_c.reward_risk) - (1.0 - s_c.probability)
                candidates.sort(key=lambda c: (-_score(c), c[0]))

            # PASS 2 — allocate in the chosen order.
            # Simultaneous mode (W1): permitted candidates are COLLECTED, not booked;
            # one end-of-bar gamma is then applied to all of them at once (below), so
            # the portfolio-risk budget is shared proportionally instead of consumed
            # first-come-first-served. The provisional book is still extended per
            # candidate so the hard count caps and correlation cap bind in the ranked
            # (panel-order-independent) sequence. Sequential mode is byte-identical to
            # the certified path.
            permitted_today: list[tuple] = []
            for inst, d, i, atr_i, vol_i, signal in candidates:

                corrs: dict[str, float] = {}
                if book:
                    if cm is None:
                        cm = R[R.index <= t].tail(self.corr_window).corr()
                    for op in book:
                        c = (cm.loc[inst, op.instrument]
                             if inst in cm.index and op.instrument in cm.columns else np.nan)
                        corrs[op.instrument] = float(abs(c)) if np.isfinite(c) else 0.0

                account = AccountState(equity=eq, peak_equity=peak, open_positions=book,
                                      day_start_equity=day_start_eq or None)
                market = MarketState(
                    instrument=inst, price=float(d["close"][i]), ann_vol=float(vol_i),
                    atr=float(atr_i), correlations=corrs,
                    **({"cf_tail_long": float(d["cf_long"][i]),
                        "cf_tail_short": float(d["cf_short"][i])} if cf_enabled else {}),
                )
                regime = self._regime_for(inst, d["tf"]).classify(pits[inst], t) if self.use_regime else None
                pos = self.risk.permit(signal, account, market, regime=regime, t=t)
                for c in pos.constraints_applied:
                    constraint_log[c] += 1
                if pos.permitted:
                    if self._simultaneous_risk_cap:
                        permitted_today.append((inst, d, i, pos))
                    else:
                        pending[inst] = {"pos": pos, "dec": float(d["close"][i]),
                                         "risk_abs": pos.risk_fraction * eq, "tf": d["tf"]}
                    # provisionally add so later candidates this bar respect the caps
                    book = book + [OpenPosition(
                        instrument=inst, direction=pos.direction, notional=pos.notional,
                        risk=pos.risk_fraction * eq, timeframe=d["tf"],
                    )]

            # PASS 3 (W1 simultaneous only) — one proportional de-risking for the bar.
            #
            # implied_total = open risk + candidate raw risk. If it exceeds the 6.5%
            # budget, gamma = budget / implied_total scales EVERY position's open risk
            # uniformly: candidates enter at gamma x their raw weight (stop/target
            # unchanged), and open positions with positive open risk are queued a
            # (1-gamma) unit trim filling at the next bar's open (step 2a). Positions
            # whose stop is at/past the mark carry zero open risk and are not trimmed
            # (0 x gamma = 0 — uniform in risk space). gamma <= 1 always: the mechanism
            # only de-risks, never re-levers. Sums are accumulated in instrument-sorted
            # (open) / ranked (candidate) order so gamma is a pure function of the
            # candidate SET — the order-invariance property the shuffle test proves.
            if self._simultaneous_risk_cap and permitted_today:
                cap = float(getattr(rcfg, "max_portfolio_risk", 0.035))
                open_risk = sum(
                    max(0.0, posd["units"] * abs(float(posd["last_px"]) - float(posd["stop"])))
                    for _k, posd in sorted(open_pos.items())
                )
                cand_risk = sum(pos.risk_fraction * eq for _, _, _, pos in permitted_today)
                implied_total = open_risk + cand_risk
                budget = cap * eq
                if implied_total > budget and implied_total > 0.0:
                    gamma = budget / implied_total
                    scaled: list[tuple] = []
                    for inst, d, i, pos in permitted_today:
                        if pos.notional * gamma <= float(getattr(rcfg, "min_position", 0.0)):
                            constraint_log["below_min_position"] += 1
                            continue
                        pos.units *= gamma
                        pos.notional *= gamma
                        pos.risk_fraction *= gamma
                        label = f"portfolio_risk_gamma={gamma:.2f}"
                        pos.constraints_applied.append(label)
                        constraint_log[label] += 1
                        scaled.append((inst, d, i, pos))
                    for _k, posd in sorted(open_pos.items()):
                        open_r = max(0.0, posd["units"]
                                     * abs(float(posd["last_px"]) - float(posd["stop"])))
                        if open_r > 0.0:
                            pending_trims[_k] = 1.0 - gamma
                    permitted_today = scaled
                for inst, d, i, pos in permitted_today:
                    pending[inst] = {"pos": pos, "dec": float(d["close"][i]),
                                     "risk_abs": pos.risk_fraction * eq, "tf": d["tf"]}

        equity_series = pd.Series(
            [v for _, v in eq_points],
            index=pd.DatetimeIndex([ts for ts, _ in eq_points], name="timestamp"),
        )
        metrics = compute_metrics(equity_series, trades, periods_per_year)
        metrics["short_borrow_fees_total"] = round(total_borrow, 2)
        if sleeve_arrays is not None:
            n_bars = len(eq_points)
            metrics["defensive_sleeve_net_pnl"] = round(sleeve_net_total, 2)
            metrics["defensive_sleeve_cost_total"] = round(sleeve_cost_total, 2)
            metrics["defensive_sleeve_mean_idle_frac"] = (
                sleeve_idle_frac_sum / n_bars if n_bars else 0.0)
            metrics["defensive_sleeve_mean_idle_capital"] = (
                sleeve_idle_capital_sum / n_bars if n_bars else 0.0)
        return PortfolioResult(
            instruments=instruments, equity=equity_series, trades=trades, metrics=metrics,
            per_instrument=per_inst, constraint_log=dict(constraint_log),
        )

    # -- mechanics (per-instrument) -------------------------------------------
    def _enter(self, pend: dict, open_price: float, t, i, instrument) -> dict:
        pos = pend["pos"]
        dec = pend["dec"]                       # close at decision time
        buying = pos.direction == Direction.LONG
        entry = self._fill(open_price, instrument, buying, timeframe=pend.get("tf"))
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
        # Open risk is what the book actually loses if this position stops out
        # NOW: remaining units x distance from last price to the live stop,
        # floored at 0 (a breakeven/trailed stop at or beyond last price risks
        # ~nothing). Scaling INITIAL risk by the remaining-units fraction kept
        # breakeven-stopped trades at ~full risk and let max_portfolio_risk
        # block entries it shouldn't (audit E6).
        risk = max(0.0, posd["units"] * abs(float(posd["last_px"]) - float(posd["stop"])))
        return OpenPosition(
            instrument=inst, direction=posd["direction"],
            notional=abs(posd["units"] * posd["last_px"]),
            risk=risk,
            timeframe=posd["tf"],
        )

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
