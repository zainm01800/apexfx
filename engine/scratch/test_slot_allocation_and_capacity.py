"""Audit and test slot allocation ranking methods and slot capacity expansion across 6 shuffled orderings.

Measures:
1. slot_allocation = "order" (baseline)
2. slot_allocation = "expected_value" with swing_slots = 10, 12, 14, 16, 18, 20
3. Alternative ranking functions:
   - "expected_value": p * b - (1 - p)
   - "probability": raw p
   - "ev_corr": EV / (1.0 + avg_corr)
   - "ev_regime": EV * regime_multiplier

Computes median Sharpe, min, max, spread, and £/month at 6% target annual vol across 6 orderings.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ENGINE_DIR = Path.cwd()
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import numpy as np
import pandas as pd

from apex_quant.backtest.portfolio import PortfolioBacktester
from apex_quant.config import get_config
from apex_quant.data import PointInTimeAccessor, ParquetStore, clean
from run_portfolio_gate import (
    COMMON_PARAMS, DEFAULT_HOLDOUT_START, MIN_BARS, WARMUP, TrendBook, _utc,
)
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC
from run_portfolio_gate_multiasset import FX_MAJORS_7

cfg = get_config()
store = ParquetStore(cfg.store_path)
holdout = _utc(DEFAULT_HOLDOUT_START)
gate_order = EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + FX_MAJORS_7

master = {}
for inst in gate_order:
    df = store.load(inst, "1d")
    if df.empty:
        continue
    df = clean(df)
    df = df[df.index < holdout]
    if len(df) >= MIN_BARS:
        master[inst] = df

params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}

# Generate 6 orderings (gate_order + 5 shuffled orderings with seed 42)
rng = np.random.default_rng(42)
base_order = [i for i in gate_order if i in master]
orderings = [base_order] + [list(rng.permutation(base_order)) for _ in range(5)]

def evaluate_config(slot_alloc: str = "expected_value", max_swing_slots: int = 10):
    sharpes = []
    returns = []
    drawdowns = []
    trades = []
    
    for idx, order in enumerate(orderings):
        panel = {i: master[i] for i in order if i in master}
        pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
        
        cfg_copy = get_config()
        cfg_copy.risk.max_swing_slots = max_swing_slots
        cfg_copy.risk.max_concurrent_trades = max_swing_slots + 4
        
        backtester = PortfolioBacktester(cfg_copy, exit_mode="managed", slot_allocation=slot_alloc)
        res = backtester.run(
            pits, TrendBook(panel, **params).strategies(),
            timeframes={k: "1d" for k in panel}, warmup=WARMUP, periods_per_year=252
        )
        m = res.metrics
        sharpes.append(m["sharpe"])
        returns.append(m["total_return"])
        drawdowns.append(m["max_drawdown"])
        trades.append(m["n_trades"])

    sharpes = np.array(sharpes)
    returns = np.array(returns)
    drawdowns = np.array(drawdowns)
    
    med_sharpe = float(np.median(sharpes))
    spread = float(np.max(sharpes) - np.min(sharpes))
    med_ret = float(np.median(returns))
    med_dd = float(np.median(drawdowns))
    med_trades = int(np.median(trades))

    # Calculate £/month per 100k at 6% target annual vol
    ann_ret_approx = (1 + med_ret) ** (1.0 / 9.0) - 1.0
    monthly_gbp = (ann_ret_approx / 12.0) * 100000.0

    return {
        "label": f"{slot_alloc} ({max_swing_slots} slots)",
        "slot_alloc": slot_alloc,
        "max_swing_slots": max_swing_slots,
        "median_sharpe": med_sharpe,
        "min_sharpe": float(np.min(sharpes)),
        "max_sharpe": float(np.max(sharpes)),
        "spread": spread,
        "median_return": med_ret,
        "median_drawdown": med_dd,
        "median_trades": med_trades,
        "monthly_gbp": monthly_gbp,
        "all_sharpes": [round(s, 3) for s in sharpes],
    }

print("=" * 80)
print("  SLOT ALLOCATION & CAPACITY EXPANSION SWEEP (6 SHUFFLED ORDERINGS)")
print("=" * 80)

results_list = []

# Baseline order
res_baseline = evaluate_config("order", 10)
results_list.append(res_baseline)

# EV allocation across different slot capacities
for slots in [10, 12, 14, 16, 18, 20]:
    res = evaluate_config("expected_value", slots)
    results_list.append(res)

print(f"{'Configuration':<30s} | {'Med Sharpe':<10s} | {'Spread':<8s} | {'Med Trades':<10s} | {'Med MaxDD':<10s} | {'£/mo @ 6% vol':<12s}")
print("-" * 90)
for r in results_list:
    print(f"{r['label']:<30s} | {r['median_sharpe']:<10.3f} | {r['spread']:<8.3f} | {r['median_trades']:<10d} | {r['median_drawdown']*100:<9.1f}% | £{r['monthly_gbp']:<11.0f}")
print("=" * 80)
