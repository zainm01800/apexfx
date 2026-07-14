"""Validation statistics: Sharpe, Deflated Sharpe Ratio, Probability of
Backtest Overfitting.

These exist to make backtest results *trustworthy* by correcting for the two
ways we fool ourselves:
  * DSR (Bailey & Lopez de Prado 2014) deflates an observed Sharpe for the number
    of trials we ran, non-normal returns, and sample length. A great Sharpe found
    after trying many configs is mostly luck; DSR quantifies how much.
  * PBO (Bailey et al. 2017, via CSCV) estimates the probability that the config
    we'd pick in-sample underperforms out-of-sample - i.e. that our selection is
    overfit.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
from scipy.stats import kurtosis, norm, rankdata, skew

EULER = 0.5772156649015329


def sharpe_ratio(returns, periods_per_year: int = 1) -> float:
    """Sharpe of a returns array. periods_per_year=1 => per-period (for DSR/PBO);
    252 => annualised."""
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    if len(r) < 2 or sd == 0:
        return 0.0
    return float(r.mean() / sd * np.sqrt(periods_per_year))


def expected_max_sharpe(sr_std: float, n_trials: int) -> float:
    """Expected maximum of ``n_trials`` independent Sharpe estimates with cross-
    trial dispersion ``sr_std`` (per-period). The benchmark a real edge must beat."""
    if n_trials < 2 or sr_std <= 0:
        return 0.0
    z1 = norm.ppf(1.0 - 1.0 / n_trials)
    z2 = norm.ppf(1.0 - 1.0 / (n_trials * np.e))
    return float(sr_std * ((1.0 - EULER) * z1 + EULER * z2))


def deflated_sharpe_ratio(returns, trial_sharpes, periods_per_year: int = 252,
                          n_trials: int | None = None) -> dict:
    """Deflated Sharpe Ratio. ``trial_sharpes`` are the per-period Sharpes of every
    configuration tried (the multiple-testing set). DSR is a probability in [0,1]:
    P(true Sharpe > deflated benchmark). > 0.95 is the usual significance bar.

    ``n_trials`` optionally overrides the multiple-testing count that sets the
    deflation benchmark. Pass the TRUE number of configurations evaluated during
    research (track it with :class:`apex_quant.validation.trials.TrialLedger`) —
    usually far more than the handful of ``trial_sharpes`` whose return series you
    kept. More honest trials => higher benchmark => a lower, less self-flattering
    DSR. The dispersion of trial Sharpes is still estimated from ``trial_sharpes``;
    ``n_trials`` only raises the count and can never fall below the number observed.
    """
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]
    T = len(r)
    sd = r.std(ddof=1) if T > 1 else 0.0
    observed_trials = max(1, len(trial_sharpes))
    effective_trials = observed_trials if n_trials is None else max(int(n_trials), observed_trials)
    if T < 10 or sd == 0:
        return {"dsr": 0.0, "observed_sharpe": 0.0, "observed_sharpe_ann": 0.0,
                "sr0": 0.0, "n_trials": effective_trials, "n_trials_observed": observed_trials,
                "n_obs": T, "note": "insufficient data / zero variance"}

    sr = float(r.mean() / sd)                      # per-period
    g3 = float(skew(r))
    g4 = float(kurtosis(r, fisher=False))          # Pearson (normal == 3)
    sr_std = float(np.std(trial_sharpes, ddof=1)) if observed_trials > 1 else 0.0
    sr0 = expected_max_sharpe(sr_std, effective_trials)

    denom = np.sqrt(max(1e-12, 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr * sr))
    dsr = float(norm.cdf((sr - sr0) * np.sqrt(max(1, T - 1)) / denom))
    return {
        "dsr": dsr,
        "observed_sharpe": sr,
        "observed_sharpe_ann": sr * np.sqrt(periods_per_year),
        "sr0": sr0,
        "n_trials": effective_trials,
        "n_trials_observed": observed_trials,
        "n_obs": T,
        "skew": g3,
        "kurtosis": g4,
    }


def probability_of_backtest_overfitting(
    M, n_splits: int = 10, max_combos: int = 4000, seed: int = 42
) -> dict:
    """CSCV PBO. ``M`` is a (T observations x C configs) matrix of per-period
    returns. Returns PBO in [0,1]; lower is better (less overfit selection)."""
    M = np.asarray(M, dtype="float64")
    if M.ndim != 2 or M.shape[1] < 2:
        return {"pbo": None, "note": "need >= 2 configurations"}
    T, C = M.shape
    S = n_splits - (n_splits % 2)
    S = max(2, min(S, T))
    groups = np.array_split(np.arange(T), S)

    combos = list(combinations(range(S), S // 2))
    rng = np.random.default_rng(seed)
    if len(combos) > max_combos:
        sel = rng.choice(len(combos), max_combos, replace=False)
        combos = [combos[i] for i in sel]

    logits = []
    for is_groups in combos:
        is_rows = np.concatenate([groups[g] for g in is_groups])
        oos_rows = np.concatenate([groups[g] for g in range(S) if g not in is_groups])
        is_perf = np.array([sharpe_ratio(M[is_rows, c]) for c in range(C)])
        oos_perf = np.array([sharpe_ratio(M[oos_rows, c]) for c in range(C)])
        c_star = int(np.argmax(is_perf))
        ranks = rankdata(oos_perf)             # 1=worst .. C=best
        w = ranks[c_star] / (C + 1)
        w = min(max(w, 1e-6), 1 - 1e-6)
        logits.append(np.log(w / (1 - w)))

    logits = np.array(logits)
    pbo = float(np.mean(logits <= 0.0))        # IS-best at/below OOS median
    return {"pbo": pbo, "n_combos": len(combos), "n_splits": S, "n_configs": C}
