"""Yahoo Finance OHLCV adapter.

Hits the same public chart endpoint the JS app's ``/api/candles`` uses, so the
Python engine and the frontend draw from one source of truth. Ticker mapping for
forex pairs mirrors ``api/candles.js`` exactly.
"""

from __future__ import annotations

import httpx
import pandas as pd

from apex_quant.data.adapter import DataAdapter, register_adapter
from apex_quant.data.schema import Bar, validate_ohlcv

_YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

# Mirrors toYahooTicker() in api/candles.js for the forex universe.
_FOREX_TICKERS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "USD/CHF": "CHF=X",
    "AUD/USD": "AUDUSD=X",
    "NZD/USD": "NZDUSD=X",
    "USD/CAD": "CAD=X",
    "GBP/JPY": "GBPJPY=X",
    "EUR/GBP": "EURGBP=X",
}

# Crypto on Yahoo uses "BASE-USD" (e.g. BTC-USD), NOT the forex "=X" suffix.
_CRYPTO_TICKERS = {
    "BTC/USD": "BTC-USD",
    "ETH/USD": "ETH-USD",
    "SOL/USD": "SOL-USD",
    "XRP/USD": "XRP-USD",
    "ADA/USD": "ADA-USD",
    "DOGE/USD": "DOGE-USD",
    "BNB/USD": "BNB-USD",
    "LTC/USD": "LTC-USD",
}

_TF_INTERVAL = {"1d": "1d", "1w": "1wk", "1M": "1mo"}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def to_yahoo_ticker(instrument: str) -> str:
    """Map an APEX instrument id to a Yahoo ticker. Crypto- and forex-aware;
    falls back to the raw symbol for equities/ETFs."""
    if instrument in _CRYPTO_TICKERS:
        return _CRYPTO_TICKERS[instrument]
    if instrument in _FOREX_TICKERS:
        return _FOREX_TICKERS[instrument]
    if "/" in instrument:
        base, _, quote = instrument.partition("/")
        from apex_quant.config import CRYPTO_BASES

        if base.upper() in CRYPTO_BASES:  # generic crypto like "AVAX/USD" -> "AVAX-USD"
            return f"{base.upper()}-{quote.upper()}"
        return instrument.replace("/", "") + "=X"  # generic forex like "XAU/USD" -> "XAUUSD=X"
    return instrument


@register_adapter("yahoo")
class YahooAdapter(DataAdapter):
    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    def _fetch_json(self, ticker: str, period1: int, period2: int, interval: str) -> dict:
        url = _YF_CHART.format(ticker=ticker)
        params = {
            "period1": period1,
            "period2": period2,
            "interval": interval,
            "events": "history",
            "includePrePost": "false",
        }
        with httpx.Client(timeout=self._timeout, headers=_HEADERS) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _parse(json_obj: dict) -> pd.DataFrame:
        result = (json_obj.get("chart", {}).get("result") or [None])[0]
        if not result or not result.get("timestamp"):
            from apex_quant.data.schema import empty_ohlcv

            return empty_ohlcv()

        ts = result["timestamp"]
        q = result["indicators"]["quote"][0]
        frame = pd.DataFrame(
            {
                "open": q.get("open"),
                "high": q.get("high"),
                "low": q.get("low"),
                "close": q.get("close"),
                "volume": q.get("volume"),
            },
            index=pd.to_datetime(ts, unit="s", utc=True),
        )
        frame.index.name = "timestamp"
        # Yahoo bar timestamp is the period START; advance to the close so the
        # bar is only "known" at end-of-period (preserves the PIT convention).
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        frame["volume"] = frame["volume"].fillna(0.0)
        return validate_ohlcv(frame)

    def get_history(
        self,
        instrument: str,
        start: pd.Timestamp | str,
        end: pd.Timestamp | str,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        interval = _TF_INTERVAL.get(timeframe, "1d")
        ticker = to_yahoo_ticker(instrument)
        p1 = int(pd.Timestamp(start, tz="UTC").timestamp()) if pd.Timestamp(start).tzinfo is None \
            else int(pd.Timestamp(start).timestamp())
        p2 = int(pd.Timestamp(end, tz="UTC").timestamp()) if pd.Timestamp(end).tzinfo is None \
            else int(pd.Timestamp(end).timestamp())
        data = self._fetch_json(ticker, p1, p2, interval)
        df = self._parse(data)
        return df.loc[(df.index >= pd.Timestamp(start, tz="UTC")) & (df.index <= pd.Timestamp(end, tz="UTC"))] \
            if len(df) else df

    def get_latest(self, instrument: str, timeframe: str = "1d") -> Bar | None:
        end = pd.Timestamp.utcnow()
        start = end - pd.Timedelta(days=10)
        df = self.get_history(instrument, start, end, timeframe)
        if not len(df):
            return None
        row = df.iloc[-1]
        return Bar(
            timestamp=df.index[-1],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
