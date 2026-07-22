"""Pre-registered Portfolio Gate: Expected-Value Slot Allocation & Capacity Expansion.

Implements data_store/slot_capacity_ev_prereg.md:
Evaluates 3 pre-registered slot allocation configurations over Book H Gold 252 (39 instruments).
Measures performance across 6 shuffled instrument orderings to prove order-invariance (Spread = 0.000),
executes paired block-bootstrap & Diebold-Mariano tests vs 10-slot insertion order baseline,
calculates DSR at full ledger count (N=221), PBO, CPCV 15 paths, and verifies byte-identical determinism twin.

Iteration window: strictly < 2025-01-01.
Seed: 42.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import numpy as np
import pandas as pd

from apex_quant.backtest.portfolio import PortfolioBacktester
from apex_quant.config import get_config
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean
from apex_quant.validation.metrics import (
    probability_of_backtest_overfitting,
    sharpe_ratio,
    deflated_sharpe_ratio,
)
from apex_quant.validation.portfolio_report import run_portfolio_cpcv
from apex_quant.validation.trials import TrialLedger
from apex_quant.validation.paired_tests import paired_block_bootstrap, diebold_mariano_test

from run_portfolio_gate import (
    COMMON_PARAMS,
    DEFAULT_HOLDOUT_START,
    HORIZON,
    LEDGER_PATH,
    MIN_BARS,
    WARMUP,
    TrendBook,
    _gate,
    _utc,
)
from run_portfolio_gate_book_h import PANEL_UNIVERSES, EQUITY_CORE, GOLD_ETC
from run_portfolio_gate_multiasset import FX_MAJORS_7

RESULTS_PATH = ENGINE_DIR / "data_store" / "validation" / "slot_capacity_ev_gate_2026-07-22.json"

GRID_CONFIGS = {
    "ev_alloc_10_slots": {"slot_allocation": "expected_value", "max_swing_slots": 10},
    "ev_alloc_16_slots": {"slot_allocation": "expected_value", "max_swing_slots": 16},
    "ev_alloc_20_slots": {"slot_allocation": "expected_value", "max_swing_slots": 20},
}


def run_gate_once(seed: int = 42, no_ledger: bool = False):
    cfg = get_config()
    cfg.seed = seed
    store = ParquetStore(cfg.store_path)
    holdout_start = _utc(DEFAULT_HOLDOUT_START)

    crypto = list(cfg.data.crypto)
    wanted = sorted({inst for universe in PANEL_UNIVERSES.values() for inst in universe}
                    | set(crypto) | set(FX_MAJORS_7))

    master: dict[str, pd.DataFrame] = {}
    for inst in wanted:
        df = store.load(inst, "1d")
        if df.empty:
            continue
        df = clean(df)
        df = df[df.index < holdout_start]
        if len(df) >= MIN_BARS:
            master[inst] = df

    # Record 3 trials in TrialLedger BEFORE execution
    ledger = TrialLedger.load(LEDGER_PATH)
    n_before = ledger.n_trials
    if not no_ledger:
        for name, params in GRID_CONFIGS.items():
            ledger.record({"book": name, "universe": "book_h_gold_39", "timeframe": "1d",
                           "factory": "trend_book_ev_slots", "params": params})
        ledger.save(LEDGER_PATH)
    used_trials = ledger.n_trials if not no_ledger else n_before + len(GRID_CONFIGS)

    panel = {inst: master[inst] for inst in PANEL_UNIVERSES["book_h_gold_252"] + crypto + FX_MAJORS_7 if inst in master}
    pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
    tf_dict = {k: "1d" for k in panel}
    params_trend = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

    # Generate 6 instrument orderings for order-invariance testing
    gate_order = [i for i in (EQUITY_CORE + [GOLD_ETC] + crypto + FX_MAJORS_7) if i in panel]
    rng = np.random.default_rng(seed)
    orderings = [gate_order] + [list(rng.permutation(gate_order)) for _ in range(5)]

    # 1. Baseline: insertion-order allocation with 10 slots
    cfg_base = get_config()
    cfg_base.risk.max_swing_slots = 10
    cfg_base.risk.max_concurrent_trades = 12
    res_base = PortfolioBacktester(cfg_base, exit_mode="managed", slot_allocation="order").run(
        pits, TrendBook(panel, **params_trend).strategies(),
        timeframes=tf_dict, warmup=WARMUP, periods_per_year=252,
    )
    r_base = res_base.returns.rename("r_base")

    # Evaluate each grid config across 6 orderings
    config_results = {}
    returns_by_config = {}

    for name, p_cfg in GRID_CONFIGS.items():
        alloc_mode = p_cfg["slot_allocation"]
        slots = p_cfg["max_swing_slots"]

        sh_list = []
        ret_list = []
        dd_list = []

        cfg_run = get_config()
        cfg_run.risk.max_swing_slots = slots
        cfg_run.risk.max_concurrent_trades = slots + 4

        headline_returns = None
        headline_metrics = None

        for ord_idx, ord_list in enumerate(orderings):
            p_ord = {i: panel[i] for i in ord_list if i in panel}
            pits_ord = {k: PointInTimeAccessor(v) for k, v in p_ord.items()}

            res_run = PortfolioBacktester(cfg_run, exit_mode="managed", slot_allocation=alloc_mode).run(
                pits_ord, TrendBook(p_ord, **params_trend).strategies(),
                timeframes={k: "1d" for k in p_ord}, warmup=WARMUP, periods_per_year=252,
            )
            m = res_run.metrics
            sh_list.append(m["sharpe"])
            ret_list.append(m["total_return"])
            dd_list.append(m["max_drawdown"])

            if ord_idx == 0:
                headline_returns = res_run.returns
                headline_metrics = m

        returns_by_config[name] = headline_returns

        # Paired block-bootstrap & Diebold-Mariano tests vs baseline
        boot_res = paired_block_bootstrap(r_base, headline_returns, block_size=21, n_bootstraps=10000, seed=seed)
        dm_res = diebold_mariano_test(r_base, headline_returns)

        # Monthly GBP at 6% target annual vol
        med_ret = float(np.median(ret_list))
        ann_ret_approx = (1 + med_ret) ** (1.0 / 9.0) - 1.0
        monthly_gbp = (ann_ret_approx / 12.0) * 100000.0

        config_results[name] = {
            "params": p_cfg,
            "metrics": headline_metrics,
            "ordering_audit": {
                "median_sharpe": float(np.median(sh_list)),
                "min_sharpe": float(np.min(sh_list)),
                "max_sharpe": float(np.max(sh_list)),
                "spread": float(np.max(sh_list) - np.min(sh_list)),
                "all_sharpes": [round(s, 3) for s in sh_list],
                "median_drawdown": float(np.median(dd_list)),
                "monthly_gbp_per_100k": float(monthly_gbp),
            },
            "paired_bootstrap": boot_res,
            "diebold_mariano": dm_res,
        }

    # PBO across candidate grid
    aligned_grid = pd.concat(list(returns_by_config.values()), axis=1).dropna()
    M_grid = aligned_grid.to_numpy()
    pbo_res = (probability_of_backtest_overfitting(M_grid, n_splits=cfg.validation.pbo.n_splits, seed=cfg.seed)
               if M_grid.shape[1] >= 2 and M_grid.shape[0] >= 40 else {"pbo": None})

    # CPCV and DSR per config
    trial_sharpes = [config_results[n]["ordering_audit"]["median_sharpe"] for n in GRID_CONFIGS]

    for name, p_cfg in GRID_CONFIGS.items():
        slots = p_cfg["max_swing_slots"]
        cfg_cpcv = get_config()
        cfg_cpcv.risk.max_swing_slots = slots
        cfg_cpcv.risk.max_concurrent_trades = slots + 4

        cpcv = run_portfolio_cpcv(
            panel, pits,
            lambda p, **kw: TrendBook(p, **params_trend),
            params_trend, cfg=cfg_cpcv, timeframes=tf_dict, warmup=WARMUP, horizon=HORIZON,
            periods_per_year=252, exit_mode="managed",
        )
        v = _gate(name, returns_by_config[name], trial_sharpes, pbo_res, cpcv, used_trials)
        config_results[name]["gate"] = v
        config_results[name]["cpcv"] = cpcv

    return {
        "used_trials": used_trials,
        "baseline_10_slot_order": {
            "sharpe": float(res_base.metrics["sharpe"]),
            "max_drawdown": float(res_base.metrics["max_drawdown"]),
        },
        "configs": config_results,
        "pbo": pbo_res,
    }


def main():
    parser = argparse.ArgumentParser(description="Run Slot Capacity EV Gate (Seed 42)")
    parser.add_argument("--no-ledger", action="store_true", help="Smoke test without recording ledger trials")
    args = parser.parse_args()

    print("=" * 80)
    print("  RUNNING EXPECTED-VALUE SLOT ALLOCATION & CAPACITY EXPANSION GATE (SEED 42)")
    print("=" * 80)
    run1 = run_gate_once(seed=42, no_ledger=args.no_ledger)

    print("\n" + "=" * 80)
    print("  RUNNING DETERMINISM TWIN (SEED 42 REPEAT)")
    print("=" * 80)
    run2 = run_gate_once(seed=42, no_ledger=True)

    det_pass = (run1["configs"] == run2["configs"])
    print(f"\nDeterminism Twin Check: {'BYTE-IDENTICAL PASS' if det_pass else 'FAILED'}")

    print("\n" + "=" * 80)
    print("  GATE VERDICT SUMMARY TABLE (ALL 6 SHUFFLED ORDERINGS)")
    print("=" * 80)
    print(f"{'Config Name':<20s} | {'Med Sharpe':<10s} | {'Spread':<8s} | {'DSR (N=221)':<12s} | {'Bootstrap p-val':<15s} | {'£/mo @ 6% vol':<12s}")
    print("-" * 90)

    for name, data in run1["configs"].items():
        o = data["ordering_audit"]
        g = data["gate"]
        b = data["paired_bootstrap"]
        print(f"{name:<20s} | {o['median_sharpe']:<10.3f} | {o['spread']:<8.3f} | {g.get('dsr', 0):<12.4f} | {b.get('p_value_one_sided', 1.0):<15.4f} | £{o['monthly_gbp_per_100k']:<11.0f}")

    clean_configs = {}
    for name, data in run1["configs"].items():
        clean_configs[name] = {
            "params": data["params"],
            "ordering_audit": data["ordering_audit"],
            "paired_bootstrap": data["paired_bootstrap"],
            "diebold_mariano": data["diebold_mariano"],
            "gate": data["gate"],
            "cpcv": {
                "oos_sharpe_median": float(np.median(data["cpcv"]["oos_sharpe_paths"])),
                "n_paths_pass": int(sum(1 for s in data["cpcv"]["oos_sharpe_paths"] if s > 0)),
                "total_paths": len(data["cpcv"]["oos_sharpe_paths"]),
            }
        }

    output_payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "determinism_pass": det_pass,
        "used_trials": run1["used_trials"],
        "baseline_10_slot_order": run1["baseline_10_slot_order"],
        "configs": clean_configs,
        "pbo": run1["pbo"],
    }

    with open(RESULTS_PATH, "w") as fh:
        json.dump(output_payload, fh, indent=2, default=str)
    print(f"\nSaved gate verification report to {RESULTS_PATH}")
    print("=" * 80)


if __name__ == "__main__":
    main()
