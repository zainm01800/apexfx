"""Book E-126 HOLDOUT look (one look, user-approved 2026-07-17): 2025-01-01 -> latest.

The iteration gate (engine/data_store/book_e_gate_2026-07-17.md) PASSED book_e_126
(DSR 0.962 at n=152, PBO 0.2055, CPCV 15/15). The user approved spending the project's
ONE holdout look on it. This script runs the SAME frozen configuration (77-instrument
wide universe, RegimeGatedMomentum + MultiTimeframeMomentum 1w x 50, momentum_lookback 126,
vol 63, hold 21, rr 1.5, rule_based regime, managed exits, vol-scaled sizing, config caps,
v5 per-class costs) on the holdout window and records the result. It is not a gate: the
verdict criteria were pre-fixed by the parent BEFORE the run:

  CONFIRMED = holdout Sharpe > 0.5 AND positive expectancy/trade
  DEAD      = Sharpe <= 0 OR negative expectancy
  MARGINAL  = in between -> unproven; forward paper evidence decides

Run mechanics (flat-start-at-the-boundary interpretation):
  * PointInTimeAccessors carry FULL history (2016 -> present), so signals at any holdout
    bar are fully warmed with legitimate past data (exactly what live trading sees).
  * The backtester runs from --run-start (2024-01-01) so its sliced-frame ATR/vol/squeeze
    arrays are fully warm at the boundary; warmup=250 suppresses equity entries until
    ~the boundary (crypto/FX have denser 2024 calendars and may trade from ~Sep/Dec 2024 -
    carryover positions are marked in the sliced equity and counted separately).
  * Metrics are computed on the equity/trade slice >= 2025-01-01 only, via the same
    compute_metrics(., periods_per_year=252) convention the iteration gate used.
  * Holdout-window CPCV reuses run_portfolio_cpcv unchanged: panel = holdout-sliced frames
    (timeline), pits = full history (signals). ~18 months of data -> thin folds; the
    full-window metrics are the primary evidence.
  * Determinism: the full run is executed twice and must be byte-identical.

Usage:
    cd engine
    .venv-mac/bin/python scripts/run_book_e_holdout.py
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import pandas as pd  # noqa: E402

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.backtest.result import compute_metrics  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean  # noqa: E402
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402

from run_portfolio_gate import (  # noqa: E402
    COMMON_PARAMS,
    HORIZON,
    WARMUP,
    TrendBook,
    _max_gross_leverage,
    _utc,
)
from run_portfolio_gate_multiasset import FX_MAJORS_7  # noqa: E402
from run_portfolio_gate_book_e import BOOK_E_NEW, _constraint_profile  # noqa: E402

HOLDOUT_START = "2025-01-01"
RUN_START = "2024-01-01"   # in-run warmup for the sliced-frame ATR/vol/squeeze arrays
RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "book_e_126_holdout_2026-07-17.json"
PARAMS = {"carry_filter": False, **COMMON_PARAMS}   # == book_e_126 (momentum_lookback 126)


def _window_stats(res, holdout: pd.Timestamp, cfg) -> dict:
    """All trade/metric statistics on the >= holdout slice of one full run."""
    eq_h = res.equity[res.equity.index >= holdout]
    trades_h = [t for t in res.trades if _utc(t.entry_time) >= holdout]
    carryover = [t for t in res.trades if _utc(t.entry_time) < holdout <= _utc(t.exit_time)]
    m = compute_metrics(eq_h, trades_h, 252)
    span_days = max((eq_h.index[-1] - eq_h.index[0]).days, 1)
    per_inst: dict[str, dict] = {}
    for t in trades_h:
        d = per_inst.setdefault(t.instrument, {"n_trades": 0, "net_pnl": 0.0})
        d["n_trades"] += 1
        d["net_pnl"] += t.pnl
    per_class: dict[str, dict] = {}
    for inst, d in per_inst.items():
        cls = cfg.asset_class_of(inst)
        agg = per_class.setdefault(cls, {"instruments": [], "n_trades": 0, "net_pnl": 0.0})
        agg["instruments"].append(inst)
        agg["n_trades"] += d["n_trades"]
        agg["net_pnl"] += d["net_pnl"]
    for agg in per_class.values():
        agg["net_pnl"] = round(agg["net_pnl"], 2)
        agg["instruments"] = sorted(agg["instruments"])
    pnls = sorted((t.pnl for t in trades_h), reverse=True)
    total_pnl = sum(pnls)
    top3 = sorted(trades_h, key=lambda t: -t.pnl)[:3]
    monthly_eq = pd.concat([eq_h.iloc[:1], eq_h.resample("ME").last()]).drop_duplicates()
    mrets = monthly_eq.pct_change().dropna()
    return {
        "metrics": m,
        "entries_per_week": round(m["n_trades"] / (span_days / 7.0), 3),
        "trades_per_year": round(m["n_trades"] / (span_days / 365.25), 1),
        "span_days": span_days,
        "window": f"{eq_h.index[0].date()} -> {eq_h.index[-1].date()}",
        "max_gross_leverage": _max_gross_leverage(SimpleNamespace(equity=eq_h, trades=trades_h)),
        "per_instrument": {k: {"n_trades": v["n_trades"], "net_pnl": round(v["net_pnl"], 2)}
                           for k, v in sorted(per_inst.items())},
        "per_asset_class": per_class,
        "instruments_net_positive": sum(1 for v in per_inst.values() if v["net_pnl"] > 0),
        "carryover_trades": [{"instrument": t.instrument, "entry": t.entry_time,
                              "exit": t.exit_time, "pnl": round(t.pnl, 2)} for t in carryover],
        "top3_trades": [{"instrument": t.instrument, "direction": t.direction,
                         "entry": t.entry_time, "exit": t.exit_time,
                         "pnl": round(t.pnl, 2)} for t in top3],
        "top3_pnl_share": (round(sum(t.pnl for t in top3) / total_pnl, 4)
                           if total_pnl > 0 else None),
        "monthly_returns": {str(k.date()): round(float(v), 5) for k, v in mrets.items()},
        "best_month": (str(mrets.idxmax().date()), round(float(mrets.max()), 5)) if len(mrets) else None,
        "worst_month": (str(mrets.idxmin().date()), round(float(mrets.min()), 5)) if len(mrets) else None,
        "months_positive": int((mrets > 0).sum()),
        "months_total": int(len(mrets)),
    }


def main() -> int:
    cfg = get_config()
    store = ParquetStore(cfg.store_path)
    holdout = _utc(HOLDOUT_START)
    instruments = list(cfg.data.equities) + list(cfg.data.crypto) + FX_MAJORS_7 + BOOK_E_NEW

    panel: dict[str, pd.DataFrame] = {}
    short: dict[str, int] = {}
    for inst in instruments:
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)
        n_hold = int((df.index >= holdout).sum())
        if n_hold < 60:                       # ~3 months minimum in the holdout window
            short[inst] = n_hold
            continue
        panel[inst] = df
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    timeframes = {k: "1d" for k in panel}
    n_by_class = {}
    for inst in panel:
        cls = cfg.asset_class_of(inst)
        n_by_class[cls] = n_by_class.get(cls, 0) + 1

    print("=" * 72, flush=True)
    print(f"BOOK E-126 HOLDOUT LOOK (one look, user-approved) | {HOLDOUT_START} -> latest")
    print(f"universe: {len(panel)} instruments {n_by_class} | excluded (insufficient holdout data): "
          f"{short or 'none'}")
    print(f"holdout data end: {max(df.index[-1] for df in panel.values()).date()} | "
          f"params: momentum_lookback={PARAMS['momentum_lookback']} (all else frozen)")
    print("=" * 72, flush=True)

    # 1. Full run, TWICE (determinism), metrics on the >= holdout slice.
    runs = []
    for i in (1, 2):
        t0 = time.time()
        model = TrendBook(panel, **PARAMS)
        res = PortfolioBacktester(cfg, exit_mode="managed").run(
            pits, model.strategies(), timeframes=timeframes,
            start=RUN_START, warmup=WARMUP, periods_per_year=252,
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] run {i}: "
              f"{time.time() - t0:.0f}s | {res.summary()}", flush=True)
        runs.append(res)
    r1, r2 = runs
    det = (r1.metrics["total_return"] == r2.metrics["total_return"]
           and r1.metrics["n_trades"] == r2.metrics["n_trades"]
           and float(r1.equity.iloc[-1]) == float(r2.equity.iloc[-1]))
    stats = _window_stats(r1, holdout, cfg)
    stats2 = _window_stats(r2, holdout, cfg)
    det = det and stats["metrics"] == stats2["metrics"]
    print(f"DETERMINISM: {'IDENTICAL' if det else 'MISMATCH'}", flush=True)

    m = stats["metrics"]
    print(f"\nHOLDOUT WINDOW {stats['window']} ({stats['span_days']}d): "
          f"ret={m['total_return']*100:.1f}% ann={m['ann_return']*100:.1f}% sharpe={m['sharpe']:.3f} "
          f"maxDD={m['max_drawdown']*100:.1f}% trades={m['n_trades']} "
          f"({stats['entries_per_week']}/wk, {stats['trades_per_year']}/yr)", flush=True)
    print(f"  expectancy={m['expectancy_pnl']:.2f} pnl/trade ({m['expectancy_pct']*100:.3f}%/trade) "
          f"PF={m.get('profit_factor')} win={m['win_rate']*100:.1f}% lev~{stats['max_gross_leverage']:.2f}x "
          f"| insts +ve {stats['instruments_net_positive']}/{len(stats['per_instrument'])}", flush=True)
    print(f"  carryover (entered < {HOLDOUT_START}, closed after): {len(stats['carryover_trades'])}", flush=True)
    for cls, agg in sorted(stats["per_asset_class"].items()):
        print(f"  {cls:7s}: {agg['n_trades']:4d} trades, net {agg['net_pnl']:+12.2f} "
              f"across {len(agg['instruments'])} instruments", flush=True)
    prof = _constraint_profile(r1.constraint_log)
    print(f"  constraint profile (full run incl. 2024 warmup year): vetoes={prof['veto_events']} "
          f"scalings={prof['scaling_events']} | {prof['families']}", flush=True)
    print(f"  monthly: best {stats['best_month']} worst {stats['worst_month']} "
          f"({stats['months_positive']}/{stats['months_total']} positive) "
          f"| top-3 trades carry {stats['top3_pnl_share']} of window net pnl", flush=True)

    # 2. Holdout-window CPCV (thin folds: ~18 months; interpret accordingly).
    panel_h = {k: v[v.index >= holdout] for k, v in panel.items()}
    t0 = time.time()
    cpcv = run_portfolio_cpcv(
        panel_h, pits, lambda p, **kw: TrendBook(p, **kw), PARAMS,
        cfg=cfg, timeframes=timeframes, warmup=WARMUP, horizon=HORIZON,
        periods_per_year=252, exit_mode="managed",
    )
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] holdout CPCV: "
          f"{time.time() - t0:.0f}s | paths={cpcv['oos_sharpe_paths']}", flush=True)

    # 3. Pre-fixed verdict.
    sharpe_ok = m["sharpe"] > 0.5
    exp_ok = m["expectancy_pnl"] > 0
    verdict = "CONFIRMED" if (sharpe_ok and exp_ok) else (
        "DEAD" if (m["sharpe"] <= 0 or not exp_ok) else "MARGINAL")
    print(f"\nVERDICT (pre-fixed bar: Sharpe>0.5 & expectancy>0): {verdict} "
          f"(sharpe={m['sharpe']:.3f}, expectancy={m['expectancy_pnl']:.2f})", flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "book": "book_e_126",
        "params": PARAMS,
        "holdout_start": HOLDOUT_START,
        "run_start": RUN_START,
        "universe": sorted(panel.keys()),
        "excluded_insufficient_holdout": short,
        "determinism_identical": bool(det),
        "holdout": stats,
        "cpcv_holdout_window": cpcv,
        "constraint_profile_full_run": prof,
        "verdict": verdict,
        "verdict_criteria": "CONFIRMED = sharpe>0.5 & expectancy>0; DEAD = sharpe<=0 or "
                            "expectancy<=0; MARGINAL = between (pre-fixed by parent)",
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"results written to {RESULTS_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
