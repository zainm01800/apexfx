"""Getting £700+/month on £100k: what it costs, and what it does to the funded challenge.

£587/mo is the max inside a 12% forward-p95 wall. £700/mo needs ~8.4% CAGR, and at this book's
Sharpe (~0.89) that means more volatility, which means more drawdown. The question is not
whether £700 is reachable — it is — but what it costs on the two things the owner also cares
about: drawdown, and passing a funded challenge.

The trap: a config that earns more per month can pass the challenge LESS often, because the
challenge has a hard -8% floor and rewards smoothness, not return. Those are different
objectives and they can point opposite ways.

So each candidate is scored on BOTH: monthly profit / drawdown, AND challenge pass rate walked
on the REAL historical sequence (no bootstrap — that flattered every earlier prop number).

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
from prop_historical_challenge import walk_phase, FLOOR  # noqa: E402

#: label -> (risk, concurrent slots, swing slots)
CANDIDATES = {
    "CURRENT 0.75% / 12 slots": (0.0075, 12, 10),
    "1.00% / 12 slots":         (0.0100, 12, 10),
    "1.50% / 5 slots":          (0.0150, 5, 5),
    "2.00% / 5 slots":          (0.0200, 5, 5),
    "1.25% / 8 slots":          (0.0125, 8, 8),
}
CHALLENGE_TARGET = 0.06     # the 1-step 6% product that tested best


def build_panel():
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
    return panel


def challenge_stats(r: np.ndarray, target: float) -> dict:
    """Walk EVERY start date on the real sequence."""
    out = []
    for start in range(0, len(r) - 252, 5):
        outcome, days = walk_phase(r, start, target)
        out.append((outcome, days))
    passes = [d for o, d in out if o == "pass"]
    return {
        "p_pass": sum(1 for o, _ in out if o == "pass") / len(out),
        "p_blown": sum(1 for o, _ in out if o == "blown") / len(out),
        "median_months": float(np.median(passes)) / 21.0 if passes else float("nan"),
    }


def main() -> int:
    cfg = get_config()
    panel = build_panel()
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

    print("=" * 108)
    print(f"GETTING £700+/MONTH — and what it costs on the {CHALLENGE_TARGET*100:.0f}% "
          f"1-step challenge (real sequence)")
    print("=" * 108)
    print(f"{'config':<26} {'£/mo':>7} {'Sharpe':>7} {'btDD':>6} {'fwdP95':>7} "
          f"{'trades':>7} | {'chal pass':>10} {'blown':>7} {'median':>8}")

    for label, (risk, conc, swing) in CANDIDATES.items():
        rc = cfg.risk.model_copy(update={
            "max_risk_per_trade": risk,
            "max_concurrent_trades": conc,
            "max_swing_slots": swing,
        })
        res = PortfolioBacktester(cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                                  slot_allocation="expected_value").run(
            pits, TrendBook(panel, **params).strategies(),
            timeframes={k: "1d" for k in panel}, warmup=WARMUP, periods_per_year=252)

        m, eq = res.metrics, res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        rr = res.returns.dropna()
        rng = np.random.default_rng(42)
        sim = np.cumprod(1 + rng.choice(rr.to_numpy(), size=(20000, 252), replace=True), axis=1)
        pk = np.maximum.accumulate(sim, axis=1)
        p95 = float(np.percentile(((pk - sim) / pk).max(axis=1), 95))

        c = challenge_stats(rr.to_numpy(), CHALLENGE_TARGET)
        flag = "  <-- £700+" if cagr * 100000 / 12 >= 700 else ""
        print(f"{label:<26} {cagr*100000/12:7.0f} {m['sharpe']:7.3f} "
              f"{m['max_drawdown']*100:5.1f}% {p95*100:6.1f}% {m['n_trades']:7d} | "
              f"{c['p_pass']*100:9.1f}% {c['p_blown']*100:6.1f}% "
              f"{c['median_months']:7.1f}m{flag}", flush=True)

    print("=" * 108)
    print("More £/month is bought with drawdown. Whether it is ALSO bought with a worse")
    print("challenge pass rate is the question that decides which one you actually want.")
    print("=" * 108)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
