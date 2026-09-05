"""Market data fetching and historical feature calculations for SPY intraday strategies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = timezone.utc
DATA_DIR = Path(__file__).resolve().parents[2] / "data_store"


class DataUnavailable(RuntimeError):
    """Raised when required live or historical market data is unavailable."""


@dataclass(frozen=True, slots=True)
class HistoricalWarmup:
    atr_14: float
    volatility_14: float
    prior_close: float
    noise_sigmas: dict[int, float]  # offset_minutes -> mean relative move
    fx_rate: float
    as_of_session: str


def current_ny_time() -> datetime:
    return datetime.now(NY_TZ)


def is_us_market_hours(dt: datetime | None = None) -> bool:
    """True if Monday-Friday between 09:30 and 16:00 America/New_York."""
    t = (dt or current_ny_time()).astimezone(NY_TZ)
    if t.weekday() >= 5:  # Saturday or Sunday
        return False
    market_open = time(9, 30)
    market_close = time(16, 0)
    return market_open <= t.time() <= market_close


def get_gbp_usd_rate() -> float:
    """Fetch current GBP/USD exchange rate with fallback to local store."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("GBPUSD=X")
        hist = ticker.history(period="5d")
        if not hist.empty and "Close" in hist:
            rate = float(hist["Close"].iloc[-1])
            if np.isfinite(rate) and rate > 0.5:
                return rate
    except Exception:
        pass
    # Fallback to local daily file if present
    fx_path = DATA_DIR / "fx_rates.json"
    if fx_path.exists():
        try:
            data = json.loads(fx_path.read_text())
            rate = float(data.get("GBPUSD", 1.30))
            if np.isfinite(rate) and rate > 0:
                return rate
        except Exception:
            pass
    return 1.30  # Standard conservative fallback


def get_spy_daily_dataframe() -> pd.DataFrame:
    """Load SPY daily dataframe from parquet or yfinance."""
    parquet_path = DATA_DIR / "SPY_1d.parquet"
    if parquet_path.exists():
        try:
            df = pd.read_parquet(parquet_path)
            if not df.empty and len(df) >= 20:
                df = df.sort_index()
                return df
        except Exception:
            pass
    # Download fresh daily bars
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        df = spy.history(period="60d")
        if not df.empty:
            df = df.rename(columns={c: c.lower() for c in df.columns})
            return df.sort_index()
    except Exception as exc:
        raise DataUnavailable(f"Unable to load SPY daily history: {exc}") from exc
    raise DataUnavailable("Empty SPY daily history")


def compute_historical_warmup(as_of_date: str | None = None) -> HistoricalWarmup:
    """Compute ATR14, daily volatility, prior close, and noise sigmas strictly before today."""
    daily = get_spy_daily_dataframe()
    daily_ny = daily.copy()
    if daily_ny.index.tz is None:
        daily_ny.index = pd.to_datetime(daily_ny.index).tz_localize("UTC").tz_convert(NY_TZ)
    else:
        daily_ny.index = pd.to_datetime(daily_ny.index).tz_convert(NY_TZ)

    today_str = as_of_date or current_ny_time().strftime("%Y-%m-%d")
    # Filter strictly prior to today's session
    prior_daily = daily_ny[daily_ny.index.strftime("%Y-%m-%d") < today_str]
    if len(prior_daily) < 15:
        raise DataUnavailable(f"Insufficient prior daily bars for SPY: {len(prior_daily)} < 15")

    # True Range and ATR14
    high = prior_daily["high"].to_numpy(float)
    low = prior_daily["low"].to_numpy(float)
    close = prior_daily["close"].to_numpy(float)

    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))

    if len(tr) < 14:
        raise DataUnavailable("Insufficient true range bars for ATR14")
    atr_14 = float(np.mean(tr[-14:]))
    prior_close = float(close[-1])

    # 14-period daily return volatility (standard deviation with ddof=1)
    returns = (close[1:] / close[:-1]) - 1.0
    if len(returns) < 14:
        raise DataUnavailable("Insufficient daily returns for volatility calculation")
    volatility_14 = float(np.std(returns[-14:], ddof=1))

    # Noise band sigmas from recent 15m/30m bars (prior 14 normal sessions)
    noise_sigmas = compute_noise_band_sigmas(prior_sessions_count=14, before_date=today_str)

    fx_rate = get_gbp_usd_rate()
    as_of = prior_daily.index[-1].strftime("%Y-%m-%d")

    return HistoricalWarmup(
        atr_14=atr_14,
        volatility_14=volatility_14,
        prior_close=prior_close,
        noise_sigmas=noise_sigmas,
        fx_rate=fx_rate,
        as_of_session=as_of,
    )


def compute_noise_band_sigmas(prior_sessions_count: int = 14, before_date: str | None = None) -> dict[int, float]:
    """Compute mean abs(close/open - 1) across prior normal sessions for offsets 30, 60, ..., 360."""
    # Default benchmark sigmas from research in case intraday bars are temporarily unavailable
    fallback_sigmas = {
        30: 0.0010,
        60: 0.0015,
        90: 0.0020,
        120: 0.0022,
        150: 0.0020,
        180: 0.0016,
        210: 0.0017,
        240: 0.0022,
        270: 0.0023,
        300: 0.0021,
        330: 0.0023,
        360: 0.0022,
    }
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        df = spy.history(period="30d", interval="15m")
        if df.empty:
            return fallback_sigmas
        df.index = pd.to_datetime(df.index).tz_convert(NY_TZ)
        if before_date:
            df = df[df.index.strftime("%Y-%m-%d") < before_date]
        grouped = list(df.groupby(df.index.date))
        normal_sessions = []
        for d, g in grouped:
            # Check for regular full cash session (26 15-minute bars between 09:30 and 16:00)
            if len(g) >= 25:
                o = float(g["Open"].iloc[0])
                if not np.isfinite(o) or o <= 0:
                    continue
                points = {}
                for offset in range(30, 361, 30):
                    bar_idx = (offset // 15) - 1
                    if bar_idx < len(g):
                        c = float(g["Close"].iloc[bar_idx])
                        points[offset] = abs(c / o - 1.0)
                if len(points) == 12:
                    normal_sessions.append(points)
        if len(normal_sessions) >= prior_sessions_count:
            last_n = normal_sessions[-prior_sessions_count:]
            return {
                offset: float(np.mean([s[offset] for s in last_n]))
                for offset in range(30, 361, 30)
            }
    except Exception:
        pass
    return fallback_sigmas


def fetch_live_session_minutes(session_date: str | None = None) -> pd.DataFrame:
    """Fetch 1-minute bars for today's regular trading session (09:30 to 16:00 NY)."""
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        df = spy.history(period="5d", interval="1m")
        if df.empty:
            raise DataUnavailable("No intraday 1m SPY bars returned by provider")
        df.index = pd.to_datetime(df.index).tz_convert(NY_TZ)
        target_date = session_date or current_ny_time().strftime("%Y-%m-%d")
        today_bars = df[df.index.strftime("%Y-%m-%d") == target_date].copy()
        if today_bars.empty:
            return pd.DataFrame()

        today_bars = today_bars.rename(columns={c: c.lower() for c in today_bars.columns})
        # Filter to regular market hours: 09:30 to 16:00 NY
        market_start = pd.Timestamp(f"{target_date} 09:30:00", tz=NY_TZ)
        market_end = pd.Timestamp(f"{target_date} 16:00:00", tz=NY_TZ)
        today_bars = today_bars[(today_bars.index >= market_start) & (today_bars.index < market_end)]
        return today_bars
    except Exception as exc:
        raise DataUnavailable(f"Failed to fetch live 1m bars: {exc}") from exc
