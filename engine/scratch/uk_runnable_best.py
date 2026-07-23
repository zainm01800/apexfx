"""The best configuration that a UK retail IBKR account can ACTUALLY run today.

Variant B (12 shares + 4 UCITS ETFs + 7 FX = 23 instruments) is the only universe fully
reachable from the account. At the live 0.75% it earns £636/mo but its forward p95 drawdown is
14.5% — above the owner's 12% wall. This finds the risk level where the runnable book sits
inside the wall, and what that costs in £/month.

Also prints the per-instrument breakdown so the actual tradeable list is explicit.

MEASUREMENT ONLY - no ledger charge. Adopting a universe change is pre-registered.
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

US_ETF_BLOCKED = {"XLK", "XLE", "XBI", "SMH", "SOXX"}
RISKS = [0.0040, 0.0050, 0.0060, 0.0065, 0.0075]
WALL = 0.12


def fwd(returns: pd.Series, seed: int = 42) -> dict:
    r = returns.dropna().to_numpy()
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(20000, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    dd = ((pk - eq) / pk).max(axis=1)
    return {"p95": float(np.percentile(dd, 95)), "breach": float((dd > WALL).mean()),
            "med": float(np.median(dd))}


def main() -> int:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(DEFAULT_HOLDOUT_START)

    names = [i for i in (EQUITY_CORE + [GOLD_ETC]) if i not in US_ETF_BLOCKED] + FX_MAJORS_7
    panel = {}
    for inst in names:
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)[lambda d: d.index < holdout]
        if len(df) >= MIN_BARS:
            panel[inst] = df
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

    print("=" * 92)
    print(f"BEST RUNNABLE BOOK — UK retail IBKR | {len(panel)} instruments")
    print("=" * 92)
    print("  " + ", ".join(sorted(panel)))
    print()
    print(f"{'risk':>6} {'CAGR':>7} {'£/mo':>7} {'Sharpe':>7} {'btDD':>6} {'medDD':>6} "
          f"{'fwdP95':>7} {'P(>12%)':>8} {'trades':>7}  {'inside wall':>11}")

    best = None
    for rpt in RISKS:
        rc = cfg.risk.model_copy(update={"max_risk_per_trade": rpt})
        res = PortfolioBacktester(cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                                  slot_allocation="expected_value").run(
            pits, TrendBook(panel, **params).strategies(),
            timeframes={k: "1d" for k in panel}, warmup=WARMUP, periods_per_year=252)
        m, eq = res.metrics, res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        d = fwd(res.returns)
        ok = d["p95"] <= WALL
        if ok and (best is None or cagr > best[1]):
            best = (rpt, cagr, m["sharpe"], d)
        print(f"{rpt*100:5.2f}% {cagr*100:6.2f}% {cagr*100000/12:7.0f} {m['sharpe']:7.3f} "
              f"{m['max_drawdown']*100:5.1f}% {d['med']*100:5.1f}% {d['p95']*100:6.1f}% "
              f"{d['breach']*100:7.1f}% {m['n_trades']:7d}  {'YES' if ok else 'no':>11}")

    print("=" * 92)
    if best:
        rpt, cagr, sh, d = best
        print(f"BEST RUNNABLE INSIDE THE {WALL*100:.0f}% WALL: risk {rpt*100:.2f}% -> "
              f"£{cagr*100000/12:.0f}/mo, Sharpe {sh:.3f}, fwd p95 {d['p95']*100:.1f}%")
        print(f"  capital for £700/mo at this CAGR: £{700*12/cagr:,.0f}")
        print(f"  capital for £1,000/mo:            £{1000*12/cagr:,.0f}")
    else:
        print(f"NOTHING inside the {WALL*100:.0f}% wall at these risk levels.")
    print("\nCompare: full 39-instrument certified book = £587/mo at 12.0% "
          "(but 5 US ETFs + 12 crypto are NOT tradeable here).")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
