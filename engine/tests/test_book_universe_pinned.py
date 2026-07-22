"""The frozen forward book's universe must be pinned, not read from config.

Until 2026-07-22 run_paper_portfolio.py built its universe from
``cfg.data.equities + cfg.data.crypto``, so ANY edit to config.yaml — e.g.
adding research tickers to the scan list — would silently change what the
experiment of record traded on its next step. These tests make that class of
accident impossible to reintroduce quietly.
"""

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

from run_paper_portfolio import BOOK_CRYPTO, BOOK_EQUITIES, EXCLUDED  # noqa: E402

# The universe the experiment actually started with on 2026-07-17. If a change to
# the book is ever intended, it is a NEW pre-registered experiment — update this
# literal deliberately and record the prereg, never as a side effect.
STARTED_WITH_EQUITIES = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA", "AMD", "PLTR",
    "TSM", "NFLX", "UBER", "SPY", "QQQ", "IWM", "GLD", "TLT", "XLK", "XLE",
    "XLF", "ARKK", "SMH", "SOXX", "XBI",
]
STARTED_WITH_CRYPTO = [
    "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "ADA/USD",
    "AVAX/USD", "DOGE/USD", "MATIC/USD", "LINK/USD", "ARB/USD", "SUI/USD",
]


def test_book_universe_matches_what_the_experiment_started_with():
    assert BOOK_EQUITIES == STARTED_WITH_EQUITIES
    assert BOOK_CRYPTO == STARTED_WITH_CRYPTO


def test_growing_the_research_universe_cannot_change_the_book():
    # The scan list in config.yaml is free to grow for research/Deep Analyse.
    # The book must not follow it.
    from apex_quant.config import get_config
    scan = set(get_config().data.equities)
    extra = scan - set(BOOK_EQUITIES)
    # Whatever has been added for research, the traded book is unchanged.
    assert set(BOOK_EQUITIES) == set(STARTED_WITH_EQUITIES)
    assert extra.isdisjoint(set(BOOK_EQUITIES) - set(STARTED_WITH_EQUITIES))


def test_matic_stays_excluded():
    # A future data fix must not silently add MATIC mid-experiment.
    assert "MATIC/USD" in EXCLUDED
    assert "MATIC/USD" in BOOK_CRYPTO      # present in the list, dropped by EXCLUDED
