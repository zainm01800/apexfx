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
from typing import TYPE_CHECKING

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
    ) -> None:
        self.cfg = cfg or get_config().risk
        self.bayesian_sizer = bayesian_sizer

    def permit(
        self,
        signal: Signal,
        account: AccountState,
        market: MarketState,
        *,
        regime: "RegimeLabel | None" = None,
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

        # 1. Drawdown circuit-breaker (hard, non-overridable)
        if breaker_tripped(account, cfg.drawdown_breaker):
            return veto(
                "drawdown_breaker",
                f"Drawdown {account.drawdown:.1%} >= breaker "
                f"{cfg.drawdown_breaker:.0%}; new positions halted.",
            )

        # 2. ATR stop distance
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
                return veto(
                    "bayesian_drawdown_breaker",
                    f"Bayesian drawdown breaker: drawdown {account.drawdown:.1%} "
                    f">= {self.bayesian_sizer.max_drawdown:.0%}; new positions halted.",
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

        # 5. Regime aggression scaling (optional)
        if regime is not None:
            scale = regime.aggression_scalar()
            risk_fraction *= scale
            detail["regime"] = getattr(regime, "name", "?")
            detail["regime_scale"] = scale
            applied.append(f"regime_scale={scale:.2f}")
            if risk_fraction <= 0:
                return veto("regime_zero", f"Regime {detail['regime']} scaled size to zero.")

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
