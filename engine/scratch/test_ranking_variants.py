"""Audit ranking variants for slot_allocation:
1. "expected_value": p * b - (1 - p)
2. "probability": raw p
3. "ev_regime": EV * regime_multiplier

Evaluated at 10, 16, and 20 swing slots across 6 shuffled orderings.
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

rng = np.random.default_rng(42)
base_order = [i for i in gate_order if i in master]
orderings = [base_order] + [list(rng.permutation(base_order)) for _ in range(5)]

def evaluate_variant(alloc_name: str, slots: int):
    sharpes = []
    returns = []
    drawdowns = []
    trades = []
    
    for idx, order in enumerate(orderings):
        panel = {i: master[i] for i in order if i in master}
        pits = {k: PointInTimeAccessor(v) for k, v in panel.items()}
        
        cfg_copy = get_config()
        cfg_copy.risk.max_swing_slots = slots
        cfg_copy.risk.max_concurrent_trades = slots + 4
        
        backtester = PortfolioBacktester(cfg_copy, exit_mode="managed", slot_allocation=alloc_name)
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

    ann_ret_approx = (1 + med_ret) ** (1.0 / 9.0) - 1.0
    monthly_gbp = (ann_ret_approx / 12.0) * 100000.0

    return {
        "label": f"{alloc_name} ({slots} slots)",
        "alloc_name": alloc_name,
        "slots": slots,
        "median_sharpe": med_sharpe,
        "spread": spread,
        "median_drawdown": med_dd,
        "monthly_gbp": monthly_gbp,
        "all_sharpes": [round(s, 3) for s in sharpes],
    }

print("=" * 80)
print("  EVALUATING SLOT ALLOCATION RANKING VARIANTS AT 10, 16, AND 20 SLOTS")
print("=" * 80)

for slots in [10, 16, 20]:
    for alloc in ["expected_value", "probability", "ev_regime"]:
        r = evaluate_variant(alloc, slots)
        print(f"{r['label']:<30s} | Med Sharpe: {r['median_sharpe']:.3f} | Spread: {r['spread']:.3f} | MaxDD: {r['median_drawdown']*100:.1f}% | £/mo @ 6% vol: £{r['monthly_gbp']:.0f}")

print("=" * 80)
