"""Pure close-time signal construction for frozen V14 regime_switch_5day."""

from __future__ import annotations

from math import isfinite
from typing import Mapping

import pandas as pd

from .data import decision_input_hash, iso_date, select_vix, session_label
from .spec import BookSpec, REGIME_SYMBOL, SECTOR_SYMBOLS, SYMBOLS


def _indicators(panel: Mapping[str, pd.DataFrame]) -> tuple[pd.DataFrame, ...]:
    close = pd.DataFrame({symbol: panel[symbol]["close"] for symbol in SYMBOLS})
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    rsi = 100.0 - 100.0 / (1.0 + gain / loss)
    rsi = rsi.mask((gain == 0) & (loss == 0), 50.0)
    sma = close.rolling(200).mean()
    returns = close.pct_change()
    atr_columns = {}
    for symbol in SYMBOLS:
        frame = panel[symbol]
        previous_close = frame["close"].shift()
        true_range = pd.concat(
            [
                frame["high"] - frame["low"],
                (frame["high"] - previous_close).abs(),
                (frame["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_columns[symbol] = true_range.rolling(20).mean()
    return close, rsi, sma, returns, pd.DataFrame(atr_columns)


def build_decision(
    panel: Mapping[str, pd.DataFrame],
    vix: pd.DataFrame,
    decision_session,
    spec: BookSpec,
) -> dict:
    """Build the exact V14 close decision without using the next open."""

    day = session_label(decision_session)
    if any(day not in panel[symbol].index for symbol in SYMBOLS):
        raise ValueError(f"decision session {iso_date(day)} is absent from the common panel")
    close, rsi, sma, returns, atr = _indicators(panel)
    vix_row = select_vix(vix, day, spec)

    legs: list[dict] = []
    if vix_row["close"] < spec.vix_stress_threshold:
        regime = "low_vix_rsi2_long"
        candidates = [
            symbol
            for symbol in (REGIME_SYMBOL, *SECTOR_SYMBOLS)
            if isfinite(float(rsi.at[day, symbol]))
            and isfinite(float(sma.at[day, symbol]))
            and close.at[day, symbol] > sma.at[day, symbol]
            and rsi.at[day, symbol] < spec.rsi_entry_threshold
        ]
        candidates.sort(key=lambda symbol: (float(rsi.at[day, symbol]), symbol))
        for symbol in candidates[: spec.maximum_positions]:
            legs.append(
                {
                    "instrument": symbol,
                    "direction": "long",
                    "direction_sign": 1,
                    "score": float(101.0 - rsi.at[day, symbol]),
                    "decision_atr": float(atr.at[day, symbol]),
                    "rsi2": float(rsi.at[day, symbol]),
                    "sma200": float(sma.at[day, symbol]),
                    "signal_rationale": "close above SMA200 and Wilder RSI2 below 10",
                }
            )
    else:
        regime = "stress_sector_reversal"
        ranked = sorted(
            SECTOR_SYMBOLS,
            key=lambda symbol: (float(returns.at[day, symbol]), symbol),
        )
        side = {symbol: 1 for symbol in ranked[:2]}
        side.update({symbol: -1 for symbol in ranked[-2:]})
        for symbol in sorted(side):
            direction = side[symbol]
            legs.append(
                {
                    "instrument": symbol,
                    "direction": "long" if direction > 0 else "short",
                    "direction_sign": direction,
                    "score": float(direction),
                    "decision_atr": float(atr.at[day, symbol]),
                    "one_day_return": float(returns.at[day, symbol]),
                    "signal_rationale": (
                        "lagged VIX >= 30; buy two weakest and short two strongest sectors"
                    ),
                }
            )

    for leg in legs:
        if not isfinite(leg["decision_atr"]) or leg["decision_atr"] <= 0:
            raise ValueError(f"invalid causal ATR for {leg['instrument']}")

    return {
        "strategy_id": "v14_regime_switch_5day",
        "decision_date": iso_date(day),
        "regime": regime,
        "lagged_vix": float(vix_row["close"]),
        "lagged_vix_source_date": vix_row["source_date"],
        "lagged_vix_age_days": int(vix_row["age_days"]),
        "decision_input_sha256": decision_input_hash(panel, day, vix_row),
        "legs": legs,
    }
