"""Unit and invariant tests for SPY intraday forward books (V24 and V30)."""

import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

ENGINE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_DIR))

import numpy as np
import pandas as pd

from apex_quant.forward_intraday.spec import BOOKS, BookSpec, PROFILES, SCHEMA_VERSION
from apex_quant.forward_intraday.signals import (
    compute_v24_signal,
    compute_v30_signal,
    entry_units,
)
from apex_quant.forward_intraday.engine import (
    export_public_payload,
    mark_equity,
    new_state,
    step_session,
)
from apex_quant.forward_intraday.data import HistoricalWarmup


class TestForwardIntraday(unittest.TestCase):
    def setUp(self):
        self.profile = PROFILES["higher_5_12"]
        self.v24_spec = BOOKS["v24"]
        self.v30_spec = BOOKS["v30"]

    def test_spec_invariants(self):
        self.assertEqual(self.v24_spec.initial_equity_gbp, 100_000.0)
        self.assertEqual(self.v30_spec.initial_equity_gbp, 100_000.0)
        self.assertEqual(self.v24_spec.daily_loss_fraction, 0.05)
        self.assertEqual(self.v30_spec.daily_loss_fraction, 0.05)
        self.assertEqual(self.v24_spec.maximum_loss_fraction, 0.12)
        self.assertEqual(self.v30_spec.maximum_loss_fraction, 0.12)
        self.assertEqual(self.v24_spec.per_trade_risk_fraction, 0.01)
        self.assertEqual(self.v30_spec.per_trade_risk_fraction, 0.01)

    def test_v24_signals(self):
        # Long signal: close > upper band and close > vwap
        o, p, sigma, vwap, vol = 500.0, 498.0, 0.0020, 500.5, 0.01
        upper = max(o, p) * (1.0 + sigma)  # 500.0 * 1.002 = 501.0
        sig_long = compute_v24_signal(30, 502.0, o, p, sigma, vwap, vol, "2026-09-04T10:00:00-04:00")
        self.assertIsNotNone(sig_long)
        self.assertEqual(sig_long.direction, 1)
        self.assertEqual(sig_long.barrier, max(upper, vwap))

        # Short signal: close < lower band and close < vwap
        lower = min(o, p) * (1.0 - sigma)  # 498.0 * 0.998 = 497.004
        sig_short = compute_v24_signal(60, 496.0, o, p, sigma, 497.5, vol, "2026-09-04T10:30:00-04:00")
        self.assertIsNotNone(sig_short)
        self.assertEqual(sig_short.direction, -1)
        self.assertEqual(sig_short.barrier, min(lower, 497.5))

        # Neutral signal: between bands
        sig_neutral = compute_v24_signal(30, 499.0, o, p, sigma, 499.0, vol, "2026-09-04T10:00:00-04:00")
        self.assertIsNotNone(sig_neutral)
        self.assertEqual(sig_neutral.direction, 0)

        # Off-boundary returns None
        sig_off = compute_v24_signal(15, 502.0, o, p, sigma, vwap, vol, "2026-09-04T09:45:00-04:00")
        self.assertIsNone(sig_off)

    def test_v30_signals(self):
        # ATR Breakout: upper = o + 0.5 * atr14
        o, p, atr14, vol = 500.0, 498.0, 4.0, 0.01
        # upper = 502.0, lower = 498.0
        # Long signal at offset 45 (10:15 NY)
        sig_long = compute_v30_signal(45, 503.0, o, p, atr14, vol, "2026-09-04T10:15:00-04:00")
        self.assertIsNotNone(sig_long)
        self.assertEqual(sig_long.direction, 1)
        self.assertEqual(sig_long.barrier, o)  # Stop is strictly today's session open!

        # Short signal
        sig_short = compute_v30_signal(30, 497.0, o, p, atr14, vol, "2026-09-04T10:00:00-04:00")
        self.assertIsNotNone(sig_short)
        self.assertEqual(sig_short.direction, -1)
        self.assertEqual(sig_short.barrier, o)

        # Neutral signal
        sig_neutral = compute_v30_signal(30, 500.5, o, p, atr14, vol, "2026-09-04T10:00:00-04:00")
        self.assertIsNotNone(sig_neutral)
        self.assertEqual(sig_neutral.direction, 0)

    def test_entry_units_sizing(self):
        equity = 100_000.0
        price = 500.0
        barrier = 495.0
        direction = 1
        fx = 1.30
        volatility = 0.01
        floor = 91_000.0
        units, risk_unit = entry_units(
            equity, price, barrier, direction, fx, volatility, floor, self.profile, 1.0, 1.0
        )
        self.assertGreater(units, 0)
        # Check risk ceiling: units * risk_unit <= 1% post-fee equity
        post_fee_equity = equity - (units * price * 0.0001 / fx)
        self.assertLessEqual(units * risk_unit, self.profile.risk * post_fee_equity + 1e-6)
        # Check gross ceiling: units * (price / fx) <= 4x equity
        self.assertLessEqual(units * (price / fx), self.profile.gross * post_fee_equity + 1e-6)
        # Check minimum notional £1000
        self.assertGreaterEqual(units * (price / fx), 1000.0)

    def test_engine_state_and_step(self):
        state = new_state(self.v30_spec)
        self.assertEqual(state["equity"], 100_000.0)
        self.assertEqual(state["cash"], 100_000.0)
        self.assertEqual(state["book_id"], "v30")

        # Mock warmup
        warmup = HistoricalWarmup(
            atr_14=4.0,
            volatility_14=0.01,
            prior_close=498.0,
            noise_sigmas={30: 0.0010, 60: 0.0015},
            fx_rate=1.30,
            as_of_session="2026-09-03",
        )

        # Build 390 synthetic 1-minute bars
        times = pd.date_range("2026-09-04 09:30:00", "2026-09-04 15:59:00", freq="min")
        # Session open 500.0, breaks out above 502.0 at minute 29 (offset 30), ends at 505.0
        prices = np.linspace(500.0, 505.0, len(times))
        df_bars = pd.DataFrame({
            "open": prices,
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
            "volume": 1000.0,
        }, index=times)

        stepped = step_session(state, self.v30_spec, warmup, df_bars, "2026-09-04")
        self.assertEqual(stepped["revision"], 2)
        self.assertEqual(stepped["last_processed_session"], "2026-09-04")
        # Must be flat at end of session
        self.assertIsNone(stepped["position"])
        # At least one trade should have been opened and flattened at 15:59
        self.assertGreater(len(stepped["trades"]), 0)
        self.assertEqual(stepped["trades"][-1]["exit_reason"], "scheduled_flat")

        # Test export public payload
        payload = export_public_payload(stepped, self.v30_spec)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["book_id"], "v30")
        self.assertEqual(payload["metadata"]["account_currency"], "GBP")
        self.assertEqual(payload["metadata"]["initial_equity"], 100_000.0)

    def test_engine_state_and_step_v24(self):
        state = new_state(self.v24_spec)
        self.assertEqual(state["equity"], 100_000.0)
        self.assertEqual(state["book_id"], "v24")

        warmup = HistoricalWarmup(
            atr_14=4.0,
            volatility_14=0.01,
            prior_close=498.0,
            noise_sigmas={30: 0.0010, 60: 0.0015},
            fx_rate=1.30,
            as_of_session="2026-09-03",
        )

        times = pd.date_range("2026-09-04 09:30:00", "2026-09-04 15:59:00", freq="min")
        prices = np.linspace(500.0, 505.0, len(times))
        df_bars = pd.DataFrame({
            "open": prices,
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
            "volume": 1000.0,
        }, index=times)

        stepped = step_session(state, self.v24_spec, warmup, df_bars, "2026-09-04")
        self.assertEqual(stepped["revision"], 2)
        self.assertIsNone(stepped["position"])
        self.assertGreater(len(stepped["trades"]), 0)


if __name__ == "__main__":
    unittest.main()
