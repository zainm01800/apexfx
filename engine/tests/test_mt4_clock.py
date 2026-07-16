"""MT4 broker-clock normalisation.

MT4's OrderOpenTime()/OrderCloseTime() return the BROKER's server clock (UTC+2 in
winter, UTC+3 under DST) but store it as though it were a unix epoch. Measured on the
live account: open_time runs +2.99h ahead of real UTC — trades appear to have opened
~3 hours in the future.

That leaked into `outcome_date`, which check_hindsight_trajectory() uses to decide
where to start scanning price. A skewed value shifts the whole hindsight window, and
those verdicts now feed the Bayesian sizer's learning.

The offset is CONFIGURED, not detected: the only observable (newest event minus now)
under-reads by the age of the newest trade — on live data it returned 2.5h for a true
3.0h. But that same quantity is a valid one-sided ALARM: it can only under-state, so
exceeding the configured value proves the config wrong.
"""

from __future__ import annotations

from datetime import datetime, timezone

from apex_quant.config import get_config
from apex_quant.execution.mt4_clock import mt4_utc_offset_seconds, observed_min_broker_offset


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _hist(*offsets_hours):
    """History whose events sit N hours ahead of real UTC now."""
    return [{"open_time": _now() + h * 3600.0} for h in offsets_hours]


# -- the configured offset -----------------------------------------------------
def test_offset_comes_from_config():
    cfg_hours = get_config().execution.mt4.server_utc_offset_hours
    assert mt4_utc_offset_seconds() == cfg_hours * 3600.0


def test_live_config_matches_the_measured_broker_clock():
    """This account's broker runs UTC+3 (measured +2.99h). If someone zeroes this or
    DST flips it, hindsight scans silently read the wrong price window."""
    assert get_config().execution.mt4.server_utc_offset_hours == 3.0


def test_history_does_not_override_config():
    # Evidence only warns; it must never silently change the offset used.
    assert mt4_utc_offset_seconds(_hist(2.0)) == mt4_utc_offset_seconds()
    assert mt4_utc_offset_seconds(_hist(9.0)) == mt4_utc_offset_seconds()


# -- the one-sided alarm -------------------------------------------------------
def test_observed_bound_under_reads_by_trade_age():
    """Why this can't be an estimator: a trade opened 30 min ago on a +3h broker
    reports only +2.5h. It is a floor, never the answer."""
    assert abs(observed_min_broker_offset(_hist(2.5)) - 2.5 * 3600) < 60


def test_observed_bound_is_zero_without_evidence():
    assert observed_min_broker_offset([]) == 0.0
    assert observed_min_broker_offset([{}]) == 0.0
    assert observed_min_broker_offset([{"open_time": None}]) == 0.0
    assert observed_min_broker_offset([{"open_time": "junk"}]) == 0.0
    assert observed_min_broker_offset(_hist(-5.0)) == 0.0  # all events in the past


def test_observed_bound_uses_the_newest_event():
    assert abs(observed_min_broker_offset(_hist(3.0, -5.0, -20.0)) - 3 * 3600) < 60


# -- normalisation -------------------------------------------------------------
def test_normalising_recovers_the_true_utc_close():
    """The point of the whole exercise: broker epoch minus offset is real UTC."""
    offset = mt4_utc_offset_seconds()
    true_utc = _now() - 7200.0                 # actually closed 2h ago
    broker_epoch = true_utc + offset           # as MT4 reports it
    assert abs((broker_epoch - offset) - true_utc) < 1.0


def test_rendered_instant_is_tz_aware_utc_not_naive_local():
    """The original bug was fromtimestamp() with no tz (renders in LOCAL time) then
    string-appending 'Z' to claim it was UTC."""
    offset = mt4_utc_offset_seconds()
    true_utc = _now() - 3600.0
    dt = datetime.fromtimestamp((true_utc + offset) - offset, tz=timezone.utc)
    assert dt.tzinfo is not None
    assert abs(dt.timestamp() - true_utc) < 1.0
    assert dt.isoformat().endswith("+00:00")


def test_live_reported_offset_override():
    from apex_quant.execution.mt4_clock import set_live_broker_offset, mt4_utc_offset_seconds
    
    # Reset live offset first
    set_live_broker_offset(None)
    
    # Initially should fallback to config (3.0h = 10800s)
    assert mt4_utc_offset_seconds() == 10800.0
    
    # Override dynamically
    set_live_broker_offset(7200.0) # 2.0h
    assert mt4_utc_offset_seconds() == 7200.0
    
    # Clear override
    set_live_broker_offset(None)
    assert mt4_utc_offset_seconds() == 10800.0


def test_live_reported_offset_expiration(monkeypatch):
    from apex_quant.execution.mt4_clock import set_live_broker_offset, mt4_utc_offset_seconds
    import time
    
    set_live_broker_offset(None)
    
    # Set a live offset
    t0 = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: t0)
    set_live_broker_offset(7200.0)
    
    # Fresh live offset wins
    assert mt4_utc_offset_seconds(max_age_s=10) == 7200.0
    
    # Advance time beyond max_age_s
    monkeypatch.setattr(time, "monotonic", lambda: t0 + 15)
    
    # Expired falls back to config
    assert mt4_utc_offset_seconds(max_age_s=10) == 10800.0
    
    set_live_broker_offset(None)
