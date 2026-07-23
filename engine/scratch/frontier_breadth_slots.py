"""The gap I did NOT test: BREADTH. Do slot caps, not sizing, bind this book?

Grinold: IR ~ IC * sqrt(breadth). Breadth is the number of INDEPENDENT bets, and it is the
one lever that raises Sharpe without a new signal. Every config in the sizing frontier ran
with max_concurrent_trades=12 and max_swing_slots=10, and `timeframe_bucket_full` was
previously observed firing 18,147 times in the certified book — meaning the engine was
refusing entries it wanted to take.

If slots bind, the sizing frontier measured a book that was never allowed to be fully
invested, and its Sharpe ceiling of ~0.92 is an artifact of the cap rather than the signal.

Prints the FULL constraint log so the binding rule is visible rather than assumed.

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

OUT = ENGINE_DIR / "data_store" / "validation" / "frontier_breadth_slots.json"

# (max_concurrent_trades, max_swing_slots) — 12/10 is today's setting.
SLOT_GRID = [(12, 10), (20, 18), (30, 28), (39, 39)]
RISK = [0.0050, 0.0075]


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

    print("=" * 104, flush=True)
    print(f"BREADTH FRONTIER — do SLOT caps bind? | {len(panel)} instruments", flush=True)
    print("=" * 104, flush=True)
    print(f"{'rpt':>6} {'conc':>5} {'swing':>6} {'CAGR':>7} {'£/mo':>8} {'Sharpe':>7} "
          f"{'btDD':>6} {'fwdP95':>7} {'trades':>7}   binding constraints", flush=True)

    out = []
    for rpt in RISK:
        for conc, swing in SLOT_GRID:
            rc = cfg.risk.model_copy(update={
                "max_risk_per_trade": rpt,
                "max_concurrent_trades": conc,
                "max_swing_slots": swing,
            })
            res = PortfolioBacktester(
                cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                slot_allocation="expected_value",
            ).run(pits, TrendBook(panel, **params).strategies(),
                  timeframes=tfs, warmup=WARMUP, periods_per_year=252)

            m, eq = res.metrics, res.equity
            cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
            fd = forward_dd(res.returns)
            top = sorted(res.constraint_log.items(), key=lambda kv: -kv[1])[:3]
            top_s = ", ".join(f"{k}x{v}" for k, v in top)
            out.append({"max_risk_per_trade": rpt, "max_concurrent_trades": conc,
                        "max_swing_slots": swing, "cagr": cagr,
                        "gbp_per_month": cagr * 100000 / 12, "sharpe": m["sharpe"],
                        "backtest_max_dd": m["max_drawdown"], "forward_dd": fd,
                        "n_trades": m["n_trades"],
                        "constraint_log": dict(res.constraint_log)})
            print(f"{rpt*100:5.2f}% {conc:5d} {swing:6d} {cagr*100:6.2f}% "
                  f"{cagr*100000/12:8.0f} {m['sharpe']:7.3f} {m['max_drawdown']*100:5.1f}% "
                  f"{fd.get('p95', float('nan'))*100:6.1f}% {m['n_trades']:7d}   {top_s}",
                  flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    best = max(out, key=lambda r: r["sharpe"])
    print("\n" + "=" * 104, flush=True)
    print(f"BEST SHARPE: {best['sharpe']:.3f} at rpt {best['max_risk_per_trade']*100:.2f}% "
          f"conc {best['max_concurrent_trades']} swing {best['max_swing_slots']} "
          f"-> £{best['gbp_per_month']:.0f}/mo, fwd p95 DD "
          f"{best['forward_dd'].get('p95', 0)*100:.1f}%", flush=True)
    print("If Sharpe is FLAT across slot counts, breadth was never the constraint and the",
          flush=True)
    print("~0.92 ceiling belongs to the SIGNAL, not the plumbing.", flush=True)
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
