"""Portfolio-level validation: CPCV + Deflated Sharpe + PBO over a whole book.

``validation.report.run_validation`` is structurally single-instrument — it takes one
``pit`` and one ``instrument`` — so it cannot judge a strategy whose signal only
exists across a universe (cross-sectional momentum ranks pairs against each other;
there is no such thing as "the EUR/USD version" of it). Such a sleeve was therefore
held to a weaker bar than everything else in the engine: a single walk-forward split
rather than a resampled distribution with multiple-testing correction. This module
closes that gap.

Same three gates, same thresholds, same philosophy as the single-instrument report —
most candidates should FAIL, and that is the system working:

  * DSR  > 0.95   the Sharpe survives deflation by every configuration tried
  * PBO  < 0.5    the in-sample-best config is not systematically worse out-of-sample
  * CPCV median OOS Sharpe > 0 and a majority of paths positive

Note on rule-based sleeves
--------------------------
Cross-sectional momentum has no fitted parameters — ranking is a deterministic
function of the point-in-time cross-section — so CPCV's train split is a no-op and
"training" cannot leak. The overfitting risk lives entirely in *which configuration
you picked* (lookback, quantiles, holding). That is exactly what DSR and PBO measure,
which makes them the load-bearing gates here: PBO answers "is the in-sample-best
config just the lucky peak of my sweep?" directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel

from apex_quant.backtest.portfolio import PortfolioBacktester
from apex_quant.config import AppConfig, get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.validation.cpcv import cpcv_splits
from apex_quant.validation.metrics import (
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)

DSR_THRESHOLD = 0.95
PBO_THRESHOLD = 0.50


class PortfolioValidationReport(BaseModel):
    strategy: str
    universe: list[str]
    config_version: int
    generated_for: str
    n_trials: int
    params: dict
    cpcv: dict
    dsr: dict
    pbo: dict
    verdict: dict

    def summary(self) -> str:
        v = self.verdict
        flag = "PASS" if v["passed"] else "FAIL"
        return (
            f"[{flag}] {self.strategy} on {len(self.universe)} instruments: "
            f"DSR={self.dsr.get('dsr', 0):.3f} PBO={self.pbo.get('pbo')} "
            f"CPCV medOOS={self.cpcv.get('oos_sharpe_median', 0):.3f} "
            f"({self.cpcv.get('frac_positive', 0)*100:.0f}% of "
            f"{self.cpcv.get('n_paths', 0)} paths +ve)"
        )


def _portfolio_returns(
    pits: dict[str, PointInTimeAccessor],
    strategies: dict,
    *,
    cfg: AppConfig,
    timeframes: dict[str, str] | None,
    warmup: int,
    start=None,
    end=None,
    periods_per_year: int = 252,
    exit_mode: str = "managed",
    trade_manager=None,
) -> pd.Series:
    """One portfolio backtest -> its per-bar equity returns."""
    res = PortfolioBacktester(cfg, exit_mode=exit_mode, trade_manager=trade_manager).run(
        pits, strategies, timeframes=timeframes, warmup=warmup,
        start=start, end=end, periods_per_year=periods_per_year,
    )
    return res.returns


def run_portfolio_cpcv(
    panel: dict[str, pd.DataFrame],
    pits: dict[str, PointInTimeAccessor],
    model_factory,
    params: dict,
    *,
    cfg: AppConfig | None = None,
    timeframes: dict[str, str] | None = None,
    warmup: int = 250,
    horizon: int = 21,
    periods_per_year: int = 252,
    exit_mode: str = "managed",
    trade_manager=None,
) -> dict:
    """CPCV over the shared portfolio timeline.

    ``trade_manager`` must be forwarded to every inner backtest: an exit-variant
    experiment (e.g. the uncapped runner) that is applied to the full-window run
    but NOT to CPCV would silently measure the BASELINE exit out-of-sample while
    reporting it as the challenger's — an invalid gate that looks perfectly normal.

    Mirrors ``validation.cpcv.run_cpcv``: for each combination of test groups the
    backtest is run across the span of that block and returns are then filtered to
    the test bars only, yielding a *distribution* of out-of-sample Sharpes instead
    of one fragile path.
    """
    cfg = cfg or get_config()
    c = cfg.validation.cpcv

    # Shared timeline = union of all instruments' bars (what the book actually trades).
    timeline = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in panel.values()])))
    n = len(timeline)
    splits = cpcv_splits(n, c.n_groups, c.n_test_groups, c.embargo_pct, purge=horizon)

    oos: list[float] = []
    for train_idx, test_idx in splits:
        if len(train_idx) < 60 or len(test_idx) < 30:
            continue
        model = model_factory(panel, **params)
        # A fitted sleeve would train on `train_idx` here; rule-based ranking has
        # nothing to fit, so the purged train split is intentionally unused.
        fit = getattr(model, "fit", None)
        if callable(fit):
            fit(pits, timeline[train_idx])

        t0, t1 = timeline[int(test_idx[0])], timeline[int(test_idx[-1])]
        rets = _portfolio_returns(
            pits, model.strategies(), cfg=cfg, timeframes=timeframes,
            warmup=0, start=t0, end=t1, periods_per_year=periods_per_year,
            exit_mode=exit_mode, trade_manager=trade_manager,
        )
        test_dates = timeline[test_idx]
        rets = rets[rets.index.isin(test_dates)]
        oos.append(sharpe_ratio(rets, periods_per_year=1))

    arr = np.array(oos) if oos else np.array([0.0])
    return {
        "n_paths": len(oos),
        "oos_sharpe_mean": float(arr.mean()),
        "oos_sharpe_std": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        "oos_sharpe_median": float(np.median(arr)),
        "frac_positive": float(np.mean(arr > 0)),
        "oos_sharpe_paths": [round(float(x), 4) for x in arr],
    }


def run_portfolio_validation(
    panel: dict[str, pd.DataFrame],
    pits: dict[str, PointInTimeAccessor],
    model_factory,
    param_grid: list[dict],
    *,
    strategy_name: str = "portfolio_strategy",
    cfg: AppConfig | None = None,
    timeframes: dict[str, str] | None = None,
    warmup: int = 250,
    horizon: int = 21,
    periods_per_year: int = 252,
    generated_for: str = "",
    n_trials: int | None = None,
    exit_mode: str = "managed",
    trade_manager=None,
) -> PortfolioValidationReport:
    """Run the full three-gate validation for a portfolio strategy.

    ``model_factory(panel, **params)`` must return an object exposing
    ``.strategies() -> {instrument: Strategy}`` (see
    :class:`~apex_quant.strategies.cross_sectional.CrossSectionalMomentum`).

    ``param_grid[0]`` is the headline config; the rest form the multiple-testing set
    that DSR and PBO deflate by. Pass ``n_trials`` with the TRUE number of
    configurations ever evaluated (see ``validation.trials.TrialLedger``) — deflating
    by only the configs you kept is how a sweep flatters itself.
    """
    cfg = cfg or get_config()
    baseline = param_grid[0]

    # 1. Full-period run per config -> returns columns (PBO) + per-period Sharpe (DSR trials)
    returns_by_cfg: list[pd.Series] = []
    trial_sharpes: list[float] = []
    for params in param_grid:
        model = model_factory(panel, **params)
        rets = _portfolio_returns(
            pits, model.strategies(), cfg=cfg, timeframes=timeframes,
            warmup=warmup, periods_per_year=periods_per_year,
            exit_mode=exit_mode, trade_manager=trade_manager,
        )
        returns_by_cfg.append(rets)
        trial_sharpes.append(sharpe_ratio(rets, periods_per_year=1))

    aligned = pd.concat(returns_by_cfg, axis=1).dropna()
    M = aligned.to_numpy()

    # 2. DSR on the baseline, deflated by every configuration tried.
    dsr = deflated_sharpe_ratio(
        returns_by_cfg[0].to_numpy(), trial_sharpes, periods_per_year, n_trials=n_trials
    )

    # 3. PBO across the config grid — for a rule-based sleeve this is the key gate:
    #    it asks whether the in-sample-best config stays good out-of-sample.
    pbo = (
        probability_of_backtest_overfitting(M, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
        if M.shape[1] >= 2 and M.shape[0] >= 40
        else {"pbo": None, "note": "insufficient matrix"}
    )

    # 4. CPCV OOS distribution for the baseline config.
    cpcv = run_portfolio_cpcv(
        panel, pits, model_factory, baseline, cfg=cfg, timeframes=timeframes,
        warmup=warmup, horizon=horizon, periods_per_year=periods_per_year,
        exit_mode=exit_mode,
    )

    dsr_pass = dsr.get("dsr", 0.0) > DSR_THRESHOLD
    pbo_val = pbo.get("pbo")
    pbo_pass = pbo_val is not None and pbo_val < PBO_THRESHOLD
    cpcv_pass = cpcv.get("oos_sharpe_median", 0.0) > 0 and cpcv.get("frac_positive", 0.0) > 0.5
    passed = bool(dsr_pass and pbo_pass and cpcv_pass)

    reasons = [
        f"DSR {dsr.get('dsr', 0):.3f} {'>' if dsr_pass else '<='} {DSR_THRESHOLD} "
        f"(deflated by {dsr.get('n_trials')} trials)",
        f"PBO {pbo_val if pbo_val is not None else 'n/a'} "
        f"{'<' if pbo_pass else '>='} {PBO_THRESHOLD} (config-selection overfit probability)",
        f"CPCV median OOS Sharpe {cpcv.get('oos_sharpe_median', 0):.3f}, "
        f"{cpcv.get('frac_positive', 0)*100:.0f}% of {cpcv.get('n_paths', 0)} paths positive",
    ]
    reasons.append(
        "VERDICT: passed all three gates" if passed
        else "VERDICT: rejected - insufficient evidence of a real, non-overfit edge"
    )

    return PortfolioValidationReport(
        strategy=strategy_name,
        universe=list(panel.keys()),
        config_version=cfg.version,
        generated_for=generated_for,
        n_trials=dsr.get("n_trials", len(param_grid)),
        params=baseline,
        cpcv=cpcv,
        dsr=dsr,
        pbo=pbo,
        verdict={
            "passed": passed,
            "dsr_pass": dsr_pass,
            "pbo_pass": pbo_pass,
            "cpcv_pass": cpcv_pass,
            "reasons": reasons,
        },
    )
