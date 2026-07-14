"""Microstructure alpha features — tick-level inputs for sub-hourly predictive edge.

These features address the core weakness of the legacy indicator parser: reliance
on macro-level, lagging close-price calculations. At intraday / sub-hourly horizons
the dominant price-forming mechanism is ORDER FLOW — who is buying vs. who is
selling — not smoothed price levels.

All three classes follow the standard ``base.Feature`` interface and are therefore
leakage-safe: they only ever read the ``pit.window(t, min_obs)`` slice.

References
----------
* Chordia, Roll & Subrahmanyam (2002) — Order imbalance and individual stock returns.
* Yang & Zhang (2000)                 — A new measure of historical volatility.
* Bollerslev (1986)                   — GARCH(1,1) conditional variance.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from apex_quant.features.base import Feature


# ---------------------------------------------------------------------------
#  1. Normalised Order Flow Imbalance (NOFI)
# ---------------------------------------------------------------------------
class NormalizedOFI(Feature):
    """Directional buy/sell pressure proxy derived from OHLCV bar structure.

    True Level-2 order-book data is not available via OANDA REST. We use
    the bar's body-to-range ratio as a calibrated proxy: a bar that closes
    at its high (range = body) indicates pure buy pressure; a doji (body ≈ 0)
    indicates balance. The metric is normalised to [-1, +1] so it is comparable
    across instruments and volatility regimes.

        NOFI_t = (close_t - open_t) / (high_t - low_t + ε)

    A rolling window smooths tick noise. A value near +1 signals sustained
    buy-side dominance; near -1 signals sell-side dominance.

    Economic rationale: persistent directional imbalance predicts short-run
    price continuation (Chordia et al. 2002). It is complementary to
    momentum which only captures net return, not within-bar pressure.
    """

    rationale = (
        "Normalised Order Flow Imbalance: bar body-to-range ratio averaged "
        "over a rolling window. Captures directional buy/sell pressure at the "
        "microstructure level — complementary to macro momentum, which only "
        "measures net price displacement. Evidence: Chordia, Roll & "
        "Subrahmanyam (2002)."
    )

    def __init__(self, window: int = 20) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.window = window

    @property
    def name(self) -> str:
        return f"nofi_{self.window}"

    @property
    def min_obs(self) -> int:
        return self.window

    def _compute(self, window: pd.DataFrame) -> float:
        o = window["open"].to_numpy()[-self.window:]
        h = window["high"].to_numpy()[-self.window:]
        lo = window["low"].to_numpy()[-self.window:]
        c = window["close"].to_numpy()[-self.window:]
        eps = 1e-10
        bar_ofi = (c - o) / (h - lo + eps)
        return float(np.mean(np.clip(bar_ofi, -1.0, 1.0)))


# ---------------------------------------------------------------------------
#  2. Yang-Zhang Volatility Estimator
# ---------------------------------------------------------------------------
class YangZhangVol(Feature):
    """Gap-robust, drift-independent historical volatility (Yang & Zhang 2000).

    Classic close-to-close volatility ignores overnight gaps (which are large
    in FX around data releases and weekend opens) and is biased when the
    drift is non-zero. Yang-Zhang combines three components:

        σ²_YZ = σ²_overnight + k · σ²_open_close + (1-k) · σ²_RS

    where:
    * σ²_overnight  = variance of log(open_t / close_{t-1})   — gap component
    * σ²_open_close = variance of log(close_t / open_t)       — drift component
    * σ²_RS         = Rogers-Satchell variance using O, H, L, C — range component
    * k             = 0.34 / (1.34 + (n+1)/(n-1))             — optimal weight

    The result is annualised as σ_YZ · √annualization_factor.

    Economic rationale: superior to Parkinson for FX (gapped markets), more
    efficient than close-to-close for a given window, and unbiased under
    non-zero drift — all three weaknesses of the legacy RealizedVol estimator.
    """

    rationale = (
        "Yang-Zhang (2000) gap-robust volatility: combines overnight-gap, "
        "open-to-close, and Rogers-Satchell range components. Unbiased under "
        "drift and ~5x more efficient than close-to-close for a given window. "
        "Essential for FX which has weekend gaps and data-release spikes."
    )

    def __init__(self, window: int = 21, annualization: int = 252) -> None:
        if window < 2:
            raise ValueError("window must be >= 2")
        self.window = window
        self.annualization = annualization

    @property
    def name(self) -> str:
        return f"yzvol_{self.window}"

    @property
    def min_obs(self) -> int:
        return self.window + 1  # need prev close for overnight component

    def _compute(self, window: pd.DataFrame) -> float:
        n = self.window
        o = np.log(window["open"].to_numpy())
        h = np.log(window["high"].to_numpy())
        lo = np.log(window["low"].to_numpy())
        c = np.log(window["close"].to_numpy())

        # Use the last (n+1) bars so we have n return observations
        o, h, lo, c = o[-(n + 1):], h[-(n + 1):], lo[-(n + 1):], c[-(n + 1):]

        # Overnight returns: log(open_t / close_{t-1})
        overnight = o[1:] - c[:-1]
        # Open-to-close returns
        open_close = c[1:] - o[1:]
        # Rogers-Satchell variance (within-bar only, no overnight gap)
        rs = (
            (h[1:] - o[1:]) * (h[1:] - c[1:])
            + (lo[1:] - o[1:]) * (lo[1:] - c[1:])
        )

        # Optimal weight k (Yang-Zhang 2000 eq. 16)
        k = 0.34 / (1.34 + (n + 1) / (n - 1))

        var_overnight = float(np.var(overnight, ddof=1))
        var_open_close = float(np.var(open_close, ddof=1))
        var_rs = float(np.mean(rs))

        yz_var = var_overnight + k * var_open_close + (1.0 - k) * var_rs
        return float(np.sqrt(max(yz_var, 0.0) * self.annualization))


# ---------------------------------------------------------------------------
#  3. GARCH(1,1) One-Step-Ahead Volatility Forecast
# ---------------------------------------------------------------------------
class GARCHForecast(Feature):
    """GARCH(1,1) one-step-ahead annualised volatility forecast.

    Volatility clusters (Mandelbrot 1963; Engle 1982): after a volatile bar,
    tomorrow is more likely to be volatile too. A GARCH model captures this
    persistence explicitly. The forecast is the square-root of the one-step-
    ahead conditional variance from a GARCH(1,1) fitted via maximum likelihood:

        σ²_t = ω + α · ε²_{t-1} + β · σ²_{t-1}

    The ``arch`` library (already in requirements.txt) handles MLE fitting.
    On fitting failure the estimator degrades gracefully to a simple realised
    vol — it never returns NaN due to a fitting error.

    Economic rationale: forward-looking conditional volatility is a superior
    input to position sizing than backward-looking realised vol. A large GARCH
    forecast shrinks position size *before* the volatile period, not after.
    (Bollerslev 1986; Engle & Bollerslev 1986.)
    """

    rationale = (
        "GARCH(1,1) one-step-ahead volatility forecast. Volatility clusters "
        "(Engle 1982): the GARCH forecast is predictive of tomorrow's vol where "
        "realised vol is backward-looking. Forward-looking vol shrinks position "
        "size before turbulent periods rather than after. Degrades to realised "
        "vol if fitting fails (arch library)."
    )

    def __init__(
        self,
        window: int = 252,
        annualization: int = 252,
        rescale: float = 100.0,
        refit_every: int = 1,
    ) -> None:
        if window < 50:
            raise ValueError("window must be >= 50 for GARCH fitting")
        if refit_every < 1:
            raise ValueError("refit_every must be >= 1")
        self.window = window
        self.annualization = annualization
        self.rescale = rescale  # arch library is numerically stable on rescaled returns
        # Re-estimating the GARCH MLE on every bar is the dominant cost of a
        # bar-by-bar backtest (one full maximum-likelihood fit per timestamp). With
        # refit_every > 1 the fitted (omega, alpha, beta) are cached and rolled
        # forward analytically between refits — the parameters are near-constant over
        # a handful of bars, so the one-step variance forecast is preserved for a
        # large speed-up. refit_every == 1 reproduces exact fit-every-bar behaviour.
        self.refit_every = refit_every
        self._garch_cache: dict | None = None
        self._n_fits = 0  # observability: MLE fits actually performed

    @property
    def name(self) -> str:
        return f"garch_fcast_{self.window}"

    @property
    def min_obs(self) -> int:
        return self.window + 1

    def _compute(self, window: pd.DataFrame) -> float:
        c = window["close"].to_numpy()
        log_ret = np.diff(np.log(c))[-self.window:] * self.rescale  # scale for MLE stability
        endpoint = window.index[-1]

        # Between refits: roll the cached parameters forward without an MLE fit.
        cache = self._garch_cache
        if (
            self.refit_every > 1
            and cache is not None
            and cache["ts"] < endpoint
            and int((window.index > cache["ts"]).sum()) < self.refit_every
        ):
            var_scaled = self._filter_forecast_var(
                log_ret, cache["omega"], cache["alpha"], cache["beta"]
            )
            daily_var = var_scaled / (self.rescale ** 2)
            return float(np.sqrt(max(daily_var, 0.0) * self.annualization))

        try:
            from arch import arch_model  # type: ignore[import]

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                am = arch_model(log_ret, vol="Garch", p=1, q=1, mean="Zero", dist="t",
                                rescale=False)
                res = am.fit(disp="off", show_warning=False)
            self._n_fits += 1

            # Cache the fitted params so subsequent bars can filter forward cheaply.
            p = res.params
            self._garch_cache = {
                "ts": endpoint,
                "omega": float(p.get("omega", 0.0)),
                "alpha": float(p.get("alpha[1]", 0.0)),
                "beta": float(p.get("beta[1]", 0.0)),
            }

            # One-step-ahead forecast (h.ahead=1)
            fc = res.forecast(horizon=1, reindex=False)
            var_scaled = float(fc.variance.iloc[-1, 0])

            # Un-scale back to returns space and annualise
            daily_var = var_scaled / (self.rescale ** 2)
            ann_vol = np.sqrt(max(daily_var, 0.0) * self.annualization)
            return float(ann_vol)

        except Exception:
            # Graceful fallback: simple realised vol on the same window
            self._garch_cache = None  # never reuse a failed fit
            sigma = float(np.std(log_ret / self.rescale, ddof=1))
            return float(sigma * np.sqrt(self.annualization))

    @staticmethod
    def _filter_forecast_var(x: np.ndarray, omega: float, alpha: float, beta: float) -> float:
        """One-step-ahead conditional variance from fixed GARCH(1,1) params via the
        deterministic variance recursion σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1} (no MLE).

        Seeded at the unconditional variance ω/(1−α−β); over a full window the
        recursion converges to the conditional path well before the endpoint, so the
        final one-step forecast closely tracks a fresh fit while the parameters hold.
        """
        n = len(x)
        persist = alpha + beta
        uncond = omega / (1.0 - persist) if (omega > 0.0 and 0.0 < persist < 0.999) else float(np.var(x))
        s = uncond
        for t in range(1, n):
            s = omega + alpha * x[t - 1] ** 2 + beta * s
        return omega + alpha * x[n - 1] ** 2 + beta * s
