"""Is the forex sleeve's -£9,027 a real negative edge, or noise I am about to fit to?

Dropping the worst-performing sleeve AFTER seeing it lose is in-sample pruning, and it is the
specific failure this project has already been burned by. So the order of questions matters:

  1. Is forex's per-trade P&L statistically distinguishable from zero? If not, removing it is
     fitting to noise and should NOT be expected to help out of sample.
  2. Only if it is: does removing it actually improve the BOOK — return, Sharpe, and the
     forward drawdown that binds the account?

Point 2 is not implied by point 1. Forex can lose money and still earn its place by lowering
portfolio drawdown (it is the least correlated sleeve). Removing a diversifier can raise
drawdown even while raising return.

MEASUREMENT ONLY - no ledger charge. A gate would need its own prereg.
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
from apex_quant.validation.paired_tests import paired_block_bootstrap  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS, DEFAULT_HOLDOUT_START, MIN_BARS, WARMUP, TrendBook, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402


def fwd_dd(returns: pd.Series, wall: float = 0.12, seed: int = 42) -> dict:
    r = returns.dropna().to_numpy()
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(20000, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    dd = ((pk - eq) / pk).max(axis=1)
    return {"p95": float(np.percentile(dd, 95)), "breach": float((dd > wall).mean())}


def main() -> int:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(DEFAULT_HOLDOUT_START)
    full_universe = EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7

    panel = {}
    for inst in full_universe:
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)[lambda d: d.index < holdout]
        if len(df) >= MIN_BARS:
            panel[inst] = df

    params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
    rc = cfg.risk  # the live 0.75% config

    def run(p):
        pits = {k: PointInTimeAccessor(v) for k, v in p.items()}
        return PortfolioBacktester(
            cfg, risk_manager=RiskManager(rc), exit_mode="managed",
            slot_allocation="expected_value",
        ).run(pits, TrendBook(p, **params).strategies(),
              timeframes={k: "1d" for k in p}, warmup=WARMUP, periods_per_year=252)

    print("=" * 92, flush=True)
    print("DROP-FOREX TEST — is the -£9,027 signal or noise?", flush=True)
    print("=" * 92, flush=True)

    res_full = run(panel)

    # ---- Q1: is forex per-trade P&L distinguishable from zero? -------------------
    fx = set(FX_MAJORS_7)
    fx_pnl = np.array([t.pnl for t in res_full.trades if t.instrument in fx])
    eq_pnl = np.array([t.pnl for t in res_full.trades if t.instrument not in fx])

    rng = np.random.default_rng(42)
    boot = np.array([rng.choice(fx_pnl, size=len(fx_pnl), replace=True).mean()
                     for _ in range(20000)])
    lo, hi = np.percentile(boot, [2.5, 97.5])
    p_neg = float((boot >= 0).mean())      # P(mean >= 0) under the bootstrap

    print(f"\nQ1. FOREX PER-TRADE P&L  ({len(fx_pnl)} trades)")
    print(f"  mean £{fx_pnl.mean():+,.2f}/trade   total £{fx_pnl.sum():+,.0f}")
    print(f"  bootstrap 95% CI on the mean: [£{lo:+,.2f}, £{hi:+,.2f}]")
    print(f"  P(true mean >= 0) = {p_neg*100:.1f}%")
    print(f"  non-forex mean £{eq_pnl.mean():+,.2f}/trade for comparison")
    verdict = ("DISTINGUISHABLE from zero — a real drag"
               if hi < 0 else
               "NOT distinguishable from zero — removing it is fitting to noise")
    print(f"  -> {verdict}")

    # ---- Q2: does removing it improve the book? ----------------------------------
    no_fx = {k: v for k, v in panel.items() if k not in fx}
    res_nofx = run(no_fx)

    print(f"\nQ2. BOOK-LEVEL EFFECT")
    print(f"{'book':<22} {'CAGR':>7} {'£/mo':>7} {'Sharpe':>7} {'btDD':>6} "
          f"{'fwdP95':>7} {'P(>12%)':>8} {'trades':>7}")
    out = {}
    for name, res in (("full (39 inst)", res_full), ("no forex (32 inst)", res_nofx)):
        m, eq = res.metrics, res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        d = fwd_dd(res.returns)
        out[name] = (cagr, m["sharpe"], d)
        print(f"{name:<22} {cagr*100:6.2f}% {cagr*100000/12:7.0f} {m['sharpe']:7.3f} "
              f"{m['max_drawdown']*100:5.1f}% {d['p95']*100:6.1f}% {d['breach']*100:7.1f}% "
              f"{m['n_trades']:7d}")

    pb = paired_block_bootstrap(res_full.returns, res_nofx.returns,
                                block_size=21, n_bootstraps=10000, seed=42,
                                periods_per_year=252)
    print(f"\nPAIRED BLOCK BOOTSTRAP (no-forex vs full):")
    print(f"  Δsharpe {pb.get('sharpe_delta', 0):+.3f}  "
          f"p={pb.get('p_value_one_sided', 1):.4f}  "
          f"CI [{pb.get('ci_95_lower', 0):+.3f}, {pb.get('ci_95_upper', 0):+.3f}]")

    c_full, c_nofx = out["full (39 inst)"][0], out["no forex (32 inst)"][0]
    print(f"\nTARGET CHECK (£700-1,000/mo on £100k inside a 12% wall)")
    for name, (cagr, sh, d) in out.items():
        gbp = cagr * 100000 / 12
        ok = "YES" if (gbp >= 700 and d["p95"] <= 0.12) else "no"
        print(f"  {name:<22} £{gbp:.0f}/mo, fwd p95 {d['p95']*100:.1f}%  -> {ok}")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
