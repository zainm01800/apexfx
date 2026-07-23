"""Last unexplored cells inside the user's relaxed constraints: risk <= 1.5%, DD wall 12%.

Everything else in the (risk x slots x cap x overlay) space is already measured. The known
best inside a 12% forward-p95 wall is 0.75% / 12 slots / 6.5% cap = GBP587/mo (Sharpe 0.893).
These are the only untested combinations that could conceivably beat it:

  * 5/5 slots at 1.25% (cap 6.5% never binds: 5 x 1.25% = 6.25%)
  * 5/5 slots at 1.00/1.25/1.50% with the cap relieved to 12%
  * 12/10 and 8/8 at 0.75% with cap 12% (cap bound only 3x at 0.75%, so ~no-op, run anyway)

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

#: (concurrent, swing, portfolio_cap, risk_per_trade)
CELLS = [
    (12, 10, 0.065, 0.0075),   # reference — the known best inside the 12% wall
    (5, 5, 0.065, 0.0125),
    (5, 5, 0.12, 0.0100),
    (5, 5, 0.12, 0.0125),
    (5, 5, 0.12, 0.0150),
    (12, 10, 0.12, 0.0075),
    (8, 8, 0.12, 0.0075),
]
WALL = 0.12


def fwd_dd(returns: pd.Series, seed: int = 42) -> tuple[float, float]:
    r = returns.dropna().to_numpy()
    if len(r) < 100:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(20000, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    dd = ((pk - eq) / pk).max(axis=1)
    return float(np.percentile(dd, 95)), float((dd > WALL).mean())


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
    tfs = {k: "1d" for k in panel}
    params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

    print("=" * 96, flush=True)
    print(f"FINAL CELLS — risk <= 1.5%, forward p95 wall {WALL*100:.0f}% | {len(panel)} instruments",
          flush=True)
    print("=" * 96, flush=True)
    print(f"{'slots':>6} {'cap':>6} {'rpt':>6} {'CAGR':>7} {'£/mo':>7} {'Sharpe':>7} "
          f"{'btDD':>6} {'fwdP95':>7} {'P(>12%)':>8} {'trades':>7}", flush=True)

    best = None
    for conc, swing, cap, rpt in CELLS:
        rc = cfg.risk.model_copy(update={
            "max_risk_per_trade": rpt, "max_portfolio_risk": cap,
            "max_concurrent_trades": conc, "max_swing_slots": swing,
        })
        res = PortfolioBacktester(cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                                  slot_allocation="expected_value").run(
            pits, TrendBook(panel, **params).strategies(), timeframes=tfs,
            warmup=WARMUP, periods_per_year=252)
        m, eq = res.metrics, res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        p95, pb = fwd_dd(res.returns)
        row = (conc, swing, cap, rpt, cagr, m["sharpe"], p95)
        if p95 <= WALL and (best is None or cagr > best[4]):
            best = row
        print(f"{f'{conc}/{swing}':>6} {cap*100:5.1f}% {rpt*100:5.2f}% {cagr*100:6.2f}% "
              f"{cagr*100000/12:7.0f} {m['sharpe']:7.3f} {m['max_drawdown']*100:5.1f}% "
              f"{p95*100:6.1f}% {pb*100:7.1f}% {m['n_trades']:7d}", flush=True)

    print("=" * 96, flush=True)
    if best:
        c, s, cap, rpt, cagr, sh, p95 = best
        print(f"BEST INSIDE {WALL*100:.0f}% WALL: {c}/{s} slots, cap {cap*100:.1f}%, "
              f"rpt {rpt*100:.2f}% -> £{cagr*100000/12:.0f}/mo, Sharpe {sh:.3f}, "
              f"fwd p95 {p95*100:.1f}%", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
