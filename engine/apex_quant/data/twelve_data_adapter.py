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
        symbol = instrument.upper()

        import time
        current_start = pd.Timestamp(start, tz="UTC") if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start).tz_convert("UTC")
        target_end = pd.Timestamp(end, tz="UTC") if pd.Timestamp(end).tzinfo is None else pd.Timestamp(end).tz_convert("UTC")
        
        all_dfs = []
        calls_made = 0

        while current_start < target_end:
            start_str = current_start.strftime("%Y-%m-%d %H:%M:%S")
            end_str = target_end.strftime("%Y-%m-%d %H:%M:%S")

            params = {
                "symbol": symbol,
                "interval": interval,
                "start_date": start_str,
                "end_date": end_str,
                "apikey": self.api_key,
                "outputsize": 5000,
                "timezone": "UTC",
                "order": "ASC"
            }

            try:
                # Rate limit sleep safety (8 seconds between calls for free key)
                if calls_made > 0:
                    time.sleep(8.0)
                
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.get(self.base_url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                
                calls_made += 1

                if "values" not in data:
                    print(f"[*] Twelve Data API Error: {data.get('status')} - {data.get('message')}")
                    break

                values = data["values"]
                if not values:
                    break

                df = pd.DataFrame(values)
                df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize("UTC", ambiguous="NaT", nonexistent="NaT")
                df.set_index("datetime", inplace=True)
                
                # Map columns to schema standard (open, high, low, close, volume)
                df = df.rename(columns={
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "volume": "volume"
                })

                # Handle missing volume column (common for Forex/Crypto in Twelve Data)
                if "volume" not in df.columns:
                    df["volume"] = 0.0
                else:
                    df["volume"] = df["volume"].fillna(0.0)

                # Convert types to float
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                df.index.name = "timestamp"
                df = df[["open", "high", "low", "close", "volume"]].dropna()

                if df.empty:
                    break

                all_dfs.append(df)

                # Advance search pointer to prevent infinite loops
                last_time = df.index[-1]
                if last_time <= current_start:
                    current_start += pd.Timedelta(seconds=1)
                else:
                    current_start = last_time + pd.Timedelta(seconds=1)

                # If we fetched less than the limit, we hit the end of the history
                if len(df) < 5000:
                    break

            except Exception as e:
                print(f"[*] Twelve Data Pager Error for {instrument}: {type(e).__name__}: {e}")
                break

        if not all_dfs:
            return empty_ohlcv()

        final_df = pd.concat(all_dfs)
        # Drop duplicates and sort chronologically
        final_df = final_df[~final_df.index.duplicated(keep="last")].sort_index()
        return final_df

    def get_latest(self, instrument: str, timeframe: str = "1d") -> Bar | None:
        from apex_quant.data.schema import Bar
        end = pd.Timestamp.utcnow()
        start = end - pd.Timedelta(days=10)
        df = self.get_history(instrument, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), timeframe)
        if df.empty:
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
