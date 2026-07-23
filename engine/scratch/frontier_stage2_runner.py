"""Stage 2: does the RUNNER exit add to the vol-target overlay, and is the gain real?

The runner exit (let winners run past the fixed target instead of closing at P2) previously
beat the baseline on every metric — Sharpe 1.088, 282 fewer trades — and was rejected solely
on PBO. `risk_per_trade_prereg.md` §4 established that PBO cannot discriminate books sharing a
signal and universe (~0.99 correlated), which is exactly this case, and prescribed a PAIRED
block bootstrap instead. This re-tests it correctly rather than re-running the unfit metric.

Usage:
    .venv-mac/bin/python scratch/frontier_stage2_runner.py --rpt 0.0075 --vol-target 0.07

MEASUREMENT ONLY — no ledger charge. Any adopted config must be pre-registered and gated.
"""
from __future__ import annotations

import argparse
import json
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
from apex_quant.risk.trade_manager import TradeManager  # noqa: E402
from apex_quant.validation.paired_tests import paired_block_bootstrap  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS, DEFAULT_HOLDOUT_START, MIN_BARS, WARMUP, TrendBook, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

OUT = ENGINE_DIR / "data_store" / "validation" / "frontier_stage2_runner.json"


def forward_dd(returns: pd.Series, wall: float, n_sims: int = 20000, seed: int = 42) -> dict:
    r = returns.dropna().to_numpy()
    if len(r) < 100:
        return {}
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(n_sims, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    dd = ((pk - eq) / pk).max(axis=1)
    return {"median": float(np.median(dd)), "p95": float(np.percentile(dd, 95)),
            "p_breach": float((dd > wall).mean())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpt", type=float, default=0.0075)
    ap.add_argument("--vol-target", type=float, default=0.07)
    ap.add_argument("--wall", type=float, default=0.11)
    args = ap.parse_args(argv)

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

    print("=" * 92, flush=True)
    print(f"STAGE 2 — runner exit on top of rpt={args.rpt*100:.2f}% "
          f"volTgt={args.vol_target*100:.0f}% | {len(panel)} instruments", flush=True)
    print("=" * 92, flush=True)
    print(f"{'config':<22} {'CAGR':>7} {'£/mo':>8} {'Sharpe':>7} {'btDD':>6} "
          f"{'fwdP95':>7} {'P(>wall)':>9} {'trades':>7}", flush=True)

    rets, rows = {}, {}
    for name, runner in (("baseline_fixed_exit", False), ("runner_exit", True)):
        rc = cfg.risk.model_copy(update={
            "max_risk_per_trade": args.rpt, "portfolio_vol_target": args.vol_target})
        res = PortfolioBacktester(
            cfg, risk_manager=RiskManager(rc), exit_mode="managed",
            slot_allocation="expected_value",
            trade_manager=TradeManager(runner_mode=runner),
        ).run(pits, TrendBook(panel, **params).strategies(),
              timeframes=tfs, warmup=WARMUP, periods_per_year=252)

        m, eq = res.metrics, res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        fd = forward_dd(res.returns, args.wall)
        rets[name] = res.returns
        rows[name] = {"cagr": cagr, "gbp_per_month": cagr * 100000 / 12, "metrics": m,
                      "forward_dd": fd}
        print(f"{name:<22} {cagr*100:6.2f}% {cagr*100000/12:8.0f} {m['sharpe']:7.3f} "
              f"{m['max_drawdown']*100:5.1f}% {fd['p95']*100:6.1f}% {fd['p_breach']*100:8.1f}% "
              f"{m['n_trades']:7d}", flush=True)

    print("\nPAIRED BLOCK BOOTSTRAP (block 21, B=10,000, seed 42) — runner vs baseline:", flush=True)
    pb = paired_block_bootstrap(rets["baseline_fixed_exit"], rets["runner_exit"],
                                block_size=21, n_bootstraps=10000, seed=42,
                                periods_per_year=252.0)
    for k, v in pb.items():
        print(f"  {k:<28} {v}", flush=True)
    verdict = "REAL (p<0.05)" if pb.get("p_value", 1.0) < 0.05 else "NOT SIGNIFICANT"
    print(f"\n  -> runner-exit improvement is {verdict}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"args": vars(args), "rows": rows, "paired": pb}, indent=2,
                              default=str))
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
