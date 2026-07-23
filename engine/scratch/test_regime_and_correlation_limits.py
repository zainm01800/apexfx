"""Audit regime gating and correlation cap parameters across 6 shuffled orderings.

Evaluates:
1. use_regime = True vs False
2. correlation_threshold = 0.50, 0.60, 0.70
3. max_correlated_exposure = 1.0, 1.2, 1.5, 1.8

All at 20 swing slots with expected_value slot allocation across 6 orderings.
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

def evaluate_regime_and_corr(use_regime: bool, corr_thresh: float, max_corr_exp: float, slots: int = 20):
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
        cfg_copy.risk.correlation_threshold = corr_thresh
        cfg_copy.risk.max_correlated_exposure = max_corr_exp
        
        backtester = PortfolioBacktester(cfg_copy, exit_mode="managed", use_regime=use_regime, slot_allocation="expected_value")
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
        "label": f"regime={use_regime}, corr_t={corr_thresh}, corr_exp={max_corr_exp}",
        "use_regime": use_regime,
        "corr_thresh": corr_thresh,
        "max_corr_exp": max_corr_exp,
        "median_sharpe": med_sharpe,
        "spread": spread,
        "median_drawdown": med_dd,
        "median_trades": med_trades,
        "monthly_gbp": monthly_gbp,
        "all_sharpes": [round(s, 3) for s in sharpes],
    }

print("=" * 80)
print("  EVALUATING REGIME GATING & CORRELATION EXPOSURE CEILINGS (6 ORDERINGS)")
print("=" * 80)

test_configs = [
    (True, 0.60, 1.5),   # Baseline settings
    (False, 0.60, 1.5),  # Disable regime classifier
    (True, 0.50, 1.5),   # Stricter correlation threshold
    (True, 0.70, 1.5),   # Looser correlation threshold
    (True, 0.60, 1.2),   # Stricter correlated exposure cap
    (True, 0.60, 1.8),   # Looser correlated exposure cap
]

for reg, c_t, c_e in test_configs:
    r = evaluate_regime_and_corr(reg, c_t, c_e, slots=20)
    print(f"{r['label']:<45s} | Med Sharpe: {r['median_sharpe']:.3f} | Spread: {r['spread']:.3f} | MaxDD: {r['median_drawdown']*100:.1f}% | £/mo @ 6% vol: £{r['monthly_gbp']:.0f}")

print("=" * 80)
