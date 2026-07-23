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

risk_levels = [0.0025, 0.0035, 0.005, 0.0075, 0.010]

records = []

for r_level in risk_levels:
    b_params = {
        "carry_filter": False,
        **COMMON_PARAMS,
        "momentum_lookback": 252,
        "max_risk_per_trade": r_level,
        "reward_risk_target": 1.5,
        "swing_bucket": 10
    }
    model = TrendBook(master, **b_params)
    res = PortfolioBacktester(cfg, exit_mode="managed").run(
        pits, model.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252
    )
    
    eq = res.returns.add(1.0).cumprod() * 100000.0
    tot_ret = (eq.iloc[-1] / 100000.0) - 1.0
    cagr = (1 + tot_ret) ** (1 / 10.6) - 1.0
    s_ratio = sharpe_ratio(res.returns, periods_per_year=252)
    peak = eq.cummax()
    dd = (eq - peak) / peak
    mdd = float(abs(dd.min())) * 100.0
    
    daily_returns = res.returns
    max_daily_loss = float(abs(daily_returns.min())) * 100.0
    
    pf = res.metrics.get("profit_factor", 0.0)
    n_tr = res.metrics.get("n_trades", 0)
    win_rate = res.metrics.get("win_rate", 0.0) * 100.0
    
    records.append({
        "risk_per_trade": r_level * 100,
        "ending_equity": eq.iloc[-1],
        "total_return": tot_ret * 100,
        "cagr": cagr * 100,
        "sharpe": s_ratio,
        "max_dd": mdd,
        "max_daily_dd": max_daily_loss,
        "profit_factor": pf,
        "win_rate": win_rate
    })

df_res = pd.DataFrame(records)

print("EVALUATION OF PROP RISK LEVELS (10-YEAR WALK-FORWARD 2016-2026):")
print("=" * 85)
print(f"{'RISK/TRADE':<10} | {'END EQUITY ($)':<14} | {'SHARPE':<8} | {'MAX DD (%)':<12} | {'DAILY DD (%)':<14} | {'CAGR (%)':<10} | {'PF':<6}")
print("-" * 85)
for idx, r in df_res.iterrows():
    print(f"{r['risk_per_trade']:<10.2f}% | ${r['ending_equity']:<13,.2f} | {r['sharpe']:<8.2f} | {r['max_dd']:<12.2f}% | {r['max_daily_dd']:<14.2f}% | +{r['cagr']:<9.2f}% | {r['profit_factor']:.2f}")
