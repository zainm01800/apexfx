"""Definitive: is £700/month possible at risk <= 1.50% and ~12% drawdown, on £100k?

Known so far at <=1.50% risk:
    0.75% / 12 slots           £587/mo @ 12.0%
    1.50% /  5 slots           £584/mo @ 12.6%
    1.25% /  8 slots           £325/mo @ 13.5%
    1.50% / 12 slots           £185/mo @ 11.9%   (portfolio cap collides)

All hover under £600. But the slot grid has real gaps — 4, 6, 7, 9, 10 were never tested — and
at 1.50% risk five slots already demand 7.5% of portfolio risk against a 6.5% cap, so the cap
is binding and truncating. Both are places an answer could hide.

This fills the grid: slots 4-11 x risk {1.25, 1.375, 1.50}% x portfolio cap {6.5, 12}%.
If nothing reaches £700 inside ~12.5% forward drawdown, the answer is settled and no further
sizing search is warranted.

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

SLOTS = [4, 5, 6, 7, 8, 9, 10, 11]
RISKS = [0.0125, 0.01375, 0.0150]
CAPS = [0.065, 0.12]
TARGET_GBP = 700.0
DD_LIMIT = 0.125          # "around 12%" read generously


def fwd_p95(returns: pd.Series, seed: int = 42) -> float:
    r = returns.dropna().to_numpy()
    rng = np.random.default_rng(seed)
    eq = np.cumprod(1.0 + rng.choice(r, size=(20000, 252), replace=True), axis=1)
    pk = np.maximum.accumulate(eq, axis=1)
    return float(np.percentile(((pk - eq) / pk).max(axis=1), 95))


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
    params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

    print("=" * 92, flush=True)
    print(f"IS £{TARGET_GBP:.0f}/MONTH POSSIBLE AT RISK <= 1.50% AND DD <= {DD_LIMIT*100:.1f}%?",
          flush=True)
    print(f"filling the untested slot range 4-11 | {len(panel)} instruments", flush=True)
    print("=" * 92, flush=True)
    print(f"{'slots':>6} {'risk':>7} {'cap':>6} {'£/mo':>7} {'Sharpe':>7} {'fwdP95':>7} "
          f"{'trades':>7} {'capHits':>8}  {'MEETS BOTH':>11}", flush=True)

    hits, best = [], None
    for slots in SLOTS:
        for risk in RISKS:
            for cap in CAPS:
                rc = cfg.risk.model_copy(update={
                    "max_risk_per_trade": risk, "max_portfolio_risk": cap,
                    "max_concurrent_trades": slots, "max_swing_slots": slots,
                })
                res = PortfolioBacktester(
                    cfg, risk_manager=RiskManager(rc), exit_mode="managed",
                    slot_allocation="expected_value",
                ).run(pits, TrendBook(panel, **params).strategies(),
                      timeframes={k: "1d" for k in panel}, warmup=WARMUP,
                      periods_per_year=252)
                m, eq = res.metrics, res.equity
                cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (252.0 / len(eq)) - 1
                gbp = cagr * 100_000 / 12
                p95 = fwd_p95(res.returns)
                ch = sum(v for k, v in res.constraint_log.items() if "portfolio_risk" in k)
                ok = gbp >= TARGET_GBP and p95 <= DD_LIMIT
                if ok:
                    hits.append((slots, risk, cap, gbp, m["sharpe"], p95))
                if p95 <= DD_LIMIT and (best is None or gbp > best[3]):
                    best = (slots, risk, cap, gbp, m["sharpe"], p95)
                print(f"{slots:6d} {risk*100:6.3f}% {cap*100:5.1f}% {gbp:7.0f} "
                      f"{m['sharpe']:7.3f} {p95*100:6.1f}% {m['n_trades']:7d} {ch:8d}"
                      f"  {'YES' if ok else '':>11}", flush=True)

    print("\n" + "=" * 92, flush=True)
    if hits:
        print(f"REACHED £{TARGET_GBP:.0f}/mo INSIDE {DD_LIMIT*100:.1f}% ({len(hits)} configs):")
        for s, r_, c, g, sh, p in sorted(hits, key=lambda x: -x[3]):
            print(f"  {s} slots, risk {r_*100:.3f}%, cap {c*100:.1f}% -> £{g:.0f}/mo "
                  f"Sharpe {sh:.3f} fwd p95 {p*100:.1f}%")
    else:
        print(f"NO CONFIG reaches £{TARGET_GBP:.0f}/mo at risk <= 1.50% inside "
              f"{DD_LIMIT*100:.1f}% drawdown.")
        if best:
            s, r_, c, g, sh, p = best
            print(f"  Best inside the wall: {s} slots, risk {r_*100:.3f}%, cap {c*100:.1f}% "
                  f"-> £{g:.0f}/mo, Sharpe {sh:.3f}, fwd p95 {p*100:.1f}%")
        print(f"  -> the answer is settled: no further sizing search is warranted.")
    print("=" * 92, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
