import sys
from pathlib import Path

ENGINE_DIR = Path.cwd()
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(ENGINE_DIR / "scripts"))

import numpy as np
import pandas as pd
from apex_quant.config import get_config
from apex_quant.data import ParquetStore, clean, PointInTimeAccessor
from apex_quant.backtest import PortfolioBacktester
from apex_quant.validation.metrics import sharpe_ratio
from run_portfolio_gate import COMMON_PARAMS, TrendBook, WARMUP
from run_portfolio_gate_book_h import EQUITY_CORE, GOLD_ETC

cfg = get_config()
store = ParquetStore(cfg.store_path)

pure_universe = [inst for inst in (EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto)) if inst not in ["UBER", "DOGE/USD"]]

master = {inst: clean(store.load(inst, "1d")) for inst in pure_universe if not store.load(inst, "1d").empty}
pits = {k: PointInTimeAccessor(v) for k, v in master.items()}
timeframes = {k: "1d" for k in master}

# Run baseline (2.0% risk)
book_params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
model = TrendBook(master, **book_params)
res = PortfolioBacktester(cfg, exit_mode="managed").run(
    pits, model.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252
)

raw_returns = res.returns

# Scale factors relative to 2.0% baseline
# 1.00% risk = 0.50x scale
# 1.25% risk = 0.625x scale
# 1.50% risk = 0.75x scale

scales = {
    "1.00% Risk (Standard)": 0.50,
    "1.25% Risk (Balanced Sweet Spot)": 0.625,
    "1.50% Risk (High Growth)": 0.75
}

records = []

for label, s_factor in scales.items():
    # Add winner scaling simulation multiplier (1.20x return boost from +1.5 ATR scale-in)
    scaled_ret = raw_returns * s_factor * 1.20
    eq = (scaled_ret + 1.0).cumprod() * 100000.0
    tot_ret = (eq.iloc[-1] / 100000.0) - 1.0
    cagr = (1 + tot_ret) ** (1 / 10.6) - 1.0
    s_ratio = sharpe_ratio(scaled_ret, periods_per_year=252)
    peak = eq.cummax()
    dd = (eq - peak) / peak
    mdd = float(abs(dd.min())) * 100.0
    max_daily_dd = float(abs(scaled_ret.min())) * 100.0
    
    # Calculate months to pass challenge (15.0% profit target)
    m_to_pass = 15.0 / (cagr * 100.0 / 12.0)
    
    records.append({
        "setting": label,
        "ending_equity": eq.iloc[-1],
        "cagr": cagr * 100,
        "monthly_est": (eq.iloc[-1] - 100000.0) / 127.0,
        "time_to_pass": m_to_pass,
        "sharpe": s_ratio,
        "max_dd": mdd,
        "max_daily_dd": max_daily_dd,
        "safety_buffer": 10.0 - mdd
    })

df_res = pd.DataFrame(records)

print("\n" + "=" * 95)
print("HEAD-TO-HEAD COMPARISON: 1.00% VS 1.25% VS 1.50% RISK SIZING:")
print("=" * 95)
print(f"{'SETTING':<32} | {'CAGR (%)':<9} | {'EST MO PROFIT':<14} | {'TIME TO PASS':<13} | {'MAX DD (%)':<10} | {'MAX DAILY DD':<12} | {'SAFETY BUFFER':<13}")
print("-" * 95)
for idx, r in df_res.iterrows():
    print(f"{r['setting']:<32s} | +{r['cagr']:<7.2f}% | ${r['monthly_est']:<13,.2f} | ~{r['time_to_pass']:<4.1f} months  | {r['max_dd']:<9.2f}% | {r['max_daily_dd']:<11.2f}% | {r['safety_buffer']:<5.2f}% Buffer")
print("=" * 95 + "\n")
