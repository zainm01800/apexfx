"""How likely is the 20% drawdown on the 2.00%/5-slot config?

20.0% is the worst drawdown that ACTUALLY HAPPENED once in 12.8 years of backtest. That is a
single realisation, not a probability. Two different questions matter and they have different
answers:

  1. Over ONE year, how often does a drawdown of X get exceeded?
  2. Over the multi-year horizon you would actually hold this, how often?

Reported both ways, for the candidate and the current config side by side. Bootstrap is
appropriate here (drawdown-exceedance over a horizon is being estimated from the return
distribution), but IID resampling understates clustered drawdowns — so the REAL historical
rolling-window figure is reported alongside as the honest upper reference.

MEASUREMENT ONLY - no ledger charge.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))
sys.path.insert(0, str(ENGINE_DIR / "scratch"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.risk.manager import RiskManager  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS, DEFAULT_HOLDOUT_START, MIN_BARS, WARMUP, TrendBook, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

CONFIGS = {
    "CURRENT 0.75% / 12 slots": (0.0075, 12, 10),
    "CANDIDATE 2.00% / 5 slots": (0.0200, 5, 5),
}
THRESHOLDS = [0.10, 0.12, 0.15, 0.20, 0.25, 0.30]
HORIZONS = {"1 year": 252, "2 years": 504, "3 years": 756, "5 years": 1260}


def returns_for(risk, conc, swing, pits, panel, cfg):
    rc = cfg.risk.model_copy(update={
        "max_risk_per_trade": risk, "max_concurrent_trades": conc,
        "max_swing_slots": swing,
    })
    res = PortfolioBacktester(cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                              slot_allocation="expected_value").run(
        pits, TrendBook(panel, **{"carry_filter": False, **COMMON_PARAMS,
                                  "momentum_lookback": 252}).strategies(),
        timeframes={k: "1d" for k in panel}, warmup=WARMUP, periods_per_year=252)
    return res.returns.dropna(), res.equity


def boot_dd(r: np.ndarray, days: int, n=20000, seed=42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1 + rng.choice(r, size=(n, days), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    return ((pk - eq) / pk).max(axis=1)


def historical_rolling_dd(r: np.ndarray, days: int) -> np.ndarray:
    """Worst drawdown inside every REAL rolling window of `days` — preserves clustering."""
    out = []
    for s in range(0, len(r) - days, 5):
        w = r[s:s + days]
        eq = np.cumprod(1 + w)
        pk = np.maximum.accumulate(eq)
        out.append(float(((pk - eq) / pk).max()))
    return np.asarray(out)


def main() -> int:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(DEFAULT_HOLDOUT_START)
    panel = {}
    for inst in EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7:
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)[lambda d: d.index < holdout]
        if len(df) >= MIN_BARS:
            panel[inst] = df
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}

    for label, (risk, conc, swing) in CONFIGS.items():
        r, eq = returns_for(risk, conc, swing, pits, panel, cfg)
        arr = r.to_numpy()
        realised = float(abs(((eq - eq.cummax()) / eq.cummax()).min()))

        print("=" * 96)
        print(f"{label}   |  realised worst drawdown in 12.8y backtest: {realised*100:.1f}%")
        print("=" * 96)
        print(f"{'threshold':>10} | " + " | ".join(f"{h:>8}" for h in HORIZONS))
        print(f"{'':>10} | " + " | ".join(f"{'P(exceed)':>8}" for _ in HORIZONS))
        for th in THRESHOLDS:
            cells = []
            for _, days in HORIZONS.items():
                p = float((boot_dd(arr, days) > th).mean())
                cells.append(f"{p*100:7.1f}%")
            mark = "  <-- the 20% figure" if abs(th - 0.20) < 1e-9 else ""
            print(f"{th*100:9.0f}% | " + " | ".join(cells) + mark)

        print(f"\n  REAL rolling windows (preserves clustering — the honest reference):")
        for h, days in HORIZONS.items():
            if len(arr) - days <= 0:
                continue
            hd = historical_rolling_dd(arr, days)
            print(f"    {h:>8}: median {np.median(hd)*100:5.1f}%  "
                  f"p95 {np.percentile(hd,95)*100:5.1f}%  "
                  f"worst {hd.max()*100:5.1f}%  "
                  f"P(>20%) {float((hd>0.20).mean())*100:5.1f}%")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
