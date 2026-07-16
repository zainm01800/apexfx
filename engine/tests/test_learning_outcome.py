"""How the Bayesian sizer learns from history.

Regression guard for a survivorship bias that caused real money loss: the sizer
only ever learned from `outcome=in.(tp_hit,sl_hit)` — the ~25% of trades that ran
to a barrier — so every managed/early exit was invisible. It learned a 49.4% win
rate against a real 36.8%, which pushed Kelly positive, clamped every trade to the
2% max_risk, and produced 10-18 lot positions on a 100k account.

The fix: the post-exit hindsight rescan (timeframe-aware) reports whether a bailed
trade WOULD have hit its target or stop, turning it back into usable evidence.
"""

from __future__ import annotations

from apex_quant.risk.learning import exit_decision_quality, resolve_learning_outcome


def _t(outcome, hindsight=None):
    feats = {"hindsight_outcome": hindsight} if hindsight else {}
    return {"symbol": "EUR/USD", "outcome": outcome, "setup_features": feats}


# -- resolve_learning_outcome --------------------------------------------------
def test_barrier_outcomes_answer_directly():
    assert resolve_learning_outcome(_t("tp_hit")) is True
    assert resolve_learning_outcome(_t("sl_hit")) is False


def test_managed_exit_resolved_by_hindsight():
    """The whole point: a bailed trade is not a loss — it's an unanswered question,
    and the rescan answers it."""
    assert resolve_learning_outcome(_t("invalidated", "tp_hit")) is True
    assert resolve_learning_outcome(_t("invalidated", "sl_hit")) is False
    assert resolve_learning_outcome(_t("expired", "tp_hit")) is True
    assert resolve_learning_outcome(_t("expired", "sl_hit")) is False


def test_unanswered_trades_return_none_not_a_guess():
    """Still drifting => NO information. Recording a guess is how you poison a
    posterior — the old code booked all of these as losses."""
    assert resolve_learning_outcome(_t("invalidated", "drifting")) is None
    assert resolve_learning_outcome(_t("invalidated")) is None          # never scanned
    assert resolve_learning_outcome(_t("expired", "drifting_limit")) is None


def test_barrier_outcome_wins_over_hindsight():
    # If the market already answered, a later scan cannot override it.
    assert resolve_learning_outcome(_t("tp_hit", "sl_hit")) is True
    assert resolve_learning_outcome(_t("sl_hit", "tp_hit")) is False


def test_missing_or_junk_fields_are_safe():
    assert resolve_learning_outcome({}) is None
    assert resolve_learning_outcome({"outcome": None}) is None
    assert resolve_learning_outcome({"outcome": "invalidated", "setup_features": None}) is None


# -- exit_decision_quality -----------------------------------------------------
def test_early_exit_that_dodged_the_stop_is_good():
    assert exit_decision_quality(_t("invalidated", "sl_hit")) == "good"


def test_early_exit_that_cost_a_winner_is_premature():
    assert exit_decision_quality(_t("invalidated", "tp_hit")) == "premature"


def test_no_verdict_when_the_market_hasnt_answered():
    assert exit_decision_quality(_t("invalidated", "drifting")) is None
    assert exit_decision_quality(_t("invalidated")) is None


def test_barrier_exits_have_no_exit_decision_to_judge():
    # We didn't decide anything — the stop/target did.
    assert exit_decision_quality(_t("tp_hit")) is None
    assert exit_decision_quality(_t("sl_hit")) is None


# -- the bias itself -----------------------------------------------------------
def test_managed_exits_are_no_longer_silently_counted_as_losses():
    """The old rule was `win = (outcome == "tp_hit")`, so a managed exit that would
    have reached its target counted as a LOSS. That is the bias, in one assert."""
    winner_bailed_early = _t("invalidated", "tp_hit")
    assert (winner_bailed_early["outcome"] == "tp_hit") is False   # old rule -> loss
    assert resolve_learning_outcome(winner_bailed_early) is True   # new rule -> win
