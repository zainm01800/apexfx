"""Unit tests for Book S (Session SMC & Order Flow Engine)."""

import unittest
from datetime import datetime, timezone
import pandas as pd
import numpy as np

from apex_quant.models.book_s_session_smc import (
    BOOK_LABEL,
    INITIAL_EQUITY_USD,
    advance_book_s_forward,
    new_book_s_state,
    runtime_payload,
    validate_book_s_state,
)


class TestBookSSessionSMC(unittest.TestCase):
    def test_state_lifecycle(self):
        state = new_book_s_state("2026-08-01")
        validate_book_s_state(state)
        self.assertEqual(state["book"], BOOK_LABEL)
        self.assertEqual(state["equity"], INITIAL_EQUITY_USD)
        self.assertEqual(len(state["positions"]), 0)
        self.assertEqual(len(state["trades"]), 0)

    def test_runtime_payload(self):
        state = new_book_s_state("2026-08-01")
        state["trades"] = [
            {"pnl": 630.0, "win": True},
            {"pnl": -350.0, "win": False},
        ]
        payload = runtime_payload(state)
        self.assertEqual(payload["book"], BOOK_LABEL)
        self.assertEqual(payload["total_trades"], 2)
        self.assertEqual(payload["win_rate"], 50.0)
        self.assertAlmostEqual(payload["profit_factor"], 1.8, places=1)


if __name__ == "__main__":
    unittest.main()
