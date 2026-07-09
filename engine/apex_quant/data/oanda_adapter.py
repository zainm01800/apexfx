"""OANDA v20 REST API OHLCV adapter.

Fetches historical candle bars directly from OANDA's REST API. Supports automatic
dual-endpoint resolution (Live vs Practice) depending on the API key provided, and
implements paginated fetches to bypass OANDA's 5,000 candle limit per call.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
import httpx
import pandas as pd

from apex_quant.data.adapter import DataAdapter, register_adapter
from apex_quant.data.schema import Bar, validate_ohlcv

logger = logging.getLogger(__name__)

_GRANULARITY_MAP = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "H1",
    "4h": "H4",
    "1d": "D",
    "1w": "W",
}

# Granularity duration in seconds (to advance timestamps during pagination)
_TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}


@register_adapter("oanda")
class OandaAdapter(DataAdapter):
    """Fetch candles from OANDA with pagination and automatic endpoint fallback."""

    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout
        self._api_key = os.environ.get("APEX_OANDA_API_KEY", "")
        
        # Determine the base URL by probing with the key (tries Live first, then Demo)
        self._base_url = "https://api-fxtrade.oanda.com"
        if self._api_key:
            self._base_url = self._probe_endpoint()
            logger.info("OandaAdapter initialised using endpoint: %s", self._base_url)
        else:
            logger.warning("OandaAdapter: APEX_OANDA_API_KEY not configured in env!")

    def _probe_endpoint(self) -> str:
        """Probe the OANDA Live vs Practice API endpoints to see which matches the key."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }
        # Probe using EUR_USD for 1 candle
        probe_url = "https://api-fxtrade.oanda.com/v3/instruments/EUR_USD/candles?granularity=D&count=1"
        try:
            with httpx.Client(timeout=5.0, headers=headers) as client:
                r = client.get(probe_url)
                if r.status_code == 200:
                    return "https://api-fxtrade.oanda.com"
        except Exception:
            pass

        # Fallback to demo
        return "https://api-fxpractice.oanda.com"

    def _fetch_chunk(self, ticker: str, start_iso: str, end_iso: str, granularity: str) -> dict:
        url = f"{self._base_url}/v3/instruments/{ticker}/candles"
        params = {
            "granularity": granularity,
            "price": "M",
            "from": start_iso,
            "to": end_iso,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }
        with httpx.Client(timeout=self._timeout, headers=headers) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

    def get_history(
        self,
        instrument: str,
        start: pd.Timestamp | str,
        end: pd.Timestamp | str,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLVC candles in the range [start, end] with paginated OANDA calls."""
        if not self._api_key:
            raise RuntimeError("OandaAdapter: APEX_OANDA_API_KEY is not configured in environment variables.")

        granularity = _GRANULARITY_MAP.get(timeframe)
        if not granularity:
            raise ValueError(f"OandaAdapter: Unsupported timeframe: {timeframe}")

        ticker = instrument.replace("/", "_").upper()

        ts_start = pd.Timestamp(start)
        if ts_start.tzinfo is None:
            ts_start = ts_start.tz_localize("UTC")
        else:
            ts_start = ts_start.tz_convert("UTC")

        ts_end = pd.Timestamp(end)
        if ts_end.tzinfo is None:
            ts_end = ts_end.tz_localize("UTC")
        else:
            ts_end = ts_end.tz_convert("UTC")

        all_candles = []
        current_start = ts_start
        step_seconds = _TF_SECONDS.get(timeframe, 86400)

        # Loop to handle pagination (OANDA limit is 5000 candles per call)
        while current_start < ts_end:
            start_iso = current_start.isoformat().replace("+00:00", "Z")
            end_iso = ts_end.isoformat().replace("+00:00", "Z")

            logger.info("OANDA Fetching %s (%s) from %s to %s", instrument, timeframe, start_iso, end_iso)
            try:
                data = self._fetch_chunk(ticker, start_iso, end_iso, granularity)
            except Exception as e:
                logger.error("OANDA fetch chunk failed: %s", e)
                break

            candles = data.get("candles", [])
            if not candles:
                break

            all_candles.extend(candles)

            # Find timestamp of the last candle returned
            last_time_str = candles[-1]["time"]
            # Convert RFC3339 string (e.g. 2016-10-17T15:00:00.000000000Z) to Timestamp
            last_time = pd.Timestamp(last_time_str)

            # Advance starting point by 1 timeframe period to prevent requesting duplicate overlap bar
            next_start = last_time + pd.Timedelta(seconds=step_seconds)
            if next_start <= current_start:
                # Prevent infinite loops in case OANDA returns the same timestamps
                current_start = current_start + pd.Timedelta(seconds=step_seconds * len(candles))
            else:
                current_start = next_start

            # If we received fewer candles than OANDA's limit, we've reached the end
            if len(candles) < 4800:
                break

        if not all_candles:
            from apex_quant.data.schema import empty_ohlcv
            return empty_ohlcv()

        # Parse final list of candles into DataFrame
        timestamps = [pd.to_datetime(c["time"]) for c in all_candles]
        opens = [float(c["mid"]["o"]) for c in all_candles]
        highs = [float(c["mid"]["h"]) for c in all_candles]
        lows = [float(c["mid"]["l"]) for c in all_candles]
        closes = [float(c["mid"]["c"]) for c in all_candles]
        volumes = [float(c["volume"]) for c in all_candles]

        frame = pd.DataFrame(
            {
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": volumes,
            },
            index=timestamps,
        )
        frame.index.name = "timestamp"
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        
        # Deduplicate indices in case of any overlap issues
        frame = frame[~frame.index.duplicated(keep="first")]
        
        return validate_ohlcv(frame)

    def get_latest(self, instrument: str, timeframe: str = "1d") -> Bar | None:
        end = pd.Timestamp.utcnow()
        # Lookback depends on timeframe to ensure we capture the latest bar
        lookback_days = 10 if timeframe in ("1d", "1w") else 2
        start = end - pd.Timedelta(days=lookback_days)
        
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
