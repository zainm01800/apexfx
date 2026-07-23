"""Final lever: does relieving `max_portfolio_risk` unlock the £800-1k/month target?

The 4x5 vol-target grid topped out at 7.71% CAGR (£642/mo). In every high-risk cell the
6.5% portfolio-risk cap was binding hard (163 hits at 1.00%, 635 at 1.25%), truncating
positions to whatever budget happened to be left. So the return ceiling might be the CAP,
not the book. This tests that directly: raise the cap and see whether CAGR goes anywhere.

If CAGR still cannot reach 9.6% (£800/mo) at ANY drawdown, the target is not a sizing
problem and no amount of risk configuration will reach it.

MEASUREMENT ONLY - no ledger charge.
"""
from __future__ import annotations

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

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS, DEFAULT_HOLDOUT_START, MIN_BARS, WARMUP, TrendBook, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC  # noqa: E402
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402

OUT = ENGINE_DIR / "data_store" / "validation" / "frontier_portfolio_cap.json"

GRID = [
    (rpt, cap, vt)
    for rpt in (0.0100, 0.0150, 0.0200)
    for cap in (0.065, 0.12, 0.20)
    for vt in (0.0, 0.06)
]


def forward_dd(returns: pd.Series, n_sims: int = 20000, seed: int = 42) -> dict:
    r = returns.dropna().to_numpy()
    if len(r) < 100:
        return {}
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(n_sims, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    dd = ((pk - eq) / pk).max(axis=1)
    return {"median": float(np.median(dd)), "p95": float(np.percentile(dd, 95))}


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
    print(f"PORTFOLIO-RISK-CAP FRONTIER | {len(panel)} instruments | is the CAP the ceiling?",
          flush=True)
    print("=" * 100, flush=True)
    print(f"{'rpt':>6} {'cap':>6} {'volTgt':>7} {'CAGR':>7} {'£/mo':>8} {'Sharpe':>7} "
          f"{'btDD':>6} {'fwdP95':>7} {'trades':>7} {'caphits':>8} {'grossLev':>9}", flush=True)

    out = []
    for rpt, cap, vt in GRID:
        rc = cfg.risk.model_copy(update={
            "max_risk_per_trade": rpt, "max_portfolio_risk": cap,
            "portfolio_vol_target": vt,
        })
        res = PortfolioBacktester(
            cfg, risk_manager=RiskManager(rc), exit_mode="managed",
            slot_allocation="expected_value",
        ).run(pits, TrendBook(panel, **params).strategies(),
              timeframes=tfs, warmup=WARMUP, periods_per_year=252)

        m, eq = res.metrics, res.equity
        cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
        fd = forward_dd(res.returns)
        caps = sum(v for k, v in res.constraint_log.items() if "portfolio_risk" in k)
        lev = max((sum(abs(p) for p in [0]) for _ in [0]), default=0)  # placeholder
        row = {"max_risk_per_trade": rpt, "max_portfolio_risk": cap,
               "portfolio_vol_target": vt, "cagr": cagr,
               "gbp_per_month": cagr * 100000 / 12, "sharpe": m["sharpe"],
               "backtest_max_dd": m["max_drawdown"], "forward_dd": fd,
               "n_trades": m["n_trades"], "cap_hits": caps}
        out.append(row)
        print(f"{rpt*100:5.2f}% {cap*100:5.1f}% {vt*100:6.1f}% {cagr*100:6.2f}% "
              f"{cagr*100000/12:8.0f} {m['sharpe']:7.3f} {m['max_drawdown']*100:5.1f}% "
              f"{fd.get('p95', float('nan'))*100:6.1f}% {m['n_trades']:7d} {caps:8d} "
              f"{'':>9}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))

    best = max(out, key=lambda r: r["cagr"])
    print("\n" + "=" * 100, flush=True)
    print(f"MAX CAGR ANYWHERE IN THIS GRID: {best['cagr']*100:.2f}% "
          f"(£{best['gbp_per_month']:.0f}/mo) at rpt {best['max_risk_per_trade']*100:.2f}% "
          f"cap {best['max_portfolio_risk']*100:.0f}% volTgt {best['portfolio_vol_target']*100:.0f}%"
          f" — forward p95 DD {best['forward_dd'].get('p95', 0)*100:.1f}%", flush=True)
    reach = [r for r in out if r["gbp_per_month"] >= 800]
    print(f"Configs reaching £800/mo at ANY drawdown: {len(reach)}", flush=True)
    for r in sorted(reach, key=lambda x: x["forward_dd"].get("p95", 1))[:6]:
        print(f"   rpt {r['max_risk_per_trade']*100:.2f}% cap {r['max_portfolio_risk']*100:.0f}%"
              f" volTgt {r['portfolio_vol_target']*100:.0f}% -> £{r['gbp_per_month']:.0f}/mo"
              f"  fwd p95 DD {r['forward_dd']['p95']*100:.1f}%  Sharpe {r['sharpe']:.3f}", flush=True)
    print(f"\nwrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
