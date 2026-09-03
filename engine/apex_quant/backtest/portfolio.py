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

import math
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


@dataclass(frozen=True)
class _FundedCashRiskLimits:
    """One decision's preregistered V2 cash-risk limits."""

    capital_base: float
    daily_floor: float
    max_floor: float
    day_buffer: float
    max_buffer: float
    candidate_stop_risk_cap_dollars: float
    aggregate_stop_risk_cap_dollars: float


_FUNDED_CASH_RISK_POLICIES = {
    "evaluation": {
        "base_risk_fraction": 0.0035,
        "daily_buffer_fraction": 0.15,
        "max_buffer_fraction": 0.06,
        "aggregate_risk_fraction": 0.0090,
    },
    "payout": {
        "base_risk_fraction": 0.0025,
        "daily_buffer_fraction": 0.10,
        "max_buffer_fraction": 0.04,
        "aggregate_risk_fraction": 0.0060,
    },
}

# The sizing core is useful for isolated research, but these missing mechanics
# make a funded-account verdict invalid under the frozen V2 preregistration.
# Keeping the blockers machine-readable on every V2 result prevents a caller
# from mistaking a numerically successful replay for a compliant pass.
_FUNDED_CASH_RISK_DATA_BLOCKERS = (
    "planned_loss_excludes_ordinary_entry_exit_costs",
    "aggregate_carried_stop_risk_not_continuously_rebalanced",
    "atomic_open_pending_risk_reservation_not_integrated",
    "pending_next_open_not_revalidated_against_authoritative_opening_state",
    "account_currency_conversion_not_applied",
    "authoritative_persisted_firm_session_state_not_supplied",
)


def _floor_cash(value: float) -> float:
    """Round a non-negative cash allowance down to the nearest cent."""

    cents = max(0.0, float(value)) * 100.0
    nearest_integer = round(cents)
    # Decimal policy constants such as 0.009 cannot be represented exactly as
    # binary floats. Snap only a few machine ULPs around an integer-cent boundary;
    # economically real fractions of a cent still round strictly downward.
    if abs(cents - nearest_integer) <= 4.0 * math.ulp(cents):
        cents = float(nearest_integer)
    return math.floor(cents) / 100.0


def _funded_cash_risk_limits(
    *,
    mode: str,
    max_loss_mode: str,
    equity: float,
    initial_balance: float,
    day_start_balance: float,
    peak_eod_balance: float,
) -> _FundedCashRiskLimits:
    """Calculate the frozen C_FUNDED_V2 cash limits without touching legacy paths."""

    if mode not in _FUNDED_CASH_RISK_POLICIES:
        raise ValueError("funded_cash_risk_mode must be 'evaluation' or 'payout'")
    if max_loss_mode not in ("static", "eod_trailing"):
        raise ValueError("funded_cash_max_loss_mode must be 'static' or 'eod_trailing'")
    values = (equity, initial_balance, day_start_balance, peak_eod_balance)
    if not all(np.isfinite(value) for value in values) or initial_balance <= 0.0:
        raise ValueError("funded cash-risk inputs must be finite with positive initial balance")

    policy = _FUNDED_CASH_RISK_POLICIES[mode]
    capital_base = max(0.0, min(float(equity), float(initial_balance)))
    daily_floor = float(day_start_balance) - 0.03 * float(initial_balance)
    max_reference = (
        float(initial_balance)
        if max_loss_mode == "static"
        else max(float(initial_balance), float(peak_eod_balance))
    )
    max_floor = max_reference - 0.10 * float(initial_balance)
    day_buffer = max(0.0, float(equity) - daily_floor)
    max_buffer = max(0.0, float(equity) - max_floor)
    candidate_cap = _floor_cash(min(
        policy["base_risk_fraction"] * capital_base,
        policy["daily_buffer_fraction"] * day_buffer,
        policy["max_buffer_fraction"] * max_buffer,
    ))
    aggregate_cap = _floor_cash(
        policy["aggregate_risk_fraction"] * capital_base
    )
    return _FundedCashRiskLimits(
        capital_base=capital_base,
        daily_floor=daily_floor,
        max_floor=max_floor,
        day_buffer=day_buffer,
        max_buffer=max_buffer,
        candidate_stop_risk_cap_dollars=candidate_cap,
        aggregate_stop_risk_cap_dollars=aggregate_cap,
    )


@dataclass
class PortfolioResult:
    """Result of a portfolio simulation.

    ``funded_trace`` is an optional research diagnostic.  When present it is a
    per-union-bar frame whose ``conservative_intraday_min_equity`` is *not* a
    reconstructed intraday path.  It combines the prior close, executable opening
    marks, a stop-aware pre-management snapshot that preserves original units, and
    post-fill OHLC co-extremes.  That deliberately pessimistic bound is useful for
    screening daily-loss risk, but it must not be described as an observed
    firm-rule breach without intraday quote data and account-currency conversion.
    """

    instruments: list[str]
    equity: pd.Series
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    per_instrument: dict = field(default_factory=dict)
    constraint_log: dict = field(default_factory=dict)
    funded_trace: pd.DataFrame | None = None

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
        entry_fill: Literal["open", "close"] = "open",
        earnings_derisk: "dict[str, set[int]] | None" = None,
        earnings_derisk_frac: float = 1.0,
        capture_funded_trace: bool = False,
        enforce_entry_bar_exits: bool = False,
        funded_sizing_limits: tuple[float, float] | None = None,
        funded_cash_risk_mode: Literal["evaluation", "payout"] | None = None,
        funded_cash_max_loss_mode: Literal["static", "eod_trailing"] = "eod_trailing",
        retain_pre_start_history: bool = False,
    ):
        self.cfg = cfg or get_config()
        self.bt = self.cfg.backtest
        self.risk = risk_manager or RiskManager(self.cfg.risk)
        self.use_regime = use_regime
        self.vol_window = vol_window
        self.corr_window = corr_window
        self.exit_mode = exit_mode
        # Entry fill convention. "open" (default) = certified: signals on bar t's close
        # fill at bar t+1's open. "close" (MOC gate, 2026-08-08, prereg
        # data_store/moc_entry_gate_prereg.md): fill at the decision bar's close.
        self.entry_fill = entry_fill
        # Earnings de-risk (exit-side gate, 2026-08-08, prereg
        # data_store/earnings_derisk_gate_prereg.md). None/empty = certified behaviour.
        # {instrument: set(bar_indices)} — on a listed bar, exit derisk_frac of the open
        # position at that bar's close, BEFORE TradeManager management.
        self._earnings_derisk = earnings_derisk or {}
        self._earnings_derisk_frac = earnings_derisk_frac
        # Optional, observation-only funded-account diagnostics.  The default is
        # deliberately false so certified result objects, metrics and the hot loop
        # remain unchanged unless a research caller explicitly asks for the trace.
        self.capture_funded_trace = bool(capture_funded_trace)
        # Optional research mechanic.  The certified/default path deliberately keeps
        # its historical behaviour (a next-open fill is first managed on the following
        # bar).  Opting in runs the chosen exit engine on the entry bar as well; both
        # engines check the stop before the target when OHLC cannot reveal ordering.
        self.enforce_entry_bar_exits = bool(enforce_entry_bar_exits)
        # Optional funded-account capital base for NEW decisions.  The tuple is
        # (official daily-loss percentage, strict EOD-trailing max-loss percentage).
        # It is deliberately separate from the close-only daily guard: this feature
        # sizes against the remaining rule buffers but makes no claim to reconstruct
        # an intraday liquidation process.  None is an exact historical no-op.
        if funded_sizing_limits is not None:
            daily_pct, max_pct = (float(v) for v in funded_sizing_limits)
            if not (
                np.isfinite(daily_pct)
                and np.isfinite(max_pct)
                and 0.0 < daily_pct < max_pct < 1.0
            ):
                raise ValueError(
                    "funded_sizing_limits must be finite (daily, max) percentages "
                    "with 0 < daily < max < 1"
                )
            self.funded_sizing_limits = (daily_pct, max_pct)
        else:
            self.funded_sizing_limits = None
        # C_FUNDED_V2 is an isolated opt-in cash-risk policy.  Unlike the frozen
        # V1 tuple above, it sizes from min(marked equity, initial balance) and
        # passes separate absolute candidate/aggregate loss allowances to the
        # risk layer.  Keeping the modes mutually exclusive makes it impossible
        # for a caller to silently blend the two preregistered semantics.
        if funded_cash_risk_mode is not None:
            if funded_cash_risk_mode not in _FUNDED_CASH_RISK_POLICIES:
                raise ValueError(
                    "funded_cash_risk_mode must be 'evaluation', 'payout', or None"
                )
            if funded_sizing_limits is not None:
                raise ValueError(
                    "funded_cash_risk_mode cannot be combined with funded_sizing_limits"
                )
        if funded_cash_max_loss_mode not in ("static", "eod_trailing"):
            raise ValueError(
                "funded_cash_max_loss_mode must be 'static' or 'eod_trailing'"
            )
        self.funded_cash_risk_mode = funded_cash_risk_mode
        self.funded_cash_max_loss_mode = funded_cash_max_loss_mode
        # Keep bars before ``start`` solely for indicator/signal warm-up while
        # starting the account, positions, event timeline and outputs at ``start``.
        # False preserves the legacy slice-before-indicators convention exactly.
        self.retain_pre_start_history = bool(retain_pre_start_history)
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
            if start is not None and not self.retain_pre_start_history:
                df = df[df.index >= _utc(start)]
            if end is not None:
                df = df[df.index <= _utc(end)]
            if (
                start is not None
                and self.retain_pre_start_history
                and not bool((df.index >= _utc(start)).any())
            ):
                # The retained frame contains only warm-up history and no
                # executable event inside the requested partition.
                continue
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
        if start is not None and self.retain_pre_start_history:
            timeline = timeline[timeline >= _utc(start)]

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
        peak_eod_balance = realized
        open_pos: dict[str, dict] = {}
        pending: dict[str, dict] = {}
        pending_trims: dict[str, float] = {}   # instrument -> fraction of units to de-risk (W1 gamma)
        trades: list[Trade] = []
        per_inst = {inst: {"n_trades": 0, "net_pnl": 0.0} for inst in instruments}
        constraint_log: dict[str, int] = defaultdict(int)
        eq_points: list[tuple[pd.Timestamp, float]] = []
        total_borrow = 0.0
        funded_rows: list[dict] | None = [] if self.capture_funded_trace else None

        for t_i, t in enumerate(timeline):
            # Day's opening equity = last bar's close (daily bars: one bar == one session).
            day_start_eq = eq_points[-1][1] if eq_points else realized
            # Funded rules commonly anchor the session and profit target to closed
            # balance while enforcing loss limits against live equity.  Preserve both
            # values in the opt-in trace; marked equity must never be silently treated
            # as realised balance by the rule simulator.
            trace_day_start_balance = float(realized)

            # FUNDED TRACE, opening snapshot.  This is captured before intrabar
            # management so a gap through a carried position's stop cannot disappear
            # merely because the stop is subsequently booked.  `_fill` supplies the
            # executable side of the opening mark (spread/slippage, but no hypothetical
            # close commission).  Missing bars retain the last available mark.
            trace_trades_before = len(trades)
            trace_start_contrib: dict[str, float] = {}
            trace_open_contrib: dict[str, float] = {}
            trace_pre_management_contrib: dict[str, float] = {}
            trace_opening_eq = float(day_start_eq)
            trace_pre_management_adverse_eq = float(day_start_eq)
            if funded_rows is not None:
                trace_opening_eq = float(realized)
                for inst, posd in open_pos.items():
                    start_contrib = (
                        float(posd.get("realized_pnl_total", 0.0))
                        + self._unrealized(posd, float(posd["last_px"]))
                    )
                    trace_start_contrib[inst] = start_contrib
                    d = data[inst]
                    i = d["pos"].get(t)
                    mark = float(posd["last_px"])
                    if i is not None:
                        mark = self._fill(
                            float(d["open"][i]), inst,
                            buying=posd["direction"] != Direction.LONG,
                            timeframe=posd["tf"],
                        )
                    trace_opening_eq += self._unrealized(posd, mark)
                    trace_open_contrib[inst] = (
                        float(posd.get("realized_pnl_total", 0.0))
                        + self._unrealized(posd, mark)
                    )

                # Preserve the carried positions before TradeManager, earnings
                # de-risking, or gamma trims mutate their units/stops.  If the bar
                # crosses the live stop, cap the adverse mark at the executable
                # stop (or the worse opening gap) and include the close commission;
                # otherwise use the adverse raw OHLC extreme.  This avoids both
                # post-partial unit understatement and impossible marks beyond a
                # stop that the engine would have executed first.
                carried_snapshot = {
                    inst: posd.copy() for inst, posd in open_pos.items()
                }
                (
                    trace_pre_management_adverse_eq,
                    trace_pre_management_contrib,
                ) = self._funded_adverse_snapshot(
                    carried_snapshot, realized, data, t, stop_aware=True,
                )

            # 1. manage exits on open positions via TradeManager or barrier check
            for inst in list(open_pos.keys()):
                d = data[inst]
                i = d["pos"].get(t)
                if i is None:
                    continue
                posd = open_pos[inst]

                if self.exit_mode == "barrier":
                    exit_price, exit_reason = self._check_exit(
                        posd, d["high"][i], d["low"][i], d["close"][i], i,
                        d["hold"], inst, timeframe=d["tf"], open_px=d["open"][i],
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
                    # Earnings de-risk (exit-side gate, 2026-08-08): fires BEFORE
                    # TradeManager management; full flat skips this bar's management.
                    derisk_bars = self._earnings_derisk.get(inst)
                    if derisk_bars and i in derisk_bars:
                        _frac = self._earnings_derisk_frac
                        _exit_px = self._fill(float(d["close"][i]), inst,
                                              buying=posd["direction"] != Direction.LONG,
                                              timeframe=posd["tf"])
                        _u = posd["units"] * _frac
                        _pnl = ((_exit_px - posd["entry_price"]) * _u
                                if posd["direction"] == Direction.LONG
                                else (posd["entry_price"] - _exit_px) * _u)
                        realized += _pnl - d["commission"]
                        posd["realized_pnl_total"] = posd.get("realized_pnl_total", 0.0) + (_pnl - d["commission"])
                        constraint_log["earnings_derisk"] += 1
                        if _frac >= 1.0 - 1e-12:
                            trades.append(self._record(posd, _exit_px, t, "earnings_derisk",
                                                       posd["realized_pnl_total"], inst))
                            per_inst[inst]["n_trades"] += 1
                            per_inst[inst]["net_pnl"] += posd["realized_pnl_total"]
                            del open_pos[inst]
                            continue
                        posd["units"] -= _u

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

                    before_p1 = bool(posd.get("tms_p1", False))
                    before_p2 = bool(posd.get("tms_p2", False))
                    before_units = float(posd.get("units", 0.0))
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

                    close_fills = self._managed_close_fill_count(
                        posd, before_p1, before_p2, before_units, exit_reason,
                    )
                    if realized_pnl != 0.0 or close_fills:
                        # TradeManager can execute P1, P2, and a final close in one
                        # update. Commission is per close fill, not per bar/update.
                        net_realized = realized_pnl - d["commission"] * close_fills
                        realized += net_realized
                        posd["realized_pnl_total"] = (
                            posd.get("realized_pnl_total", 0.0) + net_realized
                        )

                    if exit_reason != "":
                        exit_price = posd.get(
                            "last_exit_price",
                            d["close"][i] if exit_reason == "time" else
                            (posd["stop"] if exit_reason == "stop" else posd["target"]),
                        )
                        trades.append(self._record(posd, exit_price, t, exit_reason, posd["realized_pnl_total"], inst))
                        per_inst[inst]["n_trades"] += 1
                        per_inst[inst]["net_pnl"] += posd["realized_pnl_total"]
                        del open_pos[inst]

            # 2. execute pending entries at THIS bar's open
            entered_this_bar: list[str] = []
            trace_post_entry_adverse_eq: float | None = None
            trace_post_entry_contrib: dict[str, float] = {}
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
                entered_this_bar.append(inst)
                # The position's trade record already includes entry commission;
                # cash/equity must pay it at the same instant as well.
                realized -= d["commission"]

            if funded_rows is not None and entered_this_bar:
                # Capture new positions at their original entry size before optional
                # same-bar management can stop, target, or partially close them.  The
                # stop-aware snapshot supplies an executable bound for those fills;
                # the later raw co-extreme snapshot still preserves the historical
                # (entry-bar exits disabled) diagnostic behaviour.
                entry_snapshot = {
                    inst: posd.copy() for inst, posd in open_pos.items()
                }
                (
                    trace_post_entry_adverse_eq,
                    trace_post_entry_contrib,
                ) = self._funded_adverse_snapshot(
                    entry_snapshot, realized, data, t, stop_aware=True,
                )
                for trade in trades[trace_trades_before:]:
                    trace_post_entry_contrib[trade.instrument] = (
                        trace_post_entry_contrib.get(trade.instrument, 0.0)
                        + float(trade.pnl)
                    )

            # Optional entry-bar management.  This is intentionally disabled by
            # default so certified studies remain bit-for-bit compatible.  When it
            # is enabled, use the same selected exit engine as carried positions;
            # both implementations resolve an ambiguous stop-and-target bar in the
            # conservative stop-first order.
            if self.enforce_entry_bar_exits:
                for inst in entered_this_bar:
                    posd = open_pos.get(inst)
                    if posd is None:
                        continue
                    d = data[inst]
                    i = d["pos"].get(t)
                    if i is None:
                        continue

                    if self.exit_mode == "barrier":
                        exit_price, exit_reason = self._check_exit(
                            posd, d["high"][i], d["low"][i], d["close"][i],
                            i, d["hold"], inst, timeframe=d["tf"],
                            open_px=d["open"][i],
                        )
                        if exit_reason:
                            realized_pnl = self._pnl(posd, exit_price)
                            realized += realized_pnl - d["commission"]
                            posd["realized_pnl_total"] += (
                                realized_pnl - d["commission"]
                            )
                            trades.append(self._record(
                                posd, exit_price, t, exit_reason,
                                posd["realized_pnl_total"], inst,
                            ))
                            per_inst[inst]["n_trades"] += 1
                            per_inst[inst]["net_pnl"] += posd["realized_pnl_total"]
                            del open_pos[inst]
                        continue

                    high_window = d["high"][max(0, i - 21):i + 1]
                    low_window = d["low"][max(0, i - 21):i + 1]
                    bars_history = {
                        "high": float(high_window.max()),
                        "low": float(low_window.min()),
                        "len": i + 1,
                    }

                    def entry_fill_fn(price, buying, inst_name=inst, tf=posd["tf"]):
                        return self._fill(price, inst_name, buying, timeframe=tf)

                    before_p1 = bool(posd.get("tms_p1", False))
                    before_p2 = bool(posd.get("tms_p2", False))
                    before_units = float(posd.get("units", 0.0))
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
                        fill_fn=entry_fill_fn,
                        max_bars=d["hold"],
                    )
                    close_fills = self._managed_close_fill_count(
                        posd, before_p1, before_p2, before_units, exit_reason,
                    )
                    if realized_pnl != 0.0 or close_fills:
                        net_realized = realized_pnl - d["commission"] * close_fills
                        realized += net_realized
                        posd["realized_pnl_total"] = (
                            posd.get("realized_pnl_total", 0.0)
                            + net_realized
                        )
                    if exit_reason:
                        exit_price = posd.get(
                            "last_exit_price",
                            d["close"][i] if exit_reason == "time" else
                            (posd["stop"] if exit_reason == "stop" else posd["target"]),
                        )
                        trades.append(self._record(
                            posd, exit_price, t, exit_reason,
                            posd["realized_pnl_total"], inst,
                        ))
                        per_inst[inst]["n_trades"] += 1
                        per_inst[inst]["net_pnl"] += posd["realized_pnl_total"]
                        del open_pos[inst]

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

            # FUNDED TRACE, conservative co-extreme snapshots.  The pre-management
            # state above retains original units and caps a crossed stop at its
            # executable fill.  This final state reflects actual fills, then marks
            # every remaining long at the raw low and short at the raw high.  The
            # snapshots are alternative OHLC bounds, not a reconstructed path.
            trace_intraday_min = min(
                float(day_start_eq),
                float(trace_opening_eq),
                float(trace_pre_management_adverse_eq),
                float(eq),
            )
            trace_worst_symbol_loss = 0.0
            if funded_rows is not None:
                adverse_eq, adverse_contrib = self._funded_adverse_snapshot(
                    open_pos, realized, data, t, stop_aware=False,
                )

                # A position may have disappeared at step 1 because its stop was
                # filled at a gapped open.  Its cumulative trade P&L is the current
                # symbol contribution and is compared with the prior-close carrying
                # contribution below.  This also attributes ordinary full exits.
                for trade in trades[trace_trades_before:]:
                    adverse_contrib[trade.instrument] += float(trade.pnl)

                scenario_equities = [
                    float(day_start_eq),
                    float(trace_opening_eq),
                    float(trace_pre_management_adverse_eq),
                    float(adverse_eq),
                    float(eq),
                ]
                if trace_post_entry_adverse_eq is not None:
                    scenario_equities.append(float(trace_post_entry_adverse_eq))
                trace_intraday_min = min(scenario_equities)

                contribution_scenarios = [
                    trace_open_contrib,
                    trace_pre_management_contrib,
                    adverse_contrib,
                ]
                if trace_post_entry_adverse_eq is not None:
                    contribution_scenarios.append(trace_post_entry_contrib)
                symbols = set(trace_start_contrib)
                for scenario in contribution_scenarios:
                    symbols.update(scenario)
                for inst in symbols:
                    start = trace_start_contrib.get(inst, 0.0)
                    worst = min(
                        scenario.get(inst, 0.0)
                        for scenario in contribution_scenarios
                    )
                    trace_worst_symbol_loss = max(
                        trace_worst_symbol_loss,
                        max(0.0, start - worst),
                    )

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
            # Funded sizing is based on the nearest remaining official loss
            # buffer.  The max-loss floor is strict EOD-trailing closed balance.
            # A decision can fill either at this close or at the next open, so the
            # daily floor uses the stricter of the current session anchor and the
            # prospective next-session anchor.  In particular, a profitable close
            # must not create a fictitious extra daily-loss allowance for an order
            # queued to the next session.  This is decision sizing only.
            peak_eod_balance = max(peak_eod_balance, float(realized))
            decision_risk_sizing_base = max(0.0, float(eq))
            decision_candidate_stop_risk_cap: float | None = None
            decision_aggregate_stop_risk_cap: float | None = None
            if self.funded_sizing_limits is not None:
                funded_daily_pct, funded_max_pct = self.funded_sizing_limits
                initial_balance = float(self.bt.initial_equity)
                daily_allowance = initial_balance * funded_daily_pct
                daily_floor = max(
                    trace_day_start_balance - daily_allowance,
                    float(realized) - daily_allowance,
                )
                trailing_max_floor = (
                    peak_eod_balance - initial_balance * funded_max_pct
                )
                decision_risk_sizing_base = max(
                    0.0,
                    min(
                        float(eq),
                        initial_balance,
                        float(eq) - daily_floor,
                        float(eq) - trailing_max_floor,
                    ),
                )
            elif self.funded_cash_risk_mode is not None:
                # A next-open order belongs to the prospective next session.  A
                # profitable close on today's bar must therefore raise (never
                # lower) its daily-loss anchor to the closed balance that will
                # open that session.  Retaining today's lower anchor would grant
                # the pending order fictitious extra cushion.  After a loss the
                # current session's higher anchor remains the stricter one.
                cash_day_start_balance = float(trace_day_start_balance)
                if self.entry_fill == "open":
                    cash_day_start_balance = max(
                        cash_day_start_balance, float(realized),
                    )
                cash_limits = _funded_cash_risk_limits(
                    mode=self.funded_cash_risk_mode,
                    max_loss_mode=self.funded_cash_max_loss_mode,
                    equity=float(eq),
                    initial_balance=float(self.bt.initial_equity),
                    day_start_balance=cash_day_start_balance,
                    peak_eod_balance=peak_eod_balance,
                )
                decision_risk_sizing_base = cash_limits.capital_base
                decision_candidate_stop_risk_cap = (
                    cash_limits.candidate_stop_risk_cap_dollars
                )
                decision_aggregate_stop_risk_cap = (
                    cash_limits.aggregate_stop_risk_cap_dollars
                )
            if eq <= 0:
                if funded_rows is not None:
                    end_eq = float(eq_points[-1][1])
                    exposure = self._funded_exposure_diagnostics(
                        open_pos, pending, pending_trims,
                    )
                    funded_rows.append({
                        "timestamp": t,
                        "day_start_balance": trace_day_start_balance,
                        "day_start_equity": float(day_start_eq),
                        "opening_equity": float(trace_opening_eq),
                        "conservative_intraday_min_equity": min(
                            float(day_start_eq), trace_intraday_min, end_eq,
                        ),
                        "end_balance": float(realized),
                        "end_equity": end_eq,
                        "closed_pnl": float(realized) - trace_day_start_balance,
                        "positions_opened": int(len(entered_this_bar)),
                        "verified_flat_at_end": bool(
                            not open_pos and not pending
                        ),
                        "risk_sizing_base": decision_risk_sizing_base,
                        **exposure,
                        # Backward-compatible aliases.  Gross is actual/open;
                        # planned stop risk includes queued next-open orders.
                        "gross_exposure": exposure["actual_open_gross_exposure"],
                        "planned_stop_risk": exposure[
                            "post_pending_planned_stop_risk"
                        ],
                        "worst_symbol_adverse_loss": float(trace_worst_symbol_loss),
                    })
                continue
            book = [self._open_record(inst, posd) for inst, posd in open_pos.items()]
            if self.funded_cash_risk_mode is not None:
                # V2's aggregate ceiling is explicitly open PLUS pending risk.
                # A queued order that did not have a bar on which to fill remains
                # a live reservation; excluding it would let a later candidate
                # spend the same cash-risk budget a second time.
                for inst, order in pending.items():
                    if inst in open_pos:
                        continue
                    pending_pos = order.get("pos")
                    if pending_pos is None:
                        continue
                    book.append(OpenPosition(
                        instrument=inst,
                        direction=pending_pos.direction,
                        notional=float(pending_pos.notional),
                        risk=max(0.0, float(order.get("risk_abs", 0.0))),
                        timeframe=order.get("tf", "1d"),
                    ))
            cm = None
            close_entered_this_bar: list[str] = []

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
                                      day_start_equity=day_start_eq or None,
                                      risk_sizing_base=(
                                          decision_risk_sizing_base
                                          if (
                                              self.funded_sizing_limits is not None
                                              or self.funded_cash_risk_mode is not None
                                          )
                                          else None
                                      ),
                                      candidate_stop_risk_cap_dollars=(
                                          decision_candidate_stop_risk_cap
                                      ),
                                      aggregate_stop_risk_cap_dollars=(
                                          decision_aggregate_stop_risk_cap
                                      ))
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
                    elif self.entry_fill == "close":
                        # MOC gate (2026-08-08): fill at the decision bar's close.
                        if inst not in open_pos:
                            open_pos[inst] = self._enter(
                                {"pos": pos, "dec": float(d["close"][i]),
                                 "risk_abs": pos.risk_fraction * eq, "tf": d["tf"]},
                                d["close"][i], t, i, inst)
                            realized -= d["commission"]
                            close_entered_this_bar.append(inst)
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
                preexisting_pending_risk = (
                    sum(
                        max(0.0, float(order.get("risk_abs", 0.0)))
                        for inst, order in sorted(pending.items())
                        if inst not in open_pos
                    )
                    if self.funded_cash_risk_mode is not None
                    else 0.0
                )
                cand_risk = sum(pos.risk_fraction * eq for _, _, _, pos in permitted_today)
                existing_risk = open_risk + preexisting_pending_risk
                implied_total = existing_risk + cand_risk
                budget = cap * eq
                if decision_aggregate_stop_risk_cap is not None:
                    budget = min(budget, decision_aggregate_stop_risk_cap)
                if (
                    self.funded_cash_risk_mode is not None
                    and cand_risk > max(0.0, budget - existing_risk)
                ):
                    # V2 allocates the REMAINING aggregate allowance among this
                    # bar's new candidates. Existing positions/pending orders are
                    # senior reservations and are never resized merely because a
                    # new signal arrived.
                    remaining = max(0.0, budget - existing_risk)
                    gamma = remaining / cand_risk if cand_risk > 0.0 else 0.0
                    scaled: list[tuple] = []
                    for inst, d, i, pos in permitted_today:
                        raw_cash_risk = pos.risk_fraction * eq
                        scaled_cash_risk = _floor_cash(raw_cash_risk * gamma)
                        candidate_gamma = (
                            scaled_cash_risk / raw_cash_risk
                            if raw_cash_risk > 0.0 else 0.0
                        )
                        if pos.notional * candidate_gamma <= float(getattr(rcfg, "min_position", 0.0)):
                            constraint_log["below_min_position"] += 1
                            continue
                        pos.units *= candidate_gamma
                        pos.notional *= candidate_gamma
                        pos.risk_fraction *= candidate_gamma
                        label = f"aggregate_stop_risk_gamma={gamma:.2f}"
                        pos.constraints_applied.append(label)
                        constraint_log[label] += 1
                        scaled.append((inst, d, i, pos))
                    permitted_today = scaled
                elif implied_total > budget and implied_total > 0.0:
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
                    if self.entry_fill == "close":
                        if inst not in open_pos:
                            open_pos[inst] = self._enter(
                                {"pos": pos, "dec": float(d["close"][i]),
                                 "risk_abs": pos.risk_fraction * eq, "tf": d["tf"]},
                                d["close"][i], t, i, inst)
                            realized -= d["commission"]
                            close_entered_this_bar.append(inst)
                    else:
                        pending[inst] = {"pos": pos, "dec": float(d["close"][i]),
                                         "risk_abs": pos.risk_fraction * eq, "tf": d["tf"]}

            if close_entered_this_bar:
                # Close-filled orders are created after the ordinary close mark.  Pay
                # their entry commissions immediately and rebuild the same bar's final
                # equity so neither the equity curve nor the funded trace shifts those
                # costs into the following firm day.  Daily highs/lows pre-date a MOC
                # fill, so they are intentionally not used as entry-bar exit evidence.
                for inst in close_entered_this_bar:
                    i = data[inst]["pos"].get(t)
                    if i is not None:
                        open_pos[inst]["last_px"] = float(data[inst]["close"][i])
                eq = float(realized) + sum(
                    self._unrealized(posd, float(posd["last_px"]))
                    for posd in open_pos.values()
                )
                eq_points[-1] = (t, eq)
                if pv_target > 0.0 and eq_hist:
                    eq_hist[-1] = eq
                trace_intraday_min = min(trace_intraday_min, eq)
                if funded_rows is not None:
                    for inst in close_entered_this_bar:
                        posd = open_pos[inst]
                        contribution = (
                            float(posd.get("realized_pnl_total", 0.0))
                            + self._unrealized(posd, float(posd["last_px"]))
                        )
                        trace_worst_symbol_loss = max(
                            trace_worst_symbol_loss, max(0.0, -contribution),
                        )

            if funded_rows is not None:
                # Keep actual open exposure distinct from the book implied after
                # queued next-open entries and gamma trims.  The latter uses decision
                # marks because the future opening gap is unknowable at this bar.
                exposure = self._funded_exposure_diagnostics(
                    open_pos, pending, pending_trims,
                )
                end_eq = float(eq_points[-1][1])
                funded_rows.append({
                    "timestamp": t,
                    "day_start_balance": trace_day_start_balance,
                    "day_start_equity": float(day_start_eq),
                    "opening_equity": float(trace_opening_eq),
                    "conservative_intraday_min_equity": min(
                        float(day_start_eq), trace_intraday_min, end_eq,
                    ),
                    "end_balance": float(realized),
                    "end_equity": end_eq,
                    "closed_pnl": float(realized) - trace_day_start_balance,
                    "positions_opened": int(
                        len(entered_this_bar) + len(close_entered_this_bar)
                    ),
                    "verified_flat_at_end": bool(not open_pos and not pending),
                    "risk_sizing_base": decision_risk_sizing_base,
                    **exposure,
                    # Backward-compatible aliases.  Gross is actual/open;
                    # planned stop risk includes queued next-open orders.
                    "gross_exposure": exposure["actual_open_gross_exposure"],
                    "planned_stop_risk": exposure[
                        "post_pending_planned_stop_risk"
                    ],
                    "worst_symbol_adverse_loss": float(trace_worst_symbol_loss),
                })

        equity_series = pd.Series(
            [v for _, v in eq_points],
            index=pd.DatetimeIndex([ts for ts, _ in eq_points], name="timestamp"),
        )
        metrics = compute_metrics(equity_series, trades, periods_per_year)
        metrics["short_borrow_fees_total"] = round(total_borrow, 2)
        if self.funded_cash_risk_mode is not None:
            # Fail closed at the result boundary.  The isolated V2 sizing core
            # may be exercised in research, but it is not a prereg-compliant
            # funded verdict until every blocker below is removed in code/data.
            metrics["funded_cash_risk_status"] = "DATA_BLOCKED"
            metrics["funded_cash_risk_blockers"] = list(
                _FUNDED_CASH_RISK_DATA_BLOCKERS
            )
        if sleeve_arrays is not None:
            n_bars = len(eq_points)
            metrics["defensive_sleeve_net_pnl"] = round(sleeve_net_total, 2)
            metrics["defensive_sleeve_cost_total"] = round(sleeve_cost_total, 2)
            metrics["defensive_sleeve_mean_idle_frac"] = (
                sleeve_idle_frac_sum / n_bars if n_bars else 0.0)
            metrics["defensive_sleeve_mean_idle_capital"] = (
                sleeve_idle_capital_sum / n_bars if n_bars else 0.0)
        funded_trace = None
        if funded_rows is not None:
            trace_columns = [
                "day_start_balance", "day_start_equity", "opening_equity",
                "conservative_intraday_min_equity", "end_balance", "end_equity",
                "closed_pnl", "positions_opened", "verified_flat_at_end",
                "risk_sizing_base",
                "actual_open_gross_exposure", "actual_open_stop_risk",
                "post_pending_planned_gross_exposure",
                "post_pending_planned_stop_risk",
                "gross_exposure", "planned_stop_risk",
                "worst_symbol_adverse_loss",
            ]
            if funded_rows:
                funded_trace = pd.DataFrame(funded_rows).set_index("timestamp")
                funded_trace.index = pd.DatetimeIndex(
                    funded_trace.index, name="timestamp")
                funded_trace = funded_trace[trace_columns]
            else:
                funded_trace = pd.DataFrame(
                    columns=trace_columns,
                    index=pd.DatetimeIndex([], name="timestamp"),
                    dtype=float,
                )
            funded_trace.attrs["semantics"] = (
                "Diagnostic OHLC co-extreme bound only: day_start_balance is closed "
                "cash before the bar, day_start_equity is the previous union-bar "
                "mark, and opening marks carried positions on the executable "
                "side of today's open before management; conservative_intraday_min "
                "also includes day_start_equity, a stop-aware original-unit snapshot "
                "before management, any original-unit entry snapshot, and post-fill "
                "equity with all remaining longs at today's low and shorts at today's "
                "high simultaneously. Actual-open exposure/risk is separate from the "
                "post-pending plan. This is not a reconstructed intraday path or an "
                "exact prop-firm rule-breach series. risk_sizing_base is the capital "
                "available to new risk/vol sizing at that day's decision point (actual "
                "equity when funded sizing is disabled), including on days with no "
                "candidate. It does not identify the older sizing bases of carried "
                "positions, so day-level rescaling is not exact exposure-level replay."
                " positions_opened is the exact count of backtester entry fills on "
                "the union bar; it is the only field used for minimum-trading-day "
                "qualification. verified_flat_at_end is true only when no position "
                "or pending entry remains in the simulated book."
            )
            funded_trace.attrs["account_currency_conversion_applied"] = False
            funded_trace.attrs["currency_basis"] = "UNCONVERTED_RAW_QUOTE_CURRENCY"
            funded_trace.attrs["account_currency_limitation"] = (
                "Exposure, stop-risk, P&L, balance, and equity values are summed in "
                "each instrument's raw quote currency. Mixed-quote portfolios are "
                "not account-currency-safe and must not be used for a funded verdict "
                "until causal FX/contract-value conversion is supplied."
            )
            if self.funded_cash_risk_mode is not None:
                funded_trace.attrs["funded_cash_risk_status"] = "DATA_BLOCKED"
                funded_trace.attrs["funded_cash_risk_blockers"] = (
                    _FUNDED_CASH_RISK_DATA_BLOCKERS
                )

        return PortfolioResult(
            instruments=instruments, equity=equity_series, trades=trades, metrics=metrics,
            per_instrument=per_inst, constraint_log=dict(constraint_log),
            funded_trace=funded_trace,
        )

    # -- mechanics (per-instrument) -------------------------------------------
    @staticmethod
    def _managed_close_fill_count(
        position: dict,
        before_p1: bool,
        before_p2: bool,
        before_units: float,
        exit_reason: str,
    ) -> int:
        """Count close transactions emitted by one TradeManager update.

        The manager returns aggregate realised P&L, so P1 and P2 can otherwise be
        mistaken for one transaction when both trigger on the same bar.  The TMS
        flags identify those fills; a terminal reason adds the final close.  The
        unit-change fallback keeps custom managers from silently receiving a free
        partial when they do not expose the standard flags.
        """

        fills = int(not before_p1 and bool(position.get("tms_p1", False)))
        fills += int(not before_p2 and bool(position.get("tms_p2", False)))
        if exit_reason and exit_reason != "closed":
            fills += 1
        tolerance = max(1e-12, abs(before_units) * 1e-12)
        if fills == 0 and float(position.get("units", before_units)) < before_units - tolerance:
            fills = 1
        return fills

    def _funded_adverse_snapshot(
        self,
        positions: dict[str, dict],
        realized: float,
        data: dict[str, dict],
        timestamp,
        *,
        stop_aware: bool,
    ) -> tuple[float, dict[str, float]]:
        """Return an executable-side OHLC adverse snapshot without mutation.

        ``stop_aware`` means a crossed live stop is marked at the stop, or at the
        worse opening price after a gap, and the corresponding exit commission is
        included.  Otherwise positions are marked at the raw adverse bar extreme.
        Callers pass shallow copies when preserving pre-management units/stops is
        important; this helper never mutates either representation.
        """

        equity = float(realized)
        contributions: dict[str, float] = defaultdict(float)
        for inst, position in positions.items():
            d = data[inst]
            i = d["pos"].get(timestamp)
            mark = float(position["last_px"])
            stop_filled = False
            direction = position["direction"]
            is_long = (
                direction == Direction.LONG
                or direction == "long"
                or getattr(direction, "value", "") == "long"
            )
            if i is not None:
                open_px = float(d["open"][i])
                low = float(d["low"][i])
                high = float(d["high"][i])
                raw_mark = low if is_long else high
                stop = float(position["stop"])
                if stop_aware and np.isfinite(stop):
                    if is_long and low <= stop:
                        raw_mark = min(stop, open_px)
                        stop_filled = True
                    elif not is_long and high >= stop:
                        raw_mark = max(stop, open_px)
                        stop_filled = True
                mark = self._fill(
                    raw_mark, inst, buying=not is_long,
                    timeframe=position["tf"],
                )

            unrealized = self._unrealized(position, mark)
            close_cost = float(d["commission"]) if stop_filled else 0.0
            equity += unrealized - close_cost
            contributions[inst] += (
                float(position.get("realized_pnl_total", 0.0))
                + unrealized - close_cost
            )
        return equity, contributions

    def _funded_exposure_diagnostics(
        self,
        open_positions: dict[str, dict],
        pending: dict[str, dict],
        pending_trims: dict[str, float],
    ) -> dict[str, float]:
        """Separate current open risk from the queued next-open portfolio plan.

        Values deliberately remain in raw quote-currency units.  The trace metadata
        makes that limitation machine-visible; silently inventing an FX conversion
        here would be materially worse than an explicit research limitation.
        """

        actual_gross = 0.0
        actual_stop_risk = 0.0
        planned_gross = 0.0
        planned_stop_risk = 0.0

        for inst, position in open_positions.items():
            units = max(0.0, float(position["units"]))
            mark = float(position["last_px"])
            stop = float(position["stop"])
            direction = position["direction"]
            is_long = (
                direction == Direction.LONG
                or direction == "long"
                or getattr(direction, "value", "") == "long"
            )
            gross = abs(units * mark)
            stop_risk = units * max(
                0.0, mark - stop if is_long else stop - mark,
            )
            actual_gross += gross
            actual_stop_risk += stop_risk

            trim_fraction = float(pending_trims.get(inst, 0.0))
            retention = 1.0 - float(np.clip(trim_fraction, 0.0, 1.0))
            planned_gross += gross * retention
            planned_stop_risk += stop_risk * retention

        for inst, order in pending.items():
            # The execution loop will not fill a stale pending order over an already
            # open symbol, so neither should the planned diagnostic double count it.
            if inst in open_positions:
                continue
            position = order.get("pos")
            if position is None:
                continue
            planned_gross += abs(float(position.notional))
            planned_stop_risk += max(0.0, float(order.get("risk_abs", 0.0)))

        return {
            "actual_open_gross_exposure": float(actual_gross),
            "actual_open_stop_risk": float(actual_stop_risk),
            "post_pending_planned_gross_exposure": float(planned_gross),
            "post_pending_planned_stop_risk": float(planned_stop_risk),
        }

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

    def _check_exit(
        self,
        position,
        hi,
        lo,
        close_px,
        i,
        max_hold,
        instrument,
        timeframe: str | None = None,
        open_px: float | None = None,
    ):
        long = position["direction"] == Direction.LONG or position["direction"] == "long" or getattr(position["direction"], "value", "") == "long"
        stop, target = position["stop"], position["target"]
        # The opening print is causally first. A gap through a stop fills at the
        # worse open; a favourable gap through a resting target is credited only at
        # the target, not at the better open. Once the open is between both barriers,
        # unresolved intrabar ordering remains conservatively stop-first.
        if open_px is not None:
            open_px = float(open_px)
            if long:
                if open_px <= stop:
                    return self._fill(open_px, instrument, buying=False, timeframe=timeframe), "stop"
                if open_px >= target:
                    return self._fill(target, instrument, buying=False, timeframe=timeframe), "target"
            else:
                if open_px >= stop:
                    return self._fill(open_px, instrument, buying=True, timeframe=timeframe), "stop"
                if open_px <= target:
                    return self._fill(target, instrument, buying=True, timeframe=timeframe), "target"
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
