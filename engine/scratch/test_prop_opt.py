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
from run_portfolio_gate_multiasset import FX_MAJORS_7

cfg = get_config()
store = ParquetStore(cfg.store_path)

# Full Universe minus structural losers UBER, DOGE/USD, AUD/USD
pruned_universe = [inst for inst in (EQUITY_CORE + [GOLD_ETC] + list(cfg.data.crypto) + list(FX_MAJORS_7)) if inst not in ["UBER", "DOGE/USD", "AUD/USD"]]

master = {inst: clean(store.load(inst, "1d")) for inst in pruned_universe if not store.load(inst, "1d").empty}
pits = {k: PointInTimeAccessor(v) for k, v in master.items()}
timeframes = {k: "1d" for k in master}

# Run with Prop-Scaled 1.0% Max Risk
book_params = {"carry_filter": False, **COMMON_PARAMS, "momentum_lookback": 252, "max_risk_per_trade": 0.010}
model = TrendBook(master, **book_params)

res = PortfolioBacktester(cfg, exit_mode="managed").run(pits, model.strategies(), timeframes=timeframes, warmup=WARMUP, periods_per_year=252)

eq = res.returns.add(1.0).cumprod() * 100000.0
ret = (eq.iloc[-1] / 100000.0) - 1.0
cagr = (1 + ret) ** (1 / 10.6) - 1.0
sharpe = sharpe_ratio(res.returns, periods_per_year=252)
peak = eq.cummax()
dd = (eq - peak) / peak
max_dd = float(abs(dd.min()))

print("=" * 70)
print("OPTIMIZED PROP PORTFOLIO RESULTS (Pruned + 1.0% Scaled Risk):")
print("=" * 70)
print(f"Ending Equity:    ${eq.iloc[-1]:,.2f}")
print(f"Total Return:     {ret*100:+.2f}%")
print(f"Annual Return:    {cagr*100:.2f}% / year")
print(f"Sharpe Ratio:     {sharpe:.2f}")
print(f"Max Drawdown:     {max_dd*100:.2f}%")
print(f"Total Trades:     {res.metrics['n_trades']}")
print(f"Win Rate:         {res.metrics['win_rate']*100:.1f}%")
print(f"Profit Factor:    {res.metrics.get('profit_factor', 0.0):.2f}")
