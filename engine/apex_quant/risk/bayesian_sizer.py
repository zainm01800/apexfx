"""Beta-Binomial Bayesian position sizer.

Replaces the static ``max_risk_per_trade`` ceiling with a conjugate
Beta-Binomial win-rate estimator that updates dynamically as live trades
accumulate. The Bayesian approach is mathematically sound and adapts to
demonstrated performance rather than assuming a fixed edge.

Model
-----
For each instrument *i*, the win-rate θᵢ has a Beta prior:

    θᵢ ~ Beta(α₀, β₀)          (default: α₀=β₀=2 — weakly informative)

Each trade outcome (win=1, loss=0) updates the posterior via conjugacy:

    α_i += decay * win_i
    β_i += decay * (1 - win_i)

where ``decay`` (λ ∈ (0,1]) applies exponential down-weighting so that
older trades count less than recent trades. The posterior mean win-rate is:

    p̂_i = α_i / (α_i + β_i)

Using the posterior *mean* alone throws away the thing that makes a Bayesian
model worth having: the **uncertainty**. Two instruments can share a 60% mean
win-rate while one has 8 trades of evidence and the other 400 — you should not
bet them the same. This sizer therefore supports three win-rate estimators
(``mode``):

  * ``"mean"``       — posterior mean p̂ (the original, over-confident behaviour).
  * ``"lcb"``        — a lower confidence bound, p̂ − k·σ (default). Bets the
                       *pessimistic* edge, so size grows only as the posterior
                       tightens with evidence. This is the recommended default.
  * ``"thompson"``   — a posterior sample θ ~ Beta(α,β) (Thompson sampling),
                       drawn from a *seeded* generator so runs stay reproducible.

The chosen win-rate estimate feeds fractional Kelly:

    f = frac_kelly * (p̂ - (1-p̂)/b)      capped at max_rf; <= 0 vetoes the trade

A portfolio-level max-drawdown circuit breaker (default 15%) hard-vetoes
all new positions until equity recovers, regardless of the Bayesian edge.

Integration with RiskManager
-----------------------------
``BayesianRiskSizer`` is passed as an optional argument to ``RiskManager``.
When present it overrides the static Kelly ``risk_fraction`` computed in
step 3 of the permit() pipeline. When absent (default), behaviour is
identical to the existing engine — fully backward-compatible.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from apex_quant.risk.types import AccountState, Signal


# ---------------------------------------------------------------------------
#  Beta-Binomial Win-Rate Tracker
# ---------------------------------------------------------------------------
@dataclass
class BetaBinomialWinRate:
    """Conjugate Beta-Binomial win-rate estimator with exponential decay.

    Parameters
    ----------
    alpha0 :
        Prior pseudo-wins. ``alpha0=beta0=2`` gives a weak prior centred at 0.5
        with low variance — it yields a fair estimate even with <10 trades.
    beta0 :
        Prior pseudo-losses.
    decay :
        Exponential decay factor λ ∈ (0,1]. Each existing observation is
        multiplied by λ before the new observation is added, so recent trades
        receive higher weight. ``decay=1.0`` is uniform (no decay).
    """

    alpha0: float = 2.0
    beta0: float = 2.0
    decay: float = 0.95

    # Running posterior parameters (mutable; updated by record_outcome)
    _alpha: float = field(init=False)
    _beta: float = field(init=False)
    _n_trades: int = field(init=False, default=0)
    
    # Payoff parameters
    _sum_wins: float = field(init=False)
    _count_wins: float = field(init=False)
    _sum_losses: float = field(init=False)
    _count_losses: float = field(init=False)
    _n_pnl_trades: int = field(init=False)

    def __post_init__(self) -> None:
        self._alpha = self.alpha0
        self._beta = self.beta0
        self._n_trades = 0
        self._sum_wins = 0.0
        self._count_wins = 0.0
        self._sum_losses = 0.0
        self._count_losses = 0.0
        self._n_pnl_trades = 0

    def record_outcome(self, win: bool, pnl: float | None = None) -> None:
        """Update the posterior and payoff stats with one trade outcome.

        Existing counts are decayed first (exponential forgetting), then the
        new observation is added with weight 1.0.
        """
        self._alpha *= self.decay
        self._beta *= self.decay
        if win:
            self._alpha += 1.0
        else:
            self._beta += 1.0
        self._n_trades += 1
        
        if pnl is not None:
            val = float(pnl)
            self._sum_wins *= self.decay
            self._count_wins *= self.decay
            self._sum_losses *= self.decay
            self._count_losses *= self.decay
            
            if val > 0:
                self._sum_wins += val
                self._count_wins += 1.0
            else:
                self._sum_losses += abs(val)
                self._count_losses += 1.0
            self._n_pnl_trades += 1

    @property
    def posterior_mean(self) -> float:
        """Expected win-rate: E[θ] = α / (α + β)."""
        return self._alpha / (self._alpha + self._beta)

    @property
    def posterior_std(self) -> float:
        """Posterior standard deviation of θ."""
        a, b = self._alpha, self._beta
        return math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))

    @property
    def n_trades(self) -> int:
        """Total number of trade outcomes recorded."""
        return self._n_trades

    @property
    def avg_win(self) -> float | None:
        if self._count_wins > 0:
            return self._sum_wins / self._count_wins
        return None

    @property
    def avg_loss(self) -> float | None:
        if self._count_losses > 0:
            return self._sum_losses / self._count_losses
        return None

    @property
    def n_pnl_trades(self) -> int:
        return self._n_pnl_trades

    @property
    def realized_payoff(self) -> float | None:
        aw = self.avg_win
        al = self.avg_loss
        if aw is not None and al is not None and al > 0:
            return aw / al
        return None

    def lower_confidence_bound(self, k: float) -> float:
        """Conservative win-rate estimate: posterior mean − ``k`` std devs.

        Clipped to [0, 1]. As evidence accumulates the posterior tightens
        (σ → 0) so the LCB rises toward the mean — the sizer earns its way up
        to full size rather than assuming an edge it has not yet demonstrated.
        """
        return float(min(1.0, max(0.0, self.posterior_mean - k * self.posterior_std)))

    def sample(self, rng: np.random.Generator) -> float:
        """Draw one posterior sample θ ~ Beta(α, β) for Thompson sampling."""
        return float(rng.beta(self._alpha, self._beta))

    def describe(self) -> dict:
        return {
            "alpha": round(self._alpha, 3),
            "beta": round(self._beta, 3),
            "posterior_mean": round(self.posterior_mean, 4),
            "posterior_std": round(self.posterior_std, 4),
            "n_trades": self._n_trades,
            "avg_win": round(self.avg_win, 4) if self.avg_win is not None else None,
            "avg_loss": round(self.avg_loss, 4) if self.avg_loss is not None else None,
            "realized_payoff": round(self.realized_payoff, 4) if self.realized_payoff is not None else None,
            "n_pnl_trades": self._n_pnl_trades,
        }


# ---------------------------------------------------------------------------
#  Bayesian Risk Sizer
# ---------------------------------------------------------------------------
class BayesianRiskSizer:
    """Adaptive position sizer using per-instrument Beta-Binomial posteriors.

    Parameters
    ----------
    frac_kelly :
        Kelly fraction (default 0.25 = quarter-Kelly, conservative under
        parameter uncertainty).
    min_risk :
        Minimum allowed risk fraction per trade (floor), returned ONLY during
        the pre-adaptation cold start (fewer than ``min_trades_for_adaptation``
        recorded trades). Guards against zero-sizing when the prior is
        uninformed. It is NOT a floor after adaptation — a non-positive Kelly
        from an informed posterior vetoes the trade (returns ``None``).
    max_risk :
        Maximum allowed risk fraction per trade (ceiling). Hard cap.
    max_drawdown :
        Portfolio-level maximum drawdown circuit breaker. When
        ``account.drawdown >= max_drawdown``, ALL new positions are vetoed
        regardless of edge. Default 15%.
    mode :
        Win-rate estimator used for Kelly. ``"lcb"`` (default) is the
        uncertainty-aware lower confidence bound; ``"mean"`` reproduces the
        original posterior-mean behaviour; ``"thompson"`` samples the posterior.
    uncertainty_penalty :
        ``k`` in the ``"lcb"`` estimate (mean − k·σ). Larger ⇒ more conservative
        while evidence is thin. Ignored for other modes.
    seed :
        Seed for the Thompson-sampling generator, so ``"thompson"`` mode stays
        reproducible (the rest of the engine is deterministic and this must be
        too). Ignored for other modes.
    alpha0, beta0 :
        Prior parameters for each new instrument's Beta distribution.
    decay :
        Exponential decay per trade for existing observations.
    min_trades_for_adaptation :
        Until this many trades have been recorded for an instrument, the sizer
        returns ``min_risk`` rather than an under-informed Bayesian estimate.
        Default 20 trades.
    """

    def __init__(
        self,
        frac_kelly: float = 0.25,
        min_risk: float = 0.005,
        max_risk: float = 0.02,
        max_drawdown: float = 0.15,
        mode: Literal["mean", "lcb", "thompson"] = "lcb",
        uncertainty_penalty: float = 1.0,
        seed: int = 42,
        alpha0: float = 2.0,
        beta0: float = 2.0,
        decay: float = 0.95,
        min_trades_for_adaptation: int = 20,
    ) -> None:
        if mode not in ("mean", "lcb", "thompson"):
            raise ValueError(f"unknown mode {mode!r}; expected mean|lcb|thompson")
        self.frac_kelly = frac_kelly
        self.min_risk = min_risk
        self.max_risk = max_risk
        self.max_drawdown = max_drawdown
        self.mode = mode
        self.uncertainty_penalty = uncertainty_penalty
        self.min_trades_for_adaptation = min_trades_for_adaptation
        self._rng = np.random.default_rng(seed)

        # Per-instrument posterior trackers
        self._trackers: dict[str, BetaBinomialWinRate] = defaultdict(
            lambda: BetaBinomialWinRate(alpha0=alpha0, beta0=beta0, decay=decay)
        )

    # -- Public API -----------------------------------------------------------

    def record_outcome(self, instrument: str, win: bool, pnl: float | None = None) -> None:
        """Record a resolved trade outcome for ``instrument``."""
        self._trackers[instrument].record_outcome(win, pnl=pnl)

    def win_rate_estimate(self, instrument: str) -> float:
        """The point win-rate estimate this sizer would use for ``instrument``,
        per the configured ``mode``. Exposed for logging / dashboards."""
        return self._estimate(self._trackers[instrument])

    def _estimate(self, tracker: BetaBinomialWinRate) -> float:
        if self.mode == "mean":
            return tracker.posterior_mean
        if self.mode == "thompson":
            return tracker.sample(self._rng)
        return tracker.lower_confidence_bound(self.uncertainty_penalty)  # "lcb"

    def risk_fraction(
        self,
        signal: Signal,
        account: AccountState,
    ) -> float | None:
        """Return the Bayesian risk fraction for this signal.

        Returns
        -------
        float | None
            The risk fraction in (0, max_risk], or ``None`` when the trade must
            be vetoed: either the drawdown circuit breaker is tripped, or the
            POST-ADAPTATION Kelly is non-positive — a demonstrated losing record
            has no edge to bet (audit A-H2). The ``min_risk`` floor applies ONLY
            to the pre-adaptation cold start; it must never keep a proven loser
            in the game.
        """
        # Hard drawdown circuit breaker
        if account.drawdown >= self.max_drawdown:
            return None

        tracker = self._trackers[signal.instrument]

        # Before enough trades, use min_risk (avoid over-confident prior)
        if tracker.n_trades < self.min_trades_for_adaptation:
            return self.min_risk

        p = self._estimate(tracker)          # uncertainty-aware win-rate estimate
        
        # Payoff ratio: use realized payoff if we have enough Adaptation trades, otherwise fallback
        b = signal.reward_risk
        if tracker.n_pnl_trades >= self.min_trades_for_adaptation:
            realized_b = tracker.realized_payoff
            if realized_b is not None:
                b = float(np.clip(realized_b, 0.3, 3.0))

        # Fractional Kelly: f = frac * (p - (1-p)/b)
        raw_kelly = self.frac_kelly * (p - (1.0 - p) / b)

        # Post-adaptation a non-positive Kelly VETOES the trade (None -> the
        # RiskManager's no-edge veto), mirroring the static fractional-Kelly
        # gate. Flooring it to min_risk here kept demonstrated losers trading.
        if raw_kelly <= 0:
            return None

        # Positive edge: cap only at max_risk (no min_risk floor once informed).
        return float(min(self.max_risk, raw_kelly))

    def describe(self, instrument: str | None = None) -> dict:
        """Return a summary dict for logging / dashboard display."""
        if instrument:
            t = self._trackers.get(instrument)
            if not t:
                return {"n_trades": 0, "mode": self.mode}
            d = t.describe()
            d["mode"] = self.mode
            d["win_rate_estimate"] = round(self._estimate(t), 4)
            
            # Determine currently active payoff ratio
            b = None
            b_source = "none"
            if t.n_pnl_trades >= self.min_trades_for_adaptation:
                rp = t.realized_payoff
                if rp is not None:
                    b = round(float(np.clip(rp, 0.3, 3.0)), 4)
                    b_source = "realized"
            d["payoff_ratio_in_use"] = b
            d["payoff_source"] = b_source
            return d
        return {instr: t.describe() for instr, t in self._trackers.items()}
