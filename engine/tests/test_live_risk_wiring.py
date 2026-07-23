"""The live path's risk sizing must track config.yaml, not restate it.

On 2026-07-23 `config.yaml` moved `max_risk_per_trade` 1.00% -> 0.75% and three sites in
`run_live_paper_trading.py` did not follow, because each had the old value hardcoded:
the Bayesian sizer ceiling (0.02), the open-trade risk estimate (0.01 * equity), and the
virtual-equity reconstruction (risk_pct=0.01). A config change that does not reach the code
that spends the money is not a config change.

These tests read the SOURCE rather than importing the module: importing
`run_live_paper_trading` mutates global config as a side effect (see memory
`bayesian-learning-survivorship-2026-07`), which would corrupt other tests in the session.
"""
from __future__ import annotations

import re
from pathlib import Path

from apex_quant.config import load_config

ENGINE_DIR = Path(__file__).resolve().parent.parent
LIVE = ENGINE_DIR / "scripts" / "run_live_paper_trading.py"


def _src() -> str:
    return LIVE.read_text(encoding="utf-8")


def test_bayesian_sizer_ceiling_is_config_driven():
    src = _src()
    start = src.index("_BAYESIAN_SIZER = BayesianRiskSizer(")
    # Close on the constructor's OWN closing paren (start of a line), not the first ")"
    # encountered — that one belongs to the nested min(...)/get_config() calls.
    block = src[start: src.index("\n)", start)]
    assert "get_config().risk.max_risk_per_trade" in block, (
        "the live Bayesian sizer must take its ceiling from config.yaml, not a literal"
    )
    assert not re.search(r"max_risk\s*=\s*0\.\d", block), (
        "hardcoded max_risk found in the live sizer — it will not follow a config change"
    )


def test_open_trade_risk_estimate_is_config_driven():
    src = _src()
    assert "risk_cap = cfg.risk.max_risk_per_trade * live_equity" in src, (
        "the open-trade risk estimate must use the configured risk-per-trade"
    )
    assert "risk_cap = 0.01 * live_equity" not in src


def test_virtual_equity_defaults_to_configured_risk():
    src = _src()
    assert "def calculate_virtual_equity(trades, initial_equity=300000.0, risk_pct=None)" in src
    assert "risk_pct = get_config().risk.max_risk_per_trade" in src


def test_prop_mode_reads_the_prop_profile_and_does_NOT_track_config_yaml():
    """Prop mode is the firm's contract, not this book's optimum. It must keep 1% even
    though config.yaml is now 0.75% — but it must read that from config.prop.yaml rather
    than restating it, so the profile and the running sizer cannot drift apart."""
    src = _src()
    assert "_prop = load_config(ENGINE_DIR / \"config.prop.yaml\")" in src
    assert "_BAYESIAN_SIZER.max_risk = _prop.risk.max_risk_per_trade" in src

    prop = load_config(ENGINE_DIR / "config.prop.yaml")
    base = load_config(ENGINE_DIR / "config.yaml")
    assert prop.risk.max_risk_per_trade == 0.01, "prop firm rule is 1% per trade"
    assert base.risk.max_risk_per_trade != prop.risk.max_risk_per_trade, (
        "if these ever coincide, re-check that prop mode is still independent by design"
    )


def test_configured_risk_is_the_measured_optimum_for_this_book():
    """0.75% is not arbitrary: it is the maximum inside the owner's 12% drawdown wall
    (scratch/final_cells_12pct_wall.py). Raising it HURTS — 1.5% collides with the 6.5%
    portfolio cap and collapses return. Pinned so it cannot drift upward unnoticed."""
    base = load_config(ENGINE_DIR / "config.yaml")
    assert base.risk.max_risk_per_trade == 0.0075
    assert base.risk.max_portfolio_risk == 0.065
    assert base.risk.max_concurrent_trades == 12
