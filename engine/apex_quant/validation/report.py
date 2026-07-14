"""Validation report: assemble CPCV + DSR + PBO into a pass/fail verdict.

A strategy PASSES only if all three agree it is not a fluke:
  * DSR  > 0.95   (Sharpe survives the multiple-testing deflation)
  * PBO  < 0.5    (in-sample selection is not systematically overfit)
  * CPCV median OOS Sharpe > 0 and the majority of paths are positive

Most candidate strategies should FAIL here. That is the system working, not a bug.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel

from apex_quant.backtest.engine import Backtester
from apex_quant.config import AppConfig, get_config
from apex_quant.data.point_in_time import PointInTimeAccessor
from apex_quant.strategies.baseline import RegimeGatedMomentum
from apex_quant.validation.cpcv import run_cpcv
from apex_quant.validation.metrics import (
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)

DSR_THRESHOLD = 0.95
PBO_THRESHOLD = 0.50


class ValidationReport(BaseModel):
    strategy: str
    instrument: str
    config_version: int
    generated_for: str
    n_trials: int
    cpcv: dict
    dsr: dict
    pbo: dict
    verdict: dict

    def summary(self) -> str:
        v = self.verdict
        flag = "PASS" if v["passed"] else "FAIL"
        return (
            f"[{flag}] {self.strategy} on {self.instrument}: "
            f"DSR={self.dsr.get('dsr', 0):.2f} PBO={self.pbo.get('pbo')} "
            f"CPCV medOOS={self.cpcv.get('oos_sharpe_median', 0):.2f} "
            f"({self.cpcv.get('frac_positive', 0)*100:.0f}% paths +ve, "
            f"{self.cpcv.get('n_paths', 0)} paths)"
        )


def default_factory(**params) -> RegimeGatedMomentum:
    return RegimeGatedMomentum(**params)


def default_param_grid() -> list[dict]:
    # baseline first; the grid is the multiple-testing set used by DSR & PBO
    return [
        {"momentum_lookback": 63, "vol_window": 63},
        {"momentum_lookback": 21, "vol_window": 21},
        {"momentum_lookback": 126, "vol_window": 126},
    ]


def ml_factory(**params):
    from apex_quant.strategies.ml_strategy import MLStrategy
    return MLStrategy(**params)


def ml_param_grid() -> list[dict]:
    # GBM is the headline config; the linear and shorter-horizon variants are the
    # multiple-testing set. Hyperparameters are only ever "chosen" inside folds.
    return [
        {"model": "gbm", "holding_horizon": 10},
        {"model": "gbm", "holding_horizon": 15},
        {"model": "linear", "holding_horizon": 10},
    ]


def meta_factory(**params):
    """Regime-gated momentum PRIMARY wrapped in a meta-label gate SECONDARY."""
    from apex_quant.strategies.meta_labeling import MetaLabeledStrategy
    base = RegimeGatedMomentum(
        momentum_lookback=params.get("momentum_lookback", 63),
        vol_window=params.get("vol_window", 63),
        holding_horizon=params.get("holding_horizon", 10),
        reward_risk=params.get("reward_risk", 1.5),
    )
    return MetaLabeledStrategy(
        base, model=params.get("model", "gbm"),
        threshold=params.get("threshold", 0.5),
        holding_horizon=params.get("holding_horizon", 10),
    )


def meta_param_grid() -> list[dict]:
    # The gate threshold is the meta-label knob; sweeping it is part of the
    # multiple-testing set, so it is deflated by DSR/PBO like any other choice.
    return [
        {"model": "gbm", "threshold": 0.50, "holding_horizon": 10},
        {"model": "gbm", "threshold": 0.55, "holding_horizon": 10},
        {"model": "linear", "threshold": 0.50, "holding_horizon": 10},
    ]


# name -> (factory, grid). Used by scripts/run_validation.py and the API.
STRATEGY_SPECS = {
    "regime_gated_momentum": (default_factory, default_param_grid),
    "ml_gbm": (ml_factory, ml_param_grid),
    "meta_labeled": (meta_factory, meta_param_grid),
}


def run_validation(
    pit: PointInTimeAccessor,
    instrument: str,
    *,
    strategy_factory=default_factory,
    param_grid: list[dict] | None = None,
    cfg: AppConfig | None = None,
    generated_for: str = "",
    n_trials: int | None = None,
) -> ValidationReport:
    cfg = cfg or get_config()
    grid = param_grid or default_param_grid()
    baseline_params = grid[0]
    horizon = int(baseline_params.get("holding_horizon", 10))

    # 1. Full backtest per config -> returns columns (for PBO) + per-period Sharpe (for DSR trials)
    bt = Backtester(cfg)
    returns_by_cfg: list[pd.Series] = []
    trial_sharpes: list[float] = []
    for params in grid:
        strat = strategy_factory(**params)
        strat.fit(pit, pit.as_of(pit.end).index)        # fit once on full history for the trial matrix
        res = bt.run(pit, strat, instrument, warmup=250)
        rets = res.returns
        returns_by_cfg.append(rets)
        trial_sharpes.append(sharpe_ratio(rets, periods_per_year=1))

    # Align all configs on common dates -> matrix M (T x C)
    aligned = pd.concat(returns_by_cfg, axis=1).dropna()
    M = aligned.to_numpy()
    baseline_returns = returns_by_cfg[0]

    # 2. DSR on the baseline, deflated by the whole trial set. Annualization is
    # asset-class aware (crypto = 365 days/yr) so the reported Sharpe is correct.
    # Deflate by the TRUE trial count when the caller tracked it (TrialLedger),
    # else by the size of this grid. Honest N => harsher, more trustworthy DSR.
    dsr = deflated_sharpe_ratio(baseline_returns.to_numpy(), trial_sharpes,
                                cfg.mechanics_for(instrument).annualization,
                                n_trials=n_trials)

    # 3. PBO across the config grid
    pbo = (probability_of_backtest_overfitting(M, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
           if M.shape[1] >= 2 and M.shape[0] >= 40 else {"pbo": None, "note": "insufficient matrix"})

    # 4. CPCV OOS distribution for the baseline config
    cpcv = run_cpcv(pit, instrument, strategy_factory, baseline_params, cfg=cfg, horizon=horizon)

    # 5. Verdict
    dsr_pass = dsr.get("dsr", 0.0) > DSR_THRESHOLD
    pbo_val = pbo.get("pbo")
    pbo_pass = pbo_val is not None and pbo_val < PBO_THRESHOLD
    cpcv_pass = cpcv.get("oos_sharpe_median", 0.0) > 0 and cpcv.get("frac_positive", 0.0) > 0.5
    passed = bool(dsr_pass and pbo_pass and cpcv_pass)

    reasons = []
    reasons.append(f"DSR {dsr.get('dsr',0):.3f} {'>' if dsr_pass else '<='} {DSR_THRESHOLD} (multiple-testing deflation)")
    reasons.append(
        f"PBO {pbo_val if pbo_val is not None else 'n/a'} "
        f"{'<' if pbo_pass else '>='} {PBO_THRESHOLD} (overfit probability)"
    )
    reasons.append(
        f"CPCV median OOS Sharpe {cpcv.get('oos_sharpe_median',0):.3f}, "
        f"{cpcv.get('frac_positive',0)*100:.0f}% of {cpcv.get('n_paths',0)} paths positive"
    )
    if not passed:
        reasons.append("VERDICT: rejected - insufficient evidence of a real, non-overfit edge")
    else:
        reasons.append("VERDICT: passed all three gates")

    return ValidationReport(
        strategy=getattr(strategy_factory(**baseline_params), "name", "strategy"),
        instrument=instrument,
        config_version=cfg.version,
        generated_for=generated_for,
        n_trials=len(grid),
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
