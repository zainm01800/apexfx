"""The supreme risk layer.

``RiskManager.permit(signal, account, market, regime=)`` is the single authority
that turns a probabilistic signal into a permitted position (possibly flat). The
signal proposes; the risk layer disposes. The decision pipeline, in order:

  0. Flat signal               -> no position
  1. Drawdown circuit-breaker  -> hard veto on ALL new positions
  2. ATR stop distance         -> wider vol => wider stop => smaller size
  3. Fractional Kelly          -> edge gate; non-positive edge => no position
  4. Per-trade risk cap        -> never risk more than max_risk_per_trade
  4.6 Portfolio vol scalar     -> de-lever the whole book when realised vol runs hot
  5. Regime aggression scale   -> damp in ranging / high-vol regimes (optional)
  6. Vol-target ceiling        -> take the more conservative of risk- vs vol-size
  7. Gross exposure cap        -> book-level gross notional ceiling
  8. Correlation cluster cap   -> don't let correlated trades become one big bet
  8.5 Per-position notional cap -> bound single-name gap-tail losses (optional)
  9. Min-position floor        -> round dust to zero

Every binding rule is recorded in ``Position.constraints_applied`` and the maths
in ``Position.sizing_detail`` - full decision transparency.
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from typing import TYPE_CHECKING
from apex_quant.risk.news_calendar import NewsCalendarFilter

from apex_quant.config import RiskConfig, get_config
from apex_quant.risk.circuit_breaker import breaker_tripped
from apex_quant.risk.limits import correlation_cap, gross_exposure_cap
from apex_quant.risk.sizing import fractional_kelly, units_from_risk, vol_target_notional
from apex_quant.risk.stops import atr_stop
from apex_quant.risk.types import (
    AccountState,
    Direction,
    MarketState,
    Position,
    Signal,
)

if TYPE_CHECKING:
    from apex_quant.regime.base import RegimeLabel

from apex_quant.risk.bayesian_sizer import BayesianRiskSizer  # noqa: E402

logger = logging.getLogger("apex_quant.risk")


class RiskManager:
    def __init__(
        self,
        cfg: RiskConfig | None = None,
        bayesian_sizer: BayesianRiskSizer | None = None,
        news_filter: NewsCalendarFilter | None = None,
    ) -> None:
        self.cfg = cfg or get_config().risk
        self.bayesian_sizer = bayesian_sizer
        #: Book-wide risk multiplier set by the portfolio vol-target overlay (step 4.6).
        #: The RiskManager sizes one signal at a time and cannot see the realised
        #: volatility of the equity curve it is feeding, so whoever owns that curve
        #: (PortfolioBacktester, or the live loop) sets this each bar. 1.0 = no-op.
        self.risk_scalar: float = 1.0
        #: Runtime opt-out of the step-5.5 sequential portfolio-risk clamp, set ONLY by
        #: PortfolioBacktester when cfg.portfolio_risk_cap_mode == "simultaneous" (the
        #: backtester then applies one end-of-bar gamma uniformly; prereg
        #: engine/data_store/order_invariant_prereg.md). Deliberately an attribute, not
        #: read from config here: the live loop never sets it, so a live book always
        #: keeps the sequential cap even if the config field is flipped.
        self.defer_portfolio_risk_cap: bool = False
        # Initialize or assign the news calendar filter
        if news_filter is not None:
            self.news_filter = news_filter
        else:
            from apex_quant.risk.news_calendar import NewsCalendarFilter
            self.news_filter = NewsCalendarFilter()

    def permit(
        self,
        signal: Signal,
        account: AccountState,
        market: MarketState,
        *,
        regime: "RegimeLabel | None" = None,
        t: "pd.Timestamp | None" = None,
    ) -> Position:
        cfg = self.cfg
        applied: list[str] = []
        detail: dict = {
            "probability": signal.probability,
            "reward_risk": signal.reward_risk,
            "ann_vol": market.ann_vol,
            "atr": market.atr,
        }

        def veto(reason_key: str, msg: str) -> Position:
            applied.append(reason_key)
            pos = Position(
                instrument=signal.instrument,
                direction=signal.direction,
                permitted=False,
                risk_fraction=0.0,
                constraints_applied=applied,
                rationale=msg,
                sizing_detail=detail,
            )
            logger.info("RISK VETO %s: %s", signal.instrument, msg)
            return pos

        # 0. Flat signal
        if signal.direction == Direction.FLAT:
            return veto("flat_signal", "Signal is flat; no position.")

        # 0.1 Numerical integrity gate.  Pydantic rejects non-finite values on
        # ordinary construction, but ``model_copy(update=...)`` and
        # ``model_construct`` deliberately bypass validation.  The risk boundary
        # must therefore defend itself as well: comparisons with NaN are false and
        # previously allowed a NaN stop/FX rate to escape as a permitted order.
        for label, value in (
            ("signal_probability", signal.probability),
            ("signal_reward_risk", signal.reward_risk),
            ("signal_confidence", signal.confidence),
        ):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return veto("invalid_signal_numeric", f"{label} is not numeric; order blocked.")
            if not np.isfinite(number):
                return veto("invalid_signal_numeric", f"{label} is non-finite; order blocked.")
        if not 0.0 <= float(signal.probability) <= 1.0:
            return veto("invalid_signal_numeric", "Signal probability is outside [0, 1].")
        if float(signal.reward_risk) <= 0.0:
            return veto("invalid_signal_numeric", "Signal reward/risk must be positive.")
        if not 0.0 <= float(signal.confidence) <= 1.0:
            return veto("invalid_signal_numeric", "Signal confidence is outside [0, 1].")

        for reason, label, value in (
            ("invalid_market_price", "Market price", market.price),
            ("invalid_market_volatility", "Annualised volatility", market.ann_vol),
            ("invalid_market_atr", "ATR", market.atr),
            (
                "invalid_quote_to_account_rate",
                "Quote-to-account FX rate",
                market.quote_to_account_rate,
            ),
        ):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return veto(reason, f"{label} is not numeric; order blocked.")
            if not np.isfinite(number) or number <= 0.0:
                return veto(reason, f"{label} must be finite and positive; order blocked.")

        for reason, label, value in (
            ("invalid_account_equity", "Account equity", account.equity),
            ("invalid_account_peak_equity", "Peak equity", account.peak_equity),
        ):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return veto(reason, f"{label} is not numeric; order blocked.")
            if not np.isfinite(number) or number <= 0.0:
                return veto(reason, f"{label} must be finite and positive; order blocked.")

        if account.day_start_equity is not None:
            try:
                day_start_equity = float(account.day_start_equity)
            except (TypeError, ValueError):
                return veto(
                    "invalid_account_day_start_equity",
                    "Day-start equity is not numeric; order blocked.",
                )
            if not np.isfinite(day_start_equity) or day_start_equity <= 0.0:
                return veto(
                    "invalid_account_day_start_equity",
                    "Day-start equity must be finite and positive; order blocked.",
                )

        # AccountState/OpenPosition construction normally enforces these rules.
        # Recheck at the live-money boundary because callers can bypass Pydantic
        # validation with model_copy/model_construct.
        for index, open_position in enumerate(account.open_positions or []):
            for field in ("notional", "risk"):
                try:
                    number = float(getattr(open_position, field))
                except (AttributeError, TypeError, ValueError):
                    return veto(
                        "invalid_open_position",
                        f"Open position {index} has invalid {field}; order blocked.",
                    )
                if not np.isfinite(number) or number < 0.0:
                    return veto(
                        "invalid_open_position",
                        f"Open position {index} {field} must be finite and non-negative; "
                        "order blocked.",
                    )

        correlations = market.correlations or {}
        if not isinstance(correlations, dict):
            return veto(
                "invalid_market_correlation",
                "Market correlations must be a mapping of finite values; order blocked.",
            )
        for instrument, correlation in correlations.items():
            try:
                number = float(correlation)
            except (TypeError, ValueError):
                return veto(
                    "invalid_market_correlation",
                    f"Correlation for {instrument} is not numeric; order blocked.",
                )
            if not np.isfinite(number):
                return veto(
                    "invalid_market_correlation",
                    f"Correlation for {instrument} is non-finite; order blocked.",
                )

        # 0.5 DAILY-LOSS STOP — the prop-firm rule the from-peak breaker cannot see.
        #
        # Every prop contract has a daily-loss limit measured from the DAY'S OPENING
        # equity (3% on FundedElite, 4% on Orion). The drawdown breaker measures from
        # PEAK, so a bad day that starts at a fresh high shows near-zero drawdown while
        # blowing the daily rule — the account is gone and the breaker never fired.
        # config.prop.yaml declared this stop on 2026-07-22 and it was never implemented;
        # the book's worst day is -3.70% against a 3% limit, an 18.4% chance of losing a
        # funded account over 24 months.
        daily_limit = float(getattr(cfg, "daily_loss_limit", 0.0) or 0.0)
        if daily_limit > 0.0 and account.daily_loss >= daily_limit:
            detail["daily_loss"] = account.daily_loss
            return veto(
                "daily_loss_stop",
                f"Daily loss {account.daily_loss:.2%} >= limit {daily_limit:.2%}; "
                f"no new positions for the rest of the session.",
            )

        # 1. Drawdown circuit-breaker (Three-state: ACTIVE / REDUCING / HALTED)
        from apex_quant.risk.circuit_breaker import BreakerState, breaker_state, reducing_scale
        reducing_limit = getattr(cfg, "drawdown_reducing_limit", cfg.drawdown_breaker * 0.5)
        breaker_status = breaker_state(account, cfg.drawdown_breaker, reducing_limit)

        if breaker_status == BreakerState.HALTED:
            return veto(
                "drawdown_breaker",
                f"Drawdown {account.drawdown:.1%} >= halted breaker "
                f"{cfg.drawdown_breaker:.0%}; new positions halted.",
            )

        # Amber zone: de-risk PROGRESSIVELY (applied to risk_fraction at step 5)
        # rather than blocking entries.
        #
        # This branch used to veto anything that did not "reduce exposure" — but the
        # engine only ever signals an instrument it is FLAT on, so there was never a
        # position to reduce and EVERY entry was vetoed. That turned reducing_limit
        # into a silent second hard halt (effective breaker 10%, not the configured
        # 20%) with no way out: no entries -> flat book -> equity frozen -> drawdown
        # never recovers -> permanently stuck. Reducing an OPEN position is the exit
        # path's job (stops/targets/TradeManager); this layer only sizes NEW ones.
        dd_scale = 1.0
        if breaker_status == BreakerState.REDUCING:
            dd_scale = reducing_scale(account, cfg.drawdown_breaker, reducing_limit)
            detail["circuit_breaker_reducing_active"] = True
            detail["drawdown_reducing_scale"] = dd_scale
            logger.info(
                "RISK AMBER %s: drawdown %.1f%% in warning zone (>= %.0f%%); sizing scaled to %.0f%%.",
                signal.instrument, account.drawdown * 100, reducing_limit * 100, dd_scale * 100,
            )

        # 1.2. Economic News Calendar Filter (Nautilus-inspired)
        #
        # ``t`` is the DECISION time: backtest engines pass the current bar's
        # timestamp (deterministic — wall-clock reads made backtests depend on
        # when they were run, audit E2); only live contexts may leave it None,
        # which means "now".
        if self.news_filter is not None:
            check_t = t or pd.Timestamp.utcnow()
            blocked, reason = self.news_filter.check_veto(signal.instrument, check_t)
            if blocked:
                return veto(
                    "economic_news_veto",
                    f"Economic calendar veto on {signal.instrument}: {reason}"
                )

        # 1.5. Per-timeframe slot buckets (replaces single global cap)
        #
        #   Swing  (1d / 1w)  → max 5 concurrent positions
        #   Intraday (1h)     → max 4 concurrent positions
        #   Scalp  (15m)      → max 3 concurrent positions
        #
        # Each bucket is independent — swing trades can NEVER block
        # intraday or scalp entries. The global hard cap is the sum (12).
        # Group timeframes into semantic style buckets
        def get_style_bucket(tf: str) -> str:
            tf_clean = str(tf).lower().strip()
            if tf_clean in ("1w", "1d"):
                return "swing"
            if tf_clean == "1h":
                return "intraday"
            if tf_clean in ("15m", "5m"):
                return "scalp"
            return "swing"  # Default fallback

        _BUCKET_LIMITS: dict[str, int] = {
            "swing": getattr(cfg, "max_swing_slots", 10),      # Swing (1d / 1w) -> configurable (default 10)
            "intraday": 8,    # Intraday (1h) -> max 8 concurrent positions
            "scalp": 6,       # Scalp (15m / 5m) -> max 6 concurrent positions
        }
        _GLOBAL_HARD_CAP: int = getattr(cfg, "max_concurrent_trades", 12)

        candidate_tf: str = getattr(signal, "timeframe", None) or "1h"
        candidate_sleeve: str = getattr(signal, "sleeve", None) or "default"
        candidate_bucket = get_style_bucket(candidate_tf)
        bucket_limit = _BUCKET_LIMITS.get(candidate_bucket, 4)

        # Check per-sleeve slot capacity limit if configured (Option A: no slot starvation)
        sleeve_limit = getattr(cfg, f"max_{candidate_sleeve}_slots", None) if candidate_sleeve != "default" else None
        if sleeve_limit is not None:
            open_in_sleeve = sum(
                1 for pos in (account.open_positions or [])
                if getattr(pos, "sleeve", "default") == candidate_sleeve
            )
            if open_in_sleeve >= sleeve_limit:
                return veto(
                    "sleeve_bucket_full",
                    f"Sleeve '{candidate_sleeve}' full ({open_in_sleeve}/{sleeve_limit} slots used); "
                    f"new {signal.instrument} position blocked.",
                )
        else:
            # Count open positions in the same semantic style bucket
            open_in_bucket = sum(
                1 for pos in (account.open_positions or [])
                if getattr(pos, "sleeve", "default") == "default" and get_style_bucket(getattr(pos, "timeframe", "1d")) == candidate_bucket
            )
            if open_in_bucket >= bucket_limit:
                return veto(
                    "timeframe_bucket_full",
                    f"{candidate_bucket.upper()} bucket full ({open_in_bucket}/{bucket_limit} slots used); "
                    f"new {candidate_tf} positions blocked.",
                )

        total_open = len(account.open_positions or [])
        if total_open >= _GLOBAL_HARD_CAP:
            return veto(
                "global_trade_cap",
                f"Global trade cap reached ({total_open}/{_GLOBAL_HARD_CAP}); all new positions halted.",
            )

        # 2. Stop distance
        if getattr(signal, "stop_price", None) is not None:
            try:
                stop_price = float(signal.stop_price)
            except (TypeError, ValueError):
                return veto("invalid_stop", "Stop price is not numeric; order blocked.")
            stop_distance = abs(market.price - stop_price)
        else:
            stop_price, stop_distance = atr_stop(
                market.price, market.atr, cfg.atr_stop_mult, signal.direction
            )
        detail["stop_distance"] = stop_distance
        if (
            not np.isfinite(stop_price)
            or not np.isfinite(stop_distance)
            or stop_price <= 0.0
            or stop_distance <= 0.0
        ):
            return veto(
                "invalid_stop",
                "Stop price and distance must be finite and positive; order blocked.",
            )
        if (
            signal.direction == Direction.LONG
            and stop_price >= market.price
        ) or (
            signal.direction == Direction.SHORT
            and stop_price <= market.price
        ):
            return veto(
                "invalid_stop_side",
                f"{signal.direction.value.upper()} stop is not on the protective side "
                "of the current market price; order blocked.",
            )

        # Resolve and validate the target at the same decision price as the stop.
        # Besides malformed custom targets, this catches an ATR/R-multiple target
        # at or below zero before any position can be formed.
        if getattr(signal, "target_price", None) is not None:
            try:
                target_price = float(signal.target_price)
            except (TypeError, ValueError):
                return veto("invalid_target", "Target price is not numeric; order blocked.")
        else:
            target_distance = signal.reward_risk * stop_distance
            target_price = (
                market.price + target_distance
                if signal.direction == Direction.LONG
                else market.price - target_distance
            )
        if not np.isfinite(target_price) or target_price <= 0.0:
            return veto(
                "invalid_target",
                "Target price must be finite and positive; order blocked.",
            )
        if (
            signal.direction == Direction.LONG
            and target_price <= market.price
        ) or (
            signal.direction == Direction.SHORT
            and target_price >= market.price
        ):
            return veto(
                "invalid_target_side",
                f"{signal.direction.value.upper()} target is not on the profitable side "
                "of the current market price; order blocked.",
            )

        # 3. Fractional Kelly edge gate — or Bayesian sizer if configured
        if self.bayesian_sizer is not None:
            bayes_rf = self.bayesian_sizer.risk_fraction(signal, account)
            if bayes_rf is None:
                if account.drawdown >= self.bayesian_sizer.max_drawdown:
                    return veto(
                        "bayesian_drawdown_breaker",
                        f"Bayesian drawdown breaker: drawdown {account.drawdown:.1%} "
                        f">= {self.bayesian_sizer.max_drawdown:.0%}; new positions halted.",
                    )
                # Non-positive post-adaptation Kelly: the demonstrated record has
                # no edge — veto exactly like the static fractional-Kelly gate
                # below (audit A-H2) instead of flooring to the sizer's min_risk.
                return veto(
                    "bayesian_no_edge",
                    f"Bayesian Kelly <= 0 after adaptation on {signal.instrument}; "
                    "demonstrated record has no edge to bet.",
                )
            kelly_rf = bayes_rf
            detail["bayesian_risk_fraction"] = kelly_rf
            detail["bayesian_detail"] = self.bayesian_sizer.describe(signal.instrument)
        elif cfg.kelly_fraction > 0:
            kelly_rf = fractional_kelly(signal.probability, signal.reward_risk, cfg.kelly_fraction)
            detail["kelly_risk_fraction"] = kelly_rf
            if kelly_rf <= 0:
                return veto(
                    "no_edge",
                    f"Fractional Kelly <= 0 (p={signal.probability:.2f}, "
                    f"b={signal.reward_risk:.2f}); no edge to bet.",
                )
        else:
            kelly_rf = cfg.max_risk_per_trade
            detail["kelly_risk_fraction"] = kelly_rf

        # 4. Per-trade risk cap
        try:
            risk_fraction = float(kelly_rf)
            max_risk_per_trade = float(cfg.max_risk_per_trade)
        except (TypeError, ValueError):
            return veto(
                "invalid_risk_fraction",
                "Risk fraction or per-trade risk cap is not numeric; order blocked.",
            )
        if (
            not np.isfinite(risk_fraction)
            or not np.isfinite(max_risk_per_trade)
            or risk_fraction < 0.0
            or max_risk_per_trade < 0.0
        ):
            return veto(
                "invalid_risk_fraction",
                "Risk fraction and per-trade risk cap must be finite and non-negative; "
                "order blocked.",
            )
        if risk_fraction > max_risk_per_trade:
            risk_fraction = max_risk_per_trade
            applied.append("max_risk_per_trade")

        # 4.5. Drawdown amber-zone ramp (1.0 -> 0.0 between reducing_limit and the halt)
        if not np.isfinite(dd_scale) or not 0.0 <= dd_scale <= 1.0:
            return veto(
                "invalid_drawdown_scale",
                "Drawdown risk scalar must be finite and within [0, 1]; order blocked.",
            )
        if dd_scale < 1.0:
            risk_fraction *= dd_scale
            applied.append(f"drawdown_reducing_scale={dd_scale:.2f}")
            if risk_fraction <= 0:
                return veto(
                    "drawdown_reducing_zero",
                    f"Drawdown {account.drawdown:.1%} scaled size to zero "
                    f"(halt at {cfg.drawdown_breaker:.0%}).",
                )

        # 4.6. Portfolio vol-target overlay (book-wide de-/re-levering)
        # NB: `or 1.0` would be a live-money bug here — a deliberate 0.0 (halt the book)
        # is falsy and would silently become full size. Only None means "unset".
        scalar = getattr(self, "risk_scalar", None)
        try:
            scalar = 1.0 if scalar is None else float(scalar)
        except (TypeError, ValueError):
            return veto(
                "invalid_portfolio_vol_scalar",
                "Portfolio volatility scalar is not numeric; order blocked.",
            )
        if not np.isfinite(scalar) or scalar < 0.0:
            return veto(
                "invalid_portfolio_vol_scalar",
                "Portfolio volatility scalar must be finite and non-negative; order blocked.",
            )
        if scalar != 1.0:
            risk_fraction *= scalar
            detail["portfolio_vol_scalar"] = scalar
            applied.append(f"portfolio_vol_scalar={scalar:.2f}")
            if risk_fraction <= 0:
                return veto(
                    "portfolio_vol_scalar_zero",
                    f"Portfolio vol-target scalar {scalar:.2f} scaled size to zero.",
                )

        # 5. Regime aggression scaling (optional)
        if regime is not None:
            try:
                scale = float(regime.aggression_scalar())
            except (TypeError, ValueError):
                return veto(
                    "invalid_regime_scale",
                    "Regime aggression scalar is not numeric; order blocked.",
                )
            if not np.isfinite(scale) or not 0.0 <= scale <= 1.0:
                return veto(
                    "invalid_regime_scale",
                    "Regime aggression scalar must be finite and within [0, 1]; "
                    "order blocked.",
                )
            risk_fraction *= scale
            detail["regime"] = getattr(regime, "name", "?")
            detail["regime_scale"] = scale
            applied.append(f"regime_scale={scale:.2f}")
            if risk_fraction <= 0:
                return veto("regime_zero", f"Regime {detail['regime']} scaled size to zero.")

        # Optional funded-account sizing capital.  This changes only the capital
        # multiplied by the per-trade risk and volatility targets below.  Every
        # portfolio-level cap continues to be expressed against marked account
        # equity, so supplying a smaller base can only de-lever a new decision.
        # ``None`` is deliberately identical to the certified historical path.
        requested_sizing_base = account.risk_sizing_base
        sizing_equity = (
            float(account.equity)
            if requested_sizing_base is None
            else float(requested_sizing_base)
        )
        detail["risk_sizing_base"] = sizing_equity
        detail["risk_sizing_base_applied"] = requested_sizing_base is not None
        if not np.isfinite(sizing_equity) or sizing_equity <= 0.0:
            return veto(
                "risk_sizing_base_exhausted",
                "Funded risk-sizing buffer must be finite and positive; new positions blocked.",
            )

        # Optional absolute candidate cash-risk ceiling.  Percentage/Kelly,
        # drawdown, book-vol and regime logic above first express the strategy's
        # ordinary desired risk against ``sizing_equity``; a funded policy may
        # then cap that cash amount by its live daily/maximum-loss cushions.
        # Keeping this as a separate field avoids the V1 error of treating a loss
        # buffer as though it were the account capital to which a percentage risk
        # should be applied.  None leaves the historical arithmetic untouched.
        candidate_cash_cap = account.candidate_stop_risk_cap_dollars
        if candidate_cash_cap is not None:
            candidate_cash_cap = float(candidate_cash_cap)
            detail["candidate_stop_risk_cap_dollars"] = candidate_cash_cap
            if not np.isfinite(candidate_cash_cap) or candidate_cash_cap <= 0.0:
                return veto(
                    "candidate_stop_risk_cash_exhausted",
                    "Candidate stop-risk cash allowance must be finite and positive; "
                    "new position blocked.",
                )
            proposed_cash_risk = risk_fraction * sizing_equity
            detail["candidate_stop_risk_before_cash_cap_dollars"] = proposed_cash_risk
            if proposed_cash_risk > candidate_cash_cap:
                risk_fraction = candidate_cash_cap / sizing_equity
                applied.append("candidate_stop_risk_cash_cap")

        # 5.5. Portfolio risk cap (prop firm safety) — skipped only when the
        # backtester defers it to the simultaneous end-of-bar gamma (W1 prereg).
        if not getattr(self, "defer_portfolio_risk_cap", False):
            max_port_risk = float(getattr(cfg, "max_portfolio_risk", 0.035))
            total_open_risk = sum(getattr(p, "risk", 0.0) for p in (account.open_positions or []))
            if (
                not np.isfinite(max_port_risk)
                or max_port_risk < 0.0
                or not np.isfinite(total_open_risk)
                or total_open_risk < 0.0
            ):
                return veto(
                    "invalid_portfolio_risk",
                    "Portfolio risk cap and aggregate open risk must be finite and "
                    "non-negative; order blocked.",
                )
            absolute_portfolio_cap = account.aggregate_stop_risk_cap_dollars
            if absolute_portfolio_cap is None:
                # Preserve the certified/default percentage path exactly.
                total_open_risk_pct = total_open_risk / account.equity
                max_proposed_risk = max_port_risk - total_open_risk_pct

                detail["total_open_risk_pct"] = total_open_risk_pct
                detail["max_proposed_risk"] = max_proposed_risk

                if max_proposed_risk <= 0:
                    return veto(
                        "max_portfolio_risk_exceeded",
                        f"Active portfolio risk {total_open_risk_pct:.2%} >= limit {max_port_risk:.2%}; new trades blocked.",
                    )

                # ``risk_fraction`` is a fraction of the optional sizing base, while
                # the book cap is a fraction of actual equity.  Translate before the
                # comparison, and translate the remaining actual-equity budget back
                # if it binds.  With the default base these operations are identities.
                proposed_actual_risk = risk_fraction * sizing_equity / account.equity
                detail["proposed_actual_risk_fraction"] = proposed_actual_risk
                if proposed_actual_risk > max_proposed_risk:
                    risk_fraction = max_proposed_risk * account.equity / sizing_equity
                    applied.append("portfolio_risk_cap")
            else:
                configured_cap_dollars = float(max_port_risk) * account.equity
                absolute_portfolio_cap = float(absolute_portfolio_cap)
                if not np.isfinite(absolute_portfolio_cap) or absolute_portfolio_cap < 0.0:
                    return veto(
                        "invalid_aggregate_stop_risk_cap",
                        "Aggregate stop-risk cash cap must be finite and non-negative; "
                        "order blocked.",
                    )
                post_order_cap_dollars = min(
                    configured_cap_dollars, absolute_portfolio_cap,
                )
                remaining_cash_risk = post_order_cap_dollars - total_open_risk
                detail["total_open_risk_pct"] = total_open_risk / account.equity
                detail["aggregate_stop_risk_cap_dollars"] = absolute_portfolio_cap
                detail["effective_post_order_stop_risk_cap_dollars"] = (
                    post_order_cap_dollars
                )
                detail["remaining_stop_risk_cap_dollars"] = remaining_cash_risk
                detail["max_proposed_risk"] = remaining_cash_risk / account.equity

                if remaining_cash_risk <= 0.0:
                    return veto(
                        "aggregate_stop_risk_cash_exhausted",
                        f"Active portfolio stop risk {total_open_risk:,.2f} exhausts "
                        f"the {post_order_cap_dollars:,.2f} post-order cash limit.",
                    )

                proposed_cash_risk = risk_fraction * sizing_equity
                detail["proposed_actual_risk_fraction"] = (
                    proposed_cash_risk / account.equity
                )
                if proposed_cash_risk > remaining_cash_risk:
                    risk_fraction = remaining_cash_risk / sizing_equity
                    applied.append("aggregate_stop_risk_cash_cap")

        # 6. Risk-based vs vol-target notional -> take the more conservative
        rate = getattr(market, "quote_to_account_rate", 1.0)
        stop_distance_account = stop_distance * rate
        price_account = market.price * rate
        if (
            not np.isfinite(stop_distance_account)
            or stop_distance_account <= 0.0
            or not np.isfinite(price_account)
            or price_account <= 0.0
        ):
            return veto(
                "invalid_currency_conversion",
                "Converted price and stop distance must be finite and positive; "
                "order blocked.",
            )

        # 6a. Cornish-Fisher tail multiplier (W2, 2026-07-25; prereg
        # engine/data_store/cf_cvar_prereg.md). tau >= 1 contracts units on
        # heavy-tailed / adversely-skewed names; stops, targets and the recorded
        # (raw planned-loss) risk_fraction are unchanged — tau only shrinks size.
        # Off by default and a strict no-op when the caller did not precompute the
        # multipliers (live loop, single-instrument engine: fields are None).
        tau = 1.0
        if getattr(cfg, "cf_cvar_enabled", False):
            raw_tau = market.cf_tail_long if signal.direction == Direction.LONG else market.cf_tail_short
            if raw_tau is not None and np.isfinite(raw_tau):
                tau = float(np.clip(raw_tau,
                                    getattr(cfg, "cf_cvar_tau_min", 1.0),
                                    getattr(cfg, "cf_cvar_tau_max", 2.0)))
            if tau != 1.0:
                applied.append(f"cf_cvar_tau={tau:.2f}")
            detail["cf_cvar_tau"] = tau

        if not np.isfinite(tau) or tau <= 0.0:
            return veto(
                "invalid_cf_cvar_multiplier",
                "Tail-risk multiplier must be finite and positive; order blocked.",
            )

        if not np.isfinite(risk_fraction) or risk_fraction <= 0.0:
            return veto(
                "invalid_final_risk",
                "Final requested risk must be finite and positive; order blocked.",
            )

        units_risk = units_from_risk(sizing_equity, risk_fraction, stop_distance_account * tau)
        notional_risk = units_risk * price_account
        notional_voltarget = vol_target_notional(
            sizing_equity, cfg.target_portfolio_vol, market.ann_vol * tau
        )
        detail["notional_risk"] = notional_risk
        detail["notional_voltarget"] = notional_voltarget
        if (
            not np.isfinite(units_risk)
            or units_risk < 0.0
            or not np.isfinite(notional_risk)
            or notional_risk < 0.0
            or not np.isfinite(notional_voltarget)
            or notional_voltarget < 0.0
        ):
            return veto(
                "invalid_position_size",
                "Calculated units and notionals must be finite and non-negative; "
                "order blocked.",
            )
        notional = notional_risk
        if notional_voltarget < notional:
            notional = notional_voltarget
            applied.append("vol_target")

        # 7. Gross exposure cap
        notional, capped = gross_exposure_cap(notional, account, cfg.max_total_exposure)
        if capped:
            applied.append("max_total_exposure")

        # 8. Correlation cluster cap
        notional, capped = correlation_cap(
            notional, account, market, cfg.correlation_threshold, cfg.max_correlated_exposure
        )
        if capped:
            applied.append("max_correlated_exposure")

        # 8.5. Per-position notional cap (2026-07-24, W3 pre-registered gate)
        # Vol-scaled sizing reaches for large notional on low-vol names (the same
        # 1% risk on a quiet mega-cap can be ~15% of equity in notional). Capping
        # here — after every other cap — bounds single-name gap-tail losses; the
        # final risk_fraction below shrinks with the notional, so a capped trade
        # simply bets less, it is not re-levered back up.
        notional_cap_pct = float(getattr(cfg, "max_position_notional_pct", 0.0) or 0.0)
        if not np.isfinite(notional_cap_pct) or notional_cap_pct < 0.0:
            return veto(
                "invalid_position_notional_cap",
                "Per-position notional cap must be finite and non-negative; order blocked.",
            )
        if notional_cap_pct > 0.0 and notional > notional_cap_pct * account.equity:
            notional = notional_cap_pct * account.equity
            applied.append("max_position_notional")

        # 9. Min-position floor
        if not np.isfinite(notional) or notional < 0.0:
            return veto(
                "invalid_final_notional",
                "Final notional must be finite and non-negative; order blocked.",
            )
        if notional <= cfg.min_position:
            return veto("below_min_position", "Permitted size rounds to zero.")

        # Finalise
        units = notional / price_account
        final_risk_fraction = units * stop_distance_account / account.equity
        if (
            not np.isfinite(units)
            or units <= 0.0
            or not np.isfinite(notional)
            or notional <= 0.0
            or not np.isfinite(final_risk_fraction)
            or final_risk_fraction <= 0.0
        ):
            return veto(
                "invalid_final_position",
                "Final units, notional, and risk must be finite and positive; order blocked.",
            )

        rationale = (
            f"{signal.direction.value.upper()} {signal.instrument}: "
            f"p={signal.probability:.2f}, b={signal.reward_risk:.2f} -> "
            f"risk {final_risk_fraction*100:.2f}% of equity, notional {notional:,.0f}. "
            f"Constraints: {', '.join(applied) if applied else 'none binding'}."
        )
        logger.info("RISK PERMIT %s: %s", signal.instrument, rationale)

        return Position(
            instrument=signal.instrument,
            direction=signal.direction,
            units=units,
            notional=notional,
            risk_fraction=final_risk_fraction,
            stop_price=stop_price,
            stop_distance=stop_distance,
            target_price=target_price,
            permitted=True,
            constraints_applied=applied,
            rationale=rationale,
            sizing_detail=detail,
        )
