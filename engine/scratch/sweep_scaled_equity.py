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

# Run unscaled base run
book_params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252}
model = TrendBook(master, **book_params)
res = PortfolioBacktester(cfg, exit_mode="managed").run(
    pits, model.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252
)

raw_returns = res.returns

# Scale factors representing Risk/Trade levels relative to 2.0% unscaled baseline:
#   0.25x scale -> 0.50% risk per trade
#   0.375x scale -> 0.75% risk per trade
#   0.50x scale -> 1.00% risk per trade
#   0.75x scale -> 1.50% risk per trade
#   1.00x scale -> 2.00% risk per trade (unscaled)

scale_map = {
    "0.25% Risk (Ultra-Safe)": 0.125,
    "0.50% Risk (Prop Recommended)": 0.25,
    "0.75% Risk (Balanced Prop)": 0.375,
    "1.00% Risk (Aggressive Prop)": 0.50,
    "2.00% Risk (Personal Account)": 1.00,
}

records = []

for label, s_factor in scale_map.items():
    scaled_ret = raw_returns * s_factor
    eq = (scaled_ret + 1.0).cumprod() * 100000.0
    tot_ret = (eq.iloc[-1] / 100000.0) - 1.0
    cagr = (1 + tot_ret) ** (1 / 10.6) - 1.0
    s_ratio = sharpe_ratio(scaled_ret, periods_per_year=252)
    peak = eq.cummax()
    dd = (eq - peak) / peak
    mdd = float(abs(dd.min())) * 100.0
    max_daily_dd = float(abs(scaled_ret.min())) * 100.0
    
    # Check FTMO / Prop Rules compliance (Max DD < 10%, Daily DD < 5%)
    prop_status = "SAFE (Compliant) ✓" if mdd < 10.0 and max_daily_dd < 5.0 else "BREACH RISK ⚠️"
    
    records.append({
        "setting": label,
        "ending_equity": eq.iloc[-1],
        "cagr": cagr * 100,
        "monthly_est": (eq.iloc[-1] - 100000.0) / 127.0,
        "sharpe": s_ratio,
        "max_dd": mdd,
        "max_daily_dd": max_daily_dd,
        "status": prop_status
    })

df_eval = pd.DataFrame(records)

print("\n" + "=" * 90)
print("COMPREHENSIVE RISK & PROFIT RE-EVALUATION FOR $100K FUNDED PROP ACCOUNT:")
print("=" * 90)
print(f"{'SETTING / RISK LEVEL':<30} | {'END EQUITY ($)':<14} | {'CAGR (%)':<9} | {'EST MO PROFIT':<14} | {'MAX DD (%)':<10} | {'DAILY DD (%)':<12} | {'PROP STATUS':<18}")
print("-" * 90)
for idx, r in df_eval.iterrows():
    print(f"{r['setting']:<30s} | ${r['ending_equity']:<13,.2f} | +{r['cagr']:<7.2f}% | ${r['monthly_est']:<13,.2f} | {r['max_dd']:<9.2f}% | {r['max_daily_dd']:<11.2f}% | {r['status']:<18s}")
print("=" * 90 + "\n")
