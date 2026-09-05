"""Explicit quote-currency cash accounting for repaired paper engines.

Rates use only an eligible bar, never a future observation or a default 1:1
conversion. These are market-bar conversion proxies, not broker statements.
"""
from __future__ import annotations

import math
import pandas as pd

VERSION = "quote_cash_v2"
FIAT = {"USD", "GBP", "EUR", "JPY", "CHF", "CAD", "AUD", "NZD"}
# Listing units verified against Yahoo chart metadata on 2026-09-05.
# ISWD's issuer lists GBP; the provider's OHLC is specifically in pence.
PINNED_QUOTES = {"ISWD.L": "GBp", "ISDU.L": "USD", "ISDE.L": "USD", "SGLD.L": "USD"}


def positive(value, name="value") -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def quote_currency(symbol: str, panel: dict) -> str:
    frame = panel.get(symbol)
    explicit = frame.attrs.get("quote_currency") if frame is not None else None
    if explicit:
        return str(explicit)
    if symbol in PINNED_QUOTES:
        return PINNED_QUOTES[symbol]
    if "/" in symbol:
        return symbol.split("/")[-1]
    if "." in symbol:
        raise ValueError(f"{symbol}: explicit quote_currency metadata required")
    return "USD"  # callers' pinned, US-listed equity/ETF universe only


def conversion_rate(symbol, account, panel, timestamp, field="close") -> float:
    quote = quote_currency(symbol, panel)
    scale = .01 if quote in {"GBp", "GBX"} else 1.0
    if scale != 1:
        quote = "GBP"
    if quote == account:
        return scale
    stamp = pd.Timestamp(timestamp)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")

    def rate(base, target):
        if base == target:
            return 1.0
        for pair, inverse in ((f"{base}/{target}", False), (f"{target}/{base}", True)):
            frame = panel.get(pair)
            if frame is None or frame.empty:
                continue
            dates = pd.to_datetime(frame.index, utc=True)
            eligible = frame.loc[dates <= stamp]
            if eligible.empty:
                continue
            when = pd.to_datetime(eligible.index[-1], utc=True)
            if stamp - when > pd.Timedelta(days=4):
                raise ValueError(f"{pair}: stale conversion bar")
            # A prior day's open is not a current open: carry its settled close.
            px = positive(eligible.iloc[-1][field if when == stamp else "close"], pair)
            return 1.0 / px if inverse else px
        raise ValueError(f"missing causal conversion {base}->{target}")

    try:
        return scale * rate(quote, account)
    except ValueError:
        if quote == "USD" or account == "USD":
            raise
        return scale * rate(quote, "USD") * rate("USD", account)


def lot_pnl(position, mark) -> float:
    side = 1 if position["direction"] == "long" else -1
    return sum((float(mark) - lot["entry_price"]) * lot["units"] * side
               for lot in position["lots"])


def close_fraction(position, fraction, price) -> float:
    """Reduce each outstanding lot pro-rata; return quote-currency P&L."""
    if not 0 < fraction <= 1:
        raise ValueError("invalid closing fraction")
    pnl = lot_pnl(position, price) * fraction
    for lot in position["lots"]:
        lot["units"] *= 1 - fraction
    position["units"] = sum(lot["units"] for lot in position["lots"])
    return pnl
