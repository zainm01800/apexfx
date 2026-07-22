"""The supreme risk layer.

``RiskManager.permit(signal, account, market, regime=)`` is the single authority
that turns a probabilistic signal into a permitted position (possibly flat). The
signal proposes; the risk layer disposes. The decision pipeline, in order:

  0. Flat signal               -> no position
  1. Drawdown circuit-breaker  -> hard veto on ALL new positions
  2. ATR stop distance         -> wider vol => wider stop => smaller size
  3. Fractional Kelly          -> edge gate; non-positive edge => no position
  4. Per-trade risk cap        -> never risk more than max_risk_per_trade
  5. Regime aggression scale   -> damp in ranging / high-vol regimes (optional)
  6. Vol-target ceiling        -> take the more conservative of risk- vs vol-size
  7. Gross exposure cap        -> book-level gross notional ceiling
  8. Correlation cluster cap   -> don't let correlated trades become one big bet
  9. Min-position floor        -> round dust to zero

Every binding rule is recorded in ``Position.constraints_applied`` and the maths
in ``Position.sizing_detail`` - full decision transparency.
"""

from __future__ import annotations

import logging
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
            stop_price = signal.stop_price
            stop_distance = abs(market.price - stop_price)
        else:
            stop_price, stop_distance = atr_stop(
                market.price, market.atr, cfg.atr_stop_mult, signal.direction
            )
        detail["stop_distance"] = stop_distance
        if stop_distance <= 0:
            return veto("invalid_stop", "Non-positive stop distance; cannot size.")

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
        risk_fraction = kelly_rf
        if risk_fraction > cfg.max_risk_per_trade:
            risk_fraction = cfg.max_risk_per_trade
            applied.append("max_risk_per_trade")

        # 4.5. Drawdown amber-zone ramp (1.0 -> 0.0 between reducing_limit and the halt)
        if dd_scale < 1.0:
            risk_fraction *= dd_scale
            applied.append(f"drawdown_reducing_scale={dd_scale:.2f}")
            if risk_fraction <= 0:
                return veto(
                    "drawdown_reducing_zero",
                    f"Drawdown {account.drawdown:.1%} scaled size to zero "
                    f"(halt at {cfg.drawdown_breaker:.0%}).",
                )

        # 5. Regime aggression scaling (optional)
        if regime is not None:
            scale = regime.aggression_scalar()
            risk_fraction *= scale
            detail["regime"] = getattr(regime, "name", "?")
            detail["regime_scale"] = scale
            applied.append(f"regime_scale={scale:.2f}")
            if risk_fraction <= 0:
                return veto("regime_zero", f"Regime {detail['regime']} scaled size to zero.")

        # 5.5. Portfolio risk cap (prop firm safety)
        max_port_risk = getattr(cfg, "max_portfolio_risk", 0.035)
        total_open_risk = sum(getattr(p, "risk", 0.0) for p in (account.open_positions or []))
        total_open_risk_pct = total_open_risk / account.equity
        max_proposed_risk = max_port_risk - total_open_risk_pct
        
        detail["total_open_risk_pct"] = total_open_risk_pct
        detail["max_proposed_risk"] = max_proposed_risk
        
        if max_proposed_risk <= 0:
            return veto(
                "max_portfolio_risk_exceeded",
                f"Active portfolio risk {total_open_risk_pct:.2%} >= limit {max_port_risk:.2%}; new trades blocked.",
            )
        
        if risk_fraction > max_proposed_risk:
            risk_fraction = max_proposed_risk
            applied.append("portfolio_risk_cap")

        # 6. Risk-based vs vol-target notional -> take the more conservative
        rate = getattr(market, "quote_to_account_rate", 1.0)
        stop_distance_account = stop_distance * rate
        price_account = market.price * rate

        units_risk = units_from_risk(account.equity, risk_fraction, stop_distance_account)
        notional_risk = units_risk * price_account
        notional_voltarget = vol_target_notional(
            account.equity, cfg.target_portfolio_vol, market.ann_vol
        )
        detail["notional_risk"] = notional_risk
        detail["notional_voltarget"] = notional_voltarget
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

        # 9. Min-position floor
        if notional <= cfg.min_position:
            return veto("below_min_position", "Permitted size rounds to zero.")

        # Finalise
        units = notional / price_account
        final_risk_fraction = units * stop_distance_account / account.equity
        
        if getattr(signal, "target_price", None) is not None:
            target_price = signal.target_price
        else:
            target_distance = signal.reward_risk * stop_distance
            target_price = (
                market.price + target_distance
                if signal.direction == Direction.LONG
                else market.price - target_distance
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
