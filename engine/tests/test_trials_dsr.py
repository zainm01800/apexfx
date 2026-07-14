"""Honest multiple-testing correction: DSR ``n_trials`` override + TrialLedger."""

from __future__ import annotations

import numpy as np

from apex_quant.validation import TrialLedger, deflated_sharpe_ratio


def _returns(seed=0, mu=0.001, sd=0.01, n=500):
    return np.random.default_rng(seed).normal(mu, sd, n)


# -- deflated_sharpe_ratio n_trials override -----------------------------------
def test_default_uses_observed_count():
    d = deflated_sharpe_ratio(_returns(), [0.05, 0.08, 0.02], 252)
    assert d["n_trials"] == 3
    assert d["n_trials_observed"] == 3


def test_more_trials_raises_benchmark_and_lowers_dsr():
    returns = _returns(mu=0.0012)
    trials = [0.05, 0.08, 0.02, 0.10]  # dispersion > 0 so the benchmark is nonzero
    base = deflated_sharpe_ratio(returns, trials, 252)
    honest = deflated_sharpe_ratio(returns, trials, 252, n_trials=100)
    assert base["n_trials"] == 4
    assert honest["n_trials"] == 100
    assert honest["sr0"] > base["sr0"]        # more trials => higher bar to clear
    assert honest["dsr"] <= base["dsr"]        # => a less self-flattering DSR


def test_n_trials_cannot_fall_below_observed():
    d = deflated_sharpe_ratio(_returns(), [0.05, 0.08, 0.02, 0.10], 252, n_trials=2)
    assert d["n_trials"] == 4          # floored at the number observed
    assert d["n_trials_observed"] == 4


def test_insufficient_data_still_reports_effective_trials():
    d = deflated_sharpe_ratio([0.0, 0.0, 0.0], [0.1, 0.2], 252, n_trials=50)
    assert d["n_trials"] == 50
    assert "note" in d


# -- TrialLedger ---------------------------------------------------------------
def test_ledger_dedupes_and_counts():
    led = TrialLedger()
    led.record({"lookback": 63, "vol": 63}, 0.9)
    led.record({"lookback": 63, "vol": 63}, 0.9)          # exact dup
    led.record({"vol": 63, "lookback": 63}, 0.9)          # key-order dup
    led.record({"lookback": 21, "vol": 21}, 1.2)
    assert led.n_trials == 2
    assert sorted(led.sharpes) == [0.9, 1.2]


def test_ledger_record_without_sharpe_keeps_existing():
    led = TrialLedger()
    led.record({"a": 1}, 0.7)
    led.record({"a": 1})                                  # no sharpe -> must not wipe 0.7
    assert led.sharpes == [0.7]
    assert led.n_trials == 1


def test_ledger_record_many():
    led = TrialLedger()
    led.record_many([{"h": 10}, {"h": 15}, {"h": 20}], [0.5, 0.6, 0.7])
    assert led.n_trials == 3
    assert sorted(led.sharpes) == [0.5, 0.6, 0.7]


def test_ledger_persists(tmp_path):
    led = TrialLedger()
    led.record_many([{"h": 10}, {"h": 15}], [0.5, 0.6])
    p = led.save(tmp_path / "sub" / "ledger.json")
    assert p.exists()
    reloaded = TrialLedger.load(p)
    assert reloaded.n_trials == 2
    assert sorted(reloaded.sharpes) == [0.5, 0.6]
    assert TrialLedger.load(tmp_path / "missing.json").n_trials == 0


def test_ledger_feeds_dsr_honest_count():
    # A ledger accumulated across a sweep drives the deflation denominator.
    led = TrialLedger()
    led.record_many([{"lb": lb} for lb in range(40)], [0.03 * i for i in range(40)])
    returns = _returns(mu=0.0012)
    small = deflated_sharpe_ratio(returns, [0.05, 0.08, 0.02], 252)
    honest = deflated_sharpe_ratio(returns, [0.05, 0.08, 0.02], 252, n_trials=led.n_trials)
    assert honest["n_trials"] == 40
    assert honest["dsr"] <= small["dsr"]
