from types import SimpleNamespace

import pandas as pd

from scripts.run_book_c_deep_audit import _prop_close_diagnostics
from scripts.run_book_c_funded_diagnostics import _phase


def _result(values):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="D", tz="UTC")
    return SimpleNamespace(equity=pd.Series(values, index=idx, dtype=float))


def test_prop_diagnostics_use_fixed_initial_amounts_not_percent_returns():
    report = _prop_close_diagnostics(
        _result([100_000, 104_000, 100_900, 94_000, 89_999]),
        initial_equity=100_000,
    )

    # Day 3 falls 3,100 from the preceding midnight balance, so it breaches
    # the 1-Step daily limit even though the percentage return is under 3%.
    assert report["one_step_3pct_daily"] == {
        "breach_days": 3,
        "first_breach": "2024-01-03",
    }
    assert report["two_step_5pct_daily"]["breach_days"] == 1
    # The prior peak is 104k, making the 1-Step trailing floor 94k.
    assert report["one_step_10pct_eod_trailing"]["first_breach"] == "2024-01-04"
    assert report["two_step_10pct_static"]["first_breach"] == "2024-01-05"


def test_prop_diagnostics_reports_best_day_proxy():
    report = _prop_close_diagnostics(
        _result([100_000, 101_000, 101_500, 101_000, 102_000]),
        initial_equity=100_000,
    )

    assert report["best_day_share_of_positive_daily_equity_changes"] == 0.4
    assert report["best_day_50pct_proxy_pass"] is True


def test_funded_phase_detects_fixed_dollar_daily_loss():
    result = _phase(
        [-0.031], 0, target_pct=0.10, daily_loss_pct=0.03,
        trailing_loss=True, best_day_rule=True,
    )
    assert result["status"] == "fail"
    assert result["reason"] == "daily_loss"


def test_funded_phase_can_pass_target_and_consistency():
    result = _phase(
        [0.02] * 6, 0, target_pct=0.10, daily_loss_pct=0.03,
        trailing_loss=True, best_day_rule=True,
    )
    assert result["status"] == "pass"
    assert result["days"] == 5
    assert result["best_day_share_proxy"] < 0.50


def test_funded_phase_detects_eod_trailing_loss_after_gradual_decline():
    result = _phase(
        [0.09, -0.02, -0.02, -0.02, -0.02, -0.02], 0,
        target_pct=0.10, daily_loss_pct=0.03,
        trailing_loss=True, best_day_rule=True,
    )
    assert result["status"] == "fail"
    assert result["reason"] == "max_loss"
