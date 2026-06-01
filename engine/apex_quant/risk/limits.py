"""Portfolio-level exposure limits: gross cap + correlation-aware cap.

These shrink (or zero) a proposed notional so the book respects hard exposure
ceilings. Correlation awareness prevents 'six trades that are really one bet':
positions highly correlated with the candidate are pooled into a cluster, and
the cluster's combined notional is capped.
"""

from __future__ import annotations

from apex_quant.risk.types import AccountState, MarketState


def gross_exposure_cap(
    proposed_notional: float,
    account: AccountState,
    max_total_exposure: float,
) -> tuple[float, bool]:
    """Clamp so (existing gross + new) / equity <= ``max_total_exposure``.
    Returns ``(allowed_notional, was_capped)``."""
    ceiling = max_total_exposure * account.equity
    headroom = max(0.0, ceiling - account.gross_notional)
    if proposed_notional <= headroom:
        return proposed_notional, False
    return headroom, True


def correlation_cap(
    proposed_notional: float,
    account: AccountState,
    market: MarketState,
    correlation_threshold: float,
    max_correlated_exposure: float,
) -> tuple[float, bool]:
    """Clamp the correlated cluster's combined notional.

    The cluster = open positions whose |corr| to the candidate >= threshold,
    plus the candidate itself. Combined notional <= ``max_correlated_exposure``
    of equity. Returns ``(allowed_notional, was_capped)``."""
    cluster_existing = 0.0
    for pos in account.open_positions:
        corr = abs(market.correlations.get(pos.instrument, 0.0))
        if corr >= correlation_threshold:
            cluster_existing += abs(pos.notional)

    ceiling = max_correlated_exposure * account.equity
    headroom = max(0.0, ceiling - cluster_existing)
    if proposed_notional <= headroom:
        return proposed_notional, False
    return headroom, True
