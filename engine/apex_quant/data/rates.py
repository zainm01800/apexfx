"""Point-in-time rate provider from central bank policy rates CSV."""

from __future__ import annotations

import logging
from pathlib import Path
import pandas as pd

from apex_quant.strategies.currency_momentum import parse_base_quote

logger = logging.getLogger("apex_quant.data.rates")

# Staleness guard (audit D-M2): the CSV is hand-maintained and ends 2025-01;
# quietly answering 2026 queries with year-old rates manufactures fake carry.
# We warn (once per process) instead of fabricating newer rows.
_STALE_AFTER = pd.Timedelta(days=45)
_staleness_warned = False


class CSVRateProvider:
    """Provides point-in-time central bank policy rates from a CSV file.

    Guarantees no future lookahead: looking up a rate at time t only uses
    rows with effective_date <= t. If the newest row in the CSV is more than
    45 days older than the query date, a staleness warning is logged once per
    process — the returned rates are then by definition out of date.
    """

    def __init__(self, csv_path: str | Path | None = None) -> None:
        if csv_path is None:
            csv_path = Path(__file__).resolve().parent.parent.parent / "data_store/central_bank_rates.csv"
        
        self.csv_path = Path(csv_path)
        if not self.csv_path.exists():
            raise FileNotFoundError(f"Policy rates CSV not found at: {self.csv_path}")

        # Load rates and parse effective_date as index
        df = pd.read_csv(self.csv_path)
        df["effective_date"] = pd.to_datetime(df["effective_date"], utc=True)
        self.df = df.set_index("effective_date").sort_index()

    def __call__(self, instrument: str, t: pd.Timestamp) -> tuple[float, float] | None:
        """Return (base_rate, quote_rate) for instrument effective at time t."""
        try:
            base, quote = parse_base_quote(instrument)
        except Exception:
            return None

        # Localize t to UTC if naive
        ts = pd.Timestamp(t)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")

        # Staleness guard: warn once per process when the CSV's newest row is
        # too old for the query date (rates returned are then stale by
        # construction — update central_bank_rates.csv; never fabricate rows).
        global _staleness_warned
        latest_available = self.df.index[-1]
        if not _staleness_warned and ts - latest_available > _STALE_AFTER:
            logger.warning(
                "CSVRateProvider: central_bank_rates.csv is stale — latest row %s, "
                "query date %s (>%d days old); rates for base/quote are OUT OF DATE. "
                "Update the CSV.",
                latest_available.date(), ts.date(), _STALE_AFTER.days,
            )
            _staleness_warned = True

        # Select all rows effective at or before t
        valid_rows = self.df[self.df.index <= ts]
        if valid_rows.empty:
            return None

        # Take the most recent row
        latest_row = valid_rows.iloc[-1]

        # Get rates for base and quote
        if base not in latest_row or quote not in latest_row:
            return None

        try:
            base_rate = float(latest_row[base])
            quote_rate = float(latest_row[quote])
            return base_rate, quote_rate
        except (ValueError, TypeError):
            return None
