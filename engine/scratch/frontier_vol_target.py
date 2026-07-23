"""Frontier search: can the ENGINE hit ~£800-1k/month at ~11% drawdown?

Everything here runs through PortfolioBacktester, so unlike the pandas scripts it pays
per-asset-class costs on every fill, honours stops, gap-aware fills and EV slot allocation.

Two axes:
  * `max_risk_per_trade` - raw size.
  * `portfolio_vol_target` - the NEW book-wide vol overlay (0.0 = off, i.e. today's engine).

Reported honestly: CAGR (not arithmetic mean / 12), the forward drawdown DISTRIBUTION rather
than the single backtest path, and the realised monthly distribution including losing months.

MEASUREMENT ONLY - no ledger charge. Whatever wins here must then be pre-registered and gated
with every point in this grid charged, exactly as risk_per_trade_prereg.md did.
"""
from __future__ import annotations

import json
import sys
import time
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

OUT = ENGINE_DIR / "data_store" / "validation" / "frontier_vol_target.json"

RISK_LEVELS = [0.0050, 0.0075, 0.0100, 0.0125]
VOL_TARGETS = [0.0, 0.05, 0.06, 0.07, 0.08]
DD_WALL = 0.11


def forward_dd(returns: pd.Series, n_sims: int = 20000, seed: int = 42) -> dict:
    """Distribution of 1-year max drawdown, bootstrapped from the realised return process."""
    r = returns.dropna().to_numpy()
    if len(r) < 100:
        return {}
    rng = np.random.default_rng(seed)
    draws = rng.choice(r, size=(n_sims, 252), replace=True)
    eq = np.cumprod(1.0 + draws, axis=1)
    dd = ((np.maximum.accumulate(eq, axis=1) - eq) / np.maximum.accumulate(eq, axis=1)).max(axis=1)
    return {
        "median": float(np.median(dd)),
        "p95": float(np.percentile(dd, 95)),
        "p_breach_11": float((dd > DD_WALL).mean()),
    }


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

    print("=" * 100, flush=True)
    print(f"FRONTIER — engine-based, fully costed | {len(panel)} instruments | "
          f"iteration window < {DEFAULT_HOLDOUT_START}", flush=True)
    print(f"target: £800-1,000/mo on £100k  (9.6-12% CAGR) with forward p95 DD <= {DD_WALL*100:.0f}%",
          flush=True)
    print("=" * 100, flush=True)
    print(f"{'rpt':>6} {'volTgt':>7} {'CAGR':>7} {'£/mo':>8} {'Sharpe':>7} {'btDD':>6} "
          f"{'fwdP95':>7} {'P(>11%)':>8} {'trades':>7} {'caps':>6}", flush=True)

    out = []
    for rpt in RISK_LEVELS:
        for vt in VOL_TARGETS:
            rc = cfg.risk.model_copy(update={
                "max_risk_per_trade": rpt,
                "portfolio_vol_target": vt,
            })
            t0 = time.time()
            res = PortfolioBacktester(
                cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                slot_allocation="expected_value",
            ).run(pits, TrendBook(panel, **params).strategies(),
                  timeframes=tfs, warmup=WARMUP, periods_per_year=252)

            m = res.metrics
            eq = res.equity
            yrs = len(eq) / 252.0
            cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0.0
            fd = forward_dd(res.returns)
            caps = sum(v for k, v in res.constraint_log.items() if "portfolio_risk" in k)
            row = {
                "max_risk_per_trade": rpt, "portfolio_vol_target": vt,
                "cagr": cagr, "gbp_per_month": cagr * 100000 / 12,
                "sharpe": m["sharpe"], "backtest_max_dd": m["max_drawdown"],
                "n_trades": m["n_trades"], "portfolio_cap_hits": caps,
                "forward_dd": fd, "secs": round(time.time() - t0, 1),
            }
            out.append(row)
            print(f"{rpt*100:5.2f}% {vt*100:6.1f}% {cagr*100:6.2f}% "
                  f"{cagr*100000/12:8.0f} {m['sharpe']:7.3f} {m['max_drawdown']*100:5.1f}% "
                  f"{fd.get('p95', float('nan'))*100:6.1f}% {fd.get('p_breach_11', float('nan'))*100:7.1f}% "
                  f"{m['n_trades']:7d} {caps:6d}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))

    ok = [r for r in out
          if r["forward_dd"].get("p95", 1) <= DD_WALL and r["gbp_per_month"] >= 800]
    print("\n" + "=" * 100, flush=True)
    if ok:
        print(f"CONFIGS MEETING BOTH TARGETS ({len(ok)}):", flush=True)
        for r in sorted(ok, key=lambda x: -x["sharpe"]):
            print(f"  rpt {r['max_risk_per_trade']*100:.2f}%  volTgt {r['portfolio_vol_target']*100:.0f}%"
                  f"  -> £{r['gbp_per_month']:.0f}/mo  Sharpe {r['sharpe']:.3f}"
                  f"  fwd p95 DD {r['forward_dd']['p95']*100:.1f}%", flush=True)
    else:
        print("NO CONFIG MEETS BOTH TARGETS. Closest by £/mo among those inside the DD wall:",
              flush=True)
        inside = sorted([r for r in out if r["forward_dd"].get("p95", 1) <= DD_WALL],
                        key=lambda x: -x["gbp_per_month"])[:5]
        for r in inside:
            print(f"  rpt {r['max_risk_per_trade']*100:.2f}%  volTgt {r['portfolio_vol_target']*100:.0f}%"
                  f"  -> £{r['gbp_per_month']:.0f}/mo  Sharpe {r['sharpe']:.3f}"
                  f"  fwd p95 DD {r['forward_dd']['p95']*100:.1f}%", flush=True)
    print(f"\nwrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
