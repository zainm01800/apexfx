"""Economic News Calendar Filter.

Blocks new positions within 30 minutes before and 15 minutes after high-impact
macroeconomic news releases (like NFP, CPI, FOMC, and interest rate decisions).
"""

from __future__ import annotations

import logging
import os
import pandas as pd
from typing import TYPE_CHECKING, Tuple

from apex_quant.config import get_config

logger = logging.getLogger("apex_quant.risk.news_calendar")


class NewsCalendarFilter:
    def __init__(self, app_url: str | None = None) -> None:
        cfg = get_config()
        self.app_url = app_url or (cfg.sentiment.app_url if hasattr(cfg, "sentiment") else None)
        self._cached_events: list = []
        self._last_fetch: pd.Timestamp | None = None

    def _fetch_events(self, t: pd.Timestamp) -> list:
        """Fetch economic calendar events from the app endpoint."""
        if not self.app_url:
            return []

        # Re-fetch at most once every 6 hours
        if self._last_fetch is not None and (t - self._last_fetch).total_seconds() < 21600:
            return self._cached_events

        try:
            import httpx
            base = self.app_url.rstrip("/")
            start_date = (t - pd.Timedelta(days=2)).strftime("%Y-%m-%d")
            end_date = (t + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
            url = f"{base}/api/economic-calendar"
            
            with httpx.Client(timeout=8.0) as client:
                r = client.get(url, params={"from": start_date, "to": end_date})
                if r.status_code == 200:
                    events = r.json()
                    if isinstance(events, list):
                        self._cached_events = events
                        self._last_fetch = t
                        logger.info("Successfully fetched %d economic calendar events", len(events))
                        return events
        except Exception as e:
            logger.warning("Failed to fetch economic calendar: %s. Using static fallback.", e)
        
        return self._cached_events

    def check_veto(self, instrument: str, t: pd.Timestamp) -> Tuple[bool, str]:
        """Check if trading should be blocked due to upcoming or recent high-impact news.

        Args:
            instrument: The currency pair (e.g. 'EUR/USD') or asset ticker (e.g. 'AAPL').
            t:          The current timestamp in UTC.

        Returns:
            (True, reason) if trading is blocked, else (False, "").
        """
        # Parse currency symbols (forex specific check)
        currencies = []
        if "/" in instrument:
            currencies = [c.upper().strip() for c in instrument.split("/")]
        else:
            # For non-forex (e.g. equities), we check US events by default (USD)
            currencies = ["USD"]

        events = self._fetch_events(t)

        # Always check static regular calendar rules (like FOMC/NFP fallbacks)
        # to ensure coverage even if API fails or rate-limits.
        static_veto, static_reason = self._check_static_schedule(currencies, t)
        if static_veto:
            return True, static_reason

        for event in events:
            # Check impact level
            impact = str(event.get("impact", "")).lower()
            if impact != "high" and "high" not in impact:
                continue

            event_currency = str(event.get("country", event.get("currency", ""))).upper().strip()
            # Map country codes to standard currency codes where needed
            country_map = {"US": "USD", "EU": "EUR", "UK": "GBP", "GB": "GBP", "AU": "AUD", "JP": "JPY", "CA": "CAD", "NZ": "NZD", "CH": "CHF"}
            mapped_currency = country_map.get(event_currency, event_currency)

            if mapped_currency not in currencies:
                continue

            event_time_str = event.get("time", "")
            if not event_time_str:
                continue

            try:
                # Timestamps from Finnhub calendar are in UTC: "2026-07-14 13:30:00"
                event_time = pd.Timestamp(event_time_str)
                if event_time.tzinfo is None:
                    event_time = event_time.tz_localize("UTC")
                else:
                    event_time = event_time.tz_convert("UTC")

                now_utc = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")

                diff_minutes = (event_time - now_utc).total_seconds() / 60.0

                # Veto: -30 mins to +15 mins
                if -15.0 <= diff_minutes <= 30.0:
                    event_name = event.get("event", "High Impact News")
                    reason = (
                        f"Economic news event [{event_name}] ({mapped_currency}) "
                        f"at {event_time.strftime('%Y-%m-%d %H:%M')} UTC. "
                        f"Current time: {now_utc.strftime('%H:%M')} UTC. "
                        f"Veto window: -30m / +15m around release."
                    )
                    return True, reason
            except Exception as e:
                logger.debug("Failed to parse event time '%s': %s", event_time_str, e)

        return False, ""

    def _check_static_schedule(self, currencies: list[str], t: pd.Timestamp) -> Tuple[bool, str]:
        """A robust fallback that blocks major recurring scheduled high-impact events.
        
        Specifically:
          - Non-Farm Payrolls (NFP): First Friday of every month at 13:30 UTC (USD).
        """
        now_utc = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")

        # NFP is USD specific
        if "USD" in currencies:
            # Check if today is the first Friday of the month
            if now_utc.day_of_week == 4 and 1 <= now_utc.day <= 7:
                # NFP time is 13:30 UTC
                nfp_time = now_utc.replace(hour=13, minute=30, second=0, microsecond=0)
                diff = (nfp_time - now_utc).total_seconds() / 60.0
                if -15.0 <= diff <= 30.0:
                    return True, f"NFP Fallback Veto: First Friday of the month near 13:30 UTC."

        return False, ""
