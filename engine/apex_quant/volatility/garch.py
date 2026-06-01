"""GARCH-family forward volatility via the `arch` library.

Returns are largely unpredictable but their *variance* is not — it clusters and
mean-reverts. GARCH(1,1) exploits exactly that, giving an honest multi-step
forward volatility forecast that drives position sizing and regime detection.

Robustness: arch prefers returns scaled to ~percent; we rescale then unscale.
If the model fails to converge or there's too little history, we fall back to
EWMA and flag ``converged=False`` rather than emit a fabricated number.
"""

from __future__ import annotations

import warnings

import numpy as np

from apex_quant.config import GarchConfig, get_config
from apex_quant.volatility.realized import VolForecast, ewma_vol, log_returns


class GarchEstimator:
    def __init__(self, cfg: GarchConfig | None = None, annualization: int | None = None):
        self.cfg = cfg or get_config().volatility.garch
        self.annualization = annualization or get_config().volatility.annualization_factor

    def forecast(self, data) -> VolForecast:
        c = self.cfg
        r = log_returns(data)

        # Guardrail: GARCH on too little data is noise — fall back to EWMA.
        if len(r) < c.min_obs:
            fb = ewma_vol(data, annualization=self.annualization)
            fb.method = "garch->ewma_fallback"
            fb.converged = False
            fb.detail = f"only {len(r)} returns (<{c.min_obs}); used EWMA"
            fb.horizon = c.horizon
            return fb

        scaled = r * c.rescale_factor
        try:
            from arch import arch_model

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                am = arch_model(
                    scaled, mean=c.mean, vol="GARCH", p=c.p, q=c.q, dist=c.dist, rescale=False
                )
                res = am.fit(disp="off", show_warning=False)
                fc = res.forecast(horizon=c.horizon, reindex=False)

            # variance row for the last observation: per-step variance, in scaled^2
            var_path = np.asarray(fc.variance.values)[-1]
            mean_var_scaled = float(np.mean(var_path))
            per_bar = float(np.sqrt(mean_var_scaled)) / c.rescale_factor

            converged = getattr(res, "convergence_flag", 0) == 0
            if not np.isfinite(per_bar) or per_bar <= 0:
                raise ValueError("non-finite GARCH variance")

            return VolForecast(
                annualized=per_bar * np.sqrt(self.annualization),
                per_bar=per_bar,
                horizon=c.horizon,
                method=f"garch({c.p},{c.q})-{c.dist}",
                converged=converged,
                detail="" if converged else "optimizer did not fully converge",
            )
        except Exception as exc:  # noqa: BLE001 - any arch/optimiser failure -> safe fallback
            fb = ewma_vol(data, annualization=self.annualization)
            fb.method = "garch->ewma_fallback"
            fb.converged = False
            fb.detail = f"GARCH failed ({type(exc).__name__}); used EWMA"
            fb.horizon = c.horizon
            return fb
