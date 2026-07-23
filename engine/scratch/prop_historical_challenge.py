"""Backtest the prop challenge on the ACTUAL historical sequence, not a bootstrap.

Every prop simulation so far resampled trades IID. That destroys serial structure, and
momentum returns are not IID: winners cluster in trends and losses cluster in regime breaks
(2018 Q4, Mar 2020, 2022). Bootstrapping smooths those clusters away and therefore
OVERSTATES how reliably the challenge gets passed.

This is the honest version. For EVERY possible start date in the real equity curve, run the
challenge forward on the returns that actually followed:
    pass  when cumulative return >= target
    fail  when cumulative return <= -8% (static floor from the start date)
and record how long it took. No resampling anywhere.

The spread across start dates is the real answer to "how long will this take" — it is the
distribution of outcomes over the actual market conditions the book lived through.

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

FLOOR = -0.08
STRUCTURES = {"2-step 8%+5%": [0.08, 0.05], "1-step 8%": [0.08], "1-step 6%": [0.06]}
RISKS = [0.0075, 0.010, 0.0125, 0.015]
MAX_DAYS = 252 * 4          # give any single attempt up to 4 years before calling it stalled


def daily_returns_at(risk: float) -> pd.Series:
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
    rc = cfg.risk.model_copy(update={"max_risk_per_trade": risk})
    res = PortfolioBacktester(cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                              slot_allocation="expected_value").run(
        pits, TrendBook(panel, **{"carry_filter": False, **COMMON_PARAMS,
                                  "momentum_lookback": 252}).strategies(),
        timeframes={k: "1d" for k in panel}, warmup=WARMUP, periods_per_year=252)
    return res.returns.dropna()


def walk_phase(r: np.ndarray, start: int, target: float) -> tuple[str, int]:
    """Walk the REAL sequence from `start`. Returns (outcome, days_used)."""
    eq = 0.0
    n = len(r)
    for i in range(start, min(start + MAX_DAYS, n)):
        eq = (1.0 + eq) * (1.0 + r[i]) - 1.0
        if eq >= target:
            return "pass", i - start + 1
        if eq <= FLOOR:
            return "blown", i - start + 1
    return "stalled", min(MAX_DAYS, n - start)


def run_structure(r: np.ndarray, targets: list[float]) -> dict:
    n = len(r)
    results = []
    # step start dates weekly to keep it fast but dense
    for start in range(0, n - 252, 5):
        cur, days, ok = start, 0, True
        for tgt in targets:
            outcome, used = walk_phase(r, cur, tgt)
            days += used
            cur += used
            if outcome != "pass":
                ok = False
                results.append((outcome, days))
                break
        if ok:
            results.append(("pass", days))
    passes = [d for o, d in results if o == "pass"]
    blown = sum(1 for o, _ in results if o == "blown")
    stalled = sum(1 for o, _ in results if o == "stalled")
    total = len(results)
    return {
        "n_starts": total,
        "p_pass": len(passes) / total if total else 0.0,
        "p_blown": blown / total if total else 0.0,
        "p_stalled": stalled / total if total else 0.0,
        "median_months": float(np.median(passes)) / 21.0 if passes else float("nan"),
        "p90_months": float(np.percentile(passes, 90)) / 21.0 if passes else float("nan"),
    }


def main() -> int:
    print("=" * 104)
    print("PROP CHALLENGE ON THE REAL HISTORICAL SEQUENCE (no bootstrap)")
    print("=" * 104)
    print("Every start date in the actual equity curve, walked forward on the returns that")
    print("really followed. Compare with the IID bootstrap numbers quoted earlier.\n")
    print(f"{'structure':<15} {'risk':>6} {'starts':>7} {'P(pass)':>8} {'P(blown)':>9} "
          f"{'P(stall)':>9} {'median mo':>10} {'p90 mo':>8}")

    cache = {}
    for risk in RISKS:
        cache[risk] = daily_returns_at(risk).to_numpy()

    for label, targets in STRUCTURES.items():
        for risk in RISKS:
            s = run_structure(cache[risk], targets)
            print(f"{label:<15} {risk*100:5.2f}% {s['n_starts']:7d} {s['p_pass']*100:7.1f}% "
                  f"{s['p_blown']*100:8.1f}% {s['p_stalled']*100:8.1f}% "
                  f"{s['median_months']:9.1f}m {s['p90_months']:7.1f}m")
        print()

    print("=" * 104)
    print("BOOTSTRAP said (2-step, 1.00%): P(pass) 94.2%, median 12.4 months.")
    print("If the historical numbers are materially worse, the IID assumption was flattering")
    print("the result — real return sequences cluster, and clusters are what blow the floor.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
