"""Twelve Data OHLCV adapter.

Fetches deep historical intraday candles (15m, 1h, 1d) for Forex, Crypto,
and Equities using a Twelve Data API Key.
"""

from __future__ import annotations

import httpx
import pandas as pd

from apex_quant.data.adapter import DataAdapter, register_adapter
from apex_quant.data.schema import empty_ohlcv

@register_adapter("twelvedata")
class TwelveDataAdapter(DataAdapter):
    def __init__(self, api_key: str, timeout: float = 20.0):
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = "https://api.twelvedata.com/time_series"

    def get_history(self, instrument: str, start: str, end: str, timeframe: str = "1d") -> pd.DataFrame:
        if not self.api_key or self.api_key == "none":
            return empty_ohlcv()

        # Map timeframe to Twelve Data interval formats
        # 15m -> 15min, 1h -> 1h, 1d -> 1day
        interval_map = {"15m": "15min", "1h": "1h", "1d": "1day", "1w": "1week"}
        interval = interval_map.get(timeframe, "1day")

        # Map symbols (e.g. BTC/USD -> BTC/USD, EUR/USD -> EUR/USD, AAPL -> AAPL)
        symbol = instrument.upper()

        params = {
            "symbol": symbol,
            "interval": interval,
            "start_date": start,
            "end_date": end,
            "apikey": self.api_key,
            "outputsize": 5000, # Max historical limit per request
            "timezone": "UTC",
            "order": "ASC"      # Chronological order
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(self.base_url, params=params)
                resp.raise_for_status()
                data = resp.json()

            if "values" not in data:
                print(f"[*] Twelve Data API Error: {data.get('status')} - {data.get('message')}")
                return empty_ohlcv()

            values = data["values"]
            if not values:
                return empty_ohlcv()

            df = pd.DataFrame(values)
            df["datetime"] = pd.to_datetime(df["datetime"])
            df.set_index("datetime", inplace=True)
            
            # Map columns to schema standard (open, high, low, close, volume)
            df = df.rename(columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume"
            })

            # Convert types to float
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            df.index.name = "timestamp"
            # Return standard columns
            return df[["open", "high", "low", "close", "volume"]].dropna()

        except Exception as e:
            print(f"[*] Twelve Data Fetch Error for {instrument}: {type(e).__name__}: {e}")
            return empty_ohlcv()
