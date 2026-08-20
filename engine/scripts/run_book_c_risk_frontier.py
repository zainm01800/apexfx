"""Pre-registered Book C risk/return frontier between 0.75% and 1.00%."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

from apex_quant.backtest.portfolio import PortfolioBacktester  # noqa: E402
from apex_quant.config import get_config  # noqa: E402
from apex_quant.data import PointInTimeAccessor  # noqa: E402
from apex_quant.risk.trade_manager import TradeManager  # noqa: E402
from apex_quant.validation.metrics import deflated_sharpe_ratio, sharpe_ratio  # noqa: E402
from apex_quant.validation.paired_tests import paired_block_bootstrap  # noqa: E402
from apex_quant.validation.portfolio_report import run_portfolio_cpcv  # noqa: E402
from apex_quant.validation.trials import TrialLedger  # noqa: E402

from run_book_c_deep_audit import (  # noqa: E402
    CALENDAR_PPY,
    HORIZON,
    ITERATION_END,
    PARAMS,
    VERIFY_START,
    WARMUP,
    TrendBook,
    _load_panel,
    _monthly_tail_stats,
    _subset_metrics,
)
from run_book_c_funded_diagnostics import _rolling_evaluations  # noqa: E402
from run_portfolio_gate import LEDGER_PATH  # noqa: E402


PREREG = "engine/data_store/book_c_risk_frontier_prereg_2026-08-20.md"
DEFAULT_OUT = ENGINE_DIR / "data_store" / "validation" / "book_c_risk_frontier_2026-08-20.json"
GRID = [0.008, 0.00825, 0.0085, 0.00875, 0.009, 0.00925, 0.0095]
ANCHORS = [0.0075, 0.01]


def _cfg(risk: float, cost_mult: float = 1.0):
    cfg = copy.deepcopy(get_config())
    cfg.risk.max_risk_per_trade = risk
    if cost_mult != 1.0:
        for cls_name in ("forex", "equity", "crypto"):
            m = getattr(cfg.asset_classes, cls_name)
            m.spread_pips *= cost_mult
            m.spread_bps *= cost_mult
            m.slippage_bps *= cost_mult
            m.commission_per_trade *= cost_mult
            if cls_name == "forex":
                if m.cross_rt_cost_pips is not None:
                    m.cross_rt_cost_pips *= cost_mult
                m.pair_rt_cost_pips = {k: v * cost_mult for k, v in m.pair_rt_cost_pips.items()}
                m.pair_tf_rt_cost_pips = {
                    k: {tf: v * cost_mult for tf, v in by_tf.items()}
                    for k, by_tf in m.pair_tf_rt_cost_pips.items()
                }
    return cfg


def _run(panel, risk: float, cost_mult: float = 1.0):
    cfg = _cfg(risk, cost_mult)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    model = TrendBook(panel, **PARAMS)
    return PortfolioBacktester(
        cfg, exit_mode="managed", trade_manager=TradeManager()
    ).run(
        pits, model.strategies(), timeframes={k: "1d" for k in panel},
        warmup=WARMUP, periods_per_year=CALENDAR_PPY,
    )


def _cpcv(panel, risk: float):
    cfg = _cfg(risk)
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    return run_portfolio_cpcv(
        panel, pits, lambda p, **kw: TrendBook(p, **kw), PARAMS,
        cfg=cfg, timeframes={k: "1d" for k in panel}, warmup=WARMUP,
        horizon=HORIZON, periods_per_year=CALENDAR_PPY,
        exit_mode="managed", trade_manager=TradeManager(),
    )


def _brief(res):
    return {
        "metrics": res.metrics,
        "monthly_tail": _monthly_tail_stats(res, get_config().backtest.initial_equity),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args(argv)

    panel = _load_panel(include_post_2025=False)
    ledger = TrialLedger.load(LEDGER_PATH)
    before = ledger.n_trials
    if not args.no_ledger:
        for risk in GRID:
            ledger.record({
                "book": "book_c_risk_frontier",
                "kind": "book_c_risk_frontier_2026_08_20",
                "risk": risk,
                "params": PARAMS,
                "iteration_end_exclusive": str(ITERATION_END.date()),
            })
        ledger.save(LEDGER_PATH)
    n_trials = ledger.n_trials if not args.no_ledger else before + len(GRID)
    print(f"risk frontier | ledger {before} -> {ledger.n_trials} | DSR n={n_trials}", flush=True)

    results = {}
    raw = {}
    for risk in ANCHORS + GRID:
        t0 = time.time()
        res = _run(panel, risk)
        raw[risk] = res
        results[f"{risk:.5f}"] = _brief(res)
        m, tail = res.metrics, results[f"{risk:.5f}"]["monthly_tail"]
        print(f"risk {risk:.3%}: {time.time()-t0:.1f}s | return {m['total_return']:.2%} | "
              f"DD {m['max_drawdown']:.2%} | Sharpe {m['sharpe']:.4f} | "
              f"avg/mo {tail['avg_monthly_pnl']:+,.0f}", flush=True)

    low, high = raw[0.0075].metrics, raw[0.01].metrics
    ranked = []
    for risk in GRID:
        m = raw[risk].metrics
        rp = (m["total_return"] - low["total_return"]) / (high["total_return"] - low["total_return"])
        dp = (high["max_drawdown"] - m["max_drawdown"]) / (
            high["max_drawdown"] - low["max_drawdown"]
        )
        distance = float(np.hypot(1.0 - rp, 1.0 - dp))
        ranked.append({"risk": risk, "return_progress": rp, "drawdown_progress": dp,
                       "ideal_distance": distance})
    ranked.sort(key=lambda x: (x["ideal_distance"], x["risk"]))
    selected = ranked[0]["risk"]
    print(f"PRE-REGISTERED SELECTION: {selected:.3%} | distance {ranked[0]['ideal_distance']:.4f}", flush=True)

    trial_sharpes = [sharpe_ratio(raw[r].returns, periods_per_year=1) for r in ANCHORS + GRID]
    for risk in ANCHORS + GRID:
        key = f"{risk:.5f}"
        results[key]["paired_vs_control"] = paired_block_bootstrap(
            raw[0.01].returns, raw[risk].returns, block_size=21, n_bootstraps=10000,
            seed=get_config().seed, periods_per_year=CALENDAR_PPY,
        )
        results[key]["dsr"] = deflated_sharpe_ratio(
            raw[risk].returns.to_numpy(), trial_sharpes,
            periods_per_year=CALENDAR_PPY, n_trials=n_trials,
        )
        results[key]["funded_proxies"] = _rolling_evaluations(raw[risk].returns)

    t0 = time.time()
    selected_cpcv = _cpcv(panel, selected)
    control_paths = json.loads(
        (ENGINE_DIR / "data_store" / "validation" / "book_c_deep_audit_2026-08-19.json").read_text()
    )["books"]["book_c_control"]["cpcv"]["oos_sharpe_paths"]
    selected_paths = selected_cpcv["oos_sharpe_paths"]
    paths_won = sum(float(b) > float(a) for a, b in zip(control_paths, selected_paths))
    print(f"selected CPCV: {time.time()-t0:.1f}s | won {paths_won}/15 | {selected_paths}", flush=True)

    full_panel = _load_panel(include_post_2025=True)
    t0 = time.time()
    verify = _run(full_panel, selected)
    verification = _subset_metrics(verify, VERIFY_START)
    print(f"selected 2025+: {time.time()-t0:.1f}s | Sharpe {verification['sharpe']:.4f}", flush=True)

    t0 = time.time()
    stressed = _run(panel, selected, cost_mult=2.0)
    cost_stress = _brief(stressed)
    print(f"selected 2x costs: {time.time()-t0:.1f}s | Sharpe {stressed.metrics['sharpe']:.4f}", flush=True)

    sm = raw[selected].metrics
    stail = results[f"{selected:.5f}"]["monthly_tail"]
    checks = {
        "return_gt_075": sm["total_return"] > low["total_return"],
        "monthly_gt_075": stail["avg_monthly_pnl"] > results["0.00750"]["monthly_tail"]["avg_monthly_pnl"],
        "drawdown_lt_100": sm["max_drawdown"] < high["max_drawdown"],
        "cpcv_positive_12_of_15": sum(float(x) > 0 for x in selected_paths) >= 12,
        "cpcv_beats_control_8_of_15": paths_won >= 8,
        "cost_sharpe_drop_le_010": stressed.metrics["sharpe"] >= sm["sharpe"] - 0.10,
        "verification_positive": verification["total_return"] > 0 and verification["sharpe"] > 0,
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prereg": PREREG,
        "ledger": {"before": before, "after": ledger.n_trials, "used": n_trials},
        "grid": GRID,
        "anchors": ANCHORS,
        "ranked": ranked,
        "selected_risk": selected,
        "results": results,
        "selected_cpcv": selected_cpcv,
        "control_cpcv_paths": control_paths,
        "selected_cpcv_paths_won": paths_won,
        "selected_verification_2025_plus_nonblind": verification,
        "selected_cost_stress_2x": cost_stress,
        "checks": checks,
        "verdict": "PASS_MIDDLE_GROUND" if all(checks.values()) else "FAIL_MIDDLE_GROUND",
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"VERDICT {payload['verdict']} | {selected:.3%} | results {path}", flush=True)


if __name__ == "__main__":
    main()
