"""Nightly risk pack: the pure section builders (no I/O, no network)."""

from scripts.nightly_risk_report import (
    FLAG_MULTIPLE,
    MODELED_COST_BPS,
    data_staleness,
    dd_state,
    divergence_review,
    exposures,
    outcome_drift,
)


def test_dd_zones_follow_config_thresholds():
    curve = [["d1", 100.0], ["d2", 95.0]]
    assert dd_state(curve, 100.0, False, 0.10, 0.20)["zone"] == "OK"          # 5% DD
    assert dd_state([["d", 88.0]], 100.0, False, 0.10, 0.20)["zone"] == "REDUCING"
    assert dd_state([["d", 79.0]], 100.0, False, 0.10, 0.20)["zone"] == "BREAKER"
    assert dd_state([["d", 99.0]], 100.0, True, 0.10, 0.20)["zone"] == "HALTED"
    assert dd_state([], None, False, 0.10, 0.20)["zone"] == "UNKNOWN"


def test_exposures_signed_net_and_concentration():
    pos = {
        "AAPL": {"units": 10, "direction": "long"},
        "MSFT": {"units": 5, "direction": "short"},
        "GHOST": {"units": 1, "direction": "long"},   # no cached price
    }
    out = exposures(pos, {"AAPL": 100.0, "MSFT": 200.0}, 2000.0,
                    class_of=lambda i: "equity")
    assert out["gross"] == 2000.0                      # 1000 + 1000
    assert out["net"] == 0.0                           # long 1000, short 1000
    assert out["gross_x_equity"] == 1.0
    assert out["by_class"]["equity"]["n"] == 2
    ghost = [r for r in out["positions"] if r["instrument"] == "GHOST"][0]
    assert ghost["note"] == "no cached price"          # unpriced -> reported, not dropped


def test_divergence_flags_only_above_multiple_of_modeled():
    modeled = MODELED_COST_BPS["equity"]
    just_under = modeled * FLAG_MULTIPLE - 0.01
    rec = {"orders": [
        {"instrument": "AAPL", "asset_class": "equity", "status": "filled",
         "divergence_bps": -just_under},                        # |.| under 3x -> clean
        {"instrument": "MSFT", "asset_class": "equity", "status": "filled",
         "divergence_bps": modeled * FLAG_MULTIPLE + 1},        # over -> flagged
        {"instrument": "EUR/USD", "asset_class": "forex", "status": "filled",
         "divergence_bps": 500.0},                              # forex: no bps model, never flagged
        {"instrument": "TSLA", "asset_class": "equity", "status": "rejected",
         "divergence_bps": 999.0},                              # unfilled ignored
    ]}
    out = divergence_review([rec])
    assert out["flagged"] == ["MSFT"]
    assert out["per_instrument"]["AAPL"]["flagged"] is False
    assert out["per_instrument"]["EUR/USD"]["flagged"] is False
    assert "TSLA" not in out["per_instrument"]


def test_staleness_relative_to_last_processed():
    import datetime as dt
    bars = {"AAPL": dt.date(2026, 7, 17), "OLD": dt.date(2026, 7, 10), "GONE": None}
    out = data_staleness(bars, "2026-07-17")
    assert list(out["stale"]) == ["OLD"]
    assert out["missing"] == ["GONE"]
    assert out["n_checked"] == 3


def test_outcome_drift_honest_below_min_n_then_flags():
    assert outcome_drift(["tp_hit"] * 5)["insufficient_data"] is True
    ok = outcome_drift(["tp_hit"] * 11 + ["sl_hit"] * 9, expected_win_rate=0.558, min_n=20)
    assert ok["insufficient_data"] is False and ok["drift_flag"] is False   # 55% ~ expectation
    bad = outcome_drift(["sl_hit"] * 20, expected_win_rate=0.558, min_n=20)
    assert bad["drift_flag"] is True                                        # 0% wins -> drift
    # invalidated/managed-out closes are excluded from the tp/sl win-rate
    mixed = outcome_drift(["invalidated"] * 30 + ["tp_hit"] * 10, min_n=20)
    assert mixed["insufficient_data"] is True and mixed["n_resolved"] == 10
